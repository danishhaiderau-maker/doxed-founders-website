import hashlib
import json
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


def fixture(tmp_path: Path):
    source = tmp_path / "v3" / "ledgers" / "decision-generation-1"
    source.mkdir(parents=True)
    payload = source / "decision.jsonl.1"
    payload.write_text('{"episode_id":"e1"}\n', encoding="utf-8")
    identity = {"source_revision": REV, "deployed_revision": REV,
                "collection_epoch_id": "epoch-1", "config_signature": SHA}
    payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    manifest = {"schema": MANIFEST_SCHEMA, "generation_kind": "V3", "generation": 1,
                "generation_id": "V3:1", "identity": identity,
                "members": [{"path": payload.name, "size": payload.stat().st_size,
                             "sha256": payload_sha,
                             "seal": {"schema": "v3_ledger_rotation_seal_v1", "generation": 1,
                                      "relative_path": f"v3/ledgers/{payload.name}",
                                      "size": payload.stat().st_size, "sha256": payload_sha},
                             "lifecycle_ids": ["e1|p1|lane"]}],
                "lifecycles": [{"lifecycle_id": "e1|p1|lane", "qualification_ready": True,
                                "terminal": True, "outcome": "NO_FILL"}]}
    manifest["manifest_sha256"] = hashlib.sha256(canonical(manifest)).hexdigest()
    ack = {"schema": ACK_SCHEMA, "immutable": True, "identity": identity}
    for copy in ("canonical", "archive", "index"):
        ack[copy] = {"complete": True, "generation_id": "V3:1",
                     "manifest_sha256": manifest["manifest_sha256"], "sha256": SHA}
    ack["acknowledgement_sha256"] = hashlib.sha256(canonical(ack)).hexdigest()
    return source, manifest, ack, identity


def test_complete_manifest_produces_exact_dry_run_without_mutation(tmp_path):
    source, manifest, ack, identity = fixture(tmp_path)
    proof = verify_generation(source, manifest, ack, current_identity=identity,
                              active_leases={"reader": [], "sync": [], "analyzer": []})
    tx = RawGenerationCleanupTransaction(tmp_path)
    result = tx.quarantine(source, proof, dry_run=True)
    assert result == {"status": "DRY_RUN_SOURCE_RETAINED", "generation_id": "V3:1",
                      "planned_bytes": proof["source_bytes"], "freed_bytes": 0,
                      "source_cleanup_authorized": False}
    assert source.is_dir()


@pytest.mark.parametrize("mutation,reason", [
    (lambda manifest, ack, leases: manifest["lifecycles"][0].update(qualification_ready=False),
     "LIFECYCLE_NOT_QUALIFICATION_READY_OR_EXPLICIT_UNKNOWN"),
    (lambda manifest, ack, leases: leases["analyzer"].append("V3:1"),
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
    assert tx.reconcile() == [{"generation_id": "V3:1", "status": "QUARANTINED_RECOVERED"}]
    preview = tx.purge("V3:1", dry_run=True)
    assert preview["planned_freed_bytes"] == proof["source_bytes"] and preview["freed_bytes"] == 0
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_PURGE_ISOLATION"):
        tx.purge("V3:1", dry_run=False, failpoint="AFTER_PURGE_ISOLATION")
    assert tx.reconcile_purges() == [{"generation_id": "V3:1", "status": "PURGED_RECOVERED",
                                     "freed_bytes": proof["source_bytes"]}]
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
        tx.purge("V3:1", dry_run=False)
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
