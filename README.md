# Aggrete

[![PyPI version](https://img.shields.io/pypi/v/aggrete)](https://pypi.org/project/aggrete/)
[![Python versions](https://img.shields.io/pypi/pyversions/aggrete)](https://pypi.org/project/aggrete/)
[![License](https://img.shields.io/pypi/l/aggrete)](https://github.com/aggrete/aggrete/blob/main/LICENSE)

<p align="center"><img src="docs/demo.gif" alt="Aggrete refuses the fourth question before the upstream is contacted" width="720"></p>

The open-source proxy. Product site: https://aggrete.com. This repo is the proxy and nothing else: engine, accumulator, ingest CLI, Helm chart.

An MCP proxy that enforces a **code of conduct document** across connectors, with
state that accumulates per user.

Every MCP gateway on the market authorizes tool calls and logs them. None of them
answer the question that actually matters once an assistant can reach Glean,
Salesforce, Slack and Drive at once: *is this call, combined with everything this
person has already pulled today, something the code of conduct forbids?*

Four individually-authorized questions can assemble a layoff list. No guardrail
fires, because no single question was sensitive. This proxy is the missing layer.

## Install

```bash
pip install aggrete            # published on PyPI
# or with uv:
uv tool install aggrete        # installs the aggrete CLI
uvx aggrete --demo             # or run it without installing

aggrete --config proxy.config.yaml
```

Or clone this repo to get the demo, sample policy and Helm chart.


## What the proxy does (0.2)

- **Try it in one command:** `aggrete --demo` (or `docker run --rm ghcr.io/aggrete/aggrete --demo`) runs the four-question walkthrough with no config, auth, or network.
- Refuses forbidden calls **before** the upstream is contacted, using a YAML
  policy and per-user memory that accumulates across calls and sessions.
- **Tamper-evident audit**: every decision is one hash-chained JSON line. Verify
  with `aggrete-audit audit.jsonl` (breaks are reported by line number).
- **Selective tool exposure**: walls and blocks in the policy hide tools from
  users who could never call them, so they are never listed.
- **Output redaction**: `redact:` masks emails, SSNs, card numbers, API keys and
  bearer tokens in results before they reach the model; hits are counted in the audit.
- Holds the upstream credentials itself and never forwards the caller's token to
  an upstream (confused-deputy safe).

**Governing writes (egress).** A tool that acts on the world (create, update,
upload, post, send, share) is classified as a write and governed as egress: any
write after a session has read untrusted content is refused (the prompt-injection
shield), and a rule can target writes only with `applies: write`. This is generic
across connectors, not Drive-specific. The Google Drive connector exposes governed
`create_<folder>` tools with `--allow-write`; writes are fenced to the folder like
reads. Classify your own connectors' write tools with `write_tools:` in the config.

See [`ROADMAP.md`](ROADMAP.md) for what is shipped, in progress, and planned,
with the community requests behind each item.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install mcp pyyaml pytest
.venv/bin/python -m pytest tests -q     # tests generated from coc.yaml
.venv/bin/python demo/run_demo.py       # the four-prompt sequence, end to end
```

Output:

```
turn 1  finance__headcount_plan   allowed
turn 2  finance__budget_roles     allowed
turn 3  hr__recent_joiners        allowed   (alert: COC-HR-011, 20 distinct > 8)
turn 4  ops__oncall_draft         DENIED    COC-HR-004
```

Turn 4 is denied **before the upstream call**, so the on-call data is never
fetched. The two domains already held overlap on the same people, and this call
would complete the forbidden set.

## The document is the source of truth

`coc.yaml` holds clause text written by the clause owner, its enforcement, and its
tests. Engineering owns the compiler, not the policy.

```yaml
- rule_id: COC-HR-004
  clause: >
    Personnel records, compensation or budget records, and operational rosters
    may not be combined to derive the employment status, performance, or
    planned departure of identifiable individuals.
  owner: hr-privacy@example.com
  enforce:
    - layer: accumulation
      action: deny
      type: domain_join
      domains: [hr-personnel, finance-comp, ops-rota]
      require_entity_overlap: true
      scope: user
      window: 4h
  tests:
    - {name: four_prompt_layoff_list, expect: deny, sequence: [...]}
```

CI fails any rule without both an allow and a deny test. Clauses that compile to
nothing are worth finding. Those are the parts of your code of conduct that were
never enforceable.

Rule types: `domain_join`, `entity_budget`, `domain_block`, `self_comparison`,
`min_group` (a result about fewer than k people is one person's data; pay
transparency), `wall` (a domain open only to `allowed_users`, or closed to
`blocked_users`, optionally `until` a date; privilege, embargoes, investigation
subjects). `domain_join` and `domain_block` accept the same `allowed_users`,
`blocked_users`, `since`, `until` scoping (quiet periods). `self_comparison`
(the requester's own record plus colleagues' records in one domain. The
precondition for "how do I compare"; decided post-call, since the colleague
records have to be seen to be counted). Actions: `deny`, `alert`. Start everything at `alert`, tune against real traffic, then flip.

## How it works

```
client ──MCP──▶ proxy ──MCP──▶ hr / finance / ops connectors
                  │
                  ├─ pre_call   deny before fetching where already decidable
                  ├─ post_call  extract entities, record, re-evaluate, redact
                  └─ audit      what was handed over, not just what was asked
```

- `aggrete/policy.py`. Deterministic evaluation. No model in this path.
- `aggrete/accumulator.py`. Per-user state, TTL'd. `MemoryStore` for tests,
  `RedisStore` for deployment, because state must be shared across clients.
- `aggrete/entities.py`. Pulls stable person IDs out of tool results.
- `proxy.config.yaml`. Maps tool name patterns to the domains clauses refer to.

### Remote connectors

Upstreams are either local stdio processes (`command:`) or remote MCP
servers over streamable HTTP (`url:`). The proxy holds the credential for the
upstream; header values may reference `${ENV_VARS}` so tokens never sit in
the YAML. Because the end user never holds that token, the only path to the
connector is through the proxy.

```yaml
upstreams:
  ops:
    url: https://mcp.example.com/ops/mcp
    headers:
      Authorization: "Bearer ${OPS_MCP_TOKEN}"
```

`tests/test_http_upstream.py` runs the mock `ops` connector over HTTP
(`demo/mock_server.py --transport streamable-http`) behind the proxy end to end.


## Architecture: where the proxy lives and how the pieces connect

```
  people's assistants                 your network                          your systems
  (Claude, Copilot, Cursor)   |                                     |
                              |   mcp.example.com  (this proxy)     |   HR system (Workday)
   ── HTTPS + OAuth ────────► |   Starlette, streamable HTTP        | ─► Finance (budget lines)
                              |   identity from the token           | ─► On-call rotations
                              |   policy: coc.yaml                  | ─► Drive, Slack, CRM ...
                              |   state: Redis (or memory)          |   (reachable only from the proxy)
                              |         │ writes                    |
                              |         ▼                           |
                              |   audit.jsonl  ◄── read only ──  Aggrete Console (live.example.com)
                              |   coc.yaml                          HR / Legal / IT, behind SSO or basic auth
```

Three rules make this safe:

1. **Only the proxy holds connector credentials.** People sign in to the proxy
   (your IdP via `mode: jwt`, or the built-in sign-in via `mode: builtin` when
   you have no IdP yet); the proxy signs in to the connectors. Fence the
   connectors so they accept traffic only from the proxy host.
2. **The console never touches the connectors.** It reads two files the proxy
   writes, `audit.jsonl` and `coc.yaml`, on the same host or a shared volume,
   and it changes nothing the proxy enforces. Put it behind your SSO or, at
   minimum, HTTP basic auth; it shows who asked what.
3. **The assistants may only talk to the proxy.** Managed client policy
   (Claude Code managed settings, Claude Enterprise connectors, Copilot and
   Cursor org policies) allow-lists `https://mcp.example.com/mcp` and nothing
   else.

Connecting Claude (claude.ai): Settings → Connectors → Add custom connector →
URL `https://mcp.example.com/mcp`. Claude discovers the sign-in from the
proxy's OAuth metadata, registers itself, and sends you to `/signin`. From then
on every question Claude asks on your behalf passes the policy.

Sample handbook: `samples/northwind-handbook.docx` (synthetic, tailored to the
rule types); `coc.yaml` maps to its clauses 7.1 to 7.11 one to one (7.4 and
7.12 are not enforceable at a data proxy). `aggrete-ingest
samples/northwind-handbook.docx` reproduces it. The `samples/` directory also
has real public-domain examples (GSA/TTS code of conduct, Indiana state
employee handbook); see [`samples/README.md`](samples/README.md).

## Serving it to a whole company: streamable HTTP + OAuth

stdio is for one laptop. For everyone else, run Aggrete as a service and let
identity come from the token:

```bash
python -m aggrete.proxy --config proxy.config.yaml --transport streamable-http --host 0.0.0.0 --port 8080
```

HTTP mode refuses to start without an `auth:` block. In `jwt` mode it validates
bearer JWTs from your IdP (issuer, audience, expiry, signature via JWKS,
required scopes) and derives the user from the `email` claim. Configurable
with `identity_claim`. Every request without a valid token is a 401 with an
RFC 9728 `WWW-Authenticate` pointer, and the `user:` line in the config is
ignored entirely. `builtin` mode is a small OAuth server inside the proxy (dynamic client
registration, a sign-in page, passcodes from the environment) for teams with
no IdP yet. `static` mode (fixed tokens) exists for development and the
test-suite. The accumulator keys state on the token identity, so the same
person hitting Aggrete from Claude Code, Claude.ai and Cursor shares one
history. Which is the point.

Register it in a client as a remote MCP server at `https://<host>/mcp` with
the bearer token your IdP issues; keep the connectors themselves reachable
only from the Aggrete host.

## Inside a gateway you already run

If agentgateway, IBM ContextForge, Kong or your own gateway is already the
control plane, don't add a second one. Embed Aggrete:

```python
from aggrete.plugin import PolicyHook, AggreteMiddleware

hook = PolicyHook("coc.yaml", domains={"hr__*": "hr-personnel", "ops__*": "ops-rota"},
                  store=RedisStore(redis_client))
# as two calls from your plugin system
v = hook.before(user, tool)             # v.allow, v.message (clause + remediation)
v = hook.after(user, tool, result_text) # records entities, re-evaluates
# or as ASGI middleware around any MCP server that answers in JSON
app = AggreteMiddleware(app, hook, identity=lambda scope: scope["state"]["user"])
```

Identity is a callable over the request, so it composes with whatever auth
the host performs. The middleware refuses at pre-call without forwarding and
inspects JSON tools/call results for post-call recording.

## Ways to deploy

| Who | How |
|---|---|
| One developer | `uvx aggrete --config proxy.config.yaml` (PyPI) or the `.mcp.json` in this repo |
| A team | `docker run ghcr.io/aggrete/aggrete` with `/etc/aggrete` mounted, or `helm install aggrete deploy/helm/aggrete` (bundled Redis, JWT auth, Ingress) |
| A company | Helm/Docker behind your IdP, then make `https://aggrete.<corp>/mcp` the *only* MCP server your assistant policies allow (Claude Code managed settings, Claude Enterprise connectors, Copilot/Cursor org policies), with connectors network-restricted to the Aggrete hosts |
| Existing gateway | `aggrete.plugin` (above) |


## Putting a real system behind the proxy: Google Drive

`aggrete/connectors/drive.py` is a Drive upstream the proxy runs itself. How
it is done, in the order you do it:

1. **A service account, not a person.** In Google Cloud: enable the Drive API,
   create a service account (say `aggrete-drive`), download its JSON key. The
   proxy holds the key; nobody's personal Google login is involved, which is
   what makes the proxy the only road.
2. **Share the folders, read only.** In Drive, create a root folder (say
   `Northwind`) with one subfolder per kind of material (`Restructuring plan`,
   `Legal hold`, `Team documents`) and share the root with the service account
   email as **Viewer**. Service accounts own nothing; they only see what is
   shared with them.
3. **One tool pair per folder.** The connector lists the root's subfolders and
   exposes `search_<folder>` and `read_<folder>` for each, so the policy can
   name folders:
   ```yaml
   upstreams:
     drive: {command: python3, args: [-m, aggrete.connectors.drive, --credentials, /opt/aggrete/drive-sa.json, --root, Northwind]}
   domains:
     "drive__*_restructuring_plan": restructuring-plan   # clause 7.9: embargo until announced
     "drive__*_legal_hold": legal-hold                    # clause 7.3: never for assistants
     "drive__*": drive-general
   ```
4. **Results name people.** Every file comes back with `owner_email` and
   `editor_email`, so the policy's tallies and joins work on Drive results
   like on HR records.
5. **Remove the direct road.** Disable the assistant's native Drive connector
   for governed accounts (Claude Enterprise: managed connectors; personal
   accounts: remove it). Otherwise the assistant has two ways to Drive and the
   policy only sees one.

`python -m aggrete.connectors.drive --credentials sa.json --root Northwind --list`
prints the tools that will be exposed. If the root is not shared yet the
connector still starts and exposes a single `status` tool that says what is
missing, so the proxy never fails to boot because of Drive.

## Building your own connector

Drive is the reference; the pattern is general. A connector is just an MCP
server, and the proxy governs any MCP server, so putting a new system behind the
proxy is: expose read tools, name write tools with a write verb, and map the
tools to a policy domain.

`aggrete/connectors/base.py` removes the boilerplate:

```python
from aggrete.connectors.base import Connector

c = Connector("crm")

@c.read("search_accounts", "Search CRM accounts by name.")
def search(query: str) -> str:
    return my_crm.search(query)          # a JSON string

@c.write("create_note", "Add a note to an account.")
def create_note(account_id: str, text: str) -> str:
    return my_crm.add_note(account_id, text)

if __name__ == "__main__":
    c.run()
```

```yaml
upstreams:
  crm: {command: python3, args: [my_crm_connector.py]}
domains:
  "crm__*": crm-accounts
```

`c.write(...)` refuses a tool name with no write verb, because a mis-named write
would slip past egress governance. Full guide with the folder-fencing pattern
and a copy-paste template: [docs/CONNECTORS.md](docs/CONNECTORS.md) and
`examples/connectors/knowledgebase_connector.py`.

For teams that would rather not build and maintain their own, **Aggrete for
teams** is where supported, certified connectors live: maintained and covered by
support, with Drive shipping and Slack, GitHub, Jira, Salesforce and Workday on
the roadmap. The proxy and this SDK stay Apache-2.0.

## Starting from the document you already have

`aggrete/ingest.py` turns a code-of-conduct document into a draft `coc.yaml`:

```bash
python -m aggrete.ingest handbook.pdf --domains proxy.config.yaml -o coc.draft.yaml
```

PDFs go to the model as native document blocks; DOCX, Markdown and text as
text. The model proposes rules in the exact `coc.yaml` schema with clause text
verbatim, every action forced to `alert`, and each rule's own tests are run
through the real `Engine` before the file is written. A draft that fails its
tests is rejected. Clauses no data proxy can enforce (tone, harassment,
expenses) are listed separately with the reason. Model set by `AGGRETE_INGEST_MODEL`. Needs `ANTHROPIC_API_KEY`
or an `ant auth login` profile.

## Purpose binding

A permanent block gets routed around. `engine.grant_purpose(user, rule_id,
purpose, ttl_s)` opens a scoped window and stamps every retrieval made under it
with the stated purpose. Wire it to an approval workflow owned by the clause
owner named in the rule.

## Honest limitations

- **Entity extraction is the weak point.** `entities.py` works on stable IDs and
  emails. Tune `IDENTIFIER_KEYS` against your own connectors before trusting any
  threshold, or Layer 4 will either never fire or fire constantly.
- **Post-call denial redacts, it does not un-fetch.** The data left the upstream.
  Prefer rules that can be decided pre-call.
- **stdio identity is advisory.** The user is whoever launched the process and the
  config is user-editable. Real enforcement needs streamable HTTP with OAuth, the
  subject taken from the token, and IdP-level blocking of direct connector grants
  so this proxy is the only path.
- **Aggregation cannot be solved, only narrowed.** A user who spaces requests
  beyond the window, or paraphrases across systems this proxy doesn't front, gets
  through. This raises the cost and creates the audit trail; it is not a ceiling.
- **Not a gateway.** No multi-tenancy, no token vault, no HA. For production,
  port this policy engine onto agentgateway or IBM ContextForge as a plugin
  rather than running it as your control plane.
