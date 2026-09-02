import errno
import os
from pathlib import Path

import collector_storage
import research_v3_store
from research_v3_store import V3EvidenceStore


IDENTITY = {
    "source_revision": "a" * 40,
    "deployed_revision": "a" * 40,
    "tile_config_signature": "b" * 64,
}


def _production_identity(monkeypatch):
    monkeypatch.setattr(research_v3_store, "_provenance_cache", dict(IDENTITY))


def _storage_fraction(monkeypatch, value):
    monkeypatch.setattr(collector_storage, "disk_usage_fraction", lambda _path=None: value)
    monkeypatch.setattr(
        research_v3_store, "storage_blocks_new_nonessential_research",
        lambda _path=None: value >= .90,
    )


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    return V3EvidenceStore(tmp_path, epoch_id="epoch-runtime")


def test_low_space_terminal_row_uses_fixed_wal_and_is_idempotent(tmp_path, monkeypatch):
    _production_identity(monkeypatch)
    _storage_fraction(monkeypatch, .925)
    store = _store(tmp_path, monkeypatch)
    row = {
        "record_id": "lifecycle:episode-1:terminal",
        "episode_id": "episode-1",
        "terminal": True,
        "outcome_state": "NO_FILL",
    }

    first = store.append("lifecycle", row)
    second = store.append("lifecycle", row)

    assert first["deferred"] is True and first["written"] is False
    assert first["reason"] == "MANDATORY_ROW_DURABLY_DEFERRED_TO_PREALLOCATED_WAL"
    assert second["duplicate"] is True
    assert second["wal_generation"] == first["wal_generation"]
    assert not store.ledger_path("lifecycle").exists()
    status = store._emergency_wal().status()
    assert status["deferred_count"] == 1
    assert status["free_extents"] == 3
    public = store.emergency_wal_runtime_status()
    assert public["capacity_extents"] == 4
    assert public["free_extents"] == 3
    assert public["retained_count"] == 1
    assert public["state_counts"] == {
        "PREPARED": 0, "DEFERRED": 1, "REPLAYED": 0,
    }
    assert public["oldest_generation"] == first["wal_generation"]
    assert public["identity"] == {
        "epoch_id": "epoch-runtime", **IDENTITY,
    }
    assert "records" not in public
    assert "record_id" not in repr(public)


def test_low_space_optional_row_cannot_consume_wal(tmp_path, monkeypatch):
    _production_identity(monkeypatch)
    _storage_fraction(monkeypatch, .925)
    store = _store(tmp_path, monkeypatch)

    result = store.append("opportunity", {
        "record_id": "opportunity:new", "episode_id": "episode-new",
    })

    assert result["blocked"] is True
    assert store._emergency_wal().status()["deferred_count"] == 0


def test_enospc_terminal_append_falls_back_to_preallocated_wal(tmp_path, monkeypatch):
    _production_identity(monkeypatch)
    _storage_fraction(monkeypatch, .50)
    store = _store(tmp_path, monkeypatch)
    ledger_path = store.ledger_path("lifecycle")
    original_open = Path.open

    def fail_canonical_append(path, mode="r", *args, **kwargs):
        if path == ledger_path and mode == "a":
            raise OSError(errno.ENOSPC, "synthetic full filesystem")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_canonical_append)
    result = store.append("lifecycle", {
        "record_id": "lifecycle:episode-2:terminal",
        "episode_id": "episode-2", "terminal": True,
        "outcome_state": "NO_FILL",
    })

    assert result["deferred"] is True
    assert result["reason"] == "MANDATORY_ROW_DURABLY_DEFERRED_TO_PREALLOCATED_WAL"
    assert store._emergency_wal().status()["deferred_count"] == 1


def test_replay_is_idempotent_across_interruption_and_restart(tmp_path, monkeypatch):
    _production_identity(monkeypatch)
    _storage_fraction(monkeypatch, .925)
    store = _store(tmp_path, monkeypatch)
    deferred = store.append("lifecycle", {
        "record_id": "lifecycle:episode-restart:terminal",
        "episode_id": "episode-restart", "terminal": True,
        "outcome_state": "NO_FILL",
    })
    _storage_fraction(monkeypatch, .50)
    wal = store._emergency_wal()
    original_mark = wal.mark_replayed

    def interrupt(*args, **kwargs):
        raise RuntimeError("synthetic interruption after canonical fsync")

    monkeypatch.setattr(wal, "mark_replayed", interrupt)
    try:
        store.replay_one_emergency_wal_record()
        raise AssertionError("expected interruption")
    except RuntimeError as exc:
        assert "synthetic interruption" in str(exc)
    monkeypatch.setattr(wal, "mark_replayed", original_mark)
    restarted = _store(tmp_path, monkeypatch)
    recovered = restarted.replay_one_emergency_wal_record()
    assert recovered == {
        "replayed": True, "canonical_duplicate": True,
        "generation": deferred["wal_generation"], "state": "REPLAYED",
    }
    rows = restarted.ledger_path("lifecycle").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert restarted._emergency_wal().status()["deferred_count"] == 1


def test_runtime_wal_keeps_source_and_deployed_revision_distinct(tmp_path, monkeypatch):
    provenance = dict(IDENTITY)
    provenance["deployed_revision"] = "c" * 40
    monkeypatch.setattr(research_v3_store, "_provenance_cache", provenance)
    _storage_fraction(monkeypatch, .925)
    store = _store(tmp_path, monkeypatch)
    result = store.append("execution", {
        "record_id": "execution:separate-revisions:terminal",
        "episode_id": "separate-revisions", "status": "CLOSED",
    })
    assert result["deferred"] is True
    assert store._emergency_wal().identity["source_revision"] == "a" * 40
    assert store._emergency_wal().identity["deployed_revision"] == "c" * 40


def test_restart_surfaces_recovered_empty_reserve_incident_without_active_alarm(tmp_path, monkeypatch):
    _production_identity(monkeypatch)
    store = _store(tmp_path, monkeypatch)
    wal = store._emergency_wal()
    with wal.control_path.open("r+b") as handle:
        handle.seek(wal.control_path.stat().st_size // 2)
        handle.write(b"corrupt")
        handle.flush()
        os.fsync(handle.fileno())
    restarted = _store(tmp_path, monkeypatch)
    status = restarted.emergency_wal_runtime_status()
    assert status["retained_count"] == 0 and status["free_extents"] == 4
    assert status["alarms"] == []
    assert "EMERGENCY_WAL_CONTROL_COPY_CORRUPT" in status["incident_alarms"]
