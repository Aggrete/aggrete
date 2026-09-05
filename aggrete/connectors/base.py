"""Build an Aggrete connector.

A connector is just an MCP server. Aggrete governs its tools automatically:
each tool is mapped to a policy domain in ``proxy.config.yaml``, and any tool
whose name contains a write verb (create, update, upload, post, send, ...) is
governed as a write/egress (blocked after untrusted reads, subject to
``applies: write`` rules). So the whole job of a connector is: expose read
tools, and optionally write tools named with a write verb.

    from typing import Annotated

    from pydantic import Field

    from aggrete.connectors.base import Connector

    c = Connector("crm")

    # Give each tool a 2-3 sentence description (what it does, what boundary it
    # is fenced to, and for writes that the proxy governs it as egress), and
    # annotate every parameter so its description lands in the tool's inputSchema.

    @c.read("search_accounts", (
        "Search CRM accounts by name within this workspace. Returns matching accounts "
        "with the owner's email, so the policy can count people. Leave the query empty "
        "to list recent accounts."))
    def search(
        query: Annotated[str, Field(default="", description="Text to match against account names; empty lists recent accounts.")] = "",
    ) -> str:
        # Matching accounts. Returns JSON: {results: [{id, name, owner_email}]}.
        ...

    @c.write("create_note", (
        "Create a note on one CRM account, fenced to this workspace. The proxy governs "
        "this call as an egress/write. Provide the account id and the note text."))
    def create(
        account_id: Annotated[str, Field(description="Id of the account to attach the note to.")],
        text: Annotated[str, Field(description="Body text of the note.")],
    ) -> str:
        # Create a note. Returns JSON: {created_id}.
        ...

    if __name__ == "__main__":
        c.run()

Wire it in ``proxy.config.yaml``::

    upstreams:
      crm: {command: python3, args: [my_crm_connector.py]}
    domains:
      "crm__*": crm-accounts
"""
from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

# Kept in sync with proxy.DEFAULT_WRITE_TOOLS so a write tool is governed as egress.
WRITE_VERBS = ("create", "update", "write", "upload", "delete",
               "post", "send", "share", "append", "move", "put")


class Connector:
    """Thin base over an MCP server with read/write tool helpers."""

    def __init__(self, name: str):
        self.name = name
        self.server = MCPServer(name)

    def read(self, tool_name: str, description: str):
        """Register a read tool (search, read, list). Use as a decorator."""
        return self.server.tool(name=tool_name, description=description)

    def write(self, tool_name: str, description: str):
        """Register a write tool. Its name must contain a write verb so the
        proxy treats the call as egress (that is what makes it governable)."""
        if not any(v in tool_name for v in WRITE_VERBS):
            raise ValueError(
                f"write tool {tool_name!r} should contain a write verb "
                f"(one of: {', '.join(WRITE_VERBS)}) so the proxy governs it as egress")
        return self.server.tool(name=tool_name, description=description)

    def run(self) -> None:
        """Serve over stdio (the usual way the proxy launches a connector)."""
        asyncio.run(self.server.run_stdio_async())
