import json
from pathlib import Path

import pytest

import production_rotation_orchestrator as module
from production_rotation_orchestrator import ProductionRotationOrchestrator
from research_v3_contract import LEDGER_NAMES
from research_v3_store import V3EvidenceStore
from research.mirror_generation_lease import MirrorGenerationLease, mirror_generation_lease_held


REVISION = "a" * 40
EPOCH = "epoch-rotation-test"


def _orchestrator(tmp_path, *, enabled=True, target=1024 * 1024):
    return ProductionRotationOrchestrator(
        tmp_path, source_revision=REVISION, epoch_id=EPOCH,
        enabled=enabled, target_bytes=target,
    )


def _initialize(tmp_path):
    store = V3EvidenceStore(tmp_path, epoch_id=EPOCH)
    for ledger in LEDGER_NAMES:
        store.initialize_ledger_generation_authority(ledger)
    return store


def _sized(path: Path, size: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def test_disabled_and_below_target_are_deterministic_noops(tmp_path):
    _initialize(tmp_path)
    ledger = tmp_path / "v3" / "ledgers" / "decision.jsonl"
    _sized(ledger, 1024 * 1024)
    disabled = _orchestrator(tmp_path, enabled=False).run_caught_up_cycle(caught_up=True, pressure=False)
    assert disabled["status"] == "NOOP_DISABLED"
    enabled = _orchestrator(tmp_path, target=2 * 1024 * 1024).run_caught_up_cycle(caught_up=True, pressure=False)
    assert enabled["status"] == "NOOP_BELOW_TARGET"
    assert enabled["deletion_invoked"] is False


def test_at_most_one_rotation_per_caught_up_cycle(tmp_path):
    _initialize(tmp_path)
    for ledger in ("opportunity", "decision"):
        _sized(tmp_path / "v3" / "ledgers" / f"{ledger}.jsonl", 1024 * 1024)
    receipt = _orchestrator(tmp_path).run_caught_up_cycle(caught_up=True, pressure=False)
    assert receipt["status"] == "ROTATED_ONE"
    assert len(list((tmp_path / "v3" / "ledgers").glob("*.jsonl.1"))) == 1
    assert receipt["source_revision"] == REVISION
    assert receipt["epoch_id"] == EPOCH
    assert len(receipt["tile_config_signature"]) == 64
    assert len(receipt["config"]["config_sha256"]) == 64


def test_backlog_and_overlap_never_rotate(tmp_path):
    _initialize(tmp_path)
    _sized(tmp_path / "v3" / "ledgers" / "decision.jsonl", 1024 * 1024)
    orchestrator = _orchestrator(tmp_path)
    assert orchestrator.run_caught_up_cycle(caught_up=False, pressure=False)["status"] == "NOOP_NOT_CAUGHT_UP"
    assert orchestrator.run_caught_up_cycle(caught_up=True, pressure=False, overlap="SQLITE_SNAPSHOT_BUILDING")["status"] == "NOOP_OVERLAP"
    assert not list((tmp_path / "v3" / "ledgers").glob("*.jsonl.1"))


def test_pressure_hysteresis_uses_half_target_until_quarter_release(tmp_path):
    _initialize(tmp_path)
    target = 4 * 1024 * 1024
    ledger = tmp_path / "v3" / "ledgers" / "decision.jsonl"
    _sized(ledger, target // 2)
    orchestrator = _orchestrator(tmp_path, target=target)
    assert orchestrator.run_caught_up_cycle(caught_up=True, pressure=True)["status"] == "ROTATED_ONE"
    _sized(ledger, target // 2)
    assert orchestrator.run_caught_up_cycle(caught_up=True, pressure=False)["status"] == "ROTATED_ONE"
    assert orchestrator.run_caught_up_cycle(caught_up=True, pressure=False)["status"] == "NOOP_BELOW_TARGET"
    assert orchestrator._pressure_latched is False


def test_recovery_finalizes_one_and_does_not_start_second(tmp_path, monkeypatch):
    store = _initialize(tmp_path)
    ledger = tmp_path / "v3" / "ledgers" / "opportunity.jsonl"
    _sized(ledger, 1024 * 1024)
    with pytest.raises(RuntimeError, match="FAILPOINT_AFTER_RENAME"):
        store.rotate_ledger("opportunity", failpoint="AFTER_RENAME")
    _sized(tmp_path / "v3" / "ledgers" / "decision.jsonl", 1024 * 1024)
    receipt = _orchestrator(tmp_path).run_caught_up_cycle(caught_up=True, pressure=False)
    assert receipt["status"] == "RECOVERED_ONE"
    assert not (tmp_path / "v3" / "ledgers" / "decision.jsonl.1").exists()


def test_legacy_nonempty_ledger_remains_manual(tmp_path):
    ledger = tmp_path / "v3" / "ledgers" / "opportunity.jsonl"
    _sized(ledger, 1024 * 1024)
    receipt = _orchestrator(tmp_path).run_caught_up_cycle(caught_up=True, pressure=False)
    assert receipt["status"] == "NOOP_LEGACY_ADOPTION_REQUIRED"
    assert receipt["reason"] == "MANUAL_GUARDED_LEGACY_ADOPTION_REQUIRED"


def test_released_stale_lease_file_does_not_block_rotation(tmp_path):
    _initialize(tmp_path)
    _sized(tmp_path / "v3" / "ledgers" / "decision.jsonl", 1024 * 1024)
    lease = MirrorGenerationLease(tmp_path, owner="released-test").acquire(timeout_seconds=0)
    lease.release()
    assert (tmp_path / ".fly-mirror-generation.lease").is_file()
    assert mirror_generation_lease_held(tmp_path) is False
    overlap = "ANALYZER_GENERATION_LEASE" if mirror_generation_lease_held(tmp_path) else None
    assert _orchestrator(tmp_path).run_caught_up_cycle(
        caught_up=True, pressure=False, overlap=overlap,
    )["status"] == "ROTATED_ONE"


def test_held_os_lease_blocks_rotation(tmp_path):
    _initialize(tmp_path)
    _sized(tmp_path / "v3" / "ledgers" / "decision.jsonl", 1024 * 1024)
    lease = MirrorGenerationLease(tmp_path, owner="held-test").acquire(timeout_seconds=0)
    try:
        assert mirror_generation_lease_held(tmp_path) is True
        overlap = "ANALYZER_GENERATION_LEASE" if mirror_generation_lease_held(tmp_path) else None
        result = _orchestrator(tmp_path).run_caught_up_cycle(
            caught_up=True, pressure=False, overlap=overlap,
        )
        assert result["status"] == "NOOP_OVERLAP"
    finally:
        lease.release()
