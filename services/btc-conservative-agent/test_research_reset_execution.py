import hashlib
import json
import os
from pathlib import Path

import pytest

import research_reset_execution as execution
from research_exact_deletion import ResearchDeletionRejected
from research_reset_inventory import PROOF_SCHEMA


def settings(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    receipts = root / "deletion_receipts"
    receipts.mkdir()
    proof = {"schema": PROOF_SCHEMA, "runtime_root": str(root), "retired_epoch_id": "old",
             "new_epoch_id": "new", "source_revision": "a" * 40,
             "recovery_receipt_sha256": "b" * 64, "writers_quiesced": True,
             "paper_only": True, "live_disarmed": True, "epoch_retired": True,
             "pending_paper_orders": 0, "open_paper_positions": 0,
             "pending_wal_records": 0, "pending_recovery_records": 0}
    return {"runtime_root": root, "proof": proof, "quiescent": True,
            "recovery_states": {"emergency_wal": "EMPTY", "lifecycle_owner": "RECONCILED",
                                "sync_readers": "NOT_PRESENT"},
            "receipt_path": receipts / "reset.json"}


def put(root, name, data=b"old\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def archive(root, *, digest=None):
    data = b"archive\n"
    base = "research_archive/session_001/"
    path = put(root, base + "payload/000000_lifecycle.jsonl", data)
    meta = {"schema": "research_archive_receipt_v2", "source_inventory": [{
        "path": "v3/ledgers/lifecycle.jsonl", "preserved_path": "payload/000000_lifecycle.jsonl",
        "preserved_bytes": len(data), "preserved_sha256": digest or hashlib.sha256(data).hexdigest()}]}
    metadata = put(root, base + "archive_meta.json", json.dumps(meta).encode())
    return path, metadata


def test_success_preserves_retained_and_emits_metadata_receipts(tmp_path):
    args = settings(tmp_path)
    root = args["runtime_root"]
    live = put(root, "signal_replay.jsonl")
    payload, metadata = archive(root)
    essential = put(root, "open_positions.json", b"[]")
    result = execution.execute_research_reset(**args)
    assert result["status"] == "COMPLETE"
    assert not live.exists() and not payload.exists()
    assert essential.exists() and metadata.exists()
    assert {row["absolute_path"] for row in result["retained"]} == {str(metadata), str(essential)}
    receipt = json.loads(args["receipt_path"].read_text())
    assert len(receipt["expected_sha256_by_path"]) == 2
    assert receipt["context"]["plan_sha256"] == result["plan_sha256"]
    assert receipt["context"]["proof_sha256"] == result["proof_sha256"]
    assert {row["absolute_path"] for row in receipt["context"]["retained"]} == {str(metadata), str(essential)}
    assert Path(receipt["progress_journal"]).exists()
    assert result["physical_bytes_reclaimed"] is None
    assert result["raw_payload_copies_created"] is False


def test_archive_hash_mismatch_aborts_whole_batch_before_unlink(tmp_path):
    args = settings(tmp_path)
    live = put(args["runtime_root"], "signal_replay.jsonl")
    payload, _ = archive(args["runtime_root"], digest="c" * 64)
    with pytest.raises(ResearchDeletionRejected, match="ARCHIVE_SHA256_MISMATCH"):
        execution.execute_research_reset(**args)
    assert live.exists() and payload.exists()
    assert not args["receipt_path"].exists()


def test_changed_bytes_between_executor_and_deleter_fail_frozen_expected_hash(tmp_path, monkeypatch):
    args = settings(tmp_path)
    live = put(args["runtime_root"], "signal_replay.jsonl")
    payload, _ = archive(args["runtime_root"])
    original = execution.delete_exact_research_files
    def changed(**kwargs):
        payload.write_bytes(b"changed\n")
        return original(**kwargs)
    monkeypatch.setattr(execution, "delete_exact_research_files", changed)
    with pytest.raises(ResearchDeletionRejected, match="EXPECTED_SHA256_MISMATCH"):
        execution.execute_research_reset(**args)
    assert live.exists() and payload.exists()
    assert not args["receipt_path"].exists()


@pytest.mark.parametrize("change", [{"pending_wal_records": 1}, {"writers_quiesced": False},
                                   {"new_epoch_id": "old"}, {"live_disarmed": False}])
def test_invalid_boundary_proof_never_deletes(tmp_path, change):
    args = settings(tmp_path)
    path = put(args["runtime_root"], "signal_replay.jsonl")
    args["proof"].update(change)
    with pytest.raises(ResearchDeletionRejected, match="BOUNDARY_PROOF_INVALID"):
        execution.execute_research_reset(**args)
    assert path.exists()


@pytest.mark.parametrize("change", [{"quiescent": "true"}, {"recovery_states": {"wal": "PREPARED"}},
                                   {"recovery_states": {}}, {"recovery_states": {" ": "EMPTY"}}])
def test_invalid_quiescence_or_recovery_never_deletes(tmp_path, change):
    args = settings(tmp_path)
    path = put(args["runtime_root"], "signal_replay.jsonl")
    args.update(change)
    with pytest.raises(ResearchDeletionRejected): execution.execute_research_reset(**args)
    assert path.exists()


def test_hardlinked_protected_alias_survives_without_physical_reclaim_claim(tmp_path):
    args = settings(tmp_path)
    target = put(args["runtime_root"], "signal_replay.jsonl")
    protected = args["runtime_root"] / "open_positions.json"
    os.link(target, protected)
    result = execution.execute_research_reset(**args)
    assert not target.exists()
    assert protected.read_bytes() == b"old\n"
    assert result["hardlinked_target_count"] == 1
    assert result["physical_bytes_reclaimed"] is None


def test_incomplete_scan_and_stale_plan_block_before_delete(tmp_path):
    args = settings(tmp_path)
    target = put(args["runtime_root"], "signal_replay.jsonl")
    with pytest.raises(ResearchDeletionRejected, match="INCOMPLETE"):
        execution.execute_research_reset(**args, max_entries=1)
    with pytest.raises(ResearchDeletionRejected, match="PLAN_CHANGED"):
        execution.execute_research_reset(**args, expected_plan_sha256="f" * 64)
    assert target.exists()


def test_crash_receipt_already_retains_boundary_identity_and_retained_paths(tmp_path, monkeypatch):
    args = settings(tmp_path)
    target = put(args["runtime_root"], "signal_replay.jsonl")
    essential = put(args["runtime_root"], "open_positions.json")
    original = Path.unlink
    def crash(path, *a, **kw):
        original(path, *a, **kw)
        raise SystemExit("crash after first unlink")
    monkeypatch.setattr(Path, "unlink", crash)
    with pytest.raises(SystemExit): execution.execute_research_reset(**args)
    receipt = json.loads(args["receipt_path"].read_text())
    assert receipt["status"] == "PREPARED"
    assert len(receipt["context"]["proof_sha256"]) == 64
    assert len(receipt["context"]["plan_sha256"]) == 64
    assert receipt["context"]["retained"][0]["absolute_path"] == str(essential)
    assert essential.exists() and not target.exists()
    from research_exact_deletion import reconcile_research_deletion
    assert reconcile_research_deletion(args["receipt_path"])["counts"] == {"ABSENT_AFTER_INTENT": 1}
    receipt["context"]["retained"] = []
    args["receipt_path"].write_text(json.dumps(receipt))
    with pytest.raises(ResearchDeletionRejected, match="JOURNAL_SEED_MISMATCH"):
        reconcile_research_deletion(args["receipt_path"])
