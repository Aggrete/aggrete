"""Aggrete as a component inside someone else's gateway.

Two shapes, both transport-agnostic:

PolicyHook — the engine + accumulator behind two calls. Any gateway plugin
    system (IBM ContextForge, agentgateway, Kong, your own) can call
    `before()` ahead of forwarding a tools/call and `after()` with the result.

AggreteMiddleware — an ASGI middleware that does the same for any MCP server
    speaking streamable HTTP with JSON responses: it reads tools/call requests,
    refuses at pre-call without forwarding, and records entities from the
    response. Identity is a callable over the ASGI scope, so it composes with
    whatever auth the host already performs.

Neither knows about upstreams; that stays the host's job.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from .accumulator import MemoryStore, Store
from .entities import extract
from .policy import Decision, Engine


@dataclass
class Verdict:
    allow: bool
    message: str | None = None       # clause + remediation when refused
    rule_id: str | None = None
    alerts: list | None = None
    evidence: dict | None = None


class PolicyHook:
    def __init__(self, coc_path: str, domains: dict[str, str] | None = None,
                 default_domain: str = "unclassified", store: Store | None = None,
                 audit: Callable[[dict], None] | None = None):
        self.engine = Engine(coc_path, store or MemoryStore())
        self.domains = domains or {}
        self.default_domain = default_domain
        self.audit = audit or (lambda row: None)

    def domain_for(self, tool: str) -> str:
        for pattern, domain in self.domains.items():
            if fnmatch.fnmatch(tool, pattern):
                return domain
        return self.default_domain

    def before(self, user: str, tool: str) -> Verdict:
        domain = self.domain_for(tool)
        d = self.engine.pre_call(user, domain)
        self.audit({"user": user, "tool": tool, "domain": domain, "stage": "pre",
                    "decision": "allow" if d.allow else "deny", "rule": d.rule_id, "evidence": d.evidence})
        return self._verdict(d)

    def after(self, user: str, tool: str, result_text: str) -> Verdict:
        domain = self.domain_for(tool)
        ents = extract(result_text)
        d = self.engine.post_call(user, domain, ents)
        self.audit({"user": user, "tool": tool, "domain": domain, "stage": "post", "entities": len(ents),
                    "decision": "allow" if d.allow else "deny", "rule": d.rule_id, "alerts": d.alerts})
        return self._verdict(d)

    @staticmethod
    def _verdict(d: Decision) -> Verdict:
        return Verdict(allow=d.allow, message=None if d.allow else d.explain(),
                       rule_id=d.rule_id, alerts=d.alerts, evidence=d.evidence)


class AggreteMiddleware:
    """ASGI middleware: `app = AggreteMiddleware(app, hook, identity=lambda scope: ...)`.

    Applies to POST requests on `path` whose JSON-RPC method is tools/call.
    Requires the host to answer with `application/json` (not SSE) so the
    result can be inspected; streamable-HTTP servers usually have a
    json_response switch. Requests it doesn't understand pass through.
    """

    def __init__(self, app, hook: PolicyHook, identity: Callable[[dict], str | Awaitable[str]],
                 path: str = "/mcp"):
        self.app, self.hook, self.identity, self.path = app, hook, identity, path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self.path:
            return await self.app(scope, receive, send)

        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        try:
            rpc = json.loads(body)
        except ValueError:
            rpc = None
        if not isinstance(rpc, dict) or rpc.get("method") != "tools/call":
            return await self.app(scope, self._replay(body), send)

        tool = rpc["params"]["name"]
        user = self.identity(scope)
        if hasattr(user, "__await__"):
            user = await user
        pre = self.hook.before(user, tool)
        if not pre.allow:
            return await self._json(send, self._refusal(rpc["id"], pre.message))

        chunks, headers, status = [], [], 200

        async def capture(message):
            nonlocal headers, status
            if message["type"] == "http.response.start":
                status, headers = message["status"], message.get("headers", [])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, self._replay(body), capture)
        raw = b"".join(chunks)
        ctype = dict((k.lower(), v) for k, v in headers).get(b"content-type", b"")
        if status == 200 and ctype.startswith(b"application/json"):
            try:
                text = "".join(c.get("text", "") for c in json.loads(raw)["result"]["content"])
            except (ValueError, KeyError, TypeError):
                text = raw.decode("utf-8", "replace")
            post = self.hook.after(user, tool, text)
            if not post.allow:
                return await self._json(send, self._refusal(rpc["id"], post.message))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": raw})

    @staticmethod
    def _replay(body: bytes):
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return receive

    @staticmethod
    def _refusal(rpc_id, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": rpc_id,
                "result": {"content": [{"type": "text", "text": message}], "isError": False}}

    @staticmethod
    async def _json(send, payload: dict):
        data = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
        await send({"type": "http.response.body", "body": data})
