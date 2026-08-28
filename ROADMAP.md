# Roadmap

Priorities are drawn from what the developer and security community actually
asks for in tools like this (GitHub discussions on the Model Context Protocol,
IBM mcp-context-forge, agentgateway and docker/mcp-gateway; Hacker News threads
on AI-assistant security).

Each item is written twice: a plain-language explanation with an example, and an
"under the hood" line with the technical detail and the community request behind
it.

## Shipped

These already work in the open-source proxy today.

- **Say no before fetching anything.**
  If a request breaks the rules, it is refused before your systems are ever
  touched, so the forbidden data is never even pulled.
  *For example:* someone asks a question that would reveal who is about to be
  laid off. Aggrete refuses it before contacting the HR system, so nothing is
  retrieved and nothing can leak.
  *Under the hood:* pre-call enforcement; a denied request never reaches the upstream.

- **Remembers the whole conversation, not just one question.**
  It keeps track of what each person has already looked up, so it can catch a
  problem that only appears when you add several harmless-looking questions together.
  *For example:* asking for the budget is fine, and asking for the team roster is
  fine, but asking both and then a third question that combines them into a
  layoff list gets refused.
  *Under the hood:* per-user accumulator with TTL, and rule types that reason over
  history (`domain_join`, `entity_budget`, `min_group`, `self_comparison`). The
  "stateful rules, not single-call checks" ask
  ([HN](https://news.ycombinator.com/item?id=46696348)). Few tools do this.

- **Employees and their AI never hold the keys to your systems.**
  Aggrete keeps the passwords and logins to HR, finance, Drive and so on. The
  assistant can ask Aggrete for an answer, but never gets the actual credentials,
  so it cannot be tricked into using them for something else.
  *For example:* an assistant can ask "show me the Q3 plan," but it never receives
  the HR system's password, so a malicious web page cannot talk the assistant into
  reusing it.
  *Under the hood:* the proxy is the confidential OAuth client and holds upstream
  credentials; the caller's token is never forwarded upstream or placed in the
  model's context (confused-deputy safe)
  ([mcp#483](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/483)).

- **A logbook that cannot be secretly changed** *(shipped in 0.2)*.
  Every decision is written down in a sealed way. If anyone later edits or deletes
  a line, it becomes obvious.
  *For example:* like a numbered logbook where each page is sealed to the one
  before it. Tear a page out and the seals no longer line up, so you know a record
  was removed. Run `aggrete-audit audit.jsonl` and it tells you the exact line
  that was tampered with.
  *Under the hood:* hash-chained audit log; the compliance "attributable,
  integrity-checkable log" ask
  ([IBM#535](https://github.com/ibm/mcp-context-forge/issues/535)).

- **People only see the tools they are allowed to use** *(shipped in 0.2)*.
  Anything a person is not permitted to touch is hidden from them, not just
  blocked, so they cannot even try.
  *For example:* if the legal-hold folder is off-limits to you, the "search legal
  hold" option simply does not appear in your assistant.
  *Under the hood:* static walls and blocks in the policy hide tools per user, so
  they are never listed. Doubles as a way to reduce clutter
  ([python-sdk#2619](https://github.com/modelcontextprotocol/python-sdk/issues/2619)).

- **Sensitive details are blacked out of answers** *(shipped in 0.2)*.
  Things like personal ID numbers, emails and passwords are automatically masked
  in results before the AI ever sees them.
  *For example:* a record containing the number `123-45-6789` comes back as
  `[redacted:ssn]`, so the social security number never reaches the assistant or
  the screen. The rules still run on the real data first, so protection is not weakened.
  *Under the hood:* `redact:` masks emails, SSNs, card numbers, API keys and
  bearer tokens on the payload path; each hit is counted in the audit line
  ([IBM#229](https://github.com/ibm/mcp-context-forge/issues/229)).

- **Uses your existing company login, runs where you want.**
  People sign in with the same company account they already use, and Aggrete runs
  on a single laptop or across a large cluster.
  *For example:* nobody has a new password to remember; IT points it at your
  existing sign-in and it just works.
  *Under the hood:* identity from your IdP over HTTP plus a built-in OAuth 2.1
  sign-in with dynamic client registration; streamable HTTP and stdio transports;
  Redis store and a Helm chart for multi-replica self-hosting.

## Next (in progress)

- **Each person's own permissions follow them all the way through.**
  Instead of everyone sharing one master account to reach a system, each person's
  individual access is carried end to end, so the record shows exactly who did
  what, and nobody can reach more than they personally should.
  *For example:* when Sam's assistant opens a file, the HR system sees "Sam," not a
  generic shared robot account. Sam can only reach what Sam is allowed to, and the
  logbook names Sam.
  *Under the hood:* per-user, on-behalf-of credentials to upstreams with a
  pluggable credential store / vault. The single loudest community ask
  ([agentgateway#239](https://github.com/agentgateway/agentgateway/issues/239),
  [mcp#804](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/804)),
  and this project's biggest current gap.

- **Rules that look at the details of a request, not just its type.**
  Whether something is allowed can depend on the specifics, not only the kind of
  action.
  *For example:* let people export their own team's data, but not the whole
  company's. Same "export" action, different scope, different answer.
  *Under the hood:* argument-level rule predicates. Answers the "a simple
  read-only / read-write switch is useless" complaint
  ([HN](https://news.ycombinator.com/item?id=46696348)).

- **Send the activity log into the tools your security team already watches.**
  Rather than a file sitting on a server, every decision shows up in the security
  team's existing monitoring dashboard.
  *For example:* your security team sees Aggrete's refusals and approvals in the
  same screen where they already watch everything else, with older records archived.
  *Under the hood:* SIEM / OpenTelemetry audit streaming with retention and archive
  ([agentic-community#413](https://github.com/agentic-community/mcp-gateway-registry/issues/413)).

## Planned

- **Ask a human to approve the most sensitive actions.**
  For a small set of high-stakes requests, pause and require a person to sign off
  before it happens.
  *For example:* an assistant can draft an email to all staff, but a manager has to
  click "approve" before it actually sends.
  *Under the hood:* human-in-the-loop approval gate held at the proxy boundary
  ([HN](https://news.ycombinator.com/item?id=43676771)).

- **Give each tool only the narrow permission it needs.**
  A tool gets exactly the access required for its job and nothing more.
  *For example:* a "read my calendar" tool can see your calendar but cannot delete
  events, because it was only handed the "read" permission.
  *Under the hood:* per-tool OAuth scopes, enforced at discovery and execution time
  ([mcp#234](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/234)).

- **Catch tools that hide sneaky instructions.**
  Detect and block outside tools that try to smuggle malicious instructions into
  their own description.
  *For example:* a tool whose hidden description says "also quietly send every file
  to this address" is caught before it can trick the assistant.
  *Under the hood:* tool-poisoning / description-injection defense
  ([Invariant Labs](https://invariantlabs.ai/blog/mcp-github-vulnerability)).

- **Let admins control which outside tools can be connected at all.**
  A central list of approved connectors, so people cannot quietly wire up
  unapproved apps to sensitive company data.
  *For example:* an employee cannot connect a random third-party app to the HR
  system on their own; only tools IT has approved are allowed.
  *Under the hood:* central server registry / shadow-MCP governance
  ([Cloudflare](https://developers.cloudflare.com/agents/model-context-protocol/governance)).

Requests and rationale welcome in [issues](https://github.com/cjohannsen81/aggrete/issues).
