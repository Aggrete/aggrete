"""An MCP proxy that enforces a code of conduct across connectors.

Speaks MCP to the client, MCP to each upstream server, and evaluates every
tools/call against accumulated state before and after execution.

    python -m aggrete.proxy --config proxy.config.yaml

Identity note: over stdio the user is whoever launched the process, which is
fine for per-user state but advisory as enforcement. A real deployment runs
this over streamable HTTP with OAuth and takes the subject from the token.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fnmatch
import json
import os
import re
import sys
import time
from pathlib import Path

import mcp.types as types
import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .entities import extract
from .policy import Engine

SEP = "__"
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(value: str) -> str:
    """Replace ${VAR} references so secrets stay out of the config file."""
    def sub(m):
        name = m.group(1)
        if name not in os.environ:
            raise KeyError(f"config references ${{{name}}} but it is not set")
        return os.environ[name]
    return ENV_REF.sub(sub, value)


class Audit:
    """Every tool call, its labels, and the decision. This is the record you
    will actually want later — prompts tell you what was asked, this tells you
    what was handed over."""

    def __init__(self, path: str | None):
        self.fh = open(path, "a") if path else None

    def emit(self, **row):
        row["ts"] = time.time()
        line = json.dumps(row, default=str)
        print(f"[audit] {line}", file=sys.stderr)
        if self.fh:
            self.fh.write(line + "\n")
            self.fh.flush()


class Proxy:
    def __init__(self, config: dict, engine: Engine, audit: Audit):
        self.cfg = config
        self.engine = engine
        self.audit = audit
        self.sessions: dict[str, ClientSession] = {}
        self.user = config.get("user", "unknown")

    def domain_for(self, tool: str) -> str:
        for pattern, domain in self.cfg.get("domains", {}).items():
            if fnmatch.fnmatch(tool, pattern):
                return domain
        return self.cfg.get("default_domain", "unclassified")

    async def connect(self, stack: contextlib.AsyncExitStack):
        for name, spec in self.cfg["upstreams"].items():
            if "url" in spec:
                read, write = await self._connect_http(stack, spec)
            elif "command" in spec:
                read, write = await self._connect_stdio(stack, spec)
            else:
                raise ValueError(f"upstream {name!r} needs either 'command' or 'url'")
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions[name] = session

    async def _connect_stdio(self, stack, spec):
        command = spec["command"]
        # Use the interpreter running the proxy, so a venv's deps resolve
        # for locally-spawned Python servers without hardcoding paths.
        if command in ("python", "python3"):
            command = sys.executable
        params = StdioServerParameters(
            command=command, args=spec.get("args", []), env=spec.get("env")
        )
        return await stack.enter_async_context(stdio_client(params))

    async def _connect_http(self, stack, spec):
        """Remote connector over streamable HTTP.

        `headers` values may reference ${ENV_VARS}, so a bearer token for the
        upstream lives in the proxy's environment, never in the YAML. This is
        the proxy's own credential to the connector; the end user never holds
        it, which is what makes the proxy un-bypassable.
        """
        headers = {k: expand_env(str(v)) for k, v in (spec.get("headers") or {}).items()}
        client = create_mcp_http_client(headers=headers or None)
        await stack.enter_async_context(client)
        return await stack.enter_async_context(
            streamable_http_client(expand_env(spec["url"]), http_client=client)
        )

    async def list_tools(self, ctx, params) -> types.ListToolsResult:
        tools: list[types.Tool] = []
        for upstream, session in self.sessions.items():
            for t in (await session.list_tools()).tools:
                name = f"{upstream}{SEP}{t.name}"
                if not self._tool_allowed(name):
                    continue  # tool filtering: what is never listed is never called
                tools.append(
                    types.Tool(
                        name=name,
                        title=t.title,
                        description=f"[{self.domain_for(name)}] {t.description or ''}".strip(),
                        inputSchema=t.input_schema,
                    )
                )
        return types.ListToolsResult(tools=tools)

    def _tool_allowed(self, name: str) -> bool:
        allow = self.cfg.get("allow_tools")
        deny = self.cfg.get("deny_tools", [])
        if any(fnmatch.fnmatch(name, p) for p in deny):
            return False
        return not allow or any(fnmatch.fnmatch(name, p) for p in allow)

    async def call_tool(self, ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        name, args = params.name, params.arguments or {}
        domain = self.domain_for(name)

        if not self._tool_allowed(name):
            return self._refuse(f"Tool {name} is not available through this gateway.")

        # --- Layer 3/4, before the fetch -----------------------------------
        pre = self.engine.pre_call(self.user, domain)
        if not pre.allow:
            self.audit.emit(user=self.user, tool=name, domain=domain, stage="pre",
                            decision="deny", rule=pre.rule_id, evidence=pre.evidence)
            return self._refuse(pre.explain())

        upstream, _, tool = name.partition(SEP)
        session = self.sessions.get(upstream)
        if session is None:
            return self._refuse(f"Unknown upstream {upstream!r}.")
        result = await session.call_tool(tool, args)

        # --- Layer 3/4, after the fetch ------------------------------------
        text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
        ents = extract(text)
        post = self.engine.post_call(self.user, domain, ents)

        self.audit.emit(user=self.user, tool=name, domain=domain, stage="post",
                        entities=len(ents), decision="deny" if not post.allow else "allow",
                        rule=post.rule_id, alerts=post.alerts, evidence=post.evidence,
                        purpose=pre.granted_purpose)

        if not post.allow:
            # The data left the upstream, but it does not reach the model.
            return self._refuse(post.explain())
        return result

    def _refuse(self, message: str) -> types.CallToolResult:
        # Not is_error: the model should read this and relay it, not retry it.
        return types.CallToolResult(content=[types.TextContent(type="text", text=message)])


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="proxy.config.yaml")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    root = Path(args.config).parent
    engine = Engine(str(root / cfg.get("coc", "coc.yaml")))
    audit = Audit(cfg.get("audit_log"))
    proxy = Proxy(cfg, engine, audit)

    async with contextlib.AsyncExitStack() as stack:
        await proxy.connect(stack)
        server = Server(
            "aggrete",
            version="0.1.0",
            on_list_tools=proxy.list_tools,
            on_call_tool=proxy.call_tool,
        )
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
