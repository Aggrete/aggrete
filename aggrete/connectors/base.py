"""Build an Aggrete connector.

A connector is just an MCP server. Aggrete governs its tools automatically:
each tool is mapped to a policy domain in ``proxy.config.yaml``, and any tool
whose name contains a write verb (create, update, upload, post, send, ...) is
governed as a write/egress (blocked after untrusted reads, subject to
``applies: write`` rules). So the whole job of a connector is: expose read
tools, and optionally write tools named with a write verb.

    from aggrete.connectors.base import Connector

    c = Connector("crm")

    @c.read("search_accounts", "Search CRM accounts by name.")
    def search(query: str) -> str:
        ...

    @c.write("create_note", "Create a note on an account.")
    def create(account_id: str, text: str) -> str:
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
