"""Drive connector: slugs, query building, folder fencing, entity extraction. No network."""
import json
from aggrete.connectors import drive as d
from aggrete.entities import extract


def test_slug_and_tool_names():
    assert d.slug("Restructuring plan") == "restructuring_plan" and d.slug("Legal hold (2026)") == "legal_hold_2026"


def test_search_query_scopes_to_folder_and_escapes(monkeypatch):
    calls = []
    class Fake(d.Drive):
        def __init__(self): self.sa = {"client_email": "sa@x"}
        def subfolders(self, fid): return []
        def list(self, q, n=20): calls.append(q); return [{"id": "1", "name": "Q4 plan", "owners": [{"emailAddress": "cfo@corp"}], "lastModifyingUser": {"emailAddress": "chro@corp"}}]
    r = Fake().search("F1", "layoff's")
    assert "'F1' in parents" in calls[0] and "fullText contains 'layoff\\'s'" in calls[0] and r[0]["name"] == "Q4 plan"


def test_read_refuses_files_outside_the_folder():
    class Fake(d.Drive):
        def __init__(self): self.sa = {}
        def get(self, path, **kw):
            class R: 
                def json(self_inner): return {"id": "9", "name": "x", "mimeType": "text/plain", "parents": ["ELSEWHERE"]}
            return R()
        def descendants(self, fid, depth=3): return {fid}
    import pytest
    with pytest.raises(PermissionError):
        Fake().read("9", "F1")


def test_drive_results_yield_people():
    payload = json.dumps({"folder": "Restructuring plan", "files": [{"id": "1", "name": "Q4 plan", "owner_email": "CFO@corp.example", "editor_email": "chro@corp.example"}]})
    assert extract(payload) == ["p:cfo@corp.example"]     # one record, one person (owner preferred); editor is the same record
