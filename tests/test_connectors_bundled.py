"""The bundled connectors import, expose the connector contract, and are all
listed in the manifest. Offline only: no network, no credentials."""

from __future__ import annotations

import importlib
import json
import pathlib

import pytest

from aggrete.connectors.base import Connector

BUNDLED = ["drive", "slack", "github", "jira", "salesforce", "notion"]
MANIFEST = pathlib.Path(__file__).parent.parent / "aggrete" / "connectors" / "connectors.json"


@pytest.mark.parametrize("name", BUNDLED)
def test_connector_imports_and_contract(name):
    mod = importlib.import_module(f"aggrete.connectors.{name}")
    assert hasattr(mod, "build"), f"{name} has no build()"
    assert hasattr(mod, "main"), f"{name} has no main()"
    assert isinstance(getattr(mod, "__version__", None), str), f"{name} has no __version__"
    assert isinstance(getattr(mod, "TARGET_API", None), str), f"{name} has no TARGET_API"


def test_write_verb_is_enforced():
    c = Connector("x")
    # a write tool must contain a write verb, or the proxy cannot govern it as egress
    with pytest.raises(ValueError):
        c.write("emit_report", "not a write verb")
    # a proper write verb is accepted
    c.write("create_note", "fine")


def test_manifest_lists_every_bundled_connector():
    data = json.loads(MANIFEST.read_text())
    ids = {c["id"] for c in data["connectors"]}
    assert set(BUNDLED) == ids, f"manifest {ids} != bundled {set(BUNDLED)}"
    for c in data["connectors"]:
        for key in ("name", "boundary", "module", "auth", "reads", "run", "person", "detail", "version", "status"):
            assert c.get(key), f"{c['id']} missing {key}"
    assert len(data["roadmap"]) >= 5, "expected at least five roadmap connectors"


def test_manifest_versions_match_modules():
    data = json.loads(MANIFEST.read_text())
    for c in data["connectors"]:
        mod = importlib.import_module(c["module"])
        assert c["version"] == mod.__version__, f"{c['id']} manifest/module version mismatch"
