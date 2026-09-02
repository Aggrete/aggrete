"""Notion as an Aggrete connector, one tool set per database.

    python -m aggrete.connectors.notion --token secret_... --databases <db_id_1>,<db_id_2>

The proxy runs this over stdio and holds the integration token; people never do.
Each allowed database becomes read tools `search_<db>` / `read_<db>` and, with
--allow-write, `create_page_<db>` (governed as egress by the proxy):

    domains:
      "notion__*_legal": legal-hold      # a legal database is its own boundary
      "notion__*": notion-general

Every page carries the ids (and, when the integration has user information,
emails) of its creator and last editor, so the policy counts people in Notion
results exactly as it does in HR records. Search is a page fetch + local title
filter, since Notion has no generic full-text database filter.
"""

from __future__ import annotations

import argparse
import json
import re

import httpx2 as httpx

from aggrete.connectors.base import Connector

__version__ = "0.1.0"
TARGET_API = "2022-06-28"

API = "https://api.notion.com/v1"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class Notion:
    """Minimal Notion API client on an integration token. httpx only."""

    def __init__(self, token: str):
        self.token = token
        self.http = httpx.Client(timeout=30, base_url=API, headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": TARGET_API,
            "Accept": "application/json"})
        self._email: dict[str, str | None] = {}

    def db_title(self, db_id: str) -> str | None:
        try:
            d = self.http.get(f"/databases/{db_id}")
            d.raise_for_status()
            parts = [t.get("plain_text", "") for t in d.json().get("title", [])]
            title = "".join(parts).strip()
            return title or None
        except httpx.HTTPStatusError:
            return None

    def query(self, db_id: str, text: str = "") -> list[dict]:
        r = self.http.post(f"/databases/{db_id}/query", json={"page_size": 25})
        r.raise_for_status()
        pages = r.json().get("results", [])
        if text.strip():
            q = text.lower()
            pages = [p for p in pages if q in self.page_title(p).lower()]
        return pages

    def page_title(self, page: dict) -> str:
        for prop in (page.get("properties") or {}).values():
            if prop.get("type") == "title":
                return "".join(t.get("plain_text", "") for t in prop.get("title", []))
        return ""

    def blocks_text(self, results: list[dict]) -> str:
        out = []
        for b in results:
            body = b.get(b.get("type", ""), {}) if isinstance(b, dict) else {}
            for t in (body.get("rich_text") or []):
                out.append(t.get("plain_text", ""))
        return "\n".join(t for t in out if t)[:20000]

    def read_page(self, page_id: str) -> dict:
        pr = self.http.get(f"/pages/{page_id}")
        pr.raise_for_status()
        page = pr.json()
        br = self.http.get(f"/blocks/{page_id}/children", params={"page_size": 50})
        br.raise_for_status()
        text = self.blocks_text(br.json().get("results", []))
        return {"id": page.get("id"), "title": self.page_title(page),
                "url": page.get("url"), "text": text}

    def user_email(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        if user_id not in self._email:
            try:
                u = self.http.get(f"/users/{user_id}")
                u.raise_for_status()
                self._email[user_id] = u.json().get("person", {}).get("email")
            except httpx.HTTPStatusError:
                self._email[user_id] = None
        return self._email[user_id]

    def create_page(self, db_id: str, title: str, content: str) -> dict:
        r = self.http.post("/pages", json={
            "parent": {"database_id": db_id},
            "properties": {"Name": {"title": [{"text": {"content": title}}]}},
            "children": [{"object": "block", "type": "paragraph",
                          "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]}}]})
        r.raise_for_status()
        return r.json()

    def page_record(self, db_title: str, page: dict) -> dict:
        created = (page.get("created_by") or {}).get("id")
        edited = (page.get("last_edited_by") or {}).get("id")
        return {"database": db_title, "id": page.get("id"),
                "title": self.page_title(page), "url": page.get("url"),
                "created_by_email": self.user_email(created),
                "last_edited_by_email": self.user_email(edited)}


def build(notion: Notion, db_ids: list[str], writable: bool = False) -> Connector:
    c = Connector("notion")
    wanted = [(did, notion.db_title(did)) for did in db_ids]

    @c.read("databases", "List the Notion databases you can read here. Call this first when asked about Notion.")
    def databases_tool() -> str:
        out = []
        for did, title in wanted:
            s = slug(title) if title else slug(did)[:12]
            out.append(dict({"database": title or did, "search_tool": f"search_{s}", "read_tool": f"read_{s}"},
                            **({"create_page_tool": f"create_page_{s}"} if writable else {})))
        return json.dumps({"notion_databases": out})

    for db_id, title in wanted:
        s = slug(title) if title else slug(db_id)[:12]
        label = title or db_id

        def make(db_id=db_id, title=title):
            def search(query: str = "") -> str:
                pages = notion.query(db_id, query)
                return json.dumps({"database": title or db_id,
                                   "results": [notion.page_record(title or db_id, p) for p in pages]})

            def read(page_id: str) -> str:
                return json.dumps({"database": title or db_id, "page": notion.read_page(page_id)})

            return search, read

        search, read = make()
        c.read(f"search_{s}", f"Search pages in the Notion database {label} by title keyword; leave the query empty to list recent pages.")(search)
        c.read(f"read_{s}", f"Read one page from the Notion database {label} by its page_id (metadata plus text).")(read)
        if writable:
            def make_create(db_id=db_id, title=title):
                def create_page(title_: str, content: str = "") -> str:
                    made = notion.create_page(db_id, title_, content)
                    return json.dumps({"database": title or db_id, "created_page": made.get("id"), "url": made.get("url")})
                return create_page
            c.write(f"create_page_{s}", f"Create a page in the Notion database {label}. Provide a title and content.")(make_create())
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="Notion integration token (secret_...)")
    ap.add_argument("--databases", required=True, help="comma-separated database ids to expose")
    ap.add_argument("--allow-write", action="store_true", help="expose create-page tools (governed as writes/egress by the proxy)")
    ap.add_argument("--list", action="store_true", help="print the tools that would be exposed and exit")
    a = ap.parse_args()
    db_ids = [d.strip() for d in a.databases.split(",") if d.strip()]
    notion = Notion(a.token)
    if a.list:
        for did in db_ids:
            title = notion.db_title(did)
            s = slug(title) if title else slug(did)[:12]
            here = "" if title else "   (not visible to this integration)"
            print(f"  {(title or did)!r:32} -> notion__search_{s}, notion__read_{s}"
                  + (f", notion__create_page_{s}" if a.allow_write else "") + here)
        return
    build(notion, db_ids, writable=a.allow_write).run()


if __name__ == "__main__":
    main()
