"""Selective tool exposure: static walls and blocks hide tools per user, so
what a person may never call is never even listed to them."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aggrete.accumulator import MemoryStore  # noqa: E402
from aggrete.policy import Engine  # noqa: E402

COC = str(pathlib.Path(__file__).resolve().parents[1] / "coc.yaml")


def eng():
    return Engine(COC, MemoryStore())


def test_domain_block_hides_a_tool_from_everyone():
    e = eng()
    assert e.tool_visible("anyone@example.com", "legal-hold") is False


def test_wall_hides_from_outsiders_but_not_the_allowed_user():
    e = eng()
    assert e.tool_visible("random@example.com", "privileged") is False
    assert e.tool_visible("counsel@example.com", "privileged") is True


def test_unwalled_domains_stay_visible():
    e = eng()
    for domain in ("hr-personnel", "finance-planning", "ops-rota"):
        assert e.tool_visible("random@example.com", domain) is True


def test_unclassified_or_missing_domain_is_visible():
    e = eng()
    assert e.tool_visible("random@example.com", None) is True


def test_domain_join_does_not_hide_tools():
    # A join rule only fires once the set is accumulated; the tool must remain
    # visible and callable until then.
    e = eng()
    assert e.tool_visible("random@example.com", "finance-comp") is True


def test_granted_purpose_reveals_a_walled_tool():
    e = eng()
    user = "paralegal@example.com"
    assert e.tool_visible(user, "privileged") is False
    e.grant_purpose(user, "COC-LEGAL-002", purpose="active matter #7", ttl_s=3600)
    assert e.tool_visible(user, "privileged") is True
