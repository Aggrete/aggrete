# Building a connector

A connector is what puts a real system behind the proxy: HR, finance, a CRM, a
wiki, a code host. Aggrete does not ship a fixed catalogue of integrations. It
governs **any MCP server**, so building a connector is mostly a matter of
writing a small MCP server and mapping its tools to a policy domain. This page
is the guide; `aggrete/connectors/base.py` is the helper that removes the
boilerplate, and `examples/connectors/knowledgebase_connector.py` is a template
you can copy.

## The one thing to understand

A connector exposes **tools**. The proxy governs those tools two ways, and both
are driven by naming, not by any connector-side code:

1. **Reads** are mapped to a **policy domain** by tool-name pattern in
   `proxy.config.yaml`. The domain is what the code of conduct reasons about
   (information barriers, entity budgets, forbidden combinations).
2. **Writes** are recognised by a write verb in the tool name
   (`create`, `update`, `upload`, `post`, `send`, `share`, ...). A write is
   governed as **egress**: refused after the session has read untrusted content
   (the prompt-injection shield) and subject to any `applies: write` rule.

So the whole job of a connector is: expose read tools, and name write tools
with a write verb. The base class enforces the second half for you.

## The 20-line version

```python
from aggrete.connectors.base import Connector

c = Connector("crm")

@c.read("search_accounts", "Search CRM accounts by name.")
def search(query: str) -> str:
    return my_crm.search(query)          # return a JSON string

@c.read("read_account", "Read one account by id.")
def read(account_id: str) -> str:
    return my_crm.get(account_id)

@c.write("create_note", "Add a note to an account.")
def create_note(account_id: str, text: str) -> str:
    return my_crm.add_note(account_id, text)

if __name__ == "__main__":
    c.run()                              # serves over stdio
```

`c.write(...)` raises at import time if the tool name has no write verb, because
a mis-named write would slip past egress governance. That check is the point.

## Wiring it into the proxy

```yaml
# proxy.config.yaml
upstreams:
  crm: {command: python3, args: [my_crm_connector.py]}   # stdio
  # or a remote MCP server:
  # crm: {url: https://mcp.example.com/crm/mcp, headers: {Authorization: "Bearer ${CRM_TOKEN}"}}

domains:
  "crm__*": crm-accounts          # every crm tool -> the crm-accounts domain
```

Tools reach the assistant namespaced as `crm__search_accounts`,
`crm__create_note`, and so on. Reference `crm-accounts` from `coc.yaml` in any
rule, exactly like the built-in domains.

## Folder-fenced connectors

When one system holds several trust boundaries (shared drives, Slack channels,
repos), expose **one tool per boundary** and let each map to its own domain.
The Google Drive connector (`aggrete/connectors/drive.py`) is the reference:
each shared subfolder becomes `drive__search_<folder>` / `drive__read_<folder>`,
folders become policy domains, and a read refuses any file outside its folder.
Copy its shape when a connector needs per-domain fencing.

## Checklist

- [ ] Read tools return a JSON string (so `entities.py` can pull person-IDs).
- [ ] Write tools are named with a write verb (`base.Connector.write` enforces).
- [ ] `domains:` maps the tool pattern to a policy domain.
- [ ] Person identifiers use the keys `entities.py` knows (`email` preferred as
      the canonical key so a person links across connectors). Tune
      `EMAIL_KEYS` / `ID_KEYS` if your payloads differ.
- [ ] Every new rule that touches the connector has an allow test and a
      deny/alert test in `coc.yaml`.

## Community vs. supported connectors

This SDK, the guide, and the Drive reference are Apache-2.0: build any connector
you need, and the proxy governs it the same way it governs the built-ins.

Aggrete for teams is where **fully supported, certified connectors** live:
maintained, tested against provider API changes, and covered by support, so a
team does not build and keep running its own. Google Drive is the first; Slack,
GitHub, Jira, Salesforce and Workday are the roadmap. See https://aggrete.com.
