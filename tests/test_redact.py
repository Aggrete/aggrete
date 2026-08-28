"""Output redaction masks secrets and PII on the payload path."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aggrete.redact import redact, rules_from_config, DEFAULT  # noqa: E402


def test_default_config_selects_the_default_rules():
    assert [n for n, _ in rules_from_config(True)] == DEFAULT
    assert rules_from_config(None) == []
    assert rules_from_config([]) == []


def test_masks_email_and_ssn():
    rules = rules_from_config(["email", "ssn"])
    out, counts = redact("reach mia@example.com, SSN 123-45-6789", rules)
    assert "mia@example.com" not in out
    assert "123-45-6789" not in out
    assert counts == {"email": 1, "ssn": 1}
    assert "[redacted:email]" in out and "[redacted:ssn]" in out


def test_masks_secrets_and_counts_multiples():
    rules = rules_from_config(True)
    text = "key AKIAABCDEFGHIJKLMNOP and token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    out, counts = redact(text, rules)
    assert "AKIA" not in out and "ghp_" not in out
    assert counts.get("aws_key") == 1 and counts.get("api_key") == 1


def test_leaves_clean_text_untouched():
    rules = rules_from_config(True)
    out, counts = redact("the Q3 headcount plan has 12 open roles", rules)
    assert counts == {}
    assert out == "the Q3 headcount plan has 12 open roles"


def test_selecting_only_one_rule_leaves_others_alone():
    rules = rules_from_config(["ssn"])
    out, counts = redact("mia@example.com / 123-45-6789", rules)
    assert "mia@example.com" in out          # email rule not selected
    assert "123-45-6789" not in out
    assert counts == {"ssn": 1}
