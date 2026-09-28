"""Real long-path I/O; deletion is confined to pytest temporary fixtures."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_exact_deletion import ResearchDeletionRejected, _checked_path, reconcile_research_deletion
from research_reset_execution import execute_research_reset
from research_reset_inventory import plan_research_reset, PROOF_SCHEMA
from research_reset_paths import io_path, logical_path


def fixture(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    base = root / "research_archive" / ("session_" + "a" * 110) / ("batch_" + "b" * 90)
    payload = base / "payload" / "000000_signal_replay.jsonl"
    assert len(str(payload)) > 260
    io_path(payload.parent).mkdir(parents=True)
    data = b'{"research":true}\n'
    io_path(payload).write_bytes(data)
    metadata = base / "archive_meta.json"
    io_path(metadata).write_text(json.dumps({"schema": "research_archive_receipt_v2", "source_inventory": [{
        "path": "signal_replay.jsonl", "preserved_path": "payload/000000_signal_replay.jsonl",
        "preserved_bytes": len(data), "preserved_sha256": hashlib.sha256(data).hexdigest()}]}))
    proof = {"schema": PROOF_SCHEMA, "runtime_root": str(root), "retired_epoch_id": "old",
        "new_epoch_id": "new", "source_revision": "a" * 40, "recovery_receipt_sha256": "b" * 64,
        "writers_quiesced": True, "paper_only": True, "live_disarmed": True, "epoch_retired": True,
        "pending_paper_orders": 0, "open_paper_positions": 0, "pending_wal_records": 0, "pending_recovery_records": 0}
    args = dict(runtime_root=root, proof=proof, quiescent=True,
        recovery_states={"test_owners": "NOT_PRESENT"}, receipt_path=base / "reset_receipt.json")
    return root, payload, metadata, args


def test_real_long_path_inventory_preflight_delete_and_reconcile(tmp_path):
    root, payload, metadata, args = fixture(tmp_path)
    plan = plan_research_reset(root, proof=args["proof"])
    assert plan["complete"] and plan["target_count"] == 1
    assert plan["targets"][0]["absolute_path"] == str(payload)
    before = sorted(str(logical_path(p)) for p in io_path(metadata.parent).iterdir())
    check = execute_research_reset(**args, validate_only=True)
    assert check["status"] == "VALIDATED"
    assert list(check["expected_sha256_by_path"]) == [str(payload)]
    assert io_path(payload).exists() and not io_path(args["receipt_path"]).exists()
    assert before == sorted(str(logical_path(p)) for p in io_path(metadata.parent).iterdir())
    result = execute_research_reset(**args)
    assert result["status"] == "COMPLETE"
    assert not io_path(payload).exists() and io_path(metadata).exists()
    receipt = json.loads(io_path(args["receipt_path"]).read_text())
    assert receipt["inventory"][0]["path"] == str(payload)
    assert receipt["receipt_path"] == str(args["receipt_path"])
    assert "\\\\?\\" not in receipt["root"]
    assert reconcile_research_deletion(args["receipt_path"])["counts"] == {"UNLINKED_CONFIRMED": 1}


def test_long_path_outside_root_still_rejected(tmp_path):
    root, payload, _, args = fixture(tmp_path)
    with pytest.raises(ResearchDeletionRejected, match="PATH_OUTSIDE_EXPLICIT_ROOT"):
        _checked_path(payload, root / "another-root")
    assert io_path(payload).exists()


def test_long_path_symlink_still_blocks_entire_inventory(tmp_path):
    root, payload, _, args = fixture(tmp_path)
    link = payload.with_name("untrusted.jsonl")
    try:
        io_path(link).symlink_to(io_path(payload))
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Host has no symlink privilege")
        raise
    plan = plan_research_reset(root, proof=args["proof"])
    assert not plan["complete"] and plan["target_count"] == 0
    assert "UNSAFE_LINK_PRESENT" in plan["errors"]
    with pytest.raises(ResearchDeletionRejected, match="SYMLINK_OR_REPARSE_POINT"):
        _checked_path(link, root)


def test_long_path_reparse_attribute_rejected_without_host_privilege(tmp_path, monkeypatch):
    root, payload, _, args = fixture(tmp_path)
    original = Path.lstat

    def inspect(path):
        info = original(path)
        if logical_path(path) == payload:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400,
                st_size=info.st_size, st_mtime_ns=info.st_mtime_ns, st_dev=info.st_dev,
                st_ino=info.st_ino, st_nlink=info.st_nlink)
        return info

    monkeypatch.setattr(Path, "lstat", inspect)
    plan = plan_research_reset(root, proof=args["proof"])
    assert not plan["complete"] and not plan["targets"]
    with pytest.raises(ResearchDeletionRejected, match="SYMLINK_OR_REPARSE_POINT"):
        _checked_path(payload, root)
