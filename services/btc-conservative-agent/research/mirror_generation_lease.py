"""Cross-process lease protecting one complete Fly-mirror generation.

The synchronizer replaces mirror files atomically one at a time.  This lease
adds the missing *generation* boundary: either the sync worker is mutating the
mirror, or one analyzer iteration is reading and publishing from it, never
both.  The operating-system handle is authoritative; the JSON owner metadata
is diagnostic only and a dead process cannot leave the lease locked.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import IO


LEASE_FILE_NAME = ".fly-mirror-generation.lease"


class MirrorGenerationLeaseTimeout(TimeoutError):
    """The mirror generation remained owned beyond the bounded wait."""


def mirror_generation_lease_held(repo_root: str | os.PathLike[str]) -> bool:
    """Probe the authoritative OS lock; a stale metadata pathname is inactive."""
    probe = MirrorGenerationLease(repo_root, owner="nonmutating-lock-probe")
    try:
        handle = probe._open_exclusive()
    except (OSError, PermissionError):
        return True
    handle.close()
    return False


class MirrorGenerationLease:
    def __init__(self, repo_root: str | os.PathLike[str], *, owner: str = "analyzer"):
        self.path = Path(repo_root).resolve() / LEASE_FILE_NAME
        self.owner = str(owner)
        self._handle: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None and not self._handle.closed

    def acquire(self, *, timeout_seconds: float = 1200, poll_seconds: float = 1.0):
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        last_error: BaseException | None = None
        while True:
            try:
                self._handle = self._open_exclusive()
                self._write_owner_metadata()
                return self
            except (OSError, PermissionError) as exc:
                self._handle = None
                last_error = exc
            if time.monotonic() >= deadline:
                raise MirrorGenerationLeaseTimeout(
                    f"MIRROR_GENERATION_LEASE_TIMEOUT owner={self.owner} path={self.path}"
                ) from last_error
            time.sleep(max(0.05, min(float(poll_seconds), deadline - time.monotonic())))

    def _open_exclusive(self) -> IO[bytes]:
        if os.name != "nt":
            import fcntl

            handle = self.path.open("a+b")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                handle.close()
                raise
            return handle

        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        raw = create_file(
            str(self.path),
            0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
            0,                         # no sharing: matches FileShare.None
            None,
            4,                         # OPEN_ALWAYS
            0x00000080,                # FILE_ATTRIBUTE_NORMAL
            None,
        )
        invalid = wintypes.HANDLE(-1).value
        if raw == invalid:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(int(raw), os.O_RDWR | os.O_BINARY)
        except BaseException:
            kernel32.CloseHandle(raw)
            raise
        return os.fdopen(fd, "r+b", closefd=True)

    def _write_owner_metadata(self) -> None:
        assert self._handle is not None
        payload = json.dumps(
            {"schema": "mirror_generation_lease_v1", "owner": self.owner,
             "pid": os.getpid(), "acquired_at": time.time()},
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        self._handle.seek(0)
        self._handle.truncate(0)
        self._handle.write(payload)
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()

    def __enter__(self):
        if not self.held:
            self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.release()
