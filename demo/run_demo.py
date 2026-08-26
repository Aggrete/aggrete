"""Runs the four-prompt sequence through the proxy as a real MCP client.

    python demo/run_demo.py
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

SEQUENCE = [
    ("Summarize the Q3 headcount plan for Platform.",
     "finance__headcount_plan", {"team": "Platform"}),
    ("Which Platform roles are marked backfill-only in the budget?",
     "finance__budget_roles", {"team": "Platform"}),
    ("Who joined Platform in the last 18 months?",
     "hr__recent_joiners", {"team": "Platform", "months": 18}),
    ("Which of those people aren't in the Q4 on-call draft?",
     "ops__oncall_draft", {"team": "Platform", "quarter": "Q4"}),
]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "aggrete.proxy", "--config", "proxy.config.yaml"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            print(f"\ntools exposed through proxy: {', '.join(tools)}\n")

            for n, (prompt, tool, args) in enumerate(SEQUENCE, 1):
                result = await session.call_tool(tool, args)
                text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
                blocked = text.startswith("Blocked by")
                print(f"--- turn {n}: {prompt}")
                print(f"    tool: {tool}")
                print(f"    {'DENIED' if blocked else 'allowed'}: "
                      f"{text if blocked else text[:96] + '...'}\n")


if __name__ == "__main__":
    asyncio.run(main())
