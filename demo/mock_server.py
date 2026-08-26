"""Stand-ins for Workday / Salesforce / Drive / Slack style connectors.

Each returns JSON containing stable person identifiers, which is the realistic
case: your connectors mostly do, and where they don't, that is the first thing
to fix.

    python demo/mock_server.py --profile hr
    python demo/mock_server.py --profile hr --transport streamable-http --port 8001
"""

from __future__ import annotations

import argparse
import asyncio
import json

from mcp.server.mcpserver import MCPServer

PEOPLE = [
    ("alice.n@example.com", "E-1041"), ("bob.k@example.com", "E-1052"),
    ("carol.s@example.com", "E-1063"), ("dan.r@example.com", "E-1074"),
    ("erin.p@example.com", "E-1085"), ("frank.w@example.com", "E-1096"),
    ("gita.m@example.com", "E-1107"), ("hugo.b@example.com", "E-1118"),
    ("iris.t@example.com", "E-1129"), ("jack.l@example.com", "E-1130"),
]


def build(profile: str) -> MCPServer:
    server = MCPServer(f"mock-{profile}")

    if profile == "finance":
        @server.tool(description="Headcount plan totals for a team (no individuals).")
        def headcount_plan(team: str) -> str:
            return json.dumps({"team": team, "approved": 24, "filled": 21, "open": 3})

        @server.tool(description="Budget lines showing which roles are backfill-only.")
        def budget_roles(team: str) -> str:
            return json.dumps({"team": team, "lines": [
                {"role": "SRE II", "backfill_only": True, "owner_email": e}
                for e, _ in PEOPLE[:6]
            ]})

    elif profile == "hr":
        @server.tool(description="People who joined a team within N months.")
        def recent_joiners(team: str, months: int = 18) -> str:
            return json.dumps({"team": team, "joiners": [
                {"email": e, "employee_id": i, "start": "2025-06-01"} for e, i in PEOPLE
            ]})

        @server.tool(description="Leave and absence balances for one person.")
        def leave_balance(email: str) -> str:
            return json.dumps({"email": email, "days_remaining": 11})

    elif profile == "ops":
        @server.tool(description="Draft on-call rotation for a quarter.")
        def oncall_draft(team: str, quarter: str) -> str:
            return json.dumps({"team": team, "quarter": quarter, "shifts": [
                {"week": n + 1, "user_email": e} for n, (e, _) in enumerate(PEOPLE[4:])
            ]})

    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, choices=["hr", "finance", "ops"])
    ap.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    server = build(args.profile)
    if args.transport == "stdio":
        asyncio.run(server.run_stdio_async())
    else:
        server.run(transport="streamable-http", host="127.0.0.1", port=args.port)
