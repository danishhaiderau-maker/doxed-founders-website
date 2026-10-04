"""Process-wide reset fence for research writers that run outside the bot process.

The reset's research barriers are in-process locks. Sidecars started by
``fly-entrypoint.sh`` (for example ``indicator_engine.py``) append to
reset-managed files from their own processes and cannot see those locks. The
reset holds this ``flock`` exclusively for its whole quiesced section; a
sidecar appends only while holding it shared and only when no reset pointer is
active. Without ``fcntl`` (Windows lab runs, where no sidecars are launched)
the exclusive side is in-process only and the sidecar side still refuses
while a reset pointer is active.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None

FENCE_FILE_NAME = ".research_reset_writer_fence.lock"
LOCK_SH = getattr(fcntl, "LOCK_SH", 1)
LOCK_EX = getattr(fcntl, "LOCK_EX", 2)
LOCK_NB = getattr(fcntl, "LOCK_NB", 4)
LOCK_UN = getattr(fcntl, "LOCK_UN", 8)


def fence_path(runtime_root) -> Path:
    return Path(os.fspath(runtime_root)) / FENCE_FILE_NAME


def _default_flock():
    return fcntl.flock if fcntl is not None else None


def _open_fence(runtime_root) -> int:
    return os.open(fence_path(runtime_root), os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)


class ExclusiveResetFence:
    """Held by the reset; a held fence excludes every sidecar append."""

    def __init__(self, runtime_root, *, flock=None, sleep=time.sleep, monotonic=time.monotonic):
        self.runtime_root = runtime_root
        self._flock = flock if flock is not None else _default_flock()
        self._sleep, self._monotonic = sleep, monotonic
        self._fd = None
        self.held = False
        self.backend = "FLOCK" if self._flock is not None else "IN_PROCESS_ONLY"

    def acquire(self, timeout_sec: float) -> bool:
        if self.held:
            raise RuntimeError("RESET_WRITER_FENCE_ALREADY_HELD")
        if self._flock is None:
            self.held = True
            return True
        fd = _open_fence(self.runtime_root)
        deadline = self._monotonic() + max(0.0, float(timeout_sec))
        while True:
            try:
                self._flock(fd, LOCK_EX | LOCK_NB)
            except BlockingIOError:
                if self._monotonic() >= deadline:
                    os.close(fd)
                    return False
                self._sleep(0.05)
                continue
            except BaseException:
                os.close(fd)
                raise
            self._fd, self.held = fd, True
            return True

    def release(self) -> None:
        fd, self._fd, self.held = self._fd, None, False
        if fd is not None:
            try:
                self._flock(fd, LOCK_UN)
            finally:
                os.close(fd)


def _reset_pointer_active(runtime_root) -> bool:
    from research_reset_receipt_state import active_reset_receipt_exists
    return active_reset_receipt_exists(Path(os.fspath(runtime_root)))


@contextlib.contextmanager
def sidecar_append_admitted(runtime_root, *, flock=None, reset_active=None):
    """Yield True only while the fence is held shared and no reset is active.

    Busy fence, active or unreadable reset pointer all yield False: the caller
    must skip the write, never wait for the reset or write around it.
    """
    flock = flock if flock is not None else _default_flock()
    reset_active = reset_active or _reset_pointer_active
    fd = None
    locked = False
    admitted = False
    try:
        if flock is not None:
            fd = _open_fence(runtime_root)
            try:
                flock(fd, LOCK_SH | LOCK_NB)
                locked = True
            except BlockingIOError:
                pass
        if locked or flock is None:
            try:
                admitted = reset_active(runtime_root) is False
            except (OSError, ValueError):
                admitted = False
        yield admitted
    finally:
        if fd is not None:
            try:
                if locked:
                    flock(fd, LOCK_UN)
            finally:
                os.close(fd)
