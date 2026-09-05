"""Atlassian Jira Cloud as an Aggrete connector, one tool set per project.

    python -m aggrete.connectors.jira --site https://yourco.atlassian.net \
        --email you@yourco.com --token <api-token> --projects LEGAL,FIN

The proxy runs this over stdio and holds the API token; people never do. Each
allowed project becomes read tools `search_<project>` / `read_<project>` and,
with --allow-write, `create_issue_<project>` (governed as egress by the proxy):

    domains:
      "jira__*_legal": legal-hold       # the LEGAL project is its own boundary
      "jira__*": jira-general

Issues carry their reporter and assignee (accountId and, when Jira exposes it,
email and display name), so the policy counts people in Jira results.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Annotated

import httpx2 as httpx
from pydantic import Field

from aggrete.connectors.base import Connector

__version__ = "0.1.0"
TARGET_API = "3"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def adf_text(node) -> str:
    """Walk an Atlassian Document Format (ADF) tree, collecting text nodes."""
    if not isinstance(node, dict):
        return ""
    parts = []
    if isinstance(node.get("text"), str):
        parts.append(node["text"])
    for child in node.get("content") or []:
        parts.append(adf_text(child))
    return "".join(parts)


class Jira:
    """Minimal Jira Cloud REST v3 client on email + API token. httpx only."""

    def __init__(self, site: str, email: str, token: str):
        self.site = site
        self.email = email
        self.http = httpx.Client(timeout=30, base_url=site, auth=(email, token),
                                 headers={"Accept": "application/json"})

    def get(self, path: str, **params):
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def project_exists(self, key: str) -> bool:
        try:
            self.get(f"/rest/api/3/project/{key}")
            return True
        except httpx.HTTPStatusError:
            return False

    def search(self, project_key: str, query: str) -> list[dict]:
        jql = f"project = {project_key}"
        if (query or "").strip():
            jql += f' AND text ~ "{query.strip()}"'
        jql += " ORDER BY updated DESC"
        d = self.get("/rest/api/3/search", jql=jql,
                     fields="summary,status,assignee,reporter,updated", maxResults=20)
        return d.get("issues", [])

    def read_issue(self, project_key: str, key: str) -> dict:
        it = self.get(f"/rest/api/3/issue/{key}",
                      fields="summary,description,status,assignee,reporter")
        rec = self.issue_record(project_key, it)
        rec["description"] = adf_text((it.get("fields") or {}).get("description"))[:20000]
        return rec

    def create_issue(self, project_key: str, summary: str, description: str) -> dict:
        body = {"fields": {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": "Task"},
            "description": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": description}]}]}}}
        r = self.http.post("/rest/api/3/issue", json=body)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def issue_record(project_key: str, it: dict) -> dict:
        f = it.get("fields") or {}
        reporter = f.get("reporter") or {}
        assignee = f.get("assignee") or {}
        status = f.get("status") or {}
        return {"project": project_key, "key": it.get("key"),
                "summary": f.get("summary"), "status": status.get("name"),
                "reporter_email": reporter.get("emailAddress") or None,
                "assignee_email": assignee.get("emailAddress") or None,
                "reporter_name": reporter.get("displayName"),
                "assignee_name": assignee.get("displayName"),
                "reporter_account_id": reporter.get("accountId"),
                "assignee_account_id": assignee.get("accountId")}


def build(jira: Jira, project_keys: list[str], writable: bool = False) -> Connector:
    c = Connector("jira")
    wanted = [k for k in project_keys if jira.project_exists(k)]

    @c.read("projects", (
        "List the Jira projects exposed here, with the search, read and (when writing is enabled) create-issue tool "
        "name for each one. Call this first when asked about Jira, so you know which project-scoped tool to use next. "
        "Takes no arguments."))
    def projects_tool() -> str:
        """Directory of the available projects and their per-project tool names.

        Returns JSON: {jira_projects: [{project, search_tool, read_tool, create_issue_tool?}]}.
        """
        return json.dumps({"jira_projects": [
            dict({"project": k, "search_tool": f"search_{slug(k)}", "read_tool": f"read_{slug(k)}"},
                 **({"create_issue_tool": f"create_issue_{slug(k)}"} if writable else {})) for k in wanted]})

    for key in wanted:
        s = slug(key)

        def make(key=key):
            def search(
                query: Annotated[str, Field(default="", description="Text to match across issues in the project (Jira 'text ~' search); leave empty to list the most recently updated issues.")] = "",
            ) -> str:
                """Issues in this project matching the query, each with reporter and assignee.

                Returns JSON: {project, results: [{project, key, summary, status, reporter_email, assignee_email, ...}]}.
                """
                issues = jira.search(key, query)
                return json.dumps({"project": key, "results": [jira.issue_record(key, it) for it in issues]})

            def read(
                issue_key: Annotated[str, Field(default="", description="Full issue key to read, including the project prefix, for example 'LEGAL-123'.")] = "",
            ) -> str:
                """One issue with its description text, plus reporter and assignee.

                Returns JSON: {project, issue: {project, key, summary, status, description, reporter_email, assignee_email, ...}}.
                """
                if not issue_key:
                    return json.dumps({"project": key, "error": "give an issue_key (e.g. LEGAL-123)"})
                return json.dumps({"project": key, "issue": jira.read_issue(key, issue_key)})

            return search, read

        search, read = make()
        c.read(f"search_{s}", (
            f"Search issues in the Jira project {key} by keyword. The search is fenced to project {key}, and each hit carries its "
            f"reporter and assignee. Leave the query empty to list recently updated issues."))(search)
        c.read(f"read_{s}", (
            f"Read one issue from the Jira project {key} by its issue_key (for example {key}-123), returned with its description text, "
            f"reporter and assignee. Fenced to project {key}."))(read)
        if writable:
            def make_create(key=key):
                def create_issue(
                    summary: Annotated[str, Field(description="One-line summary (title) for the new issue.")],
                    description: Annotated[str, Field(default="", description="Body text of the new issue; may be empty.")] = "",
                ) -> str:
                    """Create one Task issue in this project. Returns JSON: {project, created_issue, url}."""
                    made = jira.create_issue(key, summary, description)
                    return json.dumps({"project": key, "created_issue": made.get("key"), "url": made.get("self")})
                return create_issue
            c.write(f"create_issue_{s}", (
                f"Create an issue in the Jira project {key} from a summary and description. The issue is opened only in project {key}, "
                f"and the proxy governs this call as an egress/write. Provide a summary and description."))(make_create())
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="Jira site base URL (e.g. https://yourco.atlassian.net)")
    ap.add_argument("--email", required=True, help="Atlassian account email for Basic auth")
    ap.add_argument("--token", required=True, help="Atlassian API token")
    ap.add_argument("--projects", required=True, help="comma-separated project keys to expose (e.g. LEGAL,FIN)")
    ap.add_argument("--allow-write", action="store_true", help="expose create-issue tools (governed as writes/egress by the proxy)")
    ap.add_argument("--list", action="store_true", help="print the tools that would be exposed and exit")
    a = ap.parse_args()
    keys = [k.strip() for k in a.projects.split(",") if k.strip()]
    jira = Jira(a.site, a.email, a.token)
    if a.list:
        for k in keys:
            here = "" if jira.project_exists(k) else "   (not visible to this token)"
            print(f"  {k!r:16} -> jira__search_{slug(k)}, jira__read_{slug(k)}"
                  + (f", jira__create_issue_{slug(k)}" if a.allow_write else "") + here)
        return
    build(jira, keys, writable=a.allow_write).run()


if __name__ == "__main__":
    main()
