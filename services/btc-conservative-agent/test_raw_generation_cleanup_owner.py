import ast
import hashlib
import json
from pathlib import Path

import pytest

from raw_generation_cleanup import ACK_SCHEMA, MANIFEST_SCHEMA, RawGenerationCleanupRejected
from raw_generation_cleanup_owner import RawGenerationCleanupOwner

SHA = "a" * 64
REV = "b" * 40


def canonical(value): return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def persisted_authority(tmp_path: Path):
    source = tmp_path / "v3" / "ledgers" / "decision.jsonl.1"
    source.parent.mkdir(parents=True); source.write_text('{"episode_id":"e1"}\n', encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    identity = {"source_revision": REV, "deployed_revision": REV,
                "collection_epoch_id": "epoch-1", "tile_registry_signature": SHA,
                "config_signature": SHA}
    manifest = {"schema": MANIFEST_SCHEMA, "generation_kind": "V3", "generation": 1,
                "generation_id": "V3:decision:1", "ledger": "decision", "identity": identity,
                "caught_up_cycle_complete": True,
                "source_relative_path": "v3/ledgers/decision.jsonl.1",
                "members": [{"path": source.name, "size": source.stat().st_size, "sha256": digest,
                             "seal": {"schema": "v3_ledger_rotation_seal_v1", "generation": 1,
                                      "ledger": "decision", "relative_path": "ledgers/decision.jsonl.1",
                                      "sealed_ref": {"schema": "v3_ledger_generation_ref_v1", "state": "SEALED", "ledger": "decision", "generation": 1, "relative_path": "ledgers/decision.jsonl.1"},
                                      "size": source.stat().st_size, "sha256": digest},
                             "lifecycle_ids": ["e1|p1|lane"]}],
                "lifecycles": [{"lifecycle_id": "e1|p1|lane", "qualification_ready": True,
                                "terminal": True, "outcome": "NO_FILL"}]}
    manifest["manifest_sha256"] = hashlib.sha256(canonical(manifest)).hexdigest()
    ack = {"schema": ACK_SCHEMA, "immutable": True, "identity": identity}
    for name in ("canonical", "archive", "index"):
        ack[name] = {"complete": True, "generation_id": "V3:decision:1",
                     "manifest_sha256": manifest["manifest_sha256"], "sha256": SHA}
    ack["acknowledgement_sha256"] = hashlib.sha256(canonical(ack)).hexdigest()
    key = hashlib.sha256(b"V3:decision:1").hexdigest()
    for directory, value in ((tmp_path / "v3" / "raw_generation_manifests", manifest),
                             (tmp_path / "v3" / "raw_generation_laptop_acks", ack)):
        directory.mkdir(parents=True); (directory / f"{key}.json").write_text(json.dumps(value), encoding="utf-8")
    return source, identity


def owner(tmp_path, identity, *, enabled=False, leases=None):
    return RawGenerationCleanupOwner(tmp_path, enabled=enabled, identity=lambda: identity,
                                     leases=lambda _generation: leases or {"reader": [], "sync": [], "analyzer": []})


def test_owner_is_disabled_dry_run_and_retains_source(tmp_path):
    source, identity = persisted_authority(tmp_path)
    result = owner(tmp_path, identity).quarantine("V3:decision:1")
    assert result["status"] == "DRY_RUN_SOURCE_RETAINED" and result["freed_bytes"] == 0
    assert {"free_bytes_before", "free_bytes_after", "free_bytes_delta"} <= set(result)
    assert source.exists()


def test_authority_ingestion_is_verified_and_write_once(tmp_path):
    source, identity = persisted_authority(tmp_path)
    key = hashlib.sha256(b"V3:decision:1").hexdigest()
    manifest_path = tmp_path / "v3" / "raw_generation_manifests" / f"{key}.json"
    ack_path = tmp_path / "v3" / "raw_generation_laptop_acks" / f"{key}.json"
    manifest = json.loads(manifest_path.read_text("utf-8")); ack = json.loads(ack_path.read_text("utf-8"))
    manifest_path.unlink(); ack_path.unlink()
    service = owner(tmp_path, identity)
    result = service.persist_authority(manifest, ack)
    assert result["status"] == "RAW_GENERATION_AUTHORITY_REGISTERED_SOURCE_RETAINED"
    assert source.exists() and manifest_path.exists() and ack_path.exists()
    conflicting = dict(manifest); conflicting["caught_up_cycle_complete"] = False
    with pytest.raises(RawGenerationCleanupRejected):
        service.persist_authority(conflicting, ack)


def test_owner_quarantine_then_explicit_purge_one_generation(tmp_path):
    source, identity = persisted_authority(tmp_path)
    service = owner(tmp_path, identity, enabled=True)
    quarantined = service.quarantine("V3:decision:1", dry_run=False)
    assert quarantined["status"] == "QUARANTINED_SOURCE_RETAINED" and not source.exists()
    purged = service.purge("V3:decision:1", dry_run=False)
    assert purged["state"] == "PURGED" and purged["freed_bytes"] > 0
    assert {"free_bytes_before", "free_bytes_after", "free_bytes_delta"} <= set(purged)


def test_owner_refuses_active_generation_lease(tmp_path):
    source, identity = persisted_authority(tmp_path)
    with pytest.raises(RawGenerationCleanupRejected) as caught:
        owner(tmp_path, identity, enabled=True, leases={"reader": ["V3:decision:1"], "sync": [], "analyzer": []}).quarantine("V3:decision:1", dry_run=False)
    assert "ACTIVE_GENERATION_READER_LEASE" in caught.value.reasons and source.exists()


def test_ledger_qualified_keys_do_not_collide():
    assert RawGenerationCleanupOwner._key("V3:decision:1") != RawGenerationCleanupOwner._key("V3:lifecycle:1")
    with pytest.raises(RawGenerationCleanupRejected):
        RawGenerationCleanupOwner._key("V3:1")


def test_seal_ledger_and_full_path_are_bound(tmp_path):
    source, identity = persisted_authority(tmp_path)
    key = hashlib.sha256(b"V3:decision:1").hexdigest()
    manifest_path = tmp_path / "v3" / "raw_generation_manifests" / f"{key}.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["members"][0]["seal"]["sealed_ref"]["ledger"] = "lifecycle"
    manifest["manifest_sha256"] = hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"})).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RawGenerationCleanupRejected) as caught:
        owner(tmp_path, identity).quarantine("V3:decision:1")
    assert "SEALED_V3_SEAL_PATH_BINDING_INVALID" in caught.value.reasons and source.exists()


def test_process_global_lock_and_cleanup_gate_fail_closed(tmp_path):
    source, identity = persisted_authority(tmp_path)
    first = owner(tmp_path, identity); second = owner(tmp_path, identity)
    assert first.lock is second.lock
    first.lock.acquire()
    try:
        with pytest.raises(RawGenerationCleanupRejected) as caught:
            second.quarantine("V3:decision:1")
        assert "RAW_GENERATION_CLEANUP_BUSY" in caught.value.reasons
    finally:
        first.lock.release()
    gated = RawGenerationCleanupOwner(tmp_path, enabled=True, identity=lambda: identity,
                                      leases=lambda _g: {"reader": [], "sync": [], "analyzer": []},
                                      gate_acquire=lambda: False)
    with pytest.raises(RawGenerationCleanupRejected) as caught:
        gated.quarantine("V3:decision:1", dry_run=False)
    assert "LIFECYCLE_CLEANUP_LEASE_BUSY" in caught.value.reasons and source.exists()


def test_cleanup_gate_spans_initial_proof_revalidation_and_move(tmp_path):
    source, identity = persisted_authority(tmp_path)
    state = {"held": False, "lease_checks": 0}
    def acquire(): state["held"] = True; return True
    def release():
        assert not source.exists()
        state["held"] = False
    def leases(_generation):
        assert state["held"] is True
        state["lease_checks"] += 1
        return {"reader": [], "sync": [], "analyzer": []}
    service = RawGenerationCleanupOwner(tmp_path, enabled=True, identity=lambda: identity,
                                        leases=leases, gate_acquire=acquire, gate_release=release)
    result = service.quarantine("V3:decision:1", dry_run=False)
    assert result["status"] == "QUARANTINED_SOURCE_RETAINED"
    assert state == {"held": False, "lease_checks": 2}


def test_restart_is_audit_only_then_explicit_bounded_replay(tmp_path):
    source, identity = persisted_authority(tmp_path)
    service = owner(tmp_path, identity, enabled=True)
    _source, proof = service._authority("V3:decision:1")
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_MOVE"):
        service.tx.quarantine(source, proof, dry_run=False, revalidate=lambda: proof, failpoint="AFTER_MOVE")
    assert service.audit_recovery() == [{"generation_id": "V3:decision:1", "action": "QUARANTINE",
                                         "status": "EXPLICIT_REPLAY_REQUIRED"}]
    assert service.replay("V3:decision:1", "QUARANTINE") == [
        {"generation_id": "V3:decision:1", "status": "QUARANTINED_RECOVERED"}
    ]
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_PURGE_ISOLATION"):
        service.tx.purge("V3:decision:1", dry_run=False, failpoint="AFTER_PURGE_ISOLATION")
    audit = service.audit_recovery()
    assert audit == [{"generation_id": "V3:decision:1", "action": "PURGE",
                      "status": "EXPLICIT_REPLAY_REQUIRED"}]
    assert service.replay("V3:decision:1", "PURGE")[0]["status"] == "PURGED_RECOVERED"


def test_explicit_replay_revalidates_and_moves_after_prepared_crash(tmp_path):
    source, identity = persisted_authority(tmp_path)
    service = owner(tmp_path, identity, enabled=True)
    _source, proof = service._authority("V3:decision:1")
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_PREPARED"):
        service.tx.quarantine(source, proof, dry_run=False, revalidate=lambda: proof,
                              failpoint="AFTER_PREPARED")
    assert source.exists()
    result = service.replay("V3:decision:1", "QUARANTINE")
    assert result[0]["status"] == "QUARANTINED_SOURCE_RETAINED" and not source.exists()


def test_bot_and_signal_engine_expose_identical_disabled_first_contract():
    root = Path(__file__).resolve().parents[1]
    bot = (root / "btc-conservative-agent" / "bot.py").read_text("utf-8")
    engine = (root / "btc-signal-engine" / "engine.py").read_text("utf-8")
    assert bot == engine
    tree = ast.parse(bot)
    names = {node.name: ast.unparse(node) for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "RAW_GENERATION_CLEANUP_ENABLED" in names["_raw_generation_cleanup_owner"]
    assert "RAW_GENERATION_PURGE_ENABLED" in names["_raw_generation_cleanup_owner"]
    assert "_RAW_GENERATION_PURGE_CONFIRM_PREFIX" in names["api_data_sync_raw_generation_purge"]
    assert "persist_authority" in names["api_data_sync_raw_generation_authority"]
    assert "REPLAY_RAW_GENERATION" in names["api_data_sync_raw_generation_replay"]
    assert "audit_recovery" in names["_audit_raw_generation_cleanup_recovery"]
