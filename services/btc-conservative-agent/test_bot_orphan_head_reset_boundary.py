"""Exercise the actual reset-boundary AST with real leases and exact temp files."""
from types import SimpleNamespace
import time

import pytest

import lifecycle_orphan_head_repair as repair
from research_v3_contract import canonical_json
from research_v3_store import V3EvidenceStore
from test_bot_destructive_research_reset import runtime


def place(runtime, monkeypatch):
    identity = runtime["old_identity"]
    root = runtime["root"]
    store = V3EvidenceStore.open_read_only(root)
    payload = '{"lost":true}\n'
    row = {"schema": "v3_ledger_append_head_v1", "state": "PREPARED", "ledger": "lifecycle",
           "identity": identity, "offset": 0, "length": len(payload), "row_payload_utf8": payload,
           "row_sha256": repair._sha(payload.encode()), "record_id": "lost-row"}
    row["binding_sha256"] = repair._sha(canonical_json(row).encode())
    raw = canonical_json(row).encode()
    source = store._append_head_path("lifecycle").with_name(repair.TEMP_NAME)
    source.parent.mkdir(parents=True)
    source.write_bytes(raw)
    store.ledger_path("lifecycle").write_bytes(b'{"other":true}\n')
    values = {"OLD_EPOCH": identity["epoch_id"], "OLD_REVISION": identity["source_revision"],
              "CONFIG_SHA256": identity["tile_config_signature"], "SOURCE_SIZE": len(raw),
              "SOURCE_SHA256": repair._sha(raw), "OFFSET": 0, "ROW_LENGTH": len(payload),
              "ROW_SHA256": row["row_sha256"], "SOURCE_INODE": source.stat().st_ino,
              "SOURCE_DEVICE": source.stat().st_dev, "SOURCE_MTIME_NS": source.stat().st_mtime_ns}
    for key, value in values.items(): monkeypatch.setattr(repair, key, value)
    monkeypatch.setattr(repair, "_no_open_fds", lambda _: None)
    monkeypatch.setattr(repair.EmergencyEvidenceWal, "inspect_existing", lambda *a, **kw:
                        {"records": [], "deferred_count": 0, "alarms": []})
    runtime.update(_data_sync_inventory_cache={}, _data_sync_async_inventory={},
                   _data_sync_sqlite_snapshot_states={}, time=SimpleNamespace(time=time.time))
    return source


def test_actual_boundary_preserves_orphan_under_already_held_lease(runtime, monkeypatch):
    source = place(runtime, monkeypatch)
    with repair.MirrorGenerationLease(runtime["root"].parent) as held:
        runtime["_RAW_GENERATION_GATE_LOCAL"].mirror = held
        boundary = runtime["_fresh_research_reset_boundary"](time.time())
        assert held.held  # Helper neither reacquired nor released caller ownership.
    assert not source.exists()
    assert boundary["evidence"]["orphan_preservation"]["state"] == "COMPLETED"
    assert boundary["evidence"]["recovery"]["safe_for_reset_recovery_scope"] is True


@pytest.mark.parametrize("owner", ["inventory", "snapshot", "reader", "lifecycle", "invalid_lease"])
def test_actual_boundary_refuses_unproven_owners(runtime, monkeypatch, owner):
    source = place(runtime, monkeypatch)
    if owner == "inventory": runtime["_data_sync_async_inventory"]["worker_active"] = True
    if owner == "snapshot": runtime["_data_sync_sqlite_snapshot_states"]["db"] = {"status": "BUILDING"}
    if owner == "reader": runtime["_data_sync_sqlite_snapshot_states"]["db"] = {"status": "READY", "active_readers": 1}
    if owner == "lifecycle": runtime["_LIFECYCLE_PIPELINE_RUNTIME"] = object()
    with repair.MirrorGenerationLease(runtime["root"].parent) as held:
        runtime["_RAW_GENERATION_GATE_LOCAL"].mirror = held if owner != "invalid_lease" else object()
        with pytest.raises((ValueError, RuntimeError, AttributeError)):
            runtime["_fresh_research_reset_boundary"](time.time())
    assert source.exists()


def test_unrecognized_temp_still_fails_original_audit(runtime):
    path = runtime["store"]._append_head_path("lifecycle").with_name(".other-unrecognized.tmp")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"retain")
    with pytest.raises(RuntimeError, match="RESET_RECOVERY_NOT_PROVEN_CLEAR"):
        runtime["_fresh_research_reset_boundary"](time.time())
    assert path.read_bytes() == b"retain"


def test_actual_boundary_resumes_source_absent_prepared_receipt(runtime, monkeypatch):
    source = place(runtime, monkeypatch)
    original = repair._write_once
    def crash(path, payload):
        if path.name == "COMPLETED.json": raise SystemExit("after unlink")
        original(path, payload)
    with repair.MirrorGenerationLease(runtime["root"].parent) as held:
        runtime["_RAW_GENERATION_GATE_LOCAL"].mirror = held
        monkeypatch.setattr(repair, "_write_once", crash)
        with pytest.raises(SystemExit): runtime["_fresh_research_reset_boundary"](time.time())
        assert not source.exists()
        monkeypatch.setattr(repair, "_write_once", original)
        result = runtime["_fresh_research_reset_boundary"](time.time())
        assert result["evidence"]["orphan_preservation"]["state"] == "COMPLETED"


def test_terminal_forensic_payload_retained_by_reset_inventory(runtime, monkeypatch):
    from pathlib import Path
    from research_reset_inventory import plan_research_reset
    place(runtime, monkeypatch)
    with repair.MirrorGenerationLease(runtime["root"].parent) as held:
        runtime["_RAW_GENERATION_GATE_LOCAL"].mirror = held
        boundary = runtime["_fresh_research_reset_boundary"](time.time())
        second = runtime["_fresh_research_reset_boundary"](time.time())
    assert second["evidence"]["orphan_preservation"]["status"] == "PRIOR_COMPLETED_FORENSIC_RECEIPT_RETAINED"
    result = plan_research_reset(runtime["root"], proof=boundary["proof"])
    artifact = Path(boundary["evidence"]["orphan_preservation"]["artifact"])
    relative = artifact.relative_to(runtime["root"]).as_posix()
    assert relative not in {row["path"] for row in result["targets"]}
    assert relative in {row["path"] for row in result["retained"]}
