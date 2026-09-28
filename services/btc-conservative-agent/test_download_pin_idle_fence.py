import pytest

from data_sync_bundle_download_pins import DownloadProtection
from data_sync_bundle_worker import _singleton_lease, BundleWorkerError
from test_data_sync_bundle_download_pins import setup, GENERATION, SESSION, FENCE


def snapshot(root):
    return {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.iterdir()}


def test_active_session_defers_without_metadata_mutation(setup):
    root, _, _, owner = setup
    owner.pin(GENERATION, SESSION)
    before = snapshot(root)
    result = owner.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=lambda: set())
    assert result["reason"] == "SESSION_ACTIVE" and result["ready"] is False
    assert snapshot(root) == before
    with owner.read_chunk(GENERATION, SESSION):
        pass


@pytest.mark.parametrize("existing", [False, True])
def test_protection_callback_under_lease_defers_without_writes(setup, existing):
    root, lease, clock, owner = setup
    if existing:
        owner.pin(GENERATION, SESSION, ttl_seconds=1)
        clock[0] += 2
    before = snapshot(root)
    def protected():
        with pytest.raises(BundleWorkerError, match="LEASE_HELD"):
            with _singleton_lease(lease):
                pytest.fail("callback outside worker lease")
        return {GENERATION}
    result = owner.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=protected)
    assert result["reason"] == "GENERATION_PROTECTED" and result["ready"] is False
    assert snapshot(root) == before


def test_active_chunk_excludes_fencing(setup):
    root, _, _, owner = setup
    owner.pin(GENERATION, SESSION)
    before = snapshot(root)
    with owner.read_chunk(GENERATION, SESSION):
        with pytest.raises(BundleWorkerError, match="LEASE_HELD"):
            owner.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=lambda: set())
    assert snapshot(root) == before


def test_idle_fence_is_idempotent_after_restart_without_rewrite(setup):
    root, lease, clock, owner = setup
    owner.pin(GENERATION, SESSION, ttl_seconds=1)
    clock[0] += 2
    result = owner.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=lambda: set())
    assert result["status"] == "FENCED" and result["deletion_authorized"] is False
    before = snapshot(root)
    restarted = DownloadProtection(root, lease, clock=lambda: clock[0])
    assert restarted.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=lambda: set()) == result
    assert snapshot(root) == before
    with pytest.raises(ValueError, match="FENCE_TOKEN_MISMATCH"):
        restarted.fence_if_idle_unprotected(GENERATION, fence_token="e" * 64, protected_generations=lambda: set())
    with pytest.raises(ValueError, match="RETIRING"):
        restarted.pin(GENERATION, SESSION)


@pytest.mark.parametrize("view", [None, {}, {"bad"}, "unavailable"])
def test_invalid_protection_view_fails_without_metadata_mutation(setup, view):
    root, _, _, owner = setup
    with pytest.raises(ValueError, match="PROTECTION_UNAVAILABLE"):
        owner.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=lambda: view)
    assert snapshot(root) == {}


def test_clock_rollback_during_protection_callback_prevents_fence(setup):
    root, _, clock, owner = setup
    owner.pin(GENERATION, SESSION, ttl_seconds=1)
    clock[0] = 102
    before = snapshot(root)
    def protected():
        clock[0] = 100
        return set()
    with pytest.raises(ValueError, match="CLOCK_INVALID_OR_ROLLED_BACK"):
        owner.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=protected)
    assert snapshot(root) == before


def test_lost_response_after_fence_publication_resumes_same_token(setup, monkeypatch):
    root, lease, clock, owner = setup
    save = owner._save
    def publish_then_fail(*args):
        save(*args)
        raise OSError("lost response")
    monkeypatch.setattr(owner, "_save", publish_then_fail)
    with pytest.raises(OSError):
        owner.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=lambda: set())
    before = snapshot(root)
    restarted = DownloadProtection(root, lease, clock=lambda: clock[0])
    result = restarted.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=lambda: set())
    assert result["ready"] is True and snapshot(root) == before


def test_unavailable_callback_cannot_create_fence(setup):
    root, _, _, owner = setup
    def unavailable():
        raise RuntimeError("retention view unavailable")
    with pytest.raises(RuntimeError):
        owner.fence_if_idle_unprotected(GENERATION, fence_token=FENCE, protected_generations=unavailable)
    assert snapshot(root) == {}
