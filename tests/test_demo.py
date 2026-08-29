"""The `aggrete --demo` walkthrough allows the first questions and denies the
one that completes the forbidden combination."""
from __future__ import annotations
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from aggrete import _demo  # noqa: E402


def test_demo_denies_the_fourth_question(capsys):
    _demo.run()
    out = capsys.readouterr().out
    assert "Q3 headcount plan" in out
    assert "On-call gaps" in out
    assert "COC-HR-004" in out
    assert "deny" in out
    assert "alert" in out
    assert out.count("allow") >= 2
    assert "upstream is never contacted" in out
