"""ingest.verify must accept the shipped coc.yaml and reject a broken draft."""
import pathlib, sys
import yaml
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from aggrete import ingest  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_shipped_coc_passes_verify():
    assert ingest.verify(yaml.safe_load((ROOT / "coc.yaml").read_text())) == []


def test_rule_with_wrong_expectation_is_rejected():
    coc = yaml.safe_load((ROOT / "coc.yaml").read_text())
    coc["rules"][0]["tests"][0]["expect"] = "allow"  # the layoff-list case is a deny
    assert any("expected allow, got deny" in f for f in ingest.verify(coc))


def test_docx_and_text_documents_become_text_blocks(tmp_path):
    md = tmp_path / "coc.md"; md.write_text("Rosters may not be combined.")
    block = ingest.document_block(md)
    assert block["source"]["type"] == "text" and "combined" in block["source"]["data"]
