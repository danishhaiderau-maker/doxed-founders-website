import errno
import os
from pathlib import Path
import pytest

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


@pytest.mark.parametrize("phase", ["open", "partial_write", "fsync"])
def test_enospc_terminal_append_falls_back_to_preallocated_wal(tmp_path, monkeypatch, phase):
    _production_identity(monkeypatch)
    _storage_fraction(monkeypatch, .50)
    store = _store(tmp_path, monkeypatch)
    ledger_path = store.ledger_path("lifecycle")
    original_open = Path.open
    original_fsync = os.fsync
    injected = []
    descriptors = set()

    class PartialWriter:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def write(self, payload):
            self.handle.write(payload[:len(payload)//2])
            self.handle.flush()
            injected.append("partial_write")
            raise OSError(errno.ENOSPC, "synthetic partial append")

    def fail_fsync(fd):
        if phase == "fsync" and fd in descriptors and not injected:
            injected.append("fsync")
            raise OSError(errno.ENOSPC, "synthetic fsync failure")
        return original_fsync(fd)

    def fail_canonical_append(path, mode="r", *args, **kwargs):
        if path == ledger_path and mode == "ab":
            if phase == "open":
                injected.append("open")
                raise OSError(errno.ENOSPC, "synthetic full filesystem")
            handle = original_open(path, mode, *args, **kwargs)
            if phase == "partial_write": return PartialWriter(handle)
            descriptors.add(handle.fileno())
            return handle
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_canonical_append)
    monkeypatch.setattr(os, "fsync", fail_fsync)
    result = store.append("lifecycle", {
        "record_id": "lifecycle:episode-2:terminal",
        "episode_id": "episode-2", "terminal": True,
        "outcome_state": "NO_FILL",
    })

    assert injected == [phase]  # Prove the binary append fault actually fired.
    assert result["deferred"] is True
    assert result["reason"] == "MANDATORY_ROW_DURABLY_DEFERRED_TO_PREALLOCATED_WAL"
    assert store._emergency_wal().status()["deferred_count"] == 1
    monkeypatch.setattr(Path, "open", original_open)
    monkeypatch.setattr(os, "fsync", original_fsync)
    if phase == "partial_write":
        # A partial canonical tail stays fail-closed, not silently truncated or
        # duplicated. The full row remains durable in WAL awaiting exact repair.
        before = ledger_path.read_bytes()
        with pytest.raises(RuntimeError, match="CANONICAL_REPLAY_BLOCKED"):
            store.replay_one_emergency_wal_record()
        assert ledger_path.read_bytes() == before
        assert store._emergency_wal().status()["deferred_count"] == 1
        assert store._append_head_path("lifecycle").exists()
        return
    replay = store.replay_one_emergency_wal_record()
    assert replay["replayed"] is True
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1
    again = store.replay_one_emergency_wal_record()
    assert again["canonical_duplicate"] is True
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize("error,terminal", [(errno.EACCES, True), (errno.ENOSPC, False)])
def test_append_error_not_eligible_for_wal_propagates(tmp_path, monkeypatch, error, terminal):
    _production_identity(monkeypatch)
    _storage_fraction(monkeypatch, .50)
    store = _store(tmp_path, monkeypatch)
    path = store.ledger_path("lifecycle")
    original = Path.open
    injected = []
    def failing(p, mode="r", *args, **kwargs):
        if p == path and mode == "ab":
            injected.append(error)
            raise OSError(error, "synthetic error")
        return original(p, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", failing)
    with pytest.raises(OSError) as exc:
        store.append("lifecycle", {"record_id": "test:row", "episode_id": "episode", "terminal": terminal})
    assert exc.value.errno == error and injected == [error]
    assert store._emergency_wal().status()["deferred_count"] == 0


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
