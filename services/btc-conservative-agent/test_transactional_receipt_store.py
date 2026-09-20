import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

import pytest

import research_v3_store as store_module
from data_sync_inventory_worker import _transactional_receipt_authority_binding
from data_sync_sqlite_snapshot_worker import build_snapshot
from research_reset_recovery_audit import audit_research_reset_recovery
from research_v3_contract import LEDGER_NAMES, canonical_json
from research_v3_store import V3EvidenceStore, _path_signature
from transactional_receipt_store import (
    ACTIVATION_BOUNDARY_SCHEMA,
    AUTHORITY_RELATIVE,
    ReceiptAuthorityError,
    TransactionalReceiptStore,
    audit_transactional_receipt_authority,
    unpublished_authority_state,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_GIT_REV", "a" * 40)
    store_module._provenance_cache = None
    store_module.set_collection_config_signature_provider(lambda: "b" * 64)
    value = V3EvidenceStore(tmp_path / "data", epoch_id="epoch-current")
    yield value
    store_module._provenance_cache = None
    store_module.set_collection_config_signature_provider(None)


def _import_all(store, *, max_records=1):
    for ledger in LEDGER_NAMES:
        for _ in range(100):
            result = store.advance_transactional_receipt_import(
                ledger, max_bytes=8 * 1024 * 1024, max_records=max_records,
            )
            if result["complete"]:
                break
        else:
            raise AssertionError(f"import did not converge for {ledger}")


def _activate(store):
    _import_all(store)
    return store.activate_transactional_receipt_authority(boundary_proof={
        "schema": ACTIVATION_BOUNDARY_SCHEMA,
        "identity": store._identity_binding(),
        "writers_quiesced": True,
        "reset_mutators_quiesced": True,
        "nonce": "test-boundary",
    })


def test_populated_epoch_import_is_resumable_and_switch_preserves_legacy(store):
    first = store.append("decision", {"record_id": "decision-one", "value": 1})
    second = store.append("decision", {"record_id": "decision-two", "value": 2})
    legacy_paths = [
        store._record_receipt_path("decision", first["record_id"]),
        store._record_receipt_path("decision", second["record_id"]),
    ]
    assert all(path.is_file() for path in legacy_paths)

    step = store.advance_transactional_receipt_import(
        "decision", max_bytes=8 * 1024 * 1024, max_records=1,
    )
    assert step["records_imported"] == 1 and not step["complete"]
    step = store.advance_transactional_receipt_import(
        "decision", max_bytes=8 * 1024 * 1024, max_records=1,
    )
    assert step["records_imported"] == 1 and step["complete"]

    marker = _activate(store)
    assert marker["state"] == "ACTIVE"
    assert all(path.is_file() for path in legacy_paths)
    backend = TransactionalReceiptStore.active(store.root, store._identity_binding())
    assert backend is not None
    assert backend.get("decision", "decision-one")["state"] == "COMMITTED"

    new_legacy = store._record_receipt_path("decision", "decision-three")
    result = store.append("decision", {"record_id": "decision-three", "value": 3})
    assert result["written"] is True
    assert not new_legacy.exists()
    assert store.verified_record_receipt("decision", "decision-three")["state"] == "COMMITTED"


def test_append_head_recovery_preserves_prepared_fsync_committed_clear_order(store):
    _activate(store)
    row = {"record_id": "resume-me", "schema": "research_evidence_v3",
           "ledger": "decision", "epoch_id": store.epoch_id,
           **store_module._collection_provenance()}
    payload = (canonical_json(row) + "\n").encode()
    path = store.ledger_path("decision")
    before = _path_signature(path)
    offset = 0 if before is None else before[2]
    with store._exclusive(path):
        store._publish_append_head(
            "decision", "resume-me", offset=offset, payload=payload,
            pre_signature=before,
        )
        store._publish_record_receipt(
            "decision", "resume-me", offset=offset, payload=payload,
            state="PREPARED",
        )
    recovered = store.append("decision", {"record_id": "resume-me"})
    assert recovered["resumed_append_head"] is True
    assert recovered["written"] is True
    assert not store._append_head_path("decision").exists()
    assert store.verified_record_receipt("decision", "resume-me")["state"] == "COMMITTED"


def test_partial_append_and_conflicting_duplicate_fail_closed(store):
    _activate(store)
    material = {"record_id": "partial", "schema": "research_evidence_v3",
                "ledger": "decision", "epoch_id": store.epoch_id,
                **store_module._collection_provenance()}
    payload = (canonical_json(material) + "\n").encode()
    path = store.ledger_path("decision")
    before = _path_signature(path); offset = before[2] if before else 0
    with store._exclusive(path):
        store._publish_append_head(
            "decision", "partial", offset=offset, payload=payload,
            pre_signature=before,
        )
        store._publish_record_receipt(
            "decision", "partial", offset=offset, payload=payload,
            state="PREPARED",
        )
        with path.open("ab") as handle:
            handle.write(payload[:10]); handle.flush(); os.fsync(handle.fileno())
    blocked = store.append("decision", {"record_id": "other"})
    assert blocked["blocked"] is True
    assert blocked["reason"] == "LEDGER_APPEND_HEAD_LEDGER_DIVERGED"
    assert store._append_head_path("decision").exists()


def test_legacy_committed_duplicate_never_rewrites_receipt(store):
    store.append("decision", {"record_id": "stable-id", "value": 1})
    receipt_path = store._record_receipt_path("decision", "stable-id")
    receipt_before = receipt_path.read_bytes()
    ledger_before = store.ledger_path("decision").read_bytes()
    duplicate = store.append("decision", {"record_id": "stable-id", "value": 2})
    assert duplicate["duplicate"] is True
    assert receipt_path.read_bytes() == receipt_before
    assert store.ledger_path("decision").read_bytes() == ledger_before
    assert store.append(
        "decision", {"record_id": "stable-id", "value": 1},
    )["duplicate"] is True


def test_active_committed_duplicate_never_rewrites_database_row(store):
    _activate(store)
    store.append("decision", {"record_id": "stable-id", "value": 1})
    before = store.verified_record_receipt("decision", "stable-id")
    ledger_before = store.ledger_path("decision").read_bytes()
    duplicate = store.append("decision", {"record_id": "stable-id", "value": 2})
    assert duplicate["duplicate"] is True
    assert store.verified_record_receipt("decision", "stable-id") == before
    assert store.ledger_path("decision").read_bytes() == ledger_before


def test_active_prepared_repair_commits_only_verified_durable_bytes(store):
    _activate(store)
    incoming = {"record_id": "prepared-id", "value": 1}
    material = {
        **incoming,
        "schema": "research_evidence_v3",
        "ledger": "decision",
        "epoch_id": store.epoch_id,
        **store_module._collection_provenance(),
    }
    payload = (canonical_json(material) + "\n").encode("utf-8")
    store._publish_record_receipt(
        "decision", "prepared-id", offset=0, payload=payload, state="PREPARED",
    )
    with store.ledger_path("decision").open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    repaired = store.append("decision", incoming)
    assert repaired["duplicate"] is True
    assert repaired["idempotency_receipt_repaired"] is True
    receipt = store.verified_record_receipt("decision", "prepared-id")
    assert receipt["state"] == "COMMITTED"
    assert receipt["row_sha256"] == hashlib.sha256(payload).hexdigest()


def test_active_prepared_transition_rejects_changed_offset(store):
    _activate(store)
    payload = b'{"record_id":"offset-id"}\n'
    store._publish_record_receipt(
        "decision", "offset-id", offset=0, payload=payload, state="PREPARED",
    )
    backend = TransactionalReceiptStore.active(store.root, store._identity_binding())
    prepared = backend.get("decision", "offset-id")
    with pytest.raises(ReceiptAuthorityError, match="DUPLICATE_CONFLICT"):
        backend.put({**prepared, "state": "COMMITTED", "offset": 1})
    assert backend.get("decision", "offset-id") == prepared


def test_marker_boundary_hash_must_match_database_meta(store):
    _activate(store)
    marker_path = store.root / AUTHORITY_RELATIVE / "ACTIVE.json"
    marker = json.loads(marker_path.read_text("utf-8"))
    marker["activation_boundary_sha256"] = "0" * 64
    material = dict(marker)
    material.pop("binding_sha256", None)
    marker["binding_sha256"] = hashlib.sha256(
        canonical_json(material).encode("utf-8")
    ).hexdigest()
    marker_path.write_text(canonical_json(marker) + "\n", encoding="utf-8")
    with pytest.raises(ReceiptAuthorityError, match="MARKER_INVALID"):
        TransactionalReceiptStore.active(store.root, store._identity_binding())


def test_active_database_corruption_never_falls_back_to_legacy(store):
    store.append("decision", {"record_id": "legacy-row"})
    _activate(store)
    database = store.root / AUTHORITY_RELATIVE / "receipts.sqlite3"
    database.write_bytes(b"not sqlite")
    with pytest.raises(ReceiptAuthorityError):
        store._load_record_receipt("decision", "legacy-row")


def test_concurrent_duplicate_writers_have_one_canonical_row(store):
    _activate(store)
    results = []
    errors = []

    def writer():
        try:
            results.append(store.append("decision", {"record_id": "same-record"}))
        except BaseException as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors
    assert sum(result["written"] is True for result in results) == 1
    assert sum(result["duplicate"] is True for result in results) == 3
    assert store.ledger_path("decision").read_bytes().count(b"same-record") == 1


def test_import_detects_source_replacement_and_legacy_receipt_tamper(store):
    store.append("decision", {"record_id": "one"})
    store.append("decision", {"record_id": "two"})
    first = store.advance_transactional_receipt_import(
        "decision", max_bytes=8 * 1024 * 1024, max_records=1,
    )
    assert not first["complete"]
    path = store.ledger_path("decision")
    raw = path.read_bytes()
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(raw)
    os.replace(replacement, path)
    with pytest.raises(ReceiptAuthorityError, match="SOURCE_REPLACED"):
        store.advance_transactional_receipt_import("decision")

    other = V3EvidenceStore(store.root.parent / "tamper", epoch_id=store.epoch_id)
    result = other.append("decision", {"record_id": "tamper"})
    receipt = other._record_receipt_path("decision", result["record_id"])
    body = json.loads(receipt.read_text("utf-8")); body["row_sha256"] = "0" * 64
    receipt.write_text(canonical_json(body) + "\n", encoding="utf-8")
    with pytest.raises(ReceiptAuthorityError, match="RECEIPT_MISMATCH"):
        other.advance_transactional_receipt_import("decision")


def test_import_refuses_same_inode_same_length_change_between_slices(store):
    first = store.append("decision", {"record_id": "one", "value": 1})
    store.append("decision", {"record_id": "two", "value": 2})
    step = store.advance_transactional_receipt_import("decision", max_records=1)
    assert step["complete"] is False
    path = store.ledger_path("decision")
    raw = path.read_bytes()
    changed = raw.replace(b'"value":1', b'"value":9', 1)
    assert len(changed) == len(raw) and changed != raw
    path.write_bytes(changed)
    receipt_path = store._record_receipt_path("decision", first["record_id"])
    receipt = json.loads(receipt_path.read_text("utf-8"))
    first_line = changed.splitlines(keepends=True)[0]
    receipt["row_sha256"] = hashlib.sha256(first_line).hexdigest()
    receipt_path.write_text(canonical_json(receipt) + "\n", encoding="utf-8")
    with pytest.raises(ReceiptAuthorityError, match="SOURCE_CHANGED"):
        store.advance_transactional_receipt_import("decision", max_records=1)


def test_activation_rechecks_exact_legacy_receipt_hashes(store):
    result = store.append("decision", {"record_id": "tamper-after-import"})
    _import_all(store)
    receipt_path = store._record_receipt_path("decision", result["record_id"])
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["extra"] = "changed-after-import"
    receipt_path.write_text(canonical_json(receipt) + "\n", encoding="utf-8")
    with pytest.raises(ReceiptAuthorityError, match="LEGACY_COVERAGE_MISMATCH"):
        store.activate_transactional_receipt_authority(boundary_proof={
            "schema": ACTIVATION_BOUNDARY_SCHEMA,
            "identity": store._identity_binding(),
            "writers_quiesced": True,
            "reset_mutators_quiesced": True,
            "nonce": "tampered-legacy-receipt",
        })


def test_activation_rechecks_every_sealed_generation_signature(store):
    store.append("decision", {"record_id": "sealed-row", "value": 1})
    store.migrate_legacy_ledger_generation("decision")
    store.rotate_ledger("decision")
    store.append("decision", {"record_id": "active-row", "value": 2})
    _import_all(store)
    sealed = store.ledger_dir / "decision.jsonl.1"
    raw = sealed.read_bytes()
    changed = raw.replace(b'"value":1', b'"value":9', 1)
    assert len(changed) == len(raw) and changed != raw
    sealed.write_bytes(changed)
    with pytest.raises(
        ReceiptAuthorityError, match="IMPORT_(?:INCOMPLETE|COUNT_MISMATCH)",
    ):
        store.activate_transactional_receipt_authority(boundary_proof={
            "schema": ACTIVATION_BOUNDARY_SCHEMA,
            "identity": store._identity_binding(),
            "writers_quiesced": True,
            "reset_mutators_quiesced": True,
            "nonce": "tampered-sealed-generation",
        })


def test_import_covers_sealed_and_active_generations_with_one_slice_each(store):
    store.append("decision", {"record_id": "sealed-row"})
    store.migrate_legacy_ledger_generation("decision")
    store.rotate_ledger("decision")
    store.append("decision", {"record_id": "active-row"})
    first = store.advance_transactional_receipt_import(
        "decision", max_records=8,
    )
    assert first["complete"] is False
    assert first["generation_complete"] is True
    assert first["ledger_generation"]["state"] == "SEALED"
    second = store.advance_transactional_receipt_import(
        "decision", max_records=8,
    )
    assert second["complete"] is True
    assert second["all_generations_complete"] is True
    _activate(store)
    assert store.verified_record_receipt("decision", "sealed-row")["state"] == "COMMITTED"
    assert store.verified_record_receipt("decision", "active-row")["state"] == "COMMITTED"


def test_snapshot_inventory_binding_and_reset_audit_use_authority(store, tmp_path):
    store.append("decision", {"record_id": "snapshot-row"})
    marker = _activate(store)
    database = store.root / AUTHORITY_RELATIVE / "receipts.sqlite3"
    request = {"_runtime": store.root, "v3_runtime_identity": store._identity_binding()}
    binding = _transactional_receipt_authority_binding(database, request)
    assert binding["authority_id"] == marker["authority_id"]
    snapshot = tmp_path / "snapshot.sqlite3"
    result = build_snapshot({
        "source_path": str(database), "destination_path": str(snapshot),
        "deadline_seconds": 10, "memory_bytes": 128 * 1024 * 1024,
        "max_output_bytes": 32 * 1024 * 1024,
    })
    assert result["snapshot_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM record_receipts").fetchone()[0] == 1
    authority = audit_transactional_receipt_authority(store.root, store._identity_binding())
    assert authority["active"] is True and authority["receipt_count"] == 1
    recovery = audit_research_reset_recovery(
        store.root, expected_identity=store._identity_binding(),
    )
    assert recovery["transactional_receipt_authority"]["active"] is True
    assert any(
        row["code"] == "TRANSACTIONAL_RECEIPT_AUTHORITY_RETIREMENT_REQUIRED"
        for row in recovery["blockers"]
    )


def test_inventory_binding_rejects_duplicate_marker_keys(store):
    store.append("decision", {"record_id": "snapshot-row"})
    _activate(store)
    marker_path = store.root / AUTHORITY_RELATIVE / "ACTIVE.json"
    marker_path.write_bytes(b'{"schema":"duplicate",' + marker_path.read_bytes()[1:])
    database = store.root / AUTHORITY_RELATIVE / "receipts.sqlite3"
    with pytest.raises(RuntimeError, match="MARKER_INVALID"):
        _transactional_receipt_authority_binding(database, {
            "_runtime": store.root,
            "v3_runtime_identity": store._identity_binding(),
        })


def test_importing_authority_is_not_silently_treated_as_legacy(store):
    store.append("decision", {"record_id": "row"})
    store.advance_transactional_receipt_import("decision", max_records=1)
    status = audit_transactional_receipt_authority(store.root, store._identity_binding())
    assert status == {
        "present": True, "active": False, "state": "IMPORTING_OR_UNPUBLISHED",
        "database_relative": f"{AUTHORITY_RELATIVE}/receipts.sqlite3",
    }
    recovery = audit_research_reset_recovery(
        store.root, expected_identity=store._identity_binding(),
    )
    assert any(row["code"] == "TRANSACTIONAL_RECEIPT_IMPORT_INCOMPLETE"
               for row in recovery["blockers"])


def test_activation_resumes_after_database_commit_before_marker(store, monkeypatch):
    import transactional_receipt_store as authority_module
    store.append("decision", {"record_id": "activation-window"})
    _import_all(store)
    original = authority_module._atomic_json
    calls = {"count": 0}

    def fail_once(path, payload):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("injected marker publication failure")
        return original(path, payload)

    monkeypatch.setattr(authority_module, "_atomic_json", fail_once)
    proof = {
        "schema": ACTIVATION_BOUNDARY_SCHEMA,
        "identity": store._identity_binding(),
        "writers_quiesced": True, "reset_mutators_quiesced": True,
        "nonce": "activation-retry",
    }
    with pytest.raises(OSError, match="injected"):
        store.activate_transactional_receipt_authority(boundary_proof=proof)
    assert not (store.root / AUTHORITY_RELATIVE / "ACTIVE.json").exists()
    fresh = V3EvidenceStore(store.root, epoch_id=store.epoch_id)
    with pytest.raises(ReceiptAuthorityError, match="MARKER_MISSING"):
        fresh.append("decision", {"record_id": "must-not-fallback"})
    foreign_identity = {**store._identity_binding(), "epoch_id": "epoch-different"}
    assert unpublished_authority_state(
        store.root, foreign_identity,
    ) == "IDENTITY_MISMATCH"
    with pytest.raises(ReceiptAuthorityError, match="PROOF_CHANGED"):
        store.activate_transactional_receipt_authority(
            boundary_proof={**proof, "nonce": "different-boundary"},
        )
    marker = store.activate_transactional_receipt_authority(boundary_proof=proof)
    assert marker["state"] == "ACTIVE"
    assert store.verified_record_receipt("decision", "activation-window")


def test_emergency_replay_uses_verified_database_receipt_proof(store):
    _activate(store)
    material = {
        "record_id": "wal-replay", "terminal": True,
        "schema": "research_evidence_v3", "ledger": "lifecycle",
        "epoch_id": store.epoch_id, **store_module._collection_provenance(),
    }
    payload = (canonical_json(material) + "\n").encode()
    wal = store._emergency_wal()
    deferred = wal.defer(
        ledger="lifecycle", record_id="wal-replay", payload=payload,
    )
    replay = store.replay_one_emergency_wal_record()
    assert replay["replayed"] is True
    assert replay["generation"] == deferred["generation"]
    retained = wal.oldest_record()
    assert retained["state"] == "REPLAYED"
    assert store.verified_record_receipt("lifecycle", "wal-replay")
    assert not store._record_receipt_path("lifecycle", "wal-replay").exists()


def test_busy_and_full_errors_are_bounded_and_fail_closed(store, monkeypatch):
    store.append("decision", {"record_id": "existing"})
    _activate(store)
    backend = TransactionalReceiptStore.active(store.root, store._identity_binding())
    receipt = backend.get("decision", "existing")
    locked = sqlite3.connect(str(backend.database_path), isolation_level=None)
    try:
        locked.execute("BEGIN IMMEDIATE")
        with pytest.raises(ReceiptAuthorityError, match="WRITE_FAILED"):
            backend.put(receipt)
    finally:
        locked.execute("ROLLBACK")
        locked.close()

    @contextmanager
    def full_transaction():
        raise sqlite3.OperationalError("database or disk is full")
        yield  # pragma: no cover

    monkeypatch.setattr(backend, "_transaction", full_transaction)
    with pytest.raises(ReceiptAuthorityError, match="WRITE_FAILED"):
        backend.put(receipt)


def test_active_authority_is_consumed_by_real_scan_reconciliation(store, tmp_path, monkeypatch):
    from research import local_scan_reconciliation
    from research_scan_census import ScanCensus
    from research_v3_bridge import write_pre_ai_scan_opportunity

    census = ScanCensus(store, clock=lambda: 1000.0, boot_id="boot")
    scan_id = census.admit()
    census.finish(scan_id, refs=[])
    write_pre_ai_scan_opportunity({
        "research_scan_id": scan_id,
        "signal_ts": 1000.0,
        "symbol": "BTCUSD",
        "feature_snapshot_at_signal": {
            "capture_schema": "measured_feature_capture_v1",
            "captured_at_ts": 999.0,
        },
    }, epoch_id=store.epoch_id, data_dir=str(store.root))
    opportunity = json.loads(store.ledger_path("opportunity").read_bytes())
    source = {
        "epoch": store.epoch_id,
        "revision": opportunity["source_revision"],
    }
    monkeypatch.setattr(local_scan_reconciliation, "_check", lambda *a, **k: source)
    monkeypatch.setattr(local_scan_reconciliation, "_source", lambda value: dict(value))
    args = {
        "repo_root": tmp_path / "repo",
        "data_root": store.root,
        "source_revision": source["revision"],
        "config_signature": opportunity["tile_config_signature"],
    }
    _activate(store)
    result = local_scan_reconciliation.reconcile_scans(**args)
    assert result["index_caught_up"] is True
    assert result["observed_joined_opportunity_rows"] == 1
    assert store.verified_record_receipt(
        "opportunity", opportunity["record_id"],
    )["state"] == "COMMITTED"
