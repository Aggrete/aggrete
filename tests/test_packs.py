"""Protection packs group rules, can be toggled on/off (with a state file the
proxy re-reads), and the prompt-injection flow rule breaks taint->egress."""
from __future__ import annotations
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from aggrete.accumulator import MemoryStore  # noqa: E402
from aggrete.policy import Engine  # noqa: E402

COC = str(pathlib.Path(__file__).resolve().parents[1] / "coc.yaml")


def test_packs_metadata_lists_all_with_state():
    packs = {p["id"]: p for p in Engine(COC, MemoryStore()).packs()}
    assert "code-of-conduct" in packs and "prompt-injection" in packs
    assert packs["code-of-conduct"]["enabled"] is True
    assert packs["export-control"]["enabled"] is False          # ships off
    assert packs["code-of-conduct"]["rules"] >= 1


def test_disabling_a_pack_stops_its_rules():
    e = Engine(COC, MemoryStore())
    # COC-HR-004 (code-of-conduct) denies the four-question join
    def hits_deny(eng):
        for d, ents in [("finance-comp", ["p:a"]), ("hr-personnel", ["p:a"]), ("ops-rota", ["p:a"])]:
            if not eng.pre_call("m@x.com", d).allow:
                return True
            eng.post_call("m@x.com", d, ents)
        return not eng.pre_call("m@x.com", "ops-rota").allow
    assert hits_deny(Engine(COC, MemoryStore())) is True
    off = Engine(COC, MemoryStore()); off.set_pack("code-of-conduct", False)
    assert hits_deny(off) is False


def test_flow_rule_blocks_egress_after_untrusted():
    e = Engine(COC, MemoryStore())
    e.set_pack("prompt-injection", True)
    assert e.pre_call("u@x.com", "private-repos").allow            # no taint yet
    e2 = Engine(COC, MemoryStore()); e2.set_pack("prompt-injection", True)
    e2.post_call("u@x.com", "public-issues", [])                   # read untrusted content
    assert not e2.pre_call("u@x.com", "private-repos").allow       # egress now blocked


def test_pack_state_file_is_read_lazily(tmp_path):
    sf = tmp_path / "pack_state.json"
    sf.write_text(json.dumps({"code-of-conduct": False}))
    e = Engine(COC, MemoryStore(), pack_state_path=str(sf))
    assert e.pack_enabled("code-of-conduct") is False
    sf.write_text(json.dumps({"code-of-conduct": True}))           # toggled elsewhere
    assert e.pack_enabled("code-of-conduct") is True               # picked up without restart
