# Aggrete

An MCP proxy that enforces a **code of conduct document** across connectors, with
state that accumulates per user.

Every MCP gateway on the market authorizes tool calls and logs them. None of them
answer the question that actually matters once an assistant can reach Glean,
Salesforce, Slack and Drive at once: *is this call, combined with everything this
person has already pulled today, something the code of conduct forbids?*

Four individually-authorized questions can assemble a layoff list. No guardrail
fires, because no single question was sensitive. This proxy is the missing layer.

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
fetched — the two domains already held overlap on the same people, and this call
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
nothing are worth finding — those are the parts of your code of conduct that were
never enforceable.

Rule types: `domain_join`, `entity_budget`, `domain_block`. Actions: `deny`,
`alert`. Start everything at `alert`, tune against real traffic, then flip.

## How it works

```
client ──MCP──▶ proxy ──MCP──▶ hr / finance / ops connectors
                  │
                  ├─ pre_call   deny before fetching where already decidable
                  ├─ post_call  extract entities, record, re-evaluate, redact
                  └─ audit      what was handed over, not just what was asked
```

- `aggrete/policy.py` — deterministic evaluation. No model in this path.
- `aggrete/accumulator.py` — per-user state, TTL'd. `MemoryStore` for tests,
  `RedisStore` for deployment, because state must be shared across clients.
- `aggrete/entities.py` — pulls stable person IDs out of tool results.
- `proxy.config.yaml` — maps tool name patterns to the domains clauses refer to.

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

## Starting from the document you already have

`aggrete/ingest.py` turns a code-of-conduct document into a draft `coc.yaml`:

```bash
python -m aggrete.ingest handbook.pdf --domains proxy.config.yaml -o coc.draft.yaml
```

PDFs go to the model as native document blocks; DOCX, Markdown and text as
text. The model proposes rules in the exact `coc.yaml` schema with clause text
verbatim, every action forced to `alert`, and each rule's own tests are run
through the real `Engine` before the file is written — a draft that fails its
tests is rejected. Clauses no data proxy can enforce (tone, harassment,
expenses) are listed separately with the reason. Needs `ANTHROPIC_API_KEY`
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
