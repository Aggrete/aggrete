"""Drives the proxy as a real MCP client to show what it does.

First it previews a plan with the built-in `check` tool (no data fetched), then
it runs a four-call sequence for real and shows the fourth call refused before
the upstream is contacted.

    python demo/run_demo.py
"""

from __future__ import annotations

import asyncio
import sys
import textwrap

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

WIDTH = 78

# A layoff-list plan. `check` decides it without fetching anything.
PLAN = ["hr__recent_joiners", "finance__budget_roles", "ops__oncall_draft"]

# The same plan, run for real, one call at a time.
SEQUENCE = [
    ("Summarize the Q3 headcount plan for Platform.",
     "finance__headcount_plan", {"team": "Platform"}),
    ("Which Platform roles are marked backfill-only in the budget?",
     "finance__budget_roles", {"team": "Platform"}),
    ("Who joined Platform in the last 18 months?",
     "hr__recent_joiners", {"team": "Platform", "months": 18}),
    ("Draft the Q4 on-call rotation for Platform.",
     "ops__oncall_draft", {"team": "Platform", "quarter": "2026-Q4"}),
]


def text(result) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


def wrap(s: str, width: int = WIDTH) -> str:
    """Wrap to a readable width, keeping each line's leading indentation."""
    out = []
    for line in s.splitlines() or [""]:
        body = line.lstrip(" ")
        pad = line[: len(line) - len(body)]
        if not body:
            out.append("")
        else:
            out += textwrap.wrap(body, width=width, initial_indent=pad,
                                 subsequent_indent=pad + "  ") or [pad]
    return "\n".join(out)


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "aggrete.proxy", "--config", "proxy.config.yaml"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            print(wrap(f"\n{len(tools)} tools exposed, including aggrete__check and "
                       f"aggrete__scenarios (2 more are hidden from this user by policy).\n"))

            print("=== ask first: would this plan be allowed? ===")
            check = await session.call_tool("aggrete__check", {"tools": PLAN})
            print(wrap(text(check)), "\n")

            print("=== now run it for real ===")
            for n, (prompt, tool, args) in enumerate(SEQUENCE, 1):
                result = await session.call_tool(tool, args)
                body = text(result)
                if body.startswith("Blocked by"):
                    rule = body.split(".", 1)[0].replace("Blocked by ", "")
                    print(f"turn {n}  {tool:<24}  DENIED   {rule}")
                else:
                    print(f"turn {n}  {tool:<24}  allowed")
            print(wrap("\nTurn 4 was refused before the upstream call, so the on-call data "
                       "was never fetched. check reached the same verdict without fetching "
                       "anything at all."))


if __name__ == "__main__":
    asyncio.run(main())
