"""Stand-ins for Workday / Salesforce / Drive / Slack style connectors.

Each returns JSON containing stable person identifiers, which is the realistic
case: your connectors mostly do, and where they don't, that is the first thing
to fix. Tools carry full descriptions and per-parameter documentation so an
assistant (and a directory's quality scan) can understand them.

    python demo/mock_server.py --profile hr
    python demo/mock_server.py --profile hr --transport streamable-http --port 8001
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

PEOPLE = [
    ("alice.n@example.com", "E-1041"), ("bob.k@example.com", "E-1052"),
    ("carol.s@example.com", "E-1063"), ("dan.r@example.com", "E-1074"),
    ("erin.p@example.com", "E-1085"), ("frank.w@example.com", "E-1096"),
    ("gita.m@example.com", "E-1107"), ("hugo.b@example.com", "E-1118"),
    ("iris.t@example.com", "E-1129"), ("jack.l@example.com", "E-1130"),
]

Team = Annotated[str, Field(description="Team name to report on, for example 'platform', 'sre' or 'sales-emea'.")]


def build(profile: str) -> MCPServer:
    server = MCPServer(f"mock-{profile}")

    if profile == "finance":
        @server.tool(description=(
            "Aggregate headcount plan for one team: approved, filled and open role counts. "
            "Returns totals only, never individual people. Use it to see how many roles a "
            "team is budgeted for and how many are still open."))
        def headcount_plan(team: Team) -> str:
            """Approved, filled and open headcount for a team, as aggregate totals with no per-person data.

            Returns JSON: {team, approved, filled, open}.
            """
            return json.dumps({"team": team, "approved": 24, "filled": 21, "open": 3})

        @server.tool(description=(
            "Budget lines for one team, per role, including whether each role is backfill-only "
            "and the email of the role owner. In this demo, Aggrete refuses combining these "
            "budget records with HR personnel records to profile individuals (a code-of-conduct rule)."))
        def budget_roles(team: Team) -> str:
            """Per-role budget lines for a team.

            Returns JSON: {team, lines: [{role, backfill_only, owner_email}]}.
            """
            return json.dumps({"team": team, "lines": [
                {"role": "SRE II", "backfill_only": True, "owner_email": e}
                for e, _ in PEOPLE[:6]
            ]})

    elif profile == "hr":
        @server.tool(description=(
            "List the people who joined a team within the last N months, with each person's "
            "email, employee id and start date. Use it to find recent hires on a team."))
        def recent_joiners(
            team: Team,
            months: Annotated[int, Field(default=18, ge=1, le=60,
                              description="Look-back window in months (1 to 60). Defaults to 18.")] = 18,
        ) -> str:
            """Recent joiners for a team: email, employee id and start date per person.

            Returns JSON: {team, joiners: [{email, employee_id, start}]}.
            """
            return json.dumps({"team": team, "joiners": [
                {"email": e, "employee_id": i, "start": "2025-06-01"} for e, i in PEOPLE
            ]})

        @server.tool(description=(
            "Look up the remaining leave and absence balance for one person by email. In this "
            "demo, Aggrete redacts the email in the result before it reaches the model."))
        def leave_balance(
            email: Annotated[str, Field(description="Email address of the person whose leave balance to look up, for example 'alice.n@example.com'.")],
        ) -> str:
            """Remaining leave days for a single person, keyed by their email.

            Returns JSON: {email, days_remaining}.
            """
            return json.dumps({"email": email, "days_remaining": 11})

        @server.tool(description=(
            "Start here. Explains what this demo is and gives two concrete things to try so you "
            "can watch Aggrete govern a request. Takes no arguments."))
        def start_here() -> str:
            """Orientation for first-time visitors: what the demo is and two things to try."""
            return json.dumps({
                "what_this_is": ("A live demo of Aggrete, an open-source policy proxy, in front of mock HR, "
                                 "finance and ops connectors for a sample company. Aggrete checks every tool "
                                 "call against a code of conduct and refuses or redacts what would cross a line."),
                "try_redaction": "Call hr__leave_balance with any email; the email comes back redacted.",
                "try_refusal": ("Call hr__recent_joiners, then finance__budget_roles, then hr__leave_balance for "
                                "the same people. Combining personnel and budget to profile someone is refused "
                                "(rule COC-HR-004), before any data is fetched."),
                "run_your_own": "https://aggrete.com/guide",
                "source": "https://github.com/aggrete/aggrete",
            })

    elif profile == "ops":
        @server.tool(description=(
            "Draft an on-call rotation for one team and quarter: one shift per week with the "
            "assigned person's email. Use it to propose who is on call, week by week."))
        def oncall_draft(
            team: Team,
            quarter: Annotated[str, Field(description="Quarter to draft, in the form 'YYYY-Qn', for example '2026-Q1'.")],
        ) -> str:
            """A proposed weekly on-call rotation for a team and quarter.

            Returns JSON: {team, quarter, shifts: [{week, user_email}]}.
            """
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
