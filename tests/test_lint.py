"""The policy linter: it should catch fail-open and dead-rule mistakes and stay
quiet on a well-formed policy."""

from __future__ import annotations

import textwrap

from aggrete import lint


def write(tmp_path, body: str) -> str:
    p = tmp_path / "coc.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def levels(findings, level):
    return [f for f in findings if f.level == level]


def messages(findings) -> str:
    return " | ".join(f.message for f in findings)


CLEAN = """
    version: 1
    packs: [{id: p, name: P}]
    rules:
      - rule_id: R-1
        pack: p
        severity: high
        enforce:
          - {type: domain_join, action: deny, domains: [a, b], window: 4h}
        tests:
          - {name: ok, expect: allow, sequence: [{domain: a, entities: [p:x]}]}
          - {name: no, expect: deny, sequence: [{domain: a, entities: [p:x]}, {domain: b, entities: [p:x]}]}
"""


def test_clean_policy_has_no_findings(tmp_path):
    assert lint.lint(write(tmp_path, CLEAN)) == []


def test_duplicate_rule_id_is_an_error(tmp_path):
    body = CLEAN + "      - {rule_id: R-1, enforce: [{type: domain_block, domains: [z]}], tests: [{expect: allow}, {expect: deny}]}\n"
    f = lint.lint(write(tmp_path, body))
    assert any("duplicate" in x.message for x in levels(f, "error"))


def test_missing_required_field_is_an_error(tmp_path):
    body = """
        version: 1
        rules:
          - rule_id: R-min
            enforce: [{type: min_group, domain: pay}]   # no k
            tests: [{expect: allow}, {expect: deny}]
    """
    f = lint.lint(write(tmp_path, body))
    assert any("missing required field 'k'" in x.message for x in levels(f, "error"))


def test_domain_join_needs_two_domains(tmp_path):
    body = """
        version: 1
        rules:
          - rule_id: R-j
            enforce: [{type: domain_join, domains: [only]}]
            tests: [{expect: allow}, {expect: deny}]
    """
    assert any("at least two domains" in x.message for x in levels(lint.lint(write(tmp_path, body)), "error"))


def test_high_severity_but_only_alerts_is_a_warning(tmp_path):
    body = """
        version: 1
        rules:
          - rule_id: R-soft
            severity: critical
            enforce: [{type: domain_block, action: alert, domains: [secrets]}]
            tests: [{expect: allow}, {expect: alert}]
    """
    assert any("only alerts" in x.message for x in levels(lint.lint(write(tmp_path, body)), "warn"))


def test_expired_until_is_an_error(tmp_path):
    body = """
        version: 1
        rules:
          - rule_id: R-embargo
            enforce: [{type: wall, domains: [plan], until: 2000-01-01}]
            tests: [{expect: allow}, {expect: deny}]
    """
    assert any("no longer blocks" in x.message for x in levels(lint.lint(write(tmp_path, body)), "error"))


def test_unreachable_domain_flagged_with_config(tmp_path):
    coc = write(tmp_path, CLEAN)
    cfg = tmp_path / "proxy.config.yaml"
    cfg.write_text("domains:\n  \"hr__*\": a\n")   # maps 'a' but not 'b'
    f = lint.lint(coc, str(cfg))
    assert any("never fire" in x.message for x in levels(f, "warn"))
    # ...and with both domains mapped, no unreachable warning
    cfg.write_text("domains:\n  \"x__*\": a\n  \"y__*\": b\n")
    assert not any("never fire" in x.message for x in lint.lint(coc, str(cfg)))
