import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from data_sync_bundle_download_pins import DownloadProtection, MAX_SESSIONS, MAX_BYTES
from data_sync_bundle_worker import _singleton_lease

GENERATION = "a" * 64
SESSION = "b" * 64
FENCE = "f" * 64


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "pins"
    output = tmp_path / "transport"
    root.mkdir()
    output.mkdir()
    clock = [100.0]
    lease = output / ".bundle-worker.lease"
    return root, lease, clock, DownloadProtection(root, lease, clock=lambda: clock[0])


def test_reader_before_retirement_blocks_until_release(setup):
    root, lease, clock, owner = setup
    owner.pin(GENERATION, SESSION)
    result = owner.retirement(GENERATION, fence_token=FENCE)
    assert result["ready"] is False and result["active_sessions"] == 1
    assert result["deletion_authorized"] is False
    with pytest.raises(ValueError, match="RETIRING"):
        owner.pin(GENERATION, "c" * 64)
    owner.release(GENERATION, SESSION)
    restarted = DownloadProtection(root, lease, clock=lambda: clock[0])
    assert restarted.retirement(GENERATION, fence_token=result["fence_token"])["ready"] is True
    with pytest.raises(ValueError, match="RETIRING"):
        restarted.pin(GENERATION, SESSION)


def test_fence_before_reader_denies_and_never_expires(setup):
    root, lease, clock, owner = setup
    result = owner.retirement(GENERATION, fence_token=FENCE)
    assert result["ready"] is True
    clock[0] += 100000
    restarted = DownloadProtection(root, lease, clock=lambda: clock[0])
    with pytest.raises(ValueError, match="RETIRING"):
        restarted.pin(GENERATION, SESSION)
    with pytest.raises(ValueError, match="FENCE_TOKEN_MISMATCH"):
        restarted.retirement(GENERATION, fence_token="e" * 64)
    assert restarted.retirement(GENERATION, fence_token=result["fence_token"])["ready"] is True


def test_chunk_resume_renews_durable_session_not_duplicate(setup):
    root, lease, clock, owner = setup
    assert owner.pin(GENERATION, SESSION, ttl_seconds=10)["expires_at"] == 110
    clock[0] = 105
    restarted = DownloadProtection(root, lease, clock=lambda: clock[0])
    assert restarted.pin(GENERATION, SESSION, ttl_seconds=10)["expires_at"] == 115
    assert restarted.retirement(GENERATION, fence_token=FENCE)["active_sessions"] == 1


def test_expired_crashed_reader_is_safe_but_new_reader_stays_fenced(setup):
    _, _, clock, owner = setup
    owner.pin(GENERATION, SESSION, ttl_seconds=10)
    result = owner.retirement(GENERATION, fence_token=FENCE)
    clock[0] = 110
    assert owner.retirement(GENERATION, fence_token=result["fence_token"])["ready"] is True
    with pytest.raises(ValueError, match="RETIRING"):
        owner.pin(GENERATION, SESSION)


@pytest.mark.parametrize("corruption", ["{}", "{bad", '{"schema":1,"schema":2}'])
def test_malformed_state_fails_closed(setup, corruption):
    root, _, _, owner = setup
    (root / (GENERATION + ".json")).write_text(corruption)
    with pytest.raises(ValueError):
        owner.pin(GENERATION, SESSION)
    with pytest.raises(ValueError):
        owner.retirement(GENERATION, fence_token=FENCE)
    assert (root / (GENERATION + ".json")).read_text() == corruption


def test_expired_malformed_pin_does_not_get_pruned_as_safe(setup):
    root, _, clock, owner = setup
    owner.pin(GENERATION, SESSION, ttl_seconds=1)
    path = root / (GENERATION + ".json")
    state = json.loads(path.read_text())
    state["sessions"]["malformed"] = 1
    path.write_text(json.dumps(state))
    clock[0] = 200
    with pytest.raises(ValueError, match="SESSION_INVALID"):
        owner.retirement(GENERATION, fence_token=FENCE)


def test_clock_rollback_and_limits_fail_closed(setup):
    _, _, clock, owner = setup
    owner.pin(GENERATION, SESSION)
    clock[0] = 99
    with pytest.raises(ValueError, match="CLOCK_INVALID"):
        owner.retirement(GENERATION, fence_token=FENCE)
    for ttl in (0, -1, True, float("inf"), 7201):
        with pytest.raises(ValueError, match="PIN_INVALID"):
            owner.pin(GENERATION, SESSION, ttl_seconds=ttl)


def test_capacity_bounded_and_expired_slots_reused(setup):
    _, _, clock, owner = setup
    for value in range(MAX_SESSIONS):
        owner.pin(GENERATION, f"{value:064x}", ttl_seconds=1)
    with pytest.raises(ValueError, match="SESSION_LIMIT"):
        owner.pin(GENERATION, SESSION)
    clock[0] += 1
    owner.pin(GENERATION, SESSION)
    assert owner.retirement(GENERATION, fence_token=FENCE)["active_sessions"] == 1


def test_shared_lease_excludes_another_process(setup):
    root, lease, _, _ = setup
    source = """import sys
from data_sync_bundle_download_pins import DownloadProtection
try:
    DownloadProtection(sys.argv[1], sys.argv[2]).pin('a'*64, 'b'*64)
except Exception as exc:
    print(str(exc))
    raise SystemExit(7)
raise SystemExit(0)
"""
    with _singleton_lease(lease.with_name(".bundle-readers.lease")):
        result = subprocess.run([sys.executable, "-c", source, str(root), str(lease)],
                                cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
    assert result.returncode == 7 and "BUNDLE_WORKER_LEASE_HELD" in result.stdout
    assert list(root.iterdir()) == []


def test_interrupted_publication_never_returns_false_success(setup, monkeypatch):
    import data_sync_bundle_download_pins as module
    root, _, _, owner = setup
    owner.pin(GENERATION, SESSION)
    previous = (root / (GENERATION + ".json")).read_bytes()
    real_replace = module.os.replace
    def fail(*args):
        raise OSError("simulated publication interruption")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        owner.retirement(GENERATION, fence_token=FENCE)
    assert (root / (GENERATION + ".json")).read_bytes() == previous
    monkeypatch.setattr(module.os, "replace", real_replace)
    assert owner.retirement(GENERATION, fence_token=FENCE)["ready"] is False


def test_metadata_must_be_separate_from_derivatives(setup):
    _, lease, _, _ = setup
    with pytest.raises(ValueError, match="METADATA_ROOT_NOT_SEPARATE"):
        DownloadProtection(lease.parent, lease)


def test_chunk_holds_cross_process_retirement_exclusion_even_past_expiry(setup):
    root, lease, clock, owner = setup
    owner.pin(GENERATION, SESSION, ttl_seconds=1)
    source = """import sys
from data_sync_bundle_download_pins import DownloadProtection
try:
    DownloadProtection(sys.argv[1], sys.argv[2]).retirement('a'*64, fence_token='f'*64)
except Exception as exc:
    print(str(exc))
    raise SystemExit(7)
raise SystemExit(0)
"""
    with owner.read_chunk(GENERATION, SESSION):
        clock[0] = 102
        result = subprocess.run([sys.executable, "-c", source, str(root), str(lease)],
                                cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        assert result.returncode == 7 and "BUNDLE_WORKER_LEASE_HELD" in result.stdout
    with pytest.raises(ValueError, match="SESSION_EXPIRED_OR_MISSING"):
        with owner.read_chunk(GENERATION, SESSION):
            pytest.fail("expired chunk admitted")
    assert owner.retirement(GENERATION, fence_token=FENCE)["ready"] is True


def test_fence_denies_chunk_even_for_preexisting_session(setup):
    _, _, _, owner = setup
    owner.pin(GENERATION, SESSION)
    owner.retirement(GENERATION, fence_token=FENCE)
    with pytest.raises(ValueError, match="RETIRING"):
        with owner.read_chunk(GENERATION, SESSION):
            pytest.fail("fenced chunk admitted")


def test_linked_metadata_rejected_without_modifying_target(setup, tmp_path):
    root, _, _, owner = setup
    target = tmp_path / "unrelated.json"
    target.write_text("protected")
    os.link(target, root / (GENERATION + ".json"))
    with pytest.raises(ValueError, match="HARDLINK_FORBIDDEN"):
        owner.pin(GENERATION, SESSION)
    assert target.read_text() == "protected"


def test_oversized_metadata_rejected_before_json_parse(setup):
    root, _, _, owner = setup
    (root / (GENERATION + ".json")).write_bytes(b" " * (MAX_BYTES + 1))
    with pytest.raises(ValueError, match="METADATA_LIMIT"):
        owner.retirement(GENERATION, fence_token=FENCE)


def test_caller_token_resumes_after_committed_fence_response_is_lost(setup, monkeypatch):
    root, lease, clock, owner = setup
    original_save = owner._save
    def save_then_lose_response(*args):
        original_save(*args)
        raise OSError("simulated lost response after durable publication")
    monkeypatch.setattr(owner, "_save", save_then_lose_response)
    with pytest.raises(OSError):
        owner.retirement(GENERATION, fence_token=FENCE)
    restarted = DownloadProtection(root, lease, clock=lambda: clock[0])
    result = restarted.retirement(GENERATION, fence_token=FENCE)
    assert result["ready"] is True and result["fence_token"] == FENCE
    assert restarted.retirement(GENERATION, fence_token=FENCE) == result
    with pytest.raises(ValueError, match="FENCE_TOKEN_MISMATCH"):
        restarted.retirement(GENERATION, fence_token="e" * 64)


def test_missing_or_invalid_caller_token_cannot_mutate(setup):
    root, _, _, owner = setup
    with pytest.raises(TypeError):
        owner.retirement(GENERATION)
    for token in (None, "", "bad", True):
        with pytest.raises(ValueError, match="FENCE_TOKEN_INVALID"):
            owner.retirement(GENERATION, fence_token=token)
    assert list(root.iterdir()) == []
