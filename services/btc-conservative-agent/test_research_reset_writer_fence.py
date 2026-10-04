"""Cross-process reset fence: the reset excludes sidecar appends to reset targets."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import research_reset_writer_fence as fence_module
from research_reset_writer_fence import (
    LOCK_EX, LOCK_SH, LOCK_UN, ExclusiveResetFence, fence_path, sidecar_append_admitted,
)


class FakeFlock:
    """flock(2) semantics keyed by the fence file, enough to model two processes."""

    def __init__(self):
        self.shared, self.exclusive = set(), set()

    def __call__(self, fd, op):
        if op == LOCK_UN:
            self.shared.discard(fd)
            self.exclusive.discard(fd)
        elif op & LOCK_EX:
            if self.shared or self.exclusive - {fd}:
                raise BlockingIOError
            self.exclusive.add(fd)
        elif op & LOCK_SH:
            if self.exclusive:
                raise BlockingIOError
            self.shared.add(fd)


def inactive(_root):
    return False


def test_sidecar_admitted_only_without_reset_and_with_free_fence(tmp_path):
    flock = FakeFlock()
    with sidecar_append_admitted(tmp_path, flock=flock, reset_active=inactive) as admitted:
        assert admitted is True
    assert fence_path(tmp_path).exists()


@pytest.mark.parametrize("state", ["active", "unknown", "oserror", "valueerror"])
def test_active_or_unreadable_reset_pointer_refuses_sidecar(tmp_path, state):
    def reset_active(_root):
        if state == "oserror":
            raise OSError
        if state == "valueerror":
            raise ValueError("RESET_RECEIPT_INVALID_JSON")
        return True if state == "active" else None
    with sidecar_append_admitted(tmp_path, flock=FakeFlock(), reset_active=reset_active) as admitted:
        assert admitted is False


def test_held_reset_fence_excludes_sidecar_and_release_readmits(tmp_path):
    flock = FakeFlock()
    fence = ExclusiveResetFence(tmp_path, flock=flock)
    assert fence.acquire(0) is True and fence.held
    with sidecar_append_admitted(tmp_path, flock=flock, reset_active=inactive) as admitted:
        assert admitted is False
    fence.release()
    assert not fence.held
    with sidecar_append_admitted(tmp_path, flock=flock, reset_active=inactive) as admitted:
        assert admitted is True


def test_reset_fence_times_out_while_a_sidecar_append_is_in_flight(tmp_path):
    flock = FakeFlock()
    clock = iter([0.0, 0.0, 0.5, 1.5] + [2.0] * 10)
    fence = ExclusiveResetFence(tmp_path, flock=flock, sleep=lambda _s: None, monotonic=lambda: next(clock))
    with sidecar_append_admitted(tmp_path, flock=flock, reset_active=inactive) as admitted:
        assert admitted is True
        assert fence.acquire(1.0) is False
    assert not fence.held
    assert fence.acquire(0) is True
    fence.release()


def test_reset_fence_cannot_be_double_acquired(tmp_path):
    fence = ExclusiveResetFence(tmp_path, flock=FakeFlock())
    assert fence.acquire(0)
    with pytest.raises(RuntimeError, match="ALREADY_HELD"):
        fence.acquire(0)
    fence.release()


def test_default_reset_pointer_check_reads_active_receipt(tmp_path):
    receipts = tmp_path / "research_reset_receipts"
    reset_id = "0" * 24
    (receipts / reset_id).mkdir(parents=True)
    (receipts / reset_id / "operation.json").write_text(json.dumps({"stage": "FAILED"}))
    (receipts / "ACTIVE_RESET.json").write_text(json.dumps({"reset_id": reset_id}))
    with sidecar_append_admitted(tmp_path, flock=FakeFlock()) as admitted:
        assert admitted is False
    (receipts / reset_id / "operation.json").write_text(json.dumps({"stage": "COMPLETE"}))
    with sidecar_append_admitted(tmp_path, flock=FakeFlock()) as admitted:
        assert admitted is True


@pytest.mark.skipif(fence_module.fcntl is None, reason="real flock needs POSIX")
def test_real_flock_excludes_a_separate_sidecar_process(tmp_path):
    child = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).parent)!r})
        from research_reset_writer_fence import sidecar_append_admitted
        with sidecar_append_admitted({str(tmp_path)!r}, reset_active=lambda _r: False) as admitted:
            print(admitted)
    """)
    fence = ExclusiveResetFence(tmp_path)
    assert fence.acquire(1.0)
    try:
        held = subprocess.check_output([sys.executable, "-c", child], text=True).strip()
    finally:
        fence.release()
    free = subprocess.check_output([sys.executable, "-c", child], text=True).strip()
    assert (held, free) == ("False", "True")


def test_indicator_engine_skips_bar_append_while_reset_pointer_active(tmp_path):
    import indicator_engine
    engine = indicator_engine.Engine(str(tmp_path))
    receipts = tmp_path / "research_reset_receipts"
    reset_id = "1" * 24
    (receipts / reset_id).mkdir(parents=True)
    (receipts / reset_id / "operation.json").write_text(json.dumps({"stage": "FAILED"}))
    (receipts / "ACTIVE_RESET.json").write_text(json.dumps({"reset_id": reset_id}))
    assert engine._append({"bar_ts": 1}) is False
    assert not Path(engine.out_path).exists()
    assert engine.stats["reset_fenced_skips"] == 1 and engine.stats["write_failures"] == 0
    (receipts / reset_id / "operation.json").write_text(json.dumps({"stage": "COMPLETE"}))
    assert engine._append({"bar_ts": 2}) is True
    assert Path(engine.out_path).read_text().count("\n") == 1


def test_indicator_engine_skips_bar_append_while_reset_fence_held(tmp_path, monkeypatch):
    import indicator_engine
    flock = FakeFlock()
    real = fence_module.sidecar_append_admitted
    monkeypatch.setattr(indicator_engine, "sidecar_append_admitted",
                        lambda root: real(root, flock=flock, reset_active=inactive))
    engine = indicator_engine.Engine(str(tmp_path))
    fence = ExclusiveResetFence(tmp_path, flock=flock)
    assert fence.acquire(0)
    try:
        assert engine._append({"bar_ts": 1}) is False
        assert not Path(engine.out_path).exists()
    finally:
        fence.release()
    assert engine._append({"bar_ts": 2}) is True


def test_fence_file_is_never_a_reset_deletion_target(tmp_path):
    from research_reset_inventory import plan_research_reset
    root = tmp_path / "runtime"
    root.mkdir()
    fence_path(root).write_bytes(b"")
    plan = plan_research_reset(str(root), proof=None)
    assert not any(row["path"] == fence_module.FENCE_FILE_NAME and row.get("category")
                   for row in plan["retained"] + plan.get("targets", []))
