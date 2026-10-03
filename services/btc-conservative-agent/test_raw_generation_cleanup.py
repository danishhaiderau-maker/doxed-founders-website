import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from raw_generation_cleanup import (
    ACK_SCHEMA, MANIFEST_SCHEMA, RawGenerationCleanupRejected,
    RawGenerationCleanupTransaction, verify_generation,
)


SHA = "a" * 64
REV = "b" * 40


def canonical(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def eligible_now(tx):
    receipt_path = next(tx.tx_root.glob("*/QUARANTINED.json"))
    committed = json.loads(receipt_path.read_text("utf-8"))
    committed_at = datetime.fromisoformat(committed["committed_at"].replace("Z", "+00:00"))
    return committed_at + timedelta(hours=24)


def fixture(tmp_path: Path):
    source = tmp_path / "v3" / "ledgers" / "decision-generation-1"
    source.mkdir(parents=True)
    payload = source / "decision.jsonl.1"
    payload.write_text('{"episode_id":"e1"}\n', encoding="utf-8")
    identity = {"source_revision": REV, "deployed_revision": REV,
                "collection_epoch_id": "epoch-1", "tile_registry_signature": SHA,
                "config_signature": SHA}
    payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    manifest = {"schema": MANIFEST_SCHEMA, "generation_kind": "V3", "generation": 1,
                "generation_id": "V3:decision:1", "ledger": "decision", "identity": identity,
                "members": [{"path": payload.name, "size": payload.stat().st_size,
                             "sha256": payload_sha,
                             "seal": {"schema": "v3_ledger_rotation_seal_v1", "generation": 1,
                                      "ledger": "decision", "relative_path": f"ledgers/{payload.name}",
                                      "sealed_ref": {"schema": "v3_ledger_generation_ref_v1", "state": "SEALED", "ledger": "decision", "generation": 1, "relative_path": f"ledgers/{payload.name}"},
                                      "size": payload.stat().st_size, "sha256": payload_sha},
                             "lifecycle_ids": ["e1|p1|lane"]}],
                "lifecycles": [{"lifecycle_id": "e1|p1|lane", "qualification_ready": True,
                                "terminal": True, "outcome": "NO_FILL"}]}
    manifest["manifest_sha256"] = hashlib.sha256(canonical(manifest)).hexdigest()
    ack = {"schema": ACK_SCHEMA, "immutable": True, "identity": identity}
    for copy in ("canonical", "archive", "index"):
        ack[copy] = {"complete": True, "generation_id": "V3:decision:1",
                     "manifest_sha256": manifest["manifest_sha256"], "sha256": SHA}
    ack["acknowledgement_sha256"] = hashlib.sha256(canonical(ack)).hexdigest()
    return source, manifest, ack, identity


def test_complete_manifest_produces_exact_dry_run_without_mutation(tmp_path):
    source, manifest, ack, identity = fixture(tmp_path)
    proof = verify_generation(source, manifest, ack, current_identity=identity,
                              active_leases={"reader": [], "sync": [], "analyzer": []})
    tx = RawGenerationCleanupTransaction(tmp_path)
    result = tx.quarantine(source, proof, dry_run=True)
    assert result == {"status": "DRY_RUN_SOURCE_RETAINED", "generation_id": "V3:decision:1",
                      "planned_bytes": proof["source_bytes"], "freed_bytes": 0,
                      "source_cleanup_authorized": False}
    assert source.is_dir()


@pytest.mark.parametrize("mutation,reason", [
    (lambda manifest, ack, leases: manifest["lifecycles"][0].update(qualification_ready=False),
     "LIFECYCLE_NOT_QUALIFICATION_READY_OR_EXPLICIT_UNKNOWN"),
    (lambda manifest, ack, leases: leases["analyzer"].append("V3:decision:1"),
     "ACTIVE_GENERATION_ANALYZER_LEASE"),
    (lambda manifest, ack, leases: ack["archive"].update(complete=False),
     "LAPTOP_ARCHIVE_ACK_INVALID"),
])
def test_incomplete_authority_fails_closed(tmp_path, mutation, reason):
    source, manifest, ack, identity = fixture(tmp_path)
    leases = {"reader": [], "sync": [], "analyzer": []}
    mutation(manifest, ack, leases)
    # Rebind the manifest hash when testing semantic eligibility rather than tamper.
    manifest["manifest_sha256"] = hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"})).hexdigest()
    for copy in ("canonical", "archive", "index"):
        ack[copy]["manifest_sha256"] = manifest["manifest_sha256"]
    ack["acknowledgement_sha256"] = hashlib.sha256(canonical({k: v for k, v in ack.items() if k != "acknowledgement_sha256"})).hexdigest()
    with pytest.raises(RawGenerationCleanupRejected) as caught:
        verify_generation(source, manifest, ack, current_identity=identity, active_leases=leases)
    assert reason in caught.value.reasons


def test_explicit_unknown_requires_terminal_horizon_and_reconcile(tmp_path):
    source, manifest, ack, identity = fixture(tmp_path)
    manifest["lifecycles"][0] = {"lifecycle_id": "e1|p1|lane", "qualification_ready": False,
                                 "terminal": True, "outcome": "UNKNOWN",
                                 "horizon_complete": True, "reconciled": True}
    manifest["manifest_sha256"] = hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"})).hexdigest()
    for copy in ("canonical", "archive", "index"):
        ack[copy]["manifest_sha256"] = manifest["manifest_sha256"]
    ack["acknowledgement_sha256"] = hashlib.sha256(canonical({k: v for k, v in ack.items() if k != "acknowledgement_sha256"})).hexdigest()
    assert verify_generation(source, manifest, ack, current_identity=identity,
                             active_leases={"reader": [], "sync": [], "analyzer": []})["lifecycle_count"] == 1


def test_quarantine_restart_reconcile_and_exact_purge_receipt(tmp_path):
    source, manifest, ack, identity = fixture(tmp_path)
    proof = verify_generation(source, manifest, ack, current_identity=identity,
                              active_leases={"reader": [], "sync": [], "analyzer": []})
    tx = RawGenerationCleanupTransaction(tmp_path, enabled=True)
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_MOVE"):
        tx.quarantine(source, proof, dry_run=False, revalidate=lambda: proof, failpoint="AFTER_MOVE")
    assert tx.reconcile() == [{"generation_id": "V3:decision:1", "status": "QUARANTINED_RECOVERED"}]
    now = eligible_now(tx)
    preview = tx.purge("V3:decision:1", dry_run=True, now=now)
    assert preview["planned_freed_bytes"] == proof["source_bytes"] and preview["freed_bytes"] == 0
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_PURGE_ISOLATION"):
        tx.purge("V3:decision:1", dry_run=False, failpoint="AFTER_PURGE_ISOLATION", now=now)
    assert tx.reconcile_purges(now=now) == [
        {"generation_id": "V3:decision:1", "status": "PURGED_RECOVERED",
         "freed_bytes": proof["source_bytes"]},
    ]
    receipt = json.loads(next(tx.tx_root.glob("*/PURGED.json")).read_text("utf-8"))
    assert receipt["state"] == "PURGED" and receipt["freed_bytes"] == proof["source_bytes"]


def test_same_size_substitution_is_rejected_before_purge(tmp_path):
    source, manifest, ack, identity = fixture(tmp_path)
    proof = verify_generation(source, manifest, ack, current_identity=identity,
                              active_leases={"reader": [], "sync": [], "analyzer": []})
    tx = RawGenerationCleanupTransaction(tmp_path, enabled=True)
    tx.quarantine(source, proof, dry_run=False, revalidate=lambda: proof)
    quarantined = next(tx.quarantine_root.glob("*/decision.jsonl.1"))
    original = quarantined.read_bytes()
    quarantined.write_bytes(b"X" * len(original))
    with pytest.raises(RawGenerationCleanupRejected) as caught:
        tx.purge("V3:decision:1", dry_run=False, now=eligible_now(tx))
    assert "QUARANTINE_MEMBER_HASH_OR_SIZE_DRIFT" in caught.value.reasons
    assert quarantined.exists()


def test_symlink_member_is_rejected_lexically(tmp_path, monkeypatch):
    source, manifest, ack, identity = fixture(tmp_path)
    payload = source / "decision.jsonl.1"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: True if self == payload else original(self))
    with pytest.raises(RawGenerationCleanupRejected) as caught:
        verify_generation(source, manifest, ack, current_identity=identity,
                          active_leases={"reader": [], "sync": [], "analyzer": []})
    assert "GENERATION_MEMBER_UNSAFE_OR_MISSING" in caught.value.reasons


def _isolate_purge_for_restart(tmp_path):
    source, manifest, ack, identity = fixture(tmp_path)
    proof = verify_generation(source, manifest, ack, current_identity=identity,
                              active_leases={"reader": [], "sync": [], "analyzer": []})
    tx = RawGenerationCleanupTransaction(tmp_path, enabled=True)
    tx.quarantine(source, proof, dry_run=False, revalidate=lambda: proof)
    now = eligible_now(tx)
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_PURGE_ISOLATION"):
        tx.purge("V3:decision:1", dry_run=False, failpoint="AFTER_PURGE_ISOLATION", now=now)
    staging = next(tx.quarantine_root.glob(".*.purging"))
    return tx, staging, now


def test_restart_recovery_rejects_same_size_substitution(tmp_path):
    tx, staging, now = _isolate_purge_for_restart(tmp_path)
    member = staging / "decision.jsonl.1"
    member.write_bytes(b"Z" * member.stat().st_size)
    with pytest.raises(RawGenerationCleanupRejected) as caught:
        tx.reconcile_purges(now=now)
    assert "QUARANTINE_MEMBER_HASH_OR_SIZE_DRIFT" in caught.value.reasons
    assert staging.is_dir()


def test_restart_recovery_rejects_symlink_member_lexically(tmp_path, monkeypatch):
    tx, staging, now = _isolate_purge_for_restart(tmp_path)
    member = staging / "decision.jsonl.1"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: True if self == member else original(self))
    with pytest.raises(RawGenerationCleanupRejected) as caught:
        tx.reconcile_purges(now=now)
    assert "QUARANTINE_MEMBER_SYMLINK" in caught.value.reasons
    assert staging.is_dir()


def test_immediate_purge_refuses_below_hard_24_hour_floor(tmp_path):
    source, manifest, ack, identity = fixture(tmp_path)
    proof = verify_generation(source, manifest, ack, current_identity=identity,
                              active_leases={"reader": [], "sync": [], "analyzer": []})
    tx = RawGenerationCleanupTransaction(
        tmp_path, enabled=True, minimum_quarantine_age_seconds=0,
    )
    tx.quarantine(source, proof, dry_run=False, revalidate=lambda: proof)
    quarantine = next(tx.quarantine_root.glob("*"))
    receipt = json.loads(next(tx.tx_root.glob("*/QUARANTINED.json")).read_text("utf-8"))
    committed_at = datetime.fromisoformat(receipt["committed_at"])

    with pytest.raises(RawGenerationCleanupRejected) as caught:
        tx.purge(
            "V3:decision:1", dry_run=False,
            now=committed_at + timedelta(hours=23, minutes=59, seconds=59),
        )

    assert caught.value.reasons == ["RAW_GENERATION_MINIMUM_QUARANTINE_AGE_NOT_MET"]
    assert tx.minimum_quarantine_age_seconds == 86_400
    assert quarantine.is_dir()
    assert next(tx.tx_root.glob("*/PURGE_PREPARED.json"), None) is None


@pytest.mark.parametrize("committed_at", [None, "", "not-a-time", "2026-09-14T00:00:00"])
def test_purge_refuses_invalid_or_timezone_naive_quarantine_timestamp(tmp_path, committed_at):
    source, manifest, ack, identity = fixture(tmp_path)
    proof = verify_generation(source, manifest, ack, current_identity=identity,
                              active_leases={"reader": [], "sync": [], "analyzer": []})
    tx = RawGenerationCleanupTransaction(tmp_path, enabled=True)
    tx.quarantine(source, proof, dry_run=False, revalidate=lambda: proof)
    receipt_path = next(tx.tx_root.glob("*/QUARANTINED.json"))
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["committed_at"] = committed_at
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(RawGenerationCleanupRejected) as caught:
        tx.purge("V3:decision:1", dry_run=False,
                 now=datetime(2026, 9, 16, tzinfo=timezone.utc))

    assert caught.value.reasons == ["QUARANTINE_COMMITTED_AT_INVALID"]
    assert next(tx.quarantine_root.glob("*")).is_dir()


def test_purge_refuses_future_quarantine_timestamp(tmp_path):
    source, manifest, ack, identity = fixture(tmp_path)
    proof = verify_generation(source, manifest, ack, current_identity=identity,
                              active_leases={"reader": [], "sync": [], "analyzer": []})
    tx = RawGenerationCleanupTransaction(tmp_path, enabled=True)
    tx.quarantine(source, proof, dry_run=False, revalidate=lambda: proof)
    receipt_path = next(tx.tx_root.glob("*/QUARANTINED.json"))
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["committed_at"] = "2026-09-15T00:00:01Z"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(RawGenerationCleanupRejected) as caught:
        tx.purge("V3:decision:1", dry_run=False,
                 now=datetime(2026, 9, 15, tzinfo=timezone.utc))

    assert caught.value.reasons == ["QUARANTINE_COMMITTED_AT_IN_FUTURE"]
    assert next(tx.quarantine_root.glob("*")).is_dir()


def test_purge_replay_cannot_bypass_minimum_quarantine_age(tmp_path):
    source, manifest, ack, identity = fixture(tmp_path)
    proof = verify_generation(source, manifest, ack, current_identity=identity,
                              active_leases={"reader": [], "sync": [], "analyzer": []})
    tx = RawGenerationCleanupTransaction(tmp_path, enabled=True)
    tx.quarantine(source, proof, dry_run=False, revalidate=lambda: proof)
    transaction, quarantine = tx._paths("V3:decision:1")
    staging = quarantine.with_name(f".{quarantine.name}.purging")
    prepared = {
        "generation_id": "V3:decision:1",
        "quarantine": quarantine.relative_to(tx.root).as_posix(),
        "staging": staging.relative_to(tx.root).as_posix(),
        "exact_freed_bytes": proof["source_bytes"],
        "members": proof["members"],
    }
    (transaction / "PURGE_PREPARED.json").write_text(json.dumps(prepared), encoding="utf-8")
    quarantine.replace(staging)
    committed = json.loads((transaction / "QUARANTINED.json").read_text("utf-8"))
    committed_at = datetime.fromisoformat(committed["committed_at"])

    with pytest.raises(RawGenerationCleanupRejected) as caught:
        tx.reconcile_purges(
            "V3:decision:1", now=committed_at + timedelta(seconds=1),
        )

    assert caught.value.reasons == ["RAW_GENERATION_MINIMUM_QUARANTINE_AGE_NOT_MET"]
    assert staging.is_dir()
    assert not (transaction / "PURGED.json").exists()
