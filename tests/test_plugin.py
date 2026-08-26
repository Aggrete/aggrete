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
