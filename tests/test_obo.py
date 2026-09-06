"""On-behalf-of (per-user) credentials: resolution, credential merging, and an
end-to-end check that the per-user credential reaches the connector."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from aggrete import credentials
from aggrete.accumulator import MemoryStore
from aggrete.audit import Audit
from aggrete.policy import Engine
from aggrete.proxy import Proxy

COC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "coc.yaml")


# ---- credentials.resolve ----

def test_non_per_user_returns_none():
    assert credentials.resolve({"command": "x"}, "sam@corp", "drive") is None


def test_default_passes_identity_as_env():
    c = credentials.resolve({"per_user": True}, "sam@corp", "drive")
    assert c.env == {"AGGRETE_ACTING_USER": "sam@corp"} and c.headers == {}


def test_static_user_map():
    spec = {"per_user": True, "obo": {"users": {"sam@corp": {"env": {"DELEGATE": "sam@corp"},
                                                             "headers": {"X-User": "sam@corp"}}}}}
    c = credentials.resolve(spec, "sam@corp", "drive")
    assert c.env == {"DELEGATE": "sam@corp"} and c.headers == {"X-User": "sam@corp"}


def test_command_hook_token_exchange():
    # A tiny "vault" that echoes the user it was asked about into an env var.
    script = 'import os,json;print(json.dumps({"env":{"TOKEN":"tok-"+os.environ["AGGRETE_USER"]}}))'
    spec = {"per_user": True, "obo": {"command": [sys.executable, "-c", script]}}
    c = credentials.resolve(spec, "sam@corp", "drive")
    assert c.env == {"TOKEN": "tok-sam@corp"}


def test_command_hook_failure_raises():
    spec = {"per_user": True, "obo": {"command": [sys.executable, "-c", "import sys;sys.exit(3)"]}}
    with pytest.raises(RuntimeError):
        credentials.resolve(spec, "sam@corp", "drive")


# ---- credential merging in the proxy (per-user extras win) ----

def _proxy(cfg):
    return Proxy(cfg, Engine(COC, MemoryStore()), Audit(None))


def test_stdio_params_merge_per_user_env():
    p = _proxy({})
    params = p._stdio_params({"command": "python3", "args": ["s.py"], "env": {"BASE": "1"}},
                             extra_env={"AGGRETE_ACTING_USER": "sam@corp", "BASE": "2"})
    assert params.env["AGGRETE_ACTING_USER"] == "sam@corp"
    assert params.env["BASE"] == "2"          # per-user wins over the shared env


def test_http_headers_per_user_wins():
    p = _proxy({})
    h = p._http_headers({"headers": {"Authorization": "Bearer shared"}},
                        extra_headers={"Authorization": "Bearer sam"})
    assert h["Authorization"] == "Bearer sam"


# ---- end to end: a per_user connector receives the caller's identity ----

_ECHO_CONNECTOR = '''
import asyncio, os, json
from mcp.server.mcpserver import MCPServer
s = MCPServer("id")
@s.tool(description="Return the acting user the proxy injected.")
def whoami() -> str:
    return json.dumps({"acting_user": os.environ.get("AGGRETE_ACTING_USER", "")})
asyncio.run(s.run_stdio_async())
'''


def test_per_user_credential_reaches_the_connector(tmp_path):
    conn = tmp_path / "echo_server.py"; conn.write_text(_ECHO_CONNECTOR)
    (tmp_path / "coc.yaml").write_text("version: 1\nrules: []\n")
    (tmp_path / "proxy.config.yaml").write_text(
        "coc: coc.yaml\n"
        "user: sam@corp\n"
        "builtin_tools: false\n"
        "upstreams:\n"
        f"  id: {{command: python3, args: ['{conn}'], per_user: true}}\n"
        "domains: {}\n")

    async def go():
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.types import TextContent
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "aggrete.proxy", "--config", str(tmp_path / "proxy.config.yaml")])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("id__whoami", {})
                text = "".join(c.text for c in res.content if isinstance(c, TextContent))
                return json.loads(text)

    out = asyncio.run(go())
    # The proxy opened a per-user session and injected the caller's identity.
    assert out["acting_user"] == "sam@corp"
