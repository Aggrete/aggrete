"""`aggrete --demo`: a self-contained walkthrough.

Runs four reasonable-looking questions through the real policy engine and prints
the decisions. No config file, no auth, no network. The fourth question
completes a picture COC-HR-004 forbids and is refused before any upstream would
be contacted. This is what the four-question story on aggrete.com looks like,
running locally.
"""
from __future__ import annotations

import os
import sys
import tempfile

from .accumulator import MemoryStore
from .policy import Engine

_POLICY = """
version: 1
defaults: {window: 24h}
rules:
  - rule_id: COC-HR-004
    clause: >
      Personnel, budget and rotation records may not be combined to derive the
      employment status or planned departure of identifiable individuals.
    remediation: >
      If this is sanctioned workforce planning, request a purpose-bound session
      from HR Privacy. It is time-limited and every retrieval is logged.
    enforce:
      - action: deny
        type: domain_join
        domains: [hr-personnel, finance-comp, ops-rota]
        require_entity_overlap: true
        window: 4h
    tests:
      - {name: allow, expect: allow, sequence: [{domain: finance-comp, entities: [p:a]}]}
      - name: deny
        expect: deny
        sequence:
          - {domain: finance-comp, entities: [p:a]}
          - {domain: hr-personnel, entities: [p:a]}
          - {domain: ops-rota, entities: [p:a]}
  - rule_id: COC-HR-011
    clause: Bulk lists of identifiable people may not be assembled from personnel records.
    enforce:
      - {action: alert, type: entity_budget, domain: hr-personnel, max_distinct: 8, window: 24h}
    tests:
      - {name: allow, expect: allow, sequence: [{domain: hr-personnel, entities: [p:a]}]}
      - {name: alert, expect: alert, sequence: [{domain: hr-personnel, entities: [p:a, p:b, p:c, p:d, p:e, p:f, p:g, p:h, p:i]}]}
"""

_SEQUENCE = [
    ("Q3 headcount plan",   "finance-planning", []),
    ("Backfill-only roles", "finance-comp",     ["p:alice", "p:bob"]),
    ("Recent joiners",      "hr-personnel",     ["p:alice", "p:bob", "p:carol", "p:dan",
                                                 "p:erin", "p:finn", "p:gwen", "p:hank", "p:ivy", "p:jane"]),
    ("On-call gaps",        "ops-rota",         ["p:alice"]),
]


def run() -> None:
    on = sys.stdout.isatty()

    def c(code, s):
        return f"\033[{code}m{s}\033[0m" if on else s

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(_POLICY)
        path = f.name
    try:
        eng = Engine(path, MemoryStore())
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    user = "manager@example.com"
    print()
    print("  " + c("1", "aggrete demo") + c("90", "  four questions, one afternoon"))
    print("  " + c("90", "  one manager, four reasonable-looking requests"))
    print()

    denied = False
    for label, domain, ents in _SEQUENCE:
        pre = eng.pre_call(user, domain)
        if not pre.allow:
            print("  " + c("90", ">") + f" {label:<24} " + c("41;97", " deny "))
            print("     " + c("31", f"{pre.rule_id}. " + " ".join((pre.clause or "").split())))
            print("     " + c("90", "refused at pre-call, the upstream is never contacted"))
            print("     " + c("90", " ".join((pre.remediation or "").split())))
            denied = True
            break
        post = eng.post_call(user, domain, ents)
        if not post.allow:
            print("  " + c("90", ">") + f" {label:<24} " + c("41;97", " deny "))
            print("     " + c("31", f"{post.rule_id}. " + " ".join((post.clause or "").split())))
            denied = True
            break
        tag = "alert" if post.alerts else "allow"
        badge = c("43;30", " alert ") if tag == "alert" else c("42;30", " allow ")
        print("  " + c("90", ">") + f" {label:<24} " + badge)
        print("     " + c("90", f"audit.jsonl  {domain}  post  {tag}"))
    print()
    if denied:
        print("  " + c("90", "The forbidden combination was blocked before any data was fetched."))
    print("  " + c("90", "Run it for real:  https://github.com/cjohannsen81/aggrete"))
    print()
