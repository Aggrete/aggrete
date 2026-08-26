"""End-to-end: proxy in front of a mock connector served over streamable HTTP."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} never opened")


@pytest.fixture
def http_ops_server():
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "demo/mock_server.py", "--profile", "ops",
         "--transport", "streamable-http", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_port(port)
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture
def http_config(tmp_path, http_ops_server):
    cfg = yaml.safe_load((ROOT / "proxy.config.yaml").read_text())
    cfg["coc"] = str(ROOT / "coc.yaml")
    cfg["audit_log"] = str(tmp_path / "audit.jsonl")
    cfg["user"] = "http-test@example.com"
    cfg["upstreams"] = {
        "ops": {
            "url": f"http://127.0.0.1:{http_ops_server}/mcp",
            "headers": {"Authorization": "Bearer ${OPS_MCP_TOKEN}"},
        }
    }
    path = tmp_path / "proxy.config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


async def _call_through_proxy(config_path: Path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aggrete.proxy", "--config", str(config_path)],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT), "OPS_MCP_TOKEN": "test-token"},
    )
    async with contextlib.AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools = [t.name for t in (await session.list_tools()).tools]
        result = await session.call_tool("ops__oncall_draft", {"team": "platform", "quarter": "Q3"})
        return tools, result


def test_proxy_reaches_connector_over_streamable_http(http_config):
    tools, result = asyncio.run(_call_through_proxy(http_config))
    assert "ops__oncall_draft" in tools
    text = result.content[0].text
    assert '"shifts"' in text, text  # fresh user, no prior domains: allowed


def test_missing_env_reference_fails_loudly(tmp_path):
    from aggrete.proxy import expand_env
    os.environ.pop("DEFINITELY_UNSET_TOKEN", None)
    with pytest.raises(KeyError):
        expand_env("Bearer ${DEFINITELY_UNSET_TOKEN}")
