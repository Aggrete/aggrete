"""Minimal Aggrete connector. Copy this file to start a new one.

Run it stand-alone (lists nothing useful; the proxy drives it):
    python examples/connectors/knowledgebase_connector.py

Wire it into proxy.config.yaml:
    upstreams:
      kb: {command: python3, args: [examples/connectors/knowledgebase_connector.py]}
    domains:
      "kb__*": knowledge-base

Then the assistant can `kb__search_docs` / `kb__read_doc` (governed reads) and
`kb__create_doc` (a governed write: refused after the session reads untrusted
content, and subject to any `applies: write` rule).
"""
import json

from aggrete.connectors.base import Connector

DOCS = {
    "onboarding": "New hires complete account setup in their first week.",
    "pto": "Request paid time off at least two weeks ahead.",
}

c = Connector("kb")


@c.read("search_docs", "Search the knowledge base by keyword.")
def search(query: str = "") -> str:
    q = query.lower()
    hits = [k for k, v in DOCS.items() if q in (k + " " + v).lower()]
    return json.dumps({"matches": hits})


@c.read("read_doc", "Read a knowledge-base document by its id.")
def read(doc_id: str) -> str:
    return json.dumps({"id": doc_id, "text": DOCS.get(doc_id, "")})


@c.write("create_doc", "Create a knowledge-base document.")
def create(doc_id: str, text: str) -> str:
    DOCS[doc_id] = text
    return json.dumps({"created": doc_id})


if __name__ == "__main__":
    c.run()
