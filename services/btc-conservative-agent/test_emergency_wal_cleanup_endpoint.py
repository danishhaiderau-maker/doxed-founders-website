import hashlib
import json

import bot
import collector_storage
import research_v3_store
from lifecycle_cleanup_transaction import recompute_file, sign_attestation
from research_v3_store import V3EvidenceStore


def _manifest_hash(rows):
    material = [{
        "path": row["path"], "sha256": row["sha256"], "size": row["size"],
        "mtime_ns": row["mtime_ns"], "row_count": row["row_count"],
        "first_timestamp": row["first_timestamp"], "last_timestamp": row["last_timestamp"],
    } for row in rows]
    return hashlib.sha256(json.dumps(
        material, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()


class _RuntimeLease:
    def __init__(self):
        self.held = False

    def acquire_cleanup_lease(self, timeout=0.0):
        assert timeout == 0.0 and not self.held
        self.held = True
        return True

    def release_cleanup_lease(self):
        assert self.held
        self.held = False

    def status(self):
        return {"running": True, "active": False}


def test_signed_cleanup_endpoint_commits_quarantine_and_releases_exactly_one_extent(
    tmp_path, monkeypatch,
):
    root = tmp_path / "volume"
    identity = {
        "source_revision": "a" * 40, "deployed_revision": "a" * 40,
        "tile_config_signature": "b" * 64,
    }
    current = {
        "source_git_rev": "a" * 40, "deployed_git_rev": "a" * 40,
        "collection_epoch_id": "epoch-1", "tile_registry_signature": "b" * 64,
        "config_signature": "c" * 64,
    }
    monkeypatch.setenv("BOT_DATA_DIR", str(root))
    monkeypatch.setenv("LIFECYCLE_CLEANUP_ENABLED", "true")
    monkeypatch.setenv("LIFECYCLE_LAPTOP_ATTESTATION_KEY", "test-key")
    monkeypatch.setattr(research_v3_store, "_provenance_cache", identity)
    monkeypatch.setattr(collector_storage, "disk_usage_fraction", lambda _path=None: .925)
    monkeypatch.setattr(research_v3_store, "storage_blocks_new_nonessential_research", lambda _path=None: True)
    store = V3EvidenceStore(root, epoch_id="epoch-1")
    row = {
        "record_id": "lifecycle:episode-endpoint:terminal",
        "episode_id": "episode-endpoint", "policy_signature": "policy",
        "research_lane": "FIXED", "terminal": True, "outcome_state": "NO_FILL",
        "observed_at": "2026-09-02T00:00:00Z",
    }
    deferred = store.append("lifecycle", row)
    monkeypatch.setattr(collector_storage, "disk_usage_fraction", lambda _path=None: .50)
    monkeypatch.setattr(research_v3_store, "storage_blocks_new_nonessential_research", lambda _path=None: False)
    assert store.replay_one_emergency_wal_record()["replayed"] is True

    record = store._emergency_wal().oldest_record()
    bundle_id = "lifecycle-" + "1" * 64
    bundle = root / "v3" / "lifecycle_bundles" / "11" / bundle_id
    bundle.mkdir(parents=True)
    events = bundle / "events.jsonl"
    events.write_bytes(record["payload"])
    file_row = {"path": "events.jsonl", "mtime_ns": events.stat().st_mtime_ns,
                **recompute_file(events)}
    manifest_sha = _manifest_hash([file_row])
    lifecycle_id = "episode-endpoint|policy|FIXED"
    manifest = {
        "schema": "research_lifecycle_bundle_v1", "bundle_id": bundle_id,
        "lifecycle_id": lifecycle_id, "identity": {"collection_epoch_id": "epoch-1"},
        "provenance": {
            "source_revision": "a" * 40, "deployed_revision": "a" * 40,
            "tile_config_signature": "b" * 64, "config_signature": "c" * 64,
        },
        "files": [file_row], "cleanup_manifest_sha256": manifest_sha,
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    receipt = {
        "bundle_id": bundle_id, "lifecycle_id": lifecycle_id,
        "bundle_manifest_path": (bundle / "manifest.json").relative_to(root).as_posix(),
        "manifest_sha256": manifest_sha, "source_git_rev": "a" * 40,
        "deployed_git_rev": "a" * 40, "collection_epoch_id": "epoch-1",
        "tile_registry_signature": "b" * 64, "config_signature": "c" * 64,
        "terminal_outcome": "NO_FILL", "terminal_at": "2026-09-02T00:00:00Z",
    }
    identity_material = {key: receipt[key] for key in (
        "bundle_id", "lifecycle_id", "source_git_rev", "deployed_git_rev",
        "collection_epoch_id", "tile_registry_signature", "terminal_outcome",
        "terminal_at", "manifest_sha256",
    )}
    receipt["immutable_identity_sha256"] = hashlib.sha256(json.dumps(
        identity_material, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    receipt["laptop_acknowledgement"] = {name: {
        "complete": True, "bundle_id": bundle_id, "lifecycle_id": lifecycle_id,
        "sha256": hashlib.sha256(name.encode()).hexdigest(),
        "manifest_sha256": manifest_sha, "acknowledged_at": "2026-09-02T00:10:00Z",
    } for name in ("canonical", "archive", "index")}
    receipt["laptop_attestation"] = sign_attestation(receipt, b"test-key", "laptop-1")

    monkeypatch.setattr(bot, "_data_sync_volume_root", lambda: root)
    monkeypatch.setattr(bot, "_data_sync_lifecycle_cleanup_current_identity", lambda: current)
    monkeypatch.setattr(bot, "_data_sync_lifecycle_cleanup_active_references", lambda: {
        "runtime": [], "sync": [], "analyzer": [], "lifecycle_worker": [],
    })
    runtime = _RuntimeLease()
    monkeypatch.setattr(bot, "_LIFECYCLE_PIPELINE_RUNTIME", runtime)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    client = bot.app.test_client()
    refused = client.post(
        "/api/data-sync/lifecycle-cleanup/prepare", json=receipt,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert refused.status_code == 409
    assert refused.get_json()["source_cleanup_authorized"] is False
    assert bundle.is_dir() and runtime.held is False
    bot._data_sync_persist_lifecycle_ack(receipt, {"proof_complete": True})

    response = client.post(
        "/api/data-sync/lifecycle-cleanup/prepare", json=receipt,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["status"] == "COMMITTED_QUARANTINED"
    assert payload["emergency_wal_release"] == {
        "released": True, "generation": deferred["wal_generation"], "slot": 0,
    }
    assert runtime.held is False
    assert store._emergency_wal().status()["free_extents"] == 4
    assert not bundle.exists()
    assert (root / payload["quarantine"]).is_dir()


def test_lifecycle_ack_index_is_o1_self_validating_and_corruption_blocks_recovery(
    tmp_path, monkeypatch,
):
    root = tmp_path / "volume"
    monkeypatch.setattr(bot, "_data_sync_volume_root", lambda: root)
    bundle_id = "lifecycle-" + "2" * 64
    lifecycle_id = "episode|policy|MFE"
    receipt = {"bundle_id": bundle_id, "lifecycle_id": lifecycle_id}
    ack = bot._data_sync_lifecycle_ack_path(bundle_id)
    ack.write_text(json.dumps({"receipt": receipt}) + "\n", encoding="utf-8")
    index = bot._data_sync_persist_lifecycle_ack_index(receipt, ack)
    # A process restart has no in-memory index state; identical publication is
    # recovered solely from the immutable on-disk pointer.
    assert bot._data_sync_persist_lifecycle_ack_index(dict(receipt), ack) == index
    resolved, loaded = bot._data_sync_resolve_lifecycle_ack_index(lifecycle_id)
    assert resolved == ack and loaded == receipt
    corrupted = json.loads(index.read_text(encoding="utf-8"))
    corrupted["ack_sha256"] = "0" * 64
    index.write_text(json.dumps(corrupted), encoding="utf-8")
    try:
        bot._data_sync_resolve_lifecycle_ack_index(lifecycle_id)
        raise AssertionError("corrupt index accepted")
    except ValueError as exc:
        assert "index invalid" in str(exc)
