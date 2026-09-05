"""Inbound secret scanning of tool arguments (the proxy's _scan_inbound)."""

from __future__ import annotations

import os

from aggrete.accumulator import MemoryStore
from aggrete.audit import Audit
from aggrete.policy import Engine
from aggrete.proxy import Proxy

COC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "coc.yaml")


def proxy(cfg):
    return Proxy(cfg, Engine(COC, MemoryStore()), Audit(None))


def test_default_flags_credentials_only():
    p = proxy({"scan_inbound": True})
    masked, hits = p._scan_inbound({"note": "the key is AKIAIOSFODNN7EXAMPLE thanks"})
    assert hits.get("aws_key") == 1
    assert "[redacted:aws_key]" in masked["note"]


def test_email_and_id_are_not_secrets():
    p = proxy({"scan_inbound": True})
    masked, hits = p._scan_inbound({"email": "alice.n@example.com", "count": 7})
    assert hits == {}
    assert masked["email"] == "alice.n@example.com"


def test_walks_nested_structures():
    p = proxy({"scan_inbound": True})
    args = {"outer": {"headers": ["Authorization: Bearer abcdef0123456789abcd"]}}
    masked, hits = p._scan_inbound(args)
    assert hits.get("bearer") == 1
    assert "[redacted:bearer]" in masked["outer"]["headers"][0]


def test_disabled_by_default():
    p = proxy({})   # no scan_inbound
    assert p.inbound_rules == []


def test_explicit_rule_selection():
    p = proxy({"scan_inbound": ["api_key"]})
    _, hits = p._scan_inbound({"a": "sk-ABCDEFGHIJKLMNOP01234", "b": "AKIAIOSFODNN7EXAMPLE"})
    assert "api_key" in hits and "aws_key" not in hits   # only the selected rule fires
