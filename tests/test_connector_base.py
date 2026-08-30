"""Connector SDK base: read/write helpers, write-verb enforcement, tool registration. No network."""
import json
import pytest
from aggrete.connectors.base import Connector, WRITE_VERBS


def test_write_verbs_track_the_proxy_defaults():
    from aggrete.proxy import DEFAULT_WRITE_TOOLS
    # every verb the base advertises should be recognizable as a write by the proxy patterns
    joined = " ".join(DEFAULT_WRITE_TOOLS)
    for v in WRITE_VERBS:
        assert v in joined, f"write verb {v!r} is not in the proxy's default write patterns"


def test_write_helper_rejects_non_write_names():
    c = Connector("crm")
    with pytest.raises(ValueError):
        c.write("fetch_account", "not a write verb")


def test_read_and_write_tools_register_on_the_server():
    c = Connector("kb")

    @c.read("search_docs", "Search.")
    def search(query: str = "") -> str:
        return json.dumps({"matches": []})

    @c.write("create_doc", "Create.")
    def create(doc_id: str, text: str) -> str:
        return json.dumps({"created": doc_id})

    names = _tool_names(c)
    assert "search_docs" in names and "create_doc" in names


def test_example_connector_imports_and_registers():
    import importlib.util, pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "examples" / "connectors" / "knowledgebase_connector.py"
    spec = importlib.util.spec_from_file_location("kb_example", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = _tool_names(mod.c)
    assert {"search_docs", "read_doc", "create_doc"} <= set(names)


def _tool_names(c):
    """Pull registered tool names off the MCP server, tolerant of its internal shape."""
    srv = c.server
    for attr in ("_tools", "tools", "_tool_manager"):
        obj = getattr(srv, attr, None)
        if obj is None:
            continue
        reg = getattr(obj, "_tools", obj)
        try:
            if isinstance(reg, dict):
                return list(reg.keys())
            return [getattr(t, "name", None) or t.get("name") for t in reg]
        except Exception:
            continue
    # last resort: list_tools may be async; skip if we cannot introspect
    return []
