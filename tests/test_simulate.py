"""The dry-run path behind the built-in `check` tool.

`Engine.simulate` must reproduce the proxy's real pre_call -> post_call flow on
a throwaway store, so a preview matches what enforcement would actually do, and
it must never touch the engine's own accumulated state.
"""

from __future__ import annotations

import os

from aggrete.accumulator import MemoryStore
from aggrete.policy import Engine

COC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "coc.yaml")
USER = "christian@example.com"


def engine():
    return Engine(COC, MemoryStore())


def rule_at(results, i):
    return results[i]["decision"].rule_id


def test_join_trio_is_refused_on_the_completing_call():
    results, blocked = engine().simulate([
        {"domain": "hr-personnel", "entities": ["p:a"]},
        {"domain": "finance-comp", "entities": ["p:a"]},
        {"domain": "ops-rota", "entities": ["p:a"]},
    ], USER)
    assert blocked == 2
    assert rule_at(results, 2) == "COC-HR-004"
    assert results[0]["verdict"] == "allow" and results[1]["verdict"] == "allow"


def test_small_pay_group_is_individual_pay():
    results, blocked = engine().simulate(
        [{"domain": "pay-aggregates", "entities": ["p:a", "p:b", "p:c"]}], USER)
    assert blocked == 0 and rule_at(results, 0) == "COC-HR-031"


def test_large_pay_group_is_fine():
    results, blocked = engine().simulate(
        [{"domain": "pay-aggregates", "entities": [f"p:{n}" for n in range(12)]}], USER)
    assert blocked is None and results[0]["verdict"] == "allow"


def test_self_comparison_needs_own_record_plus_a_colleague():
    results, blocked = engine().simulate([
        {"domain": "timesheets", "entities": [f"p:{USER}"]},
        {"domain": "timesheets", "entities": ["p:teammate@example.com"]},
    ], USER)
    assert blocked == 1 and rule_at(results, 1) == "COC-HR-021"


def test_write_after_untrusted_read_is_refused():
    results, blocked = engine().simulate([
        {"domain": "untrusted-web"},
        {"domain": "shared-notes", "write": True},
    ], USER)
    assert blocked == 1 and rule_at(results, 1) == "COC-SEC-002"


def test_write_without_a_taint_is_allowed():
    results, blocked = engine().simulate([{"domain": "shared-notes", "write": True}], USER)
    assert blocked is None


def test_embargo_wall_and_secret_block_refuse_immediately():
    for domain, rule in [("restructuring-plan", "COC-MGMT-001"), ("secrets-store", "COC-SEC-001")]:
        results, blocked = engine().simulate([{"domain": domain}], USER)
        assert blocked == 0 and rule_at(results, 0) == rule


def test_simulate_does_not_touch_real_state():
    e = engine()
    e.simulate([
        {"domain": "hr-personnel", "entities": ["p:a"]},
        {"domain": "finance-comp", "entities": ["p:a"]},
    ], USER)
    # The dry run used a throwaway store; the engine's own accumulator is untouched.
    assert e.store.domains(USER) == set()


def test_arg_match_previews_in_simulate():
    # A step carrying tool+args runs the arg_match layer: company-wide export refused.
    results, blocked = engine().simulate(
        [{"domain": "unclassified", "tool": "crm__customer_export", "args": {"scope": "all"}}], USER)
    assert blocked == 0
    assert results[0]["verdict"] == "deny"
    assert rule_at(results, 0) == "COC-CRM-001"


def test_arg_match_scoped_export_is_fine_in_simulate():
    results, blocked = engine().simulate(
        [{"domain": "unclassified", "tool": "crm__customer_export", "args": {"scope": "team"}}], USER)
    assert blocked is None
    assert results[0]["verdict"] == "allow"
