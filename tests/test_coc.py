"""The tests come from coc.yaml. A clause nobody can test is a clause nobody
can trust, so CI also fails any rule that lacks both an allow and a deny case.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aggrete.accumulator import MemoryStore  # noqa: E402
from aggrete.policy import Engine  # noqa: E402

COC = str(pathlib.Path(__file__).resolve().parents[1] / "coc.yaml")


def run_sequence(steps, user: str = "test@example.com") -> str:
    engine = Engine(COC, MemoryStore())
    outcome = "allow"
    for step in steps:
        pre = engine.pre_call(user, step["domain"])
        if not pre.allow:
            return "deny"
        post = engine.post_call(user, step["domain"], [f"p:{user}" if x == "p:self" else x for x in step.get("entities", [])])
        if not post.allow:
            return "deny"
        if post.alerts:
            outcome = "alert"
    return outcome


def cases():
    for rule in Engine(COC, MemoryStore()).rules:
        for t in rule.tests:
            yield pytest.param(rule.id, t, id=f"{rule.id}-{t['name']}")


@pytest.mark.parametrize("rule_id,case", list(cases()))
def test_clause(rule_id, case):
    assert run_sequence(case["sequence"], case.get("user", "test@example.com")) == case["expect"]


def test_every_rule_has_positive_and_negative_coverage():
    for rule in Engine(COC, MemoryStore()).rules:
        expectations = {t["expect"] for t in rule.tests}
        assert "allow" in expectations, f"{rule.id} has no allow test"
        assert expectations & {"deny", "alert"}, f"{rule.id} has no deny/alert test"


def test_purpose_grant_opens_a_scoped_window():
    engine = Engine(COC, MemoryStore())
    user = "hrbp@example.com"
    for domain in ("finance-comp", "hr-personnel"):
        engine.post_call(user, domain, ["p:alice", "p:bob"])
    assert not engine.pre_call(user, "ops-rota").allow

    engine.grant_purpose(user, "COC-HR-004", purpose="Q4 workforce planning", ttl_s=3600)
    decision = engine.pre_call(user, "ops-rota")
    assert decision.allow and decision.granted_purpose == "Q4 workforce planning"


def test_state_is_per_user_not_per_session():
    engine = Engine(COC, MemoryStore())
    for domain in ("finance-comp", "hr-personnel"):
        engine.post_call("a@example.com", domain, ["p:alice"])
    assert not engine.pre_call("a@example.com", "ops-rota").allow
    assert engine.pre_call("b@example.com", "ops-rota").allow  # different user, clean slate


# --- entity linking -----------------------------------------------------------

def test_email_and_employee_id_in_one_record_are_one_person():
    from aggrete.entities import extract
    payload = '{"joiners": [{"email": "A.N@example.com", "employee_id": "E-1041"},' \
              '{"email": "bob.k@example.com", "employee_id": "E-1052"}]}'
    assert extract(payload) == ["p:a.n@example.com", "p:bob.k@example.com"]


def test_record_without_email_falls_back_to_id():
    from aggrete.entities import extract
    assert extract('{"assignee_id": "U123"}') == ["p:u123"]


def test_nested_person_objects_are_separate_people():
    from aggrete.entities import extract
    payload = '{"email": "a@example.com", "manager": {"email": "m@example.com"}}'
    assert extract(payload) == ["p:a@example.com", "p:m@example.com"]


def test_email_key_matches_across_connectors():
    # finance exposes owner_email only; HR exposes email + employee_id.
    from aggrete.entities import extract
    fin = extract('{"lines": [{"role": "SRE II", "owner_email": "alice.n@example.com"}]}')
    hr = extract('{"joiners": [{"email": "alice.n@example.com", "employee_id": "E-1041"}]}')
    assert fin == hr == ["p:alice.n@example.com"]
