"""An MCP proxy that enforces a code of conduct across connectors.

Speaks MCP to the client, MCP to each upstream server, and evaluates every
tools/call against accumulated state before and after execution.

    python -m aggrete.proxy --config proxy.config.yaml

    python -m aggrete.proxy --config proxy.config.yaml --transport streamable-http --port 8080

Identity: over stdio the user is `user:` from the config. Whoever launched
the process, advisory only. Over streamable HTTP every request must carry a
bearer token; the user is derived from its claims (see auth.py) and nothing in
the config can override it.
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

from .auth import build_verifier, identity_for, unexpired
from .entities import extract
from .policy import Engine
from .audit import Audit
from .redact import rules_from_config, redact

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



class Proxy:
    def __init__(self, config: dict, engine: Engine, audit: Audit):
        self.cfg = config
        self.engine = engine
        self.audit = audit
        self.sessions: dict[str, ClientSession] = {}
        self.static_user = config.get("user", "unknown")
        self.identity_claim = (config.get("auth") or {}).get("identity_claim")
        self.redact_rules = rules_from_config(config.get("redact"))

    @property
    def user(self) -> str:
        """Who the policy engine evaluates. Token first; config only when no token."""
        from mcp.server.auth.middleware.auth_context import get_access_token
        token = get_access_token()
        if token is None:
            return self.static_user
        if not unexpired(token):
            raise PermissionError("token expired")
        return identity_for(token, self.identity_claim)

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
                if not self.engine.tool_visible(self.user, self.domain_for(name)):
                    continue  # selective exposure: walls and blocks hide tools per user
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

        redacted: dict = {}
        if post.allow and self.redact_rules:
            result, redacted = self._redact_result(result)

        self.audit.emit(user=self.user, tool=name, domain=domain, stage="post",
                        entities=len(ents), decision="deny" if not post.allow else "allow",
                        entity_ids=(ents if self.cfg.get("audit_entities", True) else None),
                        rule=post.rule_id, alerts=post.alerts, evidence=post.evidence,
                        redacted=(redacted or None), purpose=pre.granted_purpose)

        if not post.allow:
            # The data left the upstream, but it does not reach the model.
            return self._refuse(post.explain())
        return result

    def _redact_result(self, result: types.CallToolResult):
        """Mask secrets/PII in text content before it reaches the model.
        Enforcement already ran on the original text; this only touches the
        payload handed back to the assistant."""
        total: dict = {}
        new_content = []
        for c in result.content:
            if isinstance(c, types.TextContent):
                masked, counts = redact(c.text, self.redact_rules)
                for k, v in counts.items():
                    total[k] = total.get(k, 0) + v
                new_content.append(types.TextContent(type="text", text=masked))
            else:
                new_content.append(c)
        result.content = new_content
        return result, total

    def _refuse(self, message: str) -> types.CallToolResult:
        # Not is_error: the model should read this and relay it, not retry it.
        return types.CallToolResult(content=[types.TextContent(type="text", text=message)])


def build_store(store_cfg: dict | None):
    """`store: {redis_url: redis://...}` for shared state across replicas; else in-memory."""
    if not store_cfg or not store_cfg.get("redis_url"):
        return None
    import redis
    from .accumulator import RedisStore
    client = redis.Redis.from_url(expand_env(store_cfg["redis_url"]))
    client.ping()
    return RedisStore(client, prefix=store_cfg.get("prefix", "aggrete"))


def cli() -> None:
    asyncio.run(main())


def build_http_app(server: Server, cfg: dict, connect):
    """Starlette app: bearer auth → auth context → MCP streamable HTTP at /mcp.

    `connect(stack)` is awaited inside the lifespan so upstream sessions live
    exactly as long as the HTTP server.
    """
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.authentication import AuthenticationMiddleware
    from starlette.routing import Route
    from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
    from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
    from mcp.server.auth.routes import build_resource_metadata_url, create_protected_resource_routes
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from pydantic import AnyHttpUrl

    auth_cfg = cfg.get("auth")
    if not auth_cfg:
        raise SystemExit("streamable-http requires an `auth:` block. Identity must come from a token")
    signin = None
    if auth_cfg.get("mode") == "builtin":
        from mcp.server.auth.provider import ProviderTokenVerifier
        from .signin import BuiltinAuthServer
        users = {k: expand_env(str(v)) for k, v in (auth_cfg.get("users") or {}).items()}
        signin = BuiltinAuthServer(users, auth_cfg["issuer"], auth_cfg.get("state"))
        verifier = ProviderTokenVerifier(signin)
    else:
        verifier = build_verifier(auth_cfg)
    http_cfg = cfg.get("http", {})
    manager = StreamableHTTPSessionManager(
        app=server, json_response=bool(http_cfg.get("json_response", False)),
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=bool(http_cfg.get("dns_rebinding_protection", False)),
            allowed_hosts=http_cfg.get("allowed_hosts", []), allowed_origins=http_cfg.get("allowed_origins", [])),
        session_idle_timeout=http_cfg.get("session_idle_timeout", 1800),
    )

    resource_url = auth_cfg.get("resource_url") or (auth_cfg["issuer"].rstrip("/") + "/mcp" if signin else None)
    routes = []
    metadata_url = None
    if signin:
        from mcp.server.auth.routes import create_auth_routes
        from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
        routes += create_auth_routes(provider=signin, issuer_url=AnyHttpUrl(auth_cfg["issuer"]),
                                     client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=["mcp"], default_scopes=["mcp"]),
                                     revocation_options=RevocationOptions(enabled=True))
        routes.append(Route("/signin", signin.signin, methods=["GET", "POST"]))

    brand = cfg.get("brand", {})
    if brand.get("icon_file"):
        from starlette.responses import FileResponse
        icon_path = str((Path(cfg.get("_config_dir", ".")) / brand["icon_file"]).resolve())
        async def _icon(request):
            return FileResponse(icon_path, media_type=brand.get("icon_mime", "image/svg+xml"))
        routes.append(Route("/icon.svg", _icon))
        routes.append(Route("/favicon.ico", _icon))
    if resource_url:
        routes += create_protected_resource_routes(
            resource_url=AnyHttpUrl(resource_url),
            authorization_servers=[AnyHttpUrl(auth_cfg["issuer"])] if auth_cfg.get("issuer") else [],
            scopes_supported=auth_cfg.get("required_scopes"), resource_name="aggrete")
        metadata_url = build_resource_metadata_url(AnyHttpUrl(resource_url))
    routes.append(Route("/mcp", endpoint=RequireAuthMiddleware(
        manager.handle_request, auth_cfg.get("required_scopes") or [], metadata_url),
        methods=["GET", "POST", "DELETE"]))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with contextlib.AsyncExitStack() as stack:
            await connect(stack)
            async with manager.run():
                yield

    return Starlette(routes=routes, lifespan=lifespan, middleware=[
        Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(verifier)),
        Middleware(AuthContextMiddleware),
    ])


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="proxy.config.yaml")
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--demo", action="store_true",
                    help="run a self-contained four-question walkthrough and exit (no config or auth)")
    args = ap.parse_args()

    if args.demo:
        from ._demo import run
        run()
        return

    cfg = yaml.safe_load(Path(args.config).read_text())
    root = Path(args.config).parent
    cfg["_config_dir"] = str(root)
    engine = Engine(str(root / cfg.get("coc", "coc.yaml")), build_store(cfg.get("store")),
                    pack_state_path=cfg.get("pack_state"))
    audit = Audit(cfg.get("audit_log"))
    proxy = Proxy(cfg, engine, audit)

    brand = cfg.get("brand", {})
    icons = None
    if brand.get("icon_url"):
        icons = [types.Icon(src=brand["icon_url"], mime_type=brand.get("icon_mime", "image/svg+xml"))]
    try:
        from importlib.metadata import version as _pkg_version
        _ver = _pkg_version("aggrete")
    except Exception:
        _ver = "0"
    server = Server(
        "aggrete",
        version=_ver,
        title=brand.get("title", "Aggrete"),
        website_url=brand.get("website_url", "https://aggrete.com"),
        icons=icons,
        on_list_tools=proxy.list_tools,
        on_call_tool=proxy.call_tool,
    )

    if args.transport == "streamable-http":
        import uvicorn
        app = build_http_app(server, cfg, proxy.connect)
        config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
        await uvicorn.Server(config).serve()
        return

    async with contextlib.AsyncExitStack() as stack:
        await proxy.connect(stack)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    cli()
