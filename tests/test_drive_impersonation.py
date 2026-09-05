"""The Drive connector must register under the MCP SDK (no tool parameter may
start with '_', which the SDK rejects) while still receiving the proxy-injected
caller identity for domain-wide-delegation impersonation."""
from __future__ import annotations

from aggrete.connectors import drive as D


class FakeDrive:
    sa = {"client_email": "sa@x.iam"}
    writable = True

    def __init__(self):
        self.subject = None

    def folder_by_name(self, name):
        return {"id": "root", "name": name}

    def subfolders(self, root_id):
        return [{"id": "f1", "name": "Restructuring"}]

    def set_subject(self, email):
        self.subject = email

    def search(self, fid, query):
        return []

    def read(self, file_id, fid):
        return ({"name": "x", "owners": [{"emailAddress": "o@x"}]}, "text")

    def create(self, fid, name, content):
        return {"name": name, "id": "n1", "webViewLink": "http://x"}


def _tools(server):
    return server._tool_manager._tools


def test_drive_build_registers_with_impersonation():
    server = D.build(FakeDrive(), "Northwind", impersonate=True)
    tools = _tools(server)
    assert {"folders", "search_restructuring", "read_restructuring", "create_restructuring"} <= set(tools)


def test_no_tool_parameter_starts_with_underscore():
    server = D.build(FakeDrive(), "Northwind", impersonate=True)
    for name, t in _tools(server).items():
        props = (t.parameters or {}).get("properties", {})
        bad = [p for p in props if p.startswith("_")]
        assert not bad, f"{name} exposes underscore params the SDK rejects: {bad}"


def test_impersonation_channel_present_as_acting_user():
    server = D.build(FakeDrive(), "Northwind", impersonate=True)
    props = (_tools(server)["search_restructuring"].parameters or {}).get("properties", {})
    assert "acting_user" in props and props["acting_user"].get("description")
