"""GitHub as an Aggrete connector, one tool set per repository.

    python -m aggrete.connectors.github --token ghp_... --repos northwind/security,northwind/product

The proxy runs this over stdio and holds the token; people never do. Each
allowed repo becomes read tools `search_<repo>` / `read_<repo>` and, with
--allow-write, `create_issue_<repo>` (governed as egress by the proxy):

    domains:
      "github__*_security": secrets-dlp   # the security repo is its own boundary
      "github__*": github-general

Issues, pull requests and commits carry their author (login and, when the
commit exposes it, email), so the policy counts people in GitHub results.
"""

from __future__ import annotations

import argparse
import base64
import json
import re

import httpx2 as httpx

from aggrete.connectors.base import Connector

__version__ = "0.1.0"
TARGET_API = "2022-11-28"

API = "https://api.github.com"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class GitHub:
    """Minimal GitHub REST client on a token (PAT or app token). httpx only."""

    def __init__(self, token: str):
        self.http = httpx.Client(timeout=30, base_url=API, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})

    def get(self, path: str, **params):
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def repo_exists(self, repo: str) -> bool:
        try:
            self.get(f"/repos/{repo}")
            return True
        except httpx.HTTPStatusError:
            return False

    def search_issues(self, repo: str, query: str) -> list[dict]:
        q = f"repo:{repo} " + (query or "")
        items = self.get("/search/issues", q=q.strip(), per_page=20).get("items", [])
        return items

    def list_issues(self, repo: str) -> list[dict]:
        return self.get(f"/repos/{repo}/issues", per_page=20, state="all")

    def read_issue(self, repo: str, number: int) -> dict:
        return self.get(f"/repos/{repo}/issues/{number}")

    def read_file(self, repo: str, path: str) -> dict:
        d = self.get(f"/repos/{repo}/contents/{path}")
        text = ""
        if isinstance(d, dict) and d.get("encoding") == "base64":
            try:
                text = base64.b64decode(d.get("content", "")).decode("utf-8", "replace")
            except Exception:
                text = "[binary file; not rendered]"
        return {"path": path, "name": d.get("name") if isinstance(d, dict) else path, "text": text[:20000]}

    def create_issue(self, repo: str, title: str, body: str) -> dict:
        r = self.http.post(f"/repos/{repo}/issues", json={"title": title, "body": body})
        r.raise_for_status()
        return r.json()

    @staticmethod
    def issue_record(repo: str, it: dict) -> dict:
        user = it.get("user") or {}
        return {"repo": repo, "number": it.get("number"), "title": it.get("title"),
                "state": it.get("state"), "kind": "pull_request" if it.get("pull_request") else "issue",
                "user_id": user.get("login"), "author_login": user.get("login"),
                "email": (it.get("user") or {}).get("email")}


def build(gh: GitHub, repos: list[str], writable: bool = False) -> Connector:
    c = Connector("github")
    wanted = [r for r in repos if gh.repo_exists(r)]

    @c.read("repos", "List the GitHub repositories you can read here. Call this first when asked about GitHub.")
    def repos_tool() -> str:
        return json.dumps({"github_repos": [
            dict({"repo": r, "search_tool": f"search_{slug(r)}", "read_tool": f"read_{slug(r)}"},
                 **({"create_issue_tool": f"create_issue_{slug(r)}"} if writable else {})) for r in wanted]})

    for repo in wanted:
        s = slug(repo)

        def make(repo=repo):
            def search(query: str = "") -> str:
                items = gh.search_issues(repo, query) if query.strip() else gh.list_issues(repo)
                return json.dumps({"repo": repo, "results": [gh.issue_record(repo, it) for it in items]})

            def read(issue_number: str = "", path: str = "") -> str:
                if path:
                    return json.dumps({"repo": repo, "file": gh.read_file(repo, path)})
                if issue_number:
                    it = gh.read_issue(repo, int(issue_number))
                    rec = gh.issue_record(repo, it)
                    rec["body"] = (it.get("body") or "")[:20000]
                    return json.dumps({"repo": repo, "issue": rec})
                return json.dumps({"repo": repo, "error": "give an issue_number or a file path"})

            return search, read

        search, read = make()
        c.read(f"search_{s}", f"Search issues and pull requests in the GitHub repo {repo} by keyword; leave the query empty to list recent ones.")(search)
        c.read(f"read_{s}", f"Read one item from the GitHub repo {repo}: pass issue_number for an issue/PR, or path to read a file.")(read)
        if writable:
            def make_create(repo=repo):
                def create_issue(title: str, body: str = "") -> str:
                    made = gh.create_issue(repo, title, body)
                    return json.dumps({"repo": repo, "created_issue": made.get("number"), "url": made.get("html_url")})
                return create_issue
            c.write(f"create_issue_{s}", f"Create an issue in the GitHub repo {repo}. Provide a title and body.")(make_create())
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="GitHub token (PAT or app token)")
    ap.add_argument("--repos", required=True, help="comma-separated owner/repo to expose (e.g. northwind/security,northwind/product)")
    ap.add_argument("--allow-write", action="store_true", help="expose create-issue tools (governed as writes/egress by the proxy)")
    ap.add_argument("--list", action="store_true", help="print the tools that would be exposed and exit")
    a = ap.parse_args()
    repos = [r.strip() for r in a.repos.split(",") if r.strip()]
    gh = GitHub(a.token)
    if a.list:
        for r in repos:
            here = "" if gh.repo_exists(r) else "   (not visible to this token)"
            print(f"  {r!r:32} -> github__search_{slug(r)}, github__read_{slug(r)}"
                  + (f", github__create_issue_{slug(r)}" if a.allow_write else "") + here)
        return
    build(gh, repos, writable=a.allow_write).run()


if __name__ == "__main__":
    main()
