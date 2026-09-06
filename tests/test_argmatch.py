"""Argument-level rules (`arg_match`): the same tool decided by what it is asked
to do, and the condition operators."""

from __future__ import annotations

import textwrap

from aggrete.accumulator import MemoryStore
from aggrete.policy import Engine, _arg_matches


def test_arg_matches_operators():
    assert _arg_matches([{"arg": "scope", "equals": "all"}], {"scope": "all"})
    assert not _arg_matches([{"arg": "scope", "equals": "all"}], {"scope": "team"})
    assert _arg_matches([{"arg": "scope", "in": ["all", "company"]}], {"scope": "company"})
    assert _arg_matches([{"arg": "q", "regex": "DROP\\s+TABLE"}], {"q": "select; DROP TABLE x"})
    assert _arg_matches([{"arg": "limit", "gt": 1000}], {"limit": 5000})
    assert not _arg_matches([{"arg": "limit", "gt": 1000}], {"limit": 10})
    assert _arg_matches([{"arg": "token", "exists": True}], {"token": "x"})
    assert _arg_matches([{"arg": "token", "missing": True}], {})
    # ALL conditions must hold
    assert _arg_matches([{"arg": "scope", "equals": "all"}, {"arg": "fmt", "equals": "csv"}],
                        {"scope": "all", "fmt": "csv"})
    assert not _arg_matches([{"arg": "scope", "equals": "all"}, {"arg": "fmt", "equals": "csv"}],
                            {"scope": "all", "fmt": "json"})
    # empty / unknown fail safe (do not match)
    assert not _arg_matches([], {"scope": "all"})
    assert not _arg_matches([{"arg": "scope", "weird": 1}], {"scope": "all"})


COC = """
version: 1
defaults: {window: 24h}
rules:
  - rule_id: COC-DATA-010
    clause: Company-wide exports are not available to assistants.
    remediation: Use a team-level scope.
    enforce:
      - type: arg_match
        tools: ["*__export*"]
        deny_when: [{arg: scope, in: [all, company]}]
        action: deny
  - rule_id: COC-DATA-011
    clause: Very large reads are flagged.
    enforce:
      - type: arg_match
        tools: ["*"]
        deny_when: [{arg: limit, gt: 10000}]
        action: alert
"""


def _engine(tmp_path):
    p = tmp_path / "coc.yaml"; p.write_text(textwrap.dedent(COC))
    return Engine(str(p), MemoryStore())


def test_check_args_denies_company_export_allows_team(tmp_path):
    e = _engine(tmp_path)
    assert e.check_args("u", "crm__export", {"scope": "all"}).rule_id == "COC-DATA-010"
    assert not e.check_args("u", "crm__export", {"scope": "all"}).allow
    assert e.check_args("u", "crm__export", {"scope": "team"}).allow
    # a tool that does not match the glob is unaffected
    assert e.check_args("u", "hr__lookup", {"scope": "all"}).allow


def test_check_args_alert_allows_but_flags(tmp_path):
    e = _engine(tmp_path)
    d = e.check_args("u", "hr__lookup", {"limit": 50000})
    assert d.allow and d.alerts and d.alerts[0]["rule_id"] == "COC-DATA-011"


def test_check_args_purpose_grant_opens_window(tmp_path):
    e = _engine(tmp_path)
    assert not e.check_args("u", "crm__export", {"scope": "all"}).allow
    e.grant_purpose("u", "COC-DATA-010", "audited quarterly export", ttl_s=3600)
    assert e.check_args("u", "crm__export", {"scope": "all"}).allow
