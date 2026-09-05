import hashlib
import json
from pathlib import Path

import pytest

import collector_storage
from research_reset_recovery_audit import audit_research_reset_recovery
from research_v3_contract import canonical_json
from research_v3_store import V3EvidenceStore

IDENTITY = {"epoch_id": "epoch-reset-audit", "source_revision": "a" * 40,
            "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64}


def store_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(collector_storage, "disk_usage_fraction", lambda _path=None: .5)
    store = V3EvidenceStore(tmp_path, epoch_id=IDENTITY["epoch_id"])
    store._read_identity_override = dict(IDENTITY)
    store.initialize_ledger_generation_authority("decision")
    store.append("decision", {"record_id": "decision:audit", "episode_id": "test"})
    return store


def audit(root, **kwargs):
    return audit_research_reset_recovery(root, expected_identity=IDENTITY, **kwargs)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_empty_scope_is_read_only_and_complete(tmp_path):
    result = audit(tmp_path)
    assert result["complete"] and result["safe_for_reset_recovery_scope"]
    assert result["pending_or_unknown_count"] == 0
    assert list(tmp_path.iterdir()) == []


def test_actual_writer_committed_rotation(tmp_path, monkeypatch):
    store = store_fixture(tmp_path, monkeypatch)
    store.rotate_ledger("decision")
    before = snapshot(tmp_path)
    result = audit(tmp_path)
    assert result["complete"] and result["safe_for_reset_recovery_scope"], result["blockers"]
    assert result["completed_rotations"] == [{"ledger": "decision", "generation": 1,
        "status": "COMMITTED_JOURNAL_CHAIN_VERIFIED"}]
    assert not result["ledger_payload_integrity_verified"]
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("failpoint", ["AFTER_PREPARED", "AFTER_RENAME", "AFTER_POINTER", "AFTER_CUTOVER"])
def test_actual_writer_unfinished_rotation_blocks_without_recovery(tmp_path, monkeypatch, failpoint):
    store = store_fixture(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="FAILPOINT"):
        store.rotate_ledger("decision", failpoint=failpoint)
    before = snapshot(tmp_path)
    result = audit(tmp_path)
    assert result["complete"] and not result["safe_for_reset_recovery_scope"]
    assert "ROTATION_UNFINISHED_OR_ORPHANED" in {r["code"] for r in result["blockers"]}
    assert snapshot(tmp_path) == before


def test_actual_append_head_pending_even_if_row_is_already_present(tmp_path, monkeypatch):
    store = store_fixture(tmp_path, monkeypatch)
    data = store.ledger_path("decision").read_bytes()
    store._publish_append_head("decision", "decision:audit", offset=0, payload=data, pre_signature=None)
    before = snapshot(tmp_path)
    result = audit(tmp_path)
    assert {r["code"] for r in result["blockers"]} == {"APPEND_HEAD_PENDING"}
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("variant", ["invalid_json", "wrong_identity", "bad_hash", "wrong_commit_seal", "wrong_reference"])
def test_corrupt_or_unbound_commit_blocks(tmp_path, monkeypatch, variant):
    store = store_fixture(tmp_path, monkeypatch)
    store.rotate_ledger("decision")
    path = store._rotation_transaction_path("decision", 1, "COMMITTED")
    row = json.loads(path.read_text())
    if variant == "invalid_json":
        path.write_text("{")
    else:
        if variant == "wrong_identity":
            row["identity"]["epoch_id"] = "epoch-other"
        if variant == "bad_hash":
            row["binding_sha256"] = "c" * 64
        if variant == "wrong_commit_seal":
            row["seal_sha256"] = "d" * 64
        if variant == "wrong_reference":
            row["sealed_ref"]["relative_path"] = "../outside"
        if variant != "bad_hash":
            row.pop("binding_sha256")
            row["binding_sha256"] = hashlib.sha256(canonical_json(row).encode()).hexdigest()
        path.write_text(canonical_json(row))
    before = snapshot(tmp_path)
    result = audit(tmp_path)
    assert result["blockers"] and not result["safe_for_reset_recovery_scope"]
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("path", [
    "v3/receipts/emergency_record_idempotency_v1/append_heads/unknown.json",
    "v3/receipts/ledger_generations_v1/unknown/ACTIVE.json",
    "v3/receipts/ledger_generations_v1/decision/rotations/unknown.json",
    "v3/receipts/ledger_generations_v1/decision/unclassified.json",
])
def test_unknown_paths_not_silently_skipped(tmp_path, path):
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    result = audit(tmp_path)
    assert result["blockers"] and result["retained_paths"]
    assert not result["safe_for_reset_recovery_scope"]


@pytest.mark.parametrize("kwargs", [{"max_entries": 1}, {"max_metadata_bytes": 1}])
def test_caps_never_report_zero_recovery(tmp_path, monkeypatch, kwargs):
    store = store_fixture(tmp_path, monkeypatch)
    store.rotate_ledger("decision")
    result = audit(tmp_path, **kwargs)
    assert not result["complete"] and not result["safe_for_reset_recovery_scope"]
    assert result["pending_or_unknown_count"] is None


def test_unknown_identity_fails_even_for_empty_scope(tmp_path):
    result = audit_research_reset_recovery(tmp_path, expected_identity={})
    assert not result["safe_for_reset_recovery_scope"]
    assert result["blockers"][0]["code"] == "CURRENT_IDENTITY_UNAVAILABLE"


def test_reparse_ancestor_blocks_scan(tmp_path, monkeypatch):
    from types import SimpleNamespace
    target = tmp_path / "v3"
    target.mkdir()
    original = Path.lstat

    def inspect(path):
        info = original(path)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", inspect)
    result = audit(tmp_path)
    assert not result["complete"] and not result["safe_for_reset_recovery_scope"]
    assert "UNSAFE_LINK_OR_REPARSE" in {r["code"] for r in result["blockers"]}
