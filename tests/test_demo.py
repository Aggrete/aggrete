"""The self-contained `aggrete --demo` policy and its interactive scenarios."""
import os, tempfile
from aggrete import _demo
from aggrete.accumulator import MemoryStore
from aggrete.policy import Engine

def _engine():
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False); f.write(_demo._POLICY); f.close()
    try: return Engine(f.name, MemoryStore())
    finally: os.unlink(f.name)

def test_demo_policy_loads_every_rule_type():
    eng = _engine()
    assert {r.id for r in eng.rules} >= {"COC-HR-004","COC-HR-011","COC-HR-031","COC-HR-021","COC-SEC-002","COC-MGMT-001","COC-SEC-001"}

def test_each_interactive_scenario_reaches_its_rule():
    eng = _engine()
    expect = ["COC-HR-004","COC-HR-011","COC-HR-031","COC-HR-021","COC-SEC-002","COC-MGMT-001","COC-SEC-001"]
    for (label, steps), want in zip(_demo._SCENARIOS, expect):
        sim = [{"domain": d, "write": w, "entities": e} for d, w, e in steps]
        results, blocked = eng.simulate(sim, _demo._USER)
        last = results[-1]; d = last["decision"]
        hit = d.rule_id == want or any(a.get("rule_id") == want for a in d.alerts)
        assert hit, f"{label}: got {d.rule_id}/{d.alerts}, expected {want}"
