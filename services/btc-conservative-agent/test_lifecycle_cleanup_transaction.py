import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lifecycle_cleanup_transaction import (
    CleanupRejected, CleanupTransaction, PurgeTransaction, recompute_file,
    sign_attestation, verify_bundle,
)

BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
ENGINE_SOURCE = (Path(__file__).parents[1] / "btc-signal-engine" / "engine.py").read_text(encoding="utf-8")


def _fixture(tmp_path: Path):
    root = tmp_path / "volume"
    bundle_id = "lifecycle-" + "1" * 64
    bundle = root / "v3" / "lifecycle_bundles" / "11" / bundle_id
    bundle.mkdir(parents=True)
    events = bundle / "events.jsonl"
    events.write_text(
        '{"record_id":"a","observed_at":"2026-09-01T00:00:00Z"}\n'
        '{"record_id":"b","observed_at":"2026-09-01T02:10:00Z"}\n',
        encoding="utf-8",
    )
    file_proof = recompute_file(events)
    manifest = {
        "schema": "research_lifecycle_bundle_v1",
        "bundle_id": bundle_id,
        "lifecycle_id": "episode|policy|FIXED",
        "identity": {"collection_epoch_id": "epoch-1"},
        "provenance": {
            "source_revision": "a" * 40,
            "deployed_revision": "a" * 40,
            "tile_config_signature": "b" * 64,
            "config_signature": "c" * 64,
        },
        "files": [{"path": "events.jsonl", "mtime_ns": events.stat().st_mtime_ns, **file_proof}],
    }
    manifest["cleanup_manifest_sha256"] = hashlib.sha256(json.dumps([{
        "path": "events.jsonl", "sha256": file_proof["sha256"],
        "size": file_proof["size"], "mtime_ns": events.stat().st_mtime_ns,
        "row_count": file_proof["row_count"],
        "first_timestamp": file_proof["first_timestamp"],
        "last_timestamp": file_proof["last_timestamp"],
    }], separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    receipt = {
        "bundle_id": bundle_id,
        "lifecycle_id": manifest["lifecycle_id"],
        "manifest_sha256": manifest["cleanup_manifest_sha256"],
        "source_git_rev": "a" * 40,
        "deployed_git_rev": "a" * 40,
        "collection_epoch_id": "epoch-1",
        "tile_registry_signature": "b" * 64,
        "config_signature": "c" * 64,
        "terminal_outcome": "UNKNOWN",
        "terminal_at": "2026-09-01T02:10:00Z",
    }
    identity = {key: receipt[key] for key in (
        "bundle_id", "lifecycle_id", "source_git_rev", "deployed_git_rev",
        "collection_epoch_id", "tile_registry_signature", "terminal_outcome",
        "terminal_at", "manifest_sha256",
    )}
    receipt["immutable_identity_sha256"] = hashlib.sha256(
        json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    receipt["laptop_acknowledgement"] = {
        name: {
            "complete": True, "bundle_id": bundle_id,
            "lifecycle_id": receipt["lifecycle_id"],
            "sha256": hashlib.sha256(name.encode()).hexdigest(),
            "manifest_sha256": receipt["manifest_sha256"],
            "acknowledged_at": "2026-09-01T03:00:00Z",
        } for name in ("canonical", "archive", "index")
    }
    key = b"test-only-attestation-key"
    receipt["laptop_attestation"] = sign_attestation(receipt, key, "laptop-1")
    current = {key: receipt[key] for key in (
        "source_git_rev", "deployed_git_rev", "collection_epoch_id",
        "tile_registry_signature", "config_signature",
    )}
    return root, bundle, receipt, current, {"laptop-1": key}


def test_full_source_parity_and_signed_attestation_pass(tmp_path):
    _, bundle, receipt, current, keys = _fixture(tmp_path)
    proof = verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    assert proof["recomputed_files"][0]["row_count"] == 2
    assert proof["recomputed_files"][0]["last_timestamp"] == "2026-09-01T02:10:00Z"


@pytest.mark.parametrize("kind", ["runtime", "sync", "analyzer", "lifecycle_worker"])
def test_active_reference_fails_closed(tmp_path, kind):
    _, bundle, receipt, current, keys = _fixture(tmp_path)
    with pytest.raises(CleanupRejected, match=f"ACTIVE_{kind.upper()}_REFERENCE"):
        verify_bundle(bundle, receipt, current_identity=current, active_references={kind: ["active"]}, attestation_keys=keys)


def test_forgery_and_identity_drift_fail_closed(tmp_path):
    _, bundle, receipt, current, keys = _fixture(tmp_path)
    receipt["laptop_attestation"]["hmac_sha256"] = "0" * 64
    current["collection_epoch_id"] = "epoch-2"
    with pytest.raises(CleanupRejected) as error:
        verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    assert set(error.value.reasons) >= {"LAPTOP_ATTESTATION_INVALID", "CURRENT_COLLECTION_EPOCH_ID_MISMATCH"}


def test_undeclared_file_and_row_timestamp_mismatch_fail_closed(tmp_path):
    _, bundle, receipt, current, keys = _fixture(tmp_path)
    (bundle / "undeclared.jsonl").write_text("{}\n", encoding="utf-8")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["row_count"] = 3
    manifest["files"][0]["last_timestamp"] = "2026-09-01T03:00:00Z"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CleanupRejected) as error:
        verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    assert set(error.value.reasons) >= {
        "SOURCE_MANIFEST_INCOMPLETE", "SOURCE_ROW_COUNT_MISMATCH", "SOURCE_TIMESTAMP_RANGE_MISMATCH",
    }


def test_disabled_transaction_never_moves_source(tmp_path):
    root, bundle, receipt, current, keys = _fixture(tmp_path)
    proof = verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    result = CleanupTransaction(root).execute(bundle, receipt, proof)
    assert result == {"status": "DISABLED_SOURCE_RETAINED", "source_cleanup_authorized": False}
    assert bundle.is_dir()


def test_prepared_interruption_resumes_idempotently_without_deletion(tmp_path):
    root, bundle, receipt, current, keys = _fixture(tmp_path)
    proof = verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    transaction = CleanupTransaction(root, enabled=True)
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_PREPARED"):
        transaction.execute(bundle, receipt, proof, failpoint="AFTER_PREPARED")
    assert bundle.is_dir()
    assert transaction.reconcile() == [{"bundle_id": receipt["bundle_id"], "status": "PREPARED_AWAITING_REVALIDATION"}]
    result = transaction.execute(bundle, receipt, proof, revalidate=lambda: proof)
    assert result["status"] == "COMMITTED_QUARANTINED"
    assert transaction.reconcile() == []
    assert not bundle.exists()
    assert (transaction.quarantine_root / receipt["bundle_id"] / "events.jsonl").is_file()


def test_quarantine_interruption_and_conflicting_paths_recover_or_reject(tmp_path):
    root, bundle, receipt, current, keys = _fixture(tmp_path)
    proof = verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    transaction = CleanupTransaction(root, enabled=True)
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_QUARANTINE"):
        transaction.execute(bundle, receipt, proof, revalidate=lambda: proof, failpoint="AFTER_QUARANTINE")
    assert transaction.reconcile()[0]["status"] == "COMMITTED_QUARANTINED"

    root2, bundle2, receipt2, current2, keys2 = _fixture(tmp_path / "conflict")
    proof2 = verify_bundle(bundle2, receipt2, current_identity=current2, active_references={}, attestation_keys=keys2)
    transaction2 = CleanupTransaction(root2, enabled=True)
    (transaction2.quarantine_root / receipt2["bundle_id"]).mkdir(parents=True)
    with pytest.raises(CleanupRejected, match="QUARANTINE_STATE_CONFLICT"):
        transaction2.execute(bundle2, receipt2, proof2, revalidate=lambda: proof2)


def test_committed_record_binds_complete_verified_identity(tmp_path):
    root, bundle, receipt, current, keys = _fixture(tmp_path)
    proof = verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    transaction = CleanupTransaction(root, enabled=True)
    transaction.execute(bundle, receipt, proof, revalidate=lambda: proof)
    committed = json.loads(next(transaction.tx_root.glob("*/COMMITTED.json")).read_text())
    assert committed["schema"] == "lifecycle_cleanup_transaction_v2"
    assert committed["receipt_sha256"] == hashlib.sha256(
        json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    assert committed["attestation_key_id"] == "laptop-1"
    assert committed["cleanup_manifest_sha256"] == receipt["manifest_sha256"]
    assert committed["immutable_identity_sha256"] == receipt["immutable_identity_sha256"]
    assert committed["lifecycle_id"] == receipt["lifecycle_id"]
    assert committed["committed_at"].endswith("Z")


@pytest.mark.parametrize("defect", ["manifest", "identity", "ack_missing", "ack_mismatch"])
def test_cleanup_verifier_rejects_missing_or_mismatched_proof(defect, tmp_path):
    _, bundle, receipt, current, keys = _fixture(tmp_path)
    if defect == "manifest":
        receipt["manifest_sha256"] = "0" * 64
    elif defect == "identity":
        receipt["immutable_identity_sha256"] = "0" * 64
    elif defect == "ack_missing":
        del receipt["laptop_acknowledgement"]["archive"]["acknowledged_at"]
    else:
        receipt["laptop_acknowledgement"]["index"]["bundle_id"] = "wrong"
    receipt["laptop_attestation"] = sign_attestation(receipt, keys["laptop-1"], "laptop-1")
    with pytest.raises(CleanupRejected):
        verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)


def test_commit_rejects_receipt_tamper_after_verification(tmp_path):
    root, bundle, receipt, current, keys = _fixture(tmp_path)
    proof = verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    receipt["laptop_acknowledgement"]["canonical"]["sha256"] = "f" * 64
    with pytest.raises(CleanupRejected, match="PROOF_RECEIPT_BINDING_MISMATCH"):
        CleanupTransaction(root, enabled=True).execute(
            bundle, receipt, proof, revalidate=lambda: proof,
        )
    assert bundle.is_dir()


def test_containment_rejects_bundle_outside_lifecycle_root(tmp_path):
    root, bundle, receipt, current, keys = _fixture(tmp_path)
    proof = verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    outside = root / "outside" / receipt["bundle_id"]
    outside.mkdir(parents=True)
    with pytest.raises(ValueError):
        CleanupTransaction(root, enabled=True).execute(outside, receipt, proof, revalidate=lambda: proof)


def test_reference_or_source_drift_between_prepare_and_move_fails_closed(tmp_path):
    root, bundle, receipt, current, keys = _fixture(tmp_path)
    proof = verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    transaction = CleanupTransaction(root, enabled=True)

    def active_now():
        return verify_bundle(
            bundle, receipt, current_identity=current,
            active_references={"runtime": ["new-order"]}, attestation_keys=keys,
        )

    with pytest.raises(CleanupRejected, match="ACTIVE_RUNTIME_REFERENCE"):
        transaction.execute(bundle, receipt, proof, revalidate=active_now)
    assert bundle.is_dir()


def test_runtime_route_wires_all_rechecks_and_keeps_default_disabled():
    for source in (BOT_SOURCE, ENGINE_SOURCE):
        assert "@app.route('/api/data-sync/lifecycle-cleanup/prepare', methods=['POST'])" in source
        assert "_data_sync_lifecycle_cleanup_active_references()" in source
        assert 'os.getenv("LIFECYCLE_CLEANUP_ENABLED", "false").lower() == "true"' in source
        assert 'os.getenv("LIFECYCLE_LAPTOP_ATTESTATION_KEY", "")' in source
        assert "_reconcile_lifecycle_cleanup_transactions()" in source
        for kind in ("runtime", "sync", "analyzer", "lifecycle_worker"):
            assert f'"{kind}"' in source


def _committed_quarantine(tmp_path):
    root, bundle, receipt, current, keys = _fixture(tmp_path)
    proof = verify_bundle(bundle, receipt, current_identity=current, active_references={}, attestation_keys=keys)
    cleanup = CleanupTransaction(root, enabled=True)
    cleanup.execute(bundle, receipt, proof, revalidate=lambda: proof)
    committed = next(cleanup.tx_root.glob("*/COMMITTED.json"))
    return (
        root, receipt, current, keys, committed,
        cleanup.quarantine_root / receipt["bundle_id"],
        datetime.now(timezone.utc) + timedelta(days=2),
    )


def test_purge_is_default_disabled_and_retains_quarantine(tmp_path):
    root, receipt, current, keys, committed, quarantine, future = _committed_quarantine(tmp_path)
    result = PurgeTransaction(root).execute_purge(
        committed, receipt, current_identity=current, active_references={},
        attestation_keys=keys, now=future,
    )
    assert result == {"status": "DISABLED_QUARANTINE_RETAINED", "purge_authorized": False}
    assert quarantine.is_dir()


def test_purge_rejects_minimum_age_and_active_reference(tmp_path):
    root, receipt, current, keys, committed, quarantine, _ = _committed_quarantine(tmp_path)
    purge = PurgeTransaction(root, enabled=True, minimum_age_seconds=86400)
    with pytest.raises(CleanupRejected, match="MINIMUM_QUARANTINE_AGE_NOT_MET"):
        purge.execute_purge(
            committed, receipt, current_identity=current, active_references={},
            attestation_keys=keys, now=datetime.now(timezone.utc),
        )
    with pytest.raises(CleanupRejected, match="ACTIVE_RUNTIME_REFERENCE"):
        purge.execute_purge(
            committed, receipt, current_identity=current,
            active_references={"runtime": ["order"]}, attestation_keys=keys,
            now=datetime.now(timezone.utc) + timedelta(days=2),
        )
    assert quarantine.is_dir()


def test_purge_rejects_current_identity_drift(tmp_path):
    root, receipt, current, keys, committed, quarantine, future = _committed_quarantine(tmp_path)
    drifted = dict(current, collection_epoch_id="different-epoch")
    with pytest.raises(CleanupRejected, match="CURRENT_COLLECTION_EPOCH_ID_MISMATCH"):
        PurgeTransaction(root, enabled=True, minimum_age_seconds=0).execute_purge(
            committed, receipt, current_identity=drifted, active_references={},
            attestation_keys=keys, now=future,
        )
    assert quarantine.is_dir()


def test_purge_rejects_receipt_tamper_and_containment_escape(tmp_path):
    root, receipt, current, keys, committed, quarantine, future = _committed_quarantine(tmp_path)
    purge = PurgeTransaction(root, enabled=True, minimum_age_seconds=0)
    tampered = json.loads(json.dumps(receipt))
    tampered["terminal_at"] = "2026-09-01T04:00:00Z"
    with pytest.raises(CleanupRejected, match="COMMITTED_RECEIPT_SHA256_MISMATCH"):
        purge.execute_purge(
            committed, tampered, current_identity=current, active_references={},
            attestation_keys=keys, now=future,
        )
    row = json.loads(committed.read_text())
    row["quarantine"] = "outside/" + receipt["bundle_id"]
    committed.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError):
        purge.execute_purge(
            committed, receipt, current_identity=current, active_references={},
            attestation_keys=keys, now=future,
        )
    assert quarantine.is_dir()


def test_purge_success_receipt_and_idempotent_replay(tmp_path):
    root, receipt, current, keys, committed, quarantine, future = _committed_quarantine(tmp_path)
    samples = iter((1000, 1300))
    purge = PurgeTransaction(root, enabled=True, minimum_age_seconds=0)
    result = purge.execute_purge(
        committed, receipt, current_identity=current, active_references={},
        attestation_keys=keys, now=future, disk_free_bytes=lambda: next(samples),
    )
    assert result["status"] == "PURGED"
    assert result["before_free_bytes"] == 1000
    assert result["after_free_bytes"] == 1300
    assert result["freed_bytes"] == 300
    assert not quarantine.exists()
    replay = purge.execute_purge(
        committed, receipt, current_identity=current, active_references={},
        attestation_keys=keys, now=future,
    )
    assert replay == result


def test_purge_partial_failure_is_recoverable_without_false_receipt(tmp_path):
    root, receipt, current, keys, committed, _, future = _committed_quarantine(tmp_path)
    purge = PurgeTransaction(root, enabled=True, minimum_age_seconds=0)
    calls = []

    def fail_second(path):
        calls.append(path.name)
        if len(calls) == 2:
            raise OSError("injected delete failure")
        path.unlink()

    with pytest.raises(OSError, match="injected delete failure"):
        purge.execute_purge(
            committed, receipt, current_identity=current, active_references={},
            attestation_keys=keys, now=future, disk_free_bytes=lambda: 1000,
            delete_file=fail_second,
        )
    assert not list(purge.purge_tx_root.glob("*/PURGED.json"))
    staging = purge.purge_staging_root / receipt["bundle_id"]
    assert staging.is_dir()
    recovered = purge.execute_purge(
        committed, receipt, current_identity=current, active_references={},
        attestation_keys=keys, now=future, disk_free_bytes=lambda: 1400,
    )
    assert recovered["status"] == "PURGED"
    assert not staging.exists()
