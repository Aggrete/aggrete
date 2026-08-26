# Aggrete — working notes

An MCP proxy enforcing a code-of-conduct document across connectors, with
per-user state that accumulates across calls and sessions (Layer 4).

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -q
.venv/bin/python demo/run_demo.py
```

`mcp>=2.0` needs Python ≥3.10 (system `python3` here is 3.9, so use `/opt/homebrew/bin/python3.13 -m venv .venv`). If `python3` on PATH lacks the deps, point `.mcp.json` and the `upstreams`
entries in `proxy.config.yaml` at `.venv/bin/python`. The proxy substitutes its
own interpreter for upstreams declared as `python`/`python3`, so setting it in
`.mcp.json` alone is usually enough.

## Registering with Claude Code

`.mcp.json` in the project root is picked up automatically — `/mcp` lists
`aggrete` and its five namespaced tools. Approve the server once when
prompted. To use it outside this directory:

```bash
claude mcp add aggrete -- /abs/path/.venv/bin/python -m aggrete.proxy \
  --config /abs/path/proxy.config.yaml
```

Run it against the mocks first: ask for the Q3 headcount plan, then backfill-only
roles, then recent joiners, then on-call gaps. The fourth is denied by COC-HR-004
before the upstream is called, and the assistant relays the clause and the
remediation path rather than an error.

## Layout

| Path | Role |
|---|---|
| `coc.yaml` | source of truth — clause text, enforcement, tests |
| `aggrete/policy.py` | deterministic evaluation; no model in this path |
| `aggrete/accumulator.py` | per-user state, TTL'd; `MemoryStore` / `RedisStore` |
| `aggrete/entities.py` | person-ID extraction from tool results |
| `aggrete/proxy.py` | MCP server (stdio or streamable HTTP) + upstream clients, pre/post enforcement |
| `aggrete/auth.py` | bearer-token verification (JWT via JWKS/PEM, or static dev tokens) → user identity |
| `proxy.config.yaml` | tool-pattern → domain mapping, upstream wiring (`command:` stdio or `url:` streamable HTTP; header values may use `${ENV_VAR}`) |
| `demo/mock_server.py` | fake HR / finance / ops connectors (`--transport streamable-http` for HTTP) |
| `aggrete/ingest.py` | document (PDF/DOCX/MD) → draft `coc.yaml` via Claude; verifies drafts through `Engine` |

## Conventions

- Policy changes go in `coc.yaml`, never in Python. Every rule needs an allow
  test and a deny/alert test; `test_every_rule_has_positive_and_negative_coverage`
  enforces that.
- New rule types are implemented in `Engine.pre_call` / `Engine.post_call` and
  dispatched by the `type:` key. Prefer pre-call decidability where possible.
- Actions start at `alert` and move to `deny` only after tuning on real traffic.
- Audit rows go to stderr and `audit.jsonl`. Keep them one JSON object per line.

## Known gaps (in priority order)

1. `entities.py` — tune `EMAIL_KEYS`/`ID_KEYS` against real connector payloads. Identifiers on one JSON object collapse to one person (email preferred as the canonical key so it matches across connectors); records that carry only a source-system ID will not link to email-only records from another connector without an external identity map.
   Every threshold in `coc.yaml` is only as good as this function.
2. Identity: solved for HTTP (`--transport streamable-http` requires `auth:`;
   user derived from the token, see `aggrete/auth.py`). stdio identity remains
   advisory by design — use it only on a single laptop.
3. No multi-tenancy, token vault, or HA. For production, port `policy.py` onto
   agentgateway or IBM ContextForge as a plugin rather than running this as the
   control plane.
