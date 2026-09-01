import hashlib
import json
from pathlib import Path

import pytest

from lifecycle_cleanup_transaction import (
    CleanupRejected, CleanupTransaction, recompute_file, sign_attestation, verify_bundle,
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
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    receipt = {
        "bundle_id": bundle_id,
        "lifecycle_id": manifest["lifecycle_id"],
        "immutable_identity_sha256": "d" * 64,
        "manifest_sha256": "e" * 64,
        "source_git_rev": "a" * 40,
        "deployed_git_rev": "a" * 40,
        "collection_epoch_id": "epoch-1",
        "tile_registry_signature": "b" * 64,
        "config_signature": "c" * 64,
        "terminal_outcome": "UNKNOWN",
        "terminal_at": "2026-09-01T02:10:00Z",
        "laptop_acknowledgement": {
            name: {"sha256": hashlib.sha256(name.encode()).hexdigest()}
            for name in ("canonical", "archive", "index")
        },
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
