"""Salesforce as an Aggrete connector, one tool set per sObject.

    python -m aggrete.connectors.salesforce --instance https://yourco.my.salesforce.com --token 00D... --objects Lead,Contact,Opportunity

The proxy runs this over stdio and holds the OAuth access token; people never
do. Each allowed object becomes read tools `search_<object>` / `read_<object>`
and, with --allow-write, `create_<object>` (governed as egress by the proxy):

    domains:
      "salesforce__*_lead": marketing-crm    # leads are their own boundary
      "salesforce__*": salesforce-general

Every record carries an identifiable person (the record's Email when it has
one, else its Owner's email), so the policy counts people in Salesforce results
exactly as it does in HR records. This is the bulk-export case: a search over an
object returns many distinct people, so the accumulator can enforce the entity
budget.
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
TARGET_API = "v60.0"

PERSON_OBJECTS = ("Lead", "Contact")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class Salesforce:
    """Minimal Salesforce REST client on an OAuth access token. httpx only."""

    def __init__(self, instance_url: str, token: str):
        self.instance_url = instance_url
        self.base = "/services/data/" + TARGET_API
        self.http = httpx.Client(timeout=30, base_url=instance_url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"})

    def object_exists(self, obj: str) -> bool:
        try:
            r = self.http.get(f"{self.base}/sobjects/{obj}/describe")
            r.raise_for_status()
            return True
        except httpx.HTTPStatusError:
            return False

    def query(self, soql: str) -> dict:
        r = self.http.get(f"{self.base}/query", params={"q": soql})
        r.raise_for_status()
        return r.json()

    def search(self, obj: str, q: str) -> list[dict]:
        if obj in PERSON_OBJECTS:
            fields = "Id, Name, Email, Company, Owner.Email"
        else:
            fields = "Id, Name, Owner.Email"
        soql = f"SELECT {fields} FROM {obj}"
        if q.strip():
            esc = q.replace("'", "''")
            soql += f" WHERE Name LIKE '%{esc}%'"
        soql += " LIMIT 50"
        recs = self.query(soql).get("records", [])
        return [self.record(obj, r) for r in recs]

    def read(self, obj: str, rec_id: str) -> dict:
        r = self.http.get(f"{self.base}/sobjects/{obj}/{rec_id}")
        r.raise_for_status()
        return r.json()

    def create(self, obj: str, fields: dict) -> dict:
        r = self.http.post(f"{self.base}/sobjects/{obj}/", json=fields)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def record(obj: str, r: dict) -> dict:
        owner_email = (r.get("Owner") or {}).get("Email")
        return {"object": obj, "id": r["Id"], "name": r.get("Name"),
                "email": r.get("Email") or owner_email, "owner_email": owner_email}


def build(sf: Salesforce, objects: list[str], writable: bool = False) -> Connector:
    c = Connector("salesforce")
    wanted = [o for o in objects if sf.object_exists(o)]

    @c.read("objects", (
        "List the Salesforce objects (sObjects) exposed here, with the search, read and (when writing is enabled) create "
        "tool name for each one. Call this first when asked about Salesforce, so you know which object-scoped tool to use "
        "next. Takes no arguments."))
    def objects_tool() -> str:
        """Directory of the available sObjects and their per-object tool names.

        Returns JSON: {salesforce_objects: [{object, search_tool, read_tool, create_tool?}]}.
        """
        return json.dumps({"salesforce_objects": [
            dict({"object": o, "search_tool": f"search_{slug(o)}", "read_tool": f"read_{slug(o)}"},
                 **({"create_tool": f"create_{slug(o)}"} if writable else {})) for o in wanted]})

    for obj in wanted:
        s = slug(obj)

        def make(obj=obj):
            def search(
                query: Annotated[str, Field(default="", description="Text to match against the record Name (SOQL LIKE); leave empty to list records of this object (up to 50).")] = "",
            ) -> str:
                """Records of this object matching the query, each with an identifiable person's email.

                Returns JSON: {object, results: [{object, id, name, email, owner_email}]}.
                """
                return json.dumps({"object": obj, "results": sf.search(obj, query)})

            def read(
                record_id: Annotated[str, Field(description="Salesforce record id to read (15 or 18 characters), as returned in the 'id' field of a search result for this object.")],
            ) -> str:
                """One record of this object by id, normalized to a person-bearing record.

                Returns JSON: {object, record: {object, id, name, email, owner_email}}.
                """
                r = sf.read(obj, record_id)
                return json.dumps({"object": obj, "record": Salesforce.record(obj, r)})

            return search, read

        search, read = make()
        c.read(f"search_{s}", (
            f"Search records of the Salesforce object {obj} by name. The search is fenced to the {obj} object, and each record "
            f"carries an identifiable person (the record's own email, else its owner's). Leave the query empty to list records."))(search)
        c.read(f"read_{s}", (
            f"Read one record from the Salesforce object {obj} by its record_id, normalized to id, name and a person's email. "
            f"Fenced to the {obj} object."))(read)
        if writable:
            def make_create(obj=obj):
                def create(
                    name: Annotated[str, Field(description="Value for the record's Name field on the new record.")],
                    fields: Annotated[dict | None, Field(default=None, description="Optional extra sObject fields as a name-to-value mapping, for example {'Company': 'Acme'}; Name is set from the 'name' argument.")] = None,
                ) -> str:
                    """Create one record of this object. Returns JSON: {object, created_id, success}."""
                    body = dict(fields or {})
                    body.setdefault("Name", name)
                    made = sf.create(obj, body)
                    return json.dumps({"object": obj, "created_id": made.get("id"), "success": made.get("success")})
                return create
            c.write(f"create_{s}", (
                f"Create a record of the Salesforce object {obj} from a name and optional fields. The record is created only on the "
                f"{obj} object, and the proxy governs this call as an egress/write. Provide a name and optional fields."))(make_create())
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True, help="Salesforce instance URL (e.g. https://yourco.my.salesforce.com)")
    ap.add_argument("--token", required=True, help="Salesforce OAuth access token")
    ap.add_argument("--objects", required=True, help="comma-separated sObjects to expose (e.g. Lead,Contact,Opportunity)")
    ap.add_argument("--allow-write", action="store_true", help="expose create tools (governed as writes/egress by the proxy)")
    ap.add_argument("--list", action="store_true", help="print the tools that would be exposed and exit")
    a = ap.parse_args()
    objects = [o.strip() for o in a.objects.split(",") if o.strip()]
    sf = Salesforce(a.instance, a.token)
    if a.list:
        for o in objects:
            here = "" if sf.object_exists(o) else "   (not visible to this token)"
            print(f"  {o!r:24} -> salesforce__search_{slug(o)}, salesforce__read_{slug(o)}"
                  + (f", salesforce__create_{slug(o)}" if a.allow_write else "") + here)
        return
    build(sf, objects, writable=a.allow_write).run()


if __name__ == "__main__":
    main()
