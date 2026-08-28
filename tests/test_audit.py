"""The audit log is hash-chained, so tampering is detectable."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aggrete.audit import Audit, verify_chain, GENESIS  # noqa: E402


def _write_log(path, n=5):
    a = Audit(str(path))
    for i in range(n):
        a.emit(user="u@example.com", tool=f"hr__t{i}", domain="hr", decision="allow")
    return a


def test_chain_links_each_row_to_the_previous(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 4)
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    assert rows[0]["prev"] == GENESIS
    for a, b in zip(rows, rows[1:]):
        assert b["prev"] == a["hash"]


def test_verify_passes_on_an_untouched_log(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 6)
    ok, bad, scanned = verify_chain(str(p))
    assert ok and bad is None and scanned == 6


def test_editing_a_row_is_detected(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    lines = p.read_text().splitlines()
    row = json.loads(lines[2])
    row["decision"] = "deny"          # flip a decision after the fact
    lines[2] = json.dumps(row)
    p.write_text("\n".join(lines) + "\n")
    ok, bad, _ = verify_chain(str(p))
    assert not ok and bad == 3         # 1-indexed line of the tampered row


def test_deleting_a_row_breaks_the_chain(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    lines = p.read_text().splitlines()
    del lines[1]
    p.write_text("\n".join(lines) + "\n")
    ok, bad, _ = verify_chain(str(p))
    assert not ok and bad == 2


def test_chain_continues_across_restart(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 3)
    _write_log(p, 2)                   # a second Audit() appends to the same file
    ok, bad, scanned = verify_chain(str(p))
    assert ok and scanned == 5
