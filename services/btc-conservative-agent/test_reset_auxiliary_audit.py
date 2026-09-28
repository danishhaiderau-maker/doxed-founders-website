import json
from research_reset_auxiliary_audit import audit_auxiliary_cleanup

def test_absent_owners_are_empty(tmp_path):
    assert audit_auxiliary_cleanup(tmp_path)["safe"]

def test_prepared_without_terminal_blocks(tmp_path):
    tx = tmp_path / "v3/lifecycle_purge_transactions/a"
    tx.mkdir(parents=True)
    row = dict(schema="lifecycle_cleanup_purge_v1", state="PREPARED", bundle_id="one")
    (tx / "PREPARED.json").write_text(json.dumps(row))
    assert not audit_auxiliary_cleanup(tmp_path)["safe"]
    (tx / "PURGED.json").write_text(json.dumps({**row, "state":"PURGED"}))
    assert audit_auxiliary_cleanup(tmp_path)["safe"]
    (tx / "PURGED.json").write_text(json.dumps({**row, "state":"PURGED", "bundle_id":"other"}))
    assert not audit_auxiliary_cleanup(tmp_path)["safe"]

def test_budget_never_reports_clean(tmp_path):
    for name in ("a", "b"):
        (tmp_path / "raw_generation_cleanup_transactions" / name).mkdir(parents=True)
    result = audit_auxiliary_cleanup(tmp_path, max_entries=1)
    assert not result["complete"] and not result["safe"]

def test_corrupt_receipt_blocks_without_mutation(tmp_path):
    tx = tmp_path / "raw_generation_cleanup_transactions/a"
    tx.mkdir(parents=True)
    path = tx / "PREPARED.json"
    path.write_text("bad")
    assert not audit_auxiliary_cleanup(tmp_path)["safe"]
    assert path.read_text() == "bad"
