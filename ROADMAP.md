# Roadmap

Priorities are drawn from what the developer and security community actually
asks for in MCP proxies, gateways and AI guardrails (GitHub discussions on the
Model Context Protocol, IBM mcp-context-forge, agentgateway and docker/mcp-gateway;
Hacker News threads on MCP security). Each item notes the demand behind it.

## Shipped

These already work in the open-source proxy.

- **Pre-call refusal.** A denied request never reaches the upstream. The most
  common reason people want a proxy in front of their connectors at all.
- **Stateful, cross-call policy.** Per-user accumulator with TTL, and rule types
  that reason over history (`domain_join`, `entity_budget`, `min_group`,
  `self_comparison`). The "stateful rules, not single-call checks" ask
  ([HN: Armour](https://news.ycombinator.com/item?id=46696348)). Few tools do this.
- **No token passthrough (confused-deputy safe).** The proxy is the confidential
  client and holds upstream credentials; the caller's token is never forwarded to
  an upstream, and never enters the model's context. Security reviewers treat this
  as a requirement ([mcp#483](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/483)).
- **Tamper-evident audit** *(0.2.0)*. Every decision is one hash-chained JSON line;
  editing, inserting or deleting a row breaks the chain. Verify with
  `aggrete-audit path/to/audit.jsonl`. The compliance "attributable, integrity-checkable
  log" ask ([IBM#535](https://github.com/ibm/mcp-context-forge/issues/535)).
- **Selective tool exposure** *(0.2.0)*. Static walls and blocks hide tools from
  users who could never call them, so they are never listed. Doubles as a
  context-bloat lever ([python-sdk#2619](https://github.com/modelcontextprotocol/python-sdk/issues/2619)).
- **Output PII / secret redaction** *(0.2.0)*. `redact:` masks emails, SSNs, card
  numbers, API keys and bearer tokens in results before they reach the model;
  redactions are counted in the audit line. Enforcement still runs on the original
  text ([IBM#229](https://github.com/ibm/mcp-context-forge/issues/229)).
- **Identity from your IdP over HTTP, plus a built-in OAuth 2.1 sign-in** with
  dynamic client registration. Streamable HTTP and stdio transports. Redis store
  and a Helm chart for multi-replica self-hosting.

## Next (in progress)

- **Per-user, on-behalf-of credentials to upstreams, with a pluggable credential
  store / vault.** Map an SSO JWT to the right per-user, per-upstream token instead
  of a shared service account, so the last hop is attributable too. The single
  loudest community ask ([agentgateway#239](https://github.com/agentgateway/agentgateway/issues/239),
  [mcp#804](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/804)),
  and this repo's known gap (no token vault today).
- **Argument-level rule predicates.** Decide on the *arguments* of a call, not just
  the tool and domain (e.g. allow a delete only on branches matching `feature-*`).
  Answers the "binary read/write is useless" complaint
  ([HN: Armour](https://news.ycombinator.com/item?id=46696348)).
- **SIEM / OpenTelemetry audit streaming** with retention and archive, so the audit
  trail lands in the security team's existing tooling
  ([agentic-community#413](https://github.com/agentic-community/mcp-gateway-registry/issues/413)).

## Planned

- **Human-in-the-loop approval gate** for permitted-but-sensitive calls, held at the
  proxy boundary ([HN](https://news.ycombinator.com/item?id=43676771)).
- **Per-tool OAuth scopes** declared and enforced at discovery and execution time
  ([mcp#234](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/234)).
- **Tool-poisoning / description-injection defense** (detect shadowed or malicious
  tool metadata) ([Invariant Labs](https://invariantlabs.ai/blog/mcp-github-vulnerability)).
- **Central server registry / shadow-MCP governance** so admins control which servers
  are allowed ([Cloudflare](https://developers.cloudflare.com/agents/model-context-protocol/governance)).

Requests and rationale welcome in [issues](https://github.com/cjohannsen81/aggrete/issues).
