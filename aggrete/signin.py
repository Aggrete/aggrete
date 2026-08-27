"""Built-in sign-in: a small OAuth 2.1 authorization server inside the proxy.

For teams without an identity provider yet. Assistants (Claude, Cursor, ...)
register themselves (dynamic client registration), send the person to
/signin, the person types the email and passcode from the config, and the
proxy issues tokens whose identity is that email. Everything else in the
proxy then works exactly as with an IdP.

    auth:
      mode: builtin
      issuer: https://mcp.example.com          # this proxy's public URL
      users:
        alice@example.com: ${ALICE_PASSCODE}   # passcodes from the environment
      state: /var/lib/aggrete/signin.json      # registered clients and tokens survive restarts

Not a replacement for SSO: no MFA, no password policy. Use it to get going;
switch `mode: jwt` when the IdP is ready.
"""

from __future__ import annotations

import hmac
import html
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken,
                                      construct_redirect_uri)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

ACCESS_TTL = 8 * 3600
REFRESH_TTL = 30 * 86400
CODE_TTL = 300

PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · Aggrete</title>
<style>body{{font-family:system-ui,sans-serif;background:#FDFBF7;color:#0E1216;margin:0;display:grid;place-items:center;min-height:100vh}}
form{{background:#fff;border:1px solid #E8E3DA;border-radius:16px;padding:32px;width:min(380px,92vw);display:flex;flex-direction:column;gap:12px}}
h1{{font-size:20px;margin:0 0 4px}}p{{margin:0;color:#4A4F55;font-size:14px}}input{{font:inherit;padding:11px 12px;border:1px solid #E8E3DA;border-radius:9px}}
button{{font:inherit;font-weight:700;padding:12px;border:0;border-radius:999px;background:#C2410C;color:#fff;cursor:pointer}}.err{{color:#B42318;font-size:14px}}</style>
<form method="post"><h1>Sign in to Aggrete</h1><p>{client} wants to connect to your company's systems through Aggrete. Sign in so decisions are made for you, not for a shared account.</p>
{error}<input type="hidden" name="txn" value="{txn}"><input name="email" type="email" placeholder="you@company.com" required autofocus><input name="passcode" type="password" placeholder="passcode" required><button type="submit">Continue</button></form>"""


class BuiltinAuthServer:
    def __init__(self, users: dict[str, str], issuer: str, state_path: str | None = None):
        self.users = {k.strip().lower(): str(v) for k, v in users.items()}
        self.issuer = issuer.rstrip("/")
        self.state_path = Path(state_path) if state_path else None
        self.clients: dict[str, dict] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}
        self.pending: dict[str, dict] = {}
        self._load()

    # ---- persistence: clients and tokens survive a restart ----
    def _load(self):
        if self.state_path and self.state_path.exists():
            d = json.loads(self.state_path.read_text())
            self.clients = d.get("clients", {})
            self.access = {k: AccessToken(**v) for k, v in d.get("access", {}).items()}
            self.refresh = {k: RefreshToken(**v) for k, v in d.get("refresh", {}).items()}

    def _save(self):
        if not self.state_path:
            return
        now = time.time()
        self.access = {k: v for k, v in self.access.items() if (v.expires_at or 0) > now}
        self.refresh = {k: v for k, v in self.refresh.items() if (v.expires_at or 0) > now}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"clients": self.clients,
                                               "access": {k: v.model_dump(mode="json") for k, v in self.access.items()},
                                               "refresh": {k: v.model_dump(mode="json") for k, v in self.refresh.items()}}))
        self.state_path.chmod(0o600)

    # ---- OAuthAuthorizationServerProvider ----
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        c = self.clients.get(client_id)
        return OAuthClientInformationFull(**c) if c else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients[client_info.client_id] = client_info.model_dump(mode="json")
        self._save()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        txn = secrets.token_urlsafe(24)
        self.pending[txn] = {"client_id": client.client_id, "client_name": client.client_name or client.client_id,
                             "params": params, "at": time.time()}
        return f"{self.issuer}/signin?{urlencode({'txn': txn})}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        c = self.codes.get(authorization_code)
        return c if c and c.client_id == client.client_id else None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.subject, authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        r = self.refresh.get(refresh_token)
        return r if r and r.client_id == client.client_id else None

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        self.refresh.pop(refresh_token.token, None)
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.subject, None)

    async def load_access_token(self, token: str) -> AccessToken | None:
        t = self.access.get(token)
        if t and (t.expires_at or 0) > time.time():
            return t
        return None

    async def revoke_token(self, token) -> None:
        self.access.pop(getattr(token, "token", token), None)
        self.refresh.pop(getattr(token, "token", token), None)
        self._save()

    def _issue(self, client_id: str, scopes: list[str], subject: str | None, resource: str | None) -> OAuthToken:
        now = int(time.time())
        at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.access[at] = AccessToken(token=at, client_id=client_id, scopes=scopes, expires_at=now + ACCESS_TTL,
                                      resource=resource, subject=subject, claims={"iss": self.issuer, "email": subject})
        self.refresh[rt] = RefreshToken(token=rt, client_id=client_id, scopes=scopes, expires_at=now + REFRESH_TTL, subject=subject)
        self._save()
        return OAuthToken(access_token=at, expires_in=ACCESS_TTL, scope=" ".join(scopes) or None, refresh_token=rt)

    # ---- the sign-in page ----
    async def signin(self, request: Request):
        if request.method == "GET":
            txn = request.query_params.get("txn", "")
            p = self.pending.get(txn)
            if not p:
                return HTMLResponse("<p>This sign-in link has expired. Start again from your assistant.</p>", status_code=400)
            return HTMLResponse(PAGE.format(client=html.escape(p["client_name"]), txn=html.escape(txn), error=""))
        form = await request.form()
        txn, email, passcode = form.get("txn", ""), str(form.get("email", "")).strip().lower(), str(form.get("passcode", ""))
        p = self.pending.get(txn)
        if not p or time.time() - p["at"] > 600:
            return HTMLResponse("<p>This sign-in link has expired. Start again from your assistant.</p>", status_code=400)
        expected = self.users.get(email)
        if not expected or not hmac.compare_digest(expected, passcode):
            return HTMLResponse(PAGE.format(client=html.escape(p["client_name"]), txn=html.escape(txn),
                                            error='<p class="err">That email and passcode do not match.</p>'), status_code=401)
        self.pending.pop(txn)
        params: AuthorizationParams = p["params"]
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(code=code, scopes=params.scopes or ["mcp"], expires_at=time.time() + CODE_TTL,
                                             client_id=p["client_id"], code_challenge=params.code_challenge,
                                             redirect_uri=params.redirect_uri, redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                                             resource=params.resource, subject=email)
        return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state), status_code=302)
