"""Built-in sign-in: an assistant registers, the person signs in, the token carries their email."""
import asyncio, base64, hashlib, json, os, secrets, socket, subprocess, sys, time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import httpx2 as httpx
import pytest, yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
import contextlib

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


@pytest.fixture
def proxy(tmp_path):
    port = free_port(); issuer = f"http://127.0.0.1:{port}"
    cfg = yaml.safe_load((ROOT / "proxy.config.yaml").read_text())
    cfg.update({"coc": str(ROOT / "coc.yaml"), "audit_log": str(tmp_path / "audit.jsonl"),
                "auth": {"mode": "builtin", "issuer": issuer, "users": {"Alice@Corp.example": "${ALICE_PASSCODE}"}, "state": str(tmp_path / "signin.json")}})
    path = tmp_path / "p.yaml"; path.write_text(yaml.safe_dump(cfg))
    proc = subprocess.Popen([sys.executable, "-m", "aggrete.proxy", "--config", str(path), "--transport", "streamable-http", "--port", str(port)],
                            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT), "ALICE_PASSCODE": "open-sesame"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0: break
            time.sleep(0.1)
        yield issuer, tmp_path / "audit.jsonl"
    finally:
        proc.terminate(); proc.wait(timeout=5)


def oauth_dance(issuer, email, passcode):
    c = httpx.Client(follow_redirects=False)
    meta = c.get(issuer + "/.well-known/oauth-authorization-server").json()
    reg = c.post(meta["registration_endpoint"], json={"client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                                                        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none"}).json()
    verifier = secrets.token_urlsafe(40)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    r = c.get(meta["authorization_endpoint"], params={"client_id": reg["client_id"], "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                                                    "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz", "scope": "mcp"})
    assert r.status_code in (302, 307) and "/signin?txn=" in r.headers["location"]
    page = c.get(r.headers["location"]); assert page.status_code == 200 and "Claude wants to connect" in page.text
    txn = parse_qs(urlparse(r.headers["location"]).query)["txn"][0]
    bad = c.post(issuer + "/signin", data={"txn": txn, "email": email, "passcode": "wrong"}); assert bad.status_code == 401
    ok = c.post(issuer + "/signin", data={"txn": txn, "email": email, "passcode": passcode}); assert ok.status_code == 302
    q = parse_qs(urlparse(ok.headers["location"]).query); assert q["state"] == ["xyz"]
    tok = c.post(meta["token_endpoint"], data={"grant_type": "authorization_code", "code": q["code"][0], "client_id": reg["client_id"],
                                              "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "code_verifier": verifier}).json()
    assert tok["token_type"].lower() == "bearer" and tok["access_token"]
    return tok


def test_signin_flow_and_identity(proxy):
    issuer, audit = proxy
    assert httpx.get(issuer + "/mcp").status_code == 401
    assert httpx.get(issuer + "/.well-known/oauth-protected-resource/mcp").status_code == 200
    tok = oauth_dance(issuer, "alice@corp.example", "open-sesame")

    async def call():
        async with contextlib.AsyncExitStack() as st:
            client = create_mcp_http_client(headers={"Authorization": f"Bearer {tok['access_token']}"}); await st.enter_async_context(client)
            r, w = await st.enter_async_context(streamable_http_client(issuer + "/mcp", http_client=client))
            s = await st.enter_async_context(ClientSession(r, w)); await s.initialize()
            return (await s.call_tool("finance__headcount_plan", {"team": "platform"})).content[0].text
    assert "approved" in asyncio.run(call())
    row = json.loads(audit.read_text().splitlines()[-1]); assert row["user"] == "alice@corp.example"
    # refresh works, and the state file persists the client
    c = httpx.Client(); meta = c.get(issuer + "/.well-known/oauth-authorization-server").json()
    r2 = c.post(meta["token_endpoint"], data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": json.loads(open(str(audit).replace("audit.jsonl", "signin.json")).read())["clients"] and list(json.load(open(str(audit).replace("audit.jsonl", "signin.json")))["clients"])[0]})
    assert r2.status_code == 200 and r2.json()["access_token"] != tok["access_token"]
