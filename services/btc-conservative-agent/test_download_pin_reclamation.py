from pathlib import Path

import pytest

from data_sync_bundle_download_pins import DownloadProtection
from data_sync_bundle_worker import _singleton_lease
from test_data_sync_bundle_download_pins import setup, GENERATION, SESSION, FENCE


def test_capacity_recovered_without_artifact_access(setup):
    root, lease, clock, owner = setup
    for i in range(64):
        owner.pin(f"{i:064x}", SESSION, ttl_seconds=1)
    with pytest.raises(ValueError, match="GENERATION_LIMIT"):
        owner.pin(GENERATION, SESSION)
    artifact = lease.parent / "keep"
    artifact.write_bytes(b"unchanged")
    clock[0] += 2
    result = owner.reclaim_expired_unfenced()
    assert result["removed_count"] == 64
    assert result["source_deletion_authorized"] is False
    owner.pin(GENERATION, SESSION)
    assert artifact.read_bytes() == b"unchanged"


def test_active_fence_and_lease_preserved(setup):
    root, lease, clock, owner = setup
    owner.pin(GENERATION, SESSION)
    other = "c" * 64
    owner.retirement(other, fence_token=FENCE)
    with _singleton_lease(lease):
        with pytest.raises(Exception, match="LEASE_HELD"):
            owner.reclaim_expired_unfenced()
    assert owner.reclaim_expired_unfenced()["removed_count"] == 0
    clock[0] += 1000
    assert owner.reclaim_expired_unfenced()["removed_count"] == 1
    restarted = DownloadProtection(root, lease, clock=lambda: clock[0])
    with pytest.raises(ValueError, match="RETIRING"):
        restarted.pin(other, SESSION)
    with pytest.raises(ValueError, match="SESSION_EXPIRED_OR_MISSING"):
        with restarted.read_chunk(GENERATION, SESSION):
            pytest.fail("expired read admitted")


@pytest.mark.parametrize("kind", ["malformed", "temp", "link"])
def test_invalid_set_preserved_before_any_delete(setup, kind, monkeypatch):
    root, lease, clock, owner = setup
    owner.pin(GENERATION, SESSION, ttl_seconds=1)
    clock[0] += 2
    bad = root / (("c" * 64) + (".tmp" if kind == "temp" else ".json"))
    bad.write_text("{}")
    if kind == "link":
        original = Path.lstat
        class Marked:
            st_file_attributes = 0x400
            def __init__(self, value): self.value = value
            def __getattr__(self, key): return getattr(self.value, key)
        monkeypatch.setattr(Path, "lstat", lambda path: Marked(original(path)) if path == bad else original(path))
    with pytest.raises(ValueError):
        owner.reclaim_expired_unfenced()
    assert (root / (GENERATION + ".json")).exists() and bad.exists()


def test_interrupted_unlink_pass_resumes_after_restart(setup, monkeypatch):
    root, lease, clock, owner = setup
    for gen in (GENERATION, "c" * 64):
        owner.pin(gen, SESSION, ttl_seconds=1)
    clock[0] += 2
    original = Path.unlink
    calls = [0]
    def fail_second(path, *args, **kwargs):
        calls[0] += 1
        if calls[0] == 2:
            raise OSError("interrupted")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(OSError):
        owner.reclaim_expired_unfenced()
    monkeypatch.setattr(Path, "unlink", original)
    restarted = DownloadProtection(root, lease, clock=lambda: clock[0])
    assert restarted.reclaim_expired_unfenced()["removed_count"] == 1
    assert restarted.reclaim_expired_unfenced()["removed_count"] == 0


@pytest.mark.parametrize("times", [(102, 100), (102, 102, 100)])
def test_clock_rollback_refuses_before_first_unlink(setup, times):
    root, lease, clock, owner = setup
    for gen in (GENERATION, "c" * 64):
        owner.pin(gen, SESSION, ttl_seconds=1)
    snapshots = {path.name: path.read_bytes() for path in root.iterdir()}
    ticks = iter(times)
    owner.clock = lambda: next(ticks)
    with pytest.raises(ValueError, match="CLOCK_INVALID_OR_ROLLED_BACK"):
        owner.reclaim_expired_unfenced()
    assert {path.name: path.read_bytes() for path in root.iterdir()} == snapshots
