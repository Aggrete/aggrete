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


def test_impersonation_claims(tmp_path):
    import json as _json
    from aggrete.connectors.drive import Drive
    sa = {"client_email": "sa@proj.iam.gserviceaccount.com",
          "token_uri": "https://oauth2.googleapis.com/token", "private_key": "unused-here"}
    (tmp_path / "sa.json").write_text(_json.dumps(sa))
    plain = Drive(tmp_path / "sa.json")
    assert "sub" not in plain._claims(1000)                        # service account acts as itself
    deleg = Drive(tmp_path / "sa.json", subject="alice@corp.com")
    c = deleg._claims(1000)
    assert c["sub"] == "alice@corp.com" and c["iss"].startswith("sa@")   # impersonates the user


def test_set_subject_switches_and_clears_token(tmp_path):
    import json as _json
    from aggrete.connectors.drive import Drive
    sa = {"client_email": "sa@p.iam", "token_uri": "https://t", "private_key": "x"}
    (tmp_path / "sa.json").write_text(_json.dumps(sa))
    d = Drive(tmp_path / "sa.json")
    d._tok, d._exp = "cached", 9e18            # pretend we hold a token
    d.set_subject("alice@corp.com")
    assert d.subject == "alice@corp.com" and d._tok is None   # switching users drops the token
    assert d._claims(1)["sub"] == "alice@corp.com"
    d.set_subject("alice@corp.com")            # same user: no-op, no error
