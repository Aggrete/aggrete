"""PolicyHook and AggreteMiddleware: the four turns through a fake host app."""
import asyncio, json, pathlib
from aggrete.plugin import AggreteMiddleware, PolicyHook

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOMAINS = {"finance__headcount_plan": "finance-planning", "finance__*": "finance-comp",
           "hr__*": "hr-personnel", "ops__*": "ops-rota"}
SIX = [f"{n}@example.com" for n in ["alice", "bob", "carol", "dan", "erin", "frank"]]
FAKE = {"finance__headcount_plan": {"approved": 24},
        "finance__budget_roles": {"lines": [{"owner_email": e} for e in SIX]},
        "hr__recent_joiners": {"joiners": [{"email": e, "employee_id": f"E{i}"} for i, e in enumerate(SIX)]},
        "ops__oncall_draft": {"shifts": [{"email": e} for e in SIX]}}


def test_policy_hook_denies_fourth_turn():
    rows = []
    hook = PolicyHook(str(ROOT / "coc.yaml"), DOMAINS, audit=rows.append)
    for tool in ["finance__headcount_plan", "finance__budget_roles", "hr__recent_joiners"]:
        assert hook.before("u", tool).allow
        assert hook.after("u", tool, json.dumps(FAKE[tool])).allow
    v = hook.before("u", "ops__oncall_draft")
    assert not v.allow and v.rule_id == "COC-HR-004" and "COC-HR-004" in v.message
    assert rows[-1]["decision"] == "deny"


async def host(scope, receive, send):
    """Stand-in for any MCP server answering tools/call with JSON."""
    msg = await receive(); rpc = json.loads(msg["body"])
    host.calls.append(rpc["params"]["name"])
    payload = {"jsonrpc": "2.0", "id": rpc["id"],
               "result": {"content": [{"type": "text", "text": json.dumps(FAKE[rpc["params"]["name"]])}]}}
    data = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": data})
host.calls = []


async def call(app, tool, user="u"):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": {}}}).encode()
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": [(b"x-user", user.encode())]}
    out = []
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    async def send(m):
        out.append(m)
    await app(scope, receive, send)
    return json.loads(b"".join(m.get("body", b"") for m in out if m["type"] == "http.response.body"))


def test_middleware_refuses_before_host_sees_the_call():
    host.calls.clear()
    hook = PolicyHook(str(ROOT / "coc.yaml"), DOMAINS)
    app = AggreteMiddleware(host, hook, identity=lambda s: dict(s["headers"])[b"x-user"].decode())
    for tool in ["finance__headcount_plan", "finance__budget_roles", "hr__recent_joiners"]:
        r = asyncio.run(call(app, tool))
        assert "content" in r["result"]
    r = asyncio.run(call(app, "ops__oncall_draft"))
    assert "COC-HR-004" in r["result"]["content"][0]["text"]
    assert host.calls == ["finance__headcount_plan", "finance__budget_roles", "hr__recent_joiners"]


def test_middleware_state_is_per_identity():
    host.calls.clear()
    hook = PolicyHook(str(ROOT / "coc.yaml"), DOMAINS)
    app = AggreteMiddleware(host, hook, identity=lambda s: dict(s["headers"])[b"x-user"].decode())
    for tool in ["finance__budget_roles", "hr__recent_joiners"]:
        asyncio.run(call(app, tool, user="alice"))
    r = asyncio.run(call(app, "ops__oncall_draft", user="bob"))
    assert "shifts" in r["result"]["content"][0]["text"]


def test_audit_rows_carry_entity_ids(tmp_path):
    """Post-call rows record who appeared, so the console can replay policies."""
    import json, subprocess, sys, os, asyncio, contextlib, yaml
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    cfg = yaml.safe_load((ROOT / "proxy.config.yaml").read_text())
    cfg["coc"] = str(ROOT / "coc.yaml"); cfg["audit_log"] = str(tmp_path / "a.jsonl")
    path = tmp_path / "p.yaml"; path.write_text(yaml.safe_dump(cfg))
    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "aggrete.proxy", "--config", str(path)],
                                       cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)})
        async with contextlib.AsyncExitStack() as st:
            r, w = await st.enter_async_context(stdio_client(params))
            s = await st.enter_async_context(ClientSession(r, w)); await s.initialize()
            await s.call_tool("finance__budget_roles", {"team": "platform"})
    asyncio.run(go())
    row = json.loads((tmp_path / "a.jsonl").read_text().splitlines()[-1])
    assert row["entities"] == 6 and len(row["entity_ids"]) == 6 and row["entity_ids"][0].startswith("p:")
