import json
from pathlib import Path

import pytest

import research_exact_deletion as deletion
from research_genome.store import ResearchStore


def setup(tmp_path, names=("old.jsonl",)):
    root = tmp_path / "runtime"
    root.mkdir()
    receipts = root / "deletion_receipts"
    receipts.mkdir()
    paths = []
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"old-research\n")
        paths.append(path)
    return {"root": root, "targets": paths, "allowed_paths": list(paths),
            "receipt_path": receipts / "discard.json", "quiescent": True,
            "recovery_states": {"emergency_wal": "EMPTY"}}


def test_exact_discard_retains_only_metadata_and_unrelated_file(tmp_path):
    args = setup(tmp_path, ("old.jsonl", "reports/old.json"))
    other = args["root"] / "keep.json"
    other.write_text("keep")
    result = deletion.delete_exact_research_files(**args)
    assert result["status"] == "COMPLETE"
    assert result["raw_payloads_retained"] is False
    assert all(not p.exists() for p in args["targets"])
    assert other.read_text() == "keep"
    receipt = json.loads(args["receipt_path"].read_text())
    assert receipt["deleted_bytes"] == 26
    assert all(len(r["sha256"]) == 64 for r in receipt["inventory"])
    assert not list(args["root"].rglob("payload"))


@pytest.mark.parametrize("state", ["PREPARED", "DEFERRED", "UNKNOWN", "", None])
def test_unreconciled_recovery_aborts_without_deletion(tmp_path, state):
    args = setup(tmp_path)
    args["recovery_states"] = {"emergency_wal": state}
    with pytest.raises(deletion.ResearchDeletionRejected, match="RECOVERY_NOT_RECONCILED"):
        deletion.delete_exact_research_files(**args)
    assert args["targets"][0].exists()
    assert not args["receipt_path"].exists()


@pytest.mark.parametrize("name", ["bot.py", ".env", "persistent_config.json", "v3/emergency_wal/one.json", "v3/lifecycle_owner.json", "keys/private.pem"])
def test_protected_file_rejected_even_if_allowlisted(tmp_path, name):
    args = setup(tmp_path, (name,))
    with pytest.raises(deletion.ResearchDeletionRejected, match="PROTECTED"):
        deletion.delete_exact_research_files(**args)
    assert args["targets"][0].exists()


@pytest.mark.parametrize("case", ["not_allowed", "outside", "root", "directory", "receipt", "not_quiescent", "limit", "protected"])
def test_fail_closed_target_boundaries(tmp_path, case):
    args = setup(tmp_path)
    if case == "not_allowed": args["allowed_paths"] = []
    if case == "outside":
        outside = tmp_path / "outside.json"
        outside.write_text("keep")
        args["targets"] = [outside]
    if case == "root": args["targets"] = [args["root"]]
    if case == "directory":
        args["targets"] = [args["root"] / "deletion_receipts"]
        args["allowed_paths"] = list(args["targets"])
    if case == "receipt": args["receipt_path"] = args["targets"][0]
    if case == "not_quiescent": args["quiescent"] = "true"
    if case == "limit": args["max_files"] = 0
    if case == "protected": args["protected_paths"] = list(args["targets"])
    with pytest.raises(deletion.ResearchDeletionRejected):
        deletion.delete_exact_research_files(**args)


def test_symlink_escape_is_not_followed(tmp_path):
    args = setup(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("keep")
    link = args["root"] / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("host cannot create symlink")
    args["targets"] = args["allowed_paths"] = [link]
    with pytest.raises(deletion.ResearchDeletionRejected, match="SYMLINK"):
        deletion.delete_exact_research_files(**args)
    assert outside.read_text() == "keep"


def test_windows_reparse_attribute_rejected_without_following(tmp_path, monkeypatch):
    args = setup(tmp_path)
    original = Path.lstat
    target = args["targets"][0]
    def lstat(path, *a, **kw):
        info = original(path, *a, **kw)
        if path == target:
            class Reparse:
                st_mode = info.st_mode
                st_file_attributes = 0x400
            return Reparse()
        return info
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(deletion.ResearchDeletionRejected, match="REPARSE"):
        deletion.delete_exact_research_files(**args)
    assert target.exists()


def test_changed_file_after_metadata_receipt_aborts_before_first_unlink(tmp_path, monkeypatch):
    args = setup(tmp_path)
    original = deletion._write_receipt
    def write(path, payload, **kwargs):
        original(path, payload, **kwargs)
        if kwargs.get("first"):
            args["targets"][0].write_text("changed")
    monkeypatch.setattr(deletion, "_write_receipt", write)
    with pytest.raises(deletion.ResearchDeletionRejected, match="CHANGED"):
        deletion.delete_exact_research_files(**args)
    assert args["targets"][0].read_text() == "changed"
    assert json.loads(args["receipt_path"].read_text())["status"] == "ABORTED"


def test_partial_failure_receipt_is_not_complete(tmp_path, monkeypatch):
    args = setup(tmp_path, ("a.jsonl", "b.jsonl"))
    original = Path.unlink
    def unlink(path, *a, **kw):
        if path.name == "b.jsonl": raise PermissionError("busy")
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(PermissionError): deletion.delete_exact_research_files(**args)
    receipt = json.loads(args["receipt_path"].read_text())
    assert receipt["status"] == "PARTIAL"
    assert len(receipt["deleted"]) == 1
    assert args["targets"][1].exists()


def test_store_destructive_mode_recreates_empty_database_without_quarantine(tmp_path):
    store = ResearchStore(str(tmp_path))
    store.append_event({"event_name": "old", "ts": "t"})
    receipt = tmp_path / "deletion.json"
    result = store.reset(destructive=True, deletion_receipt_path=receipt, quiescent=True,
                         recovery_states={"emergency_wal": "NOT_PRESENT"})
    assert result["raw_payloads_retained"] is False
    assert not (tmp_path / "epoch_quarantine").exists()
    assert store._conn.execute("select count(*) from research_events").fetchone()[0] == 0
    assert json.loads(receipt.read_text())["status"] == "COMPLETE"
    store.close()


def test_store_default_still_retains_sqlite(tmp_path):
    store = ResearchStore(str(tmp_path))
    store.reset()
    assert list((tmp_path / "epoch_quarantine").rglob("research.db"))
    store.close()


def test_store_missing_quiescence_does_not_close_or_delete(tmp_path):
    store = ResearchStore(str(tmp_path))
    with pytest.raises(deletion.ResearchDeletionRejected):
        store.reset(destructive=True, deletion_receipt_path=tmp_path / "receipt.json")
    assert store._conn is not None
    assert Path(store.db_path).exists()
    store.close()


def test_byte_limit_rejects_before_hash_or_receipt(tmp_path, monkeypatch):
    args = setup(tmp_path)
    args["max_total_bytes"] = 1
    monkeypatch.setattr(deletion, "_fingerprint", lambda _: pytest.fail("must not hash oversized targets"))
    with pytest.raises(deletion.ResearchDeletionRejected, match="BYTE_LIMIT"):
        deletion.delete_exact_research_files(**args)
    assert args["targets"][0].exists()
    assert not args["receipt_path"].exists()


def test_duplicate_allowlist_cannot_bypass_iteration_limit(tmp_path):
    args = setup(tmp_path)
    args["max_files"] = 1
    args["allowed_paths"] = [args["targets"][0]] * 2
    with pytest.raises(deletion.ResearchDeletionRejected, match="ALLOWLIST_LIMIT"):
        deletion.delete_exact_research_files(**args)


def test_abrupt_exit_after_unlink_has_durable_intent_and_readonly_reconciliation(tmp_path, monkeypatch):
    args = setup(tmp_path, ("a.jsonl", "b.jsonl"))
    original = Path.unlink
    def unlink_then_crash(path, *a, **kw):
        original(path, *a, **kw)
        raise SystemExit("process loss after unlink before outcome")
    monkeypatch.setattr(Path, "unlink", unlink_then_crash)
    with pytest.raises(SystemExit):
        deletion.delete_exact_research_files(**args)
    receipt_before = args["receipt_path"].read_bytes()
    assert json.loads(receipt_before)["status"] == "PREPARED"
    journal = Path(str(args["receipt_path"]) + ".progress.jsonl")
    assert json.loads(journal.read_text())["phase"] == "INTENT"
    result = deletion.reconcile_research_deletion(args["receipt_path"])
    assert result["counts"] == {"ABSENT_AFTER_INTENT": 1, "RETAINED": 1}
    assert result["deletion_performed"] is False
    assert args["receipt_path"].read_bytes() == receipt_before
    assert args["targets"][1].exists()


def test_complete_journal_reconciles_exact_unlinks(tmp_path):
    args = setup(tmp_path, ("a.jsonl", "b.jsonl"))
    deletion.delete_exact_research_files(**args)
    result = deletion.reconcile_research_deletion(args["receipt_path"])
    assert result["counts"] == {"UNLINKED_CONFIRMED": 2}
    assert len(Path(str(args["receipt_path"]) + ".progress.jsonl").read_text().splitlines()) == 4


def test_corrupt_progress_is_never_trusted(tmp_path):
    args = setup(tmp_path)
    deletion.delete_exact_research_files(**args)
    journal = Path(str(args["receipt_path"]) + ".progress.jsonl")
    journal.write_text(journal.read_text().replace("UNLINKED", "INTENT"))
    with pytest.raises(deletion.ResearchDeletionRejected, match="HASH_MISMATCH"):
        deletion.reconcile_research_deletion(args["receipt_path"])


def test_empty_recovery_proof_key_is_rejected(tmp_path):
    args = setup(tmp_path)
    args["recovery_states"] = {" ": "EMPTY"}
    with pytest.raises(deletion.ResearchDeletionRejected, match="PROOF_KEY"):
        deletion.delete_exact_research_files(**args)
    assert args["targets"][0].exists()


def test_progress_target_overlap_is_rejected(tmp_path):
    args = setup(tmp_path)
    journal = Path(str(args["receipt_path"]) + ".progress.jsonl")
    args["allowed_paths"].append(journal)
    with pytest.raises(deletion.ResearchDeletionRejected, match="PROGRESS"):
        deletion.delete_exact_research_files(**args)


def test_same_length_journal_from_another_receipt_is_rejected(tmp_path):
    args = setup(tmp_path, ("a.jsonl", "b.jsonl"))
    first = {**args, "targets": [args["targets"][0]], "allowed_paths": [args["targets"][0]]}
    second = {**args, "targets": [args["targets"][1]], "allowed_paths": [args["targets"][1]],
              "receipt_path": args["receipt_path"].with_name("second.json")}
    deletion.delete_exact_research_files(**first)
    deletion.delete_exact_research_files(**second)
    first_journal = Path(str(first["receipt_path"]) + ".progress.jsonl")
    second_journal = Path(str(second["receipt_path"]) + ".progress.jsonl")
    assert len(first_journal.read_bytes()) == len(second_journal.read_bytes())
    second_journal.write_bytes(first_journal.read_bytes())
    with pytest.raises(deletion.ResearchDeletionRejected, match="JOURNAL_HASH_MISMATCH"):
        deletion.reconcile_research_deletion(second["receipt_path"])


def test_seed_recomputed_from_inventory_rejects_receipt_mutation(tmp_path):
    args = setup(tmp_path)
    deletion.delete_exact_research_files(**args)
    receipt = json.loads(args["receipt_path"].read_text())
    receipt["inventory"][0]["bytes"] += 1
    args["receipt_path"].write_text(json.dumps(receipt))
    with pytest.raises(deletion.ResearchDeletionRejected, match="JOURNAL_SEED_MISMATCH"):
        deletion.reconcile_research_deletion(args["receipt_path"])


def test_initial_progress_is_bound_to_stored_receipt_seed(tmp_path):
    args = setup(tmp_path)
    deletion.delete_exact_research_files(**args)
    receipt = json.loads(args["receipt_path"].read_text())
    journal_first = json.loads(Path(receipt["progress_journal"]).read_text().splitlines()[0])
    assert journal_first["previous_sha256"] == receipt["progress_seed_sha256"]
    assert len(receipt["progress_seed_sha256"]) == 64
