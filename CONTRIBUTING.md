# Contributing to Aggrete

Thanks for being here. Aggrete is an MCP policy proxy that governs what AI
assistants can reach and do. The roadmap is built from what people ask for, in
the open.

## Good first ways in

- **Write a connector.** Put a system behind the proxy. Start from
  `examples/connectors/knowledgebase_connector.py` and the guide in
  `docs/CONNECTORS.md`. A connector fences reads to a boundary (a channel, repo,
  folder or object) and maps them to a policy domain; writes are governed as
  egress.
- **Request or add a rule type.** The engine ships domain_join, self_comparison,
  wall, min_group, entity_budget and flow. If your policy needs a shape we do not
  have, open a rule request (there is an issue template) or send a PR with a test.
- **Improve the docs.** The quickstart, the connector guide, the policy format.
  Small clarity fixes are very welcome.

Look for issues labelled `good first issue` and `help wanted`.

## Developing

```bash
git clone https://github.com/aggrete/aggrete
cd aggrete
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest            # the suite is network free
aggrete --demo              # the four question walkthrough
```

Every rule type has an allow test and a deny test in `coc.yaml`; keep that
convention when you add one. Tests must not touch the network.

## Sending a change

1. Open an issue first for anything non trivial, so we agree on the shape.
2. Keep PRs focused and add a test.
3. Match the surrounding style. The engine is deterministic; keep it that way.

## Reporting a security issue

Do not open a public issue for a vulnerability. Email security@aggrete.com with
the details and we will coordinate a fix and disclosure.

By contributing you agree that your contribution is licensed under Apache-2.0.
