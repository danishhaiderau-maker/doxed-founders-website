"""Cross-platform process singleton for the dashboard-owning bot.

The lock is held by the open file descriptor for the lifetime of the process.
Metadata in the file is diagnostic only; ownership is decided by the OS lock.
"""

from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import time
from typing import Optional


class ProcessSingletonError(RuntimeError):
    """Raised when another process already owns the singleton."""


class ProcessSingleton:
    def __init__(self, name: str, directory: Optional[Path] = None):
        safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
        root = directory or Path(os.getenv("BOT_SINGLETON_DIR") or Path(__file__).parent)
        self.path = Path(root) / f".{safe_name}.lock"
        self._handle = None
        self.owned = False

    def acquire(self) -> "ProcessSingleton":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                if handle.read(1) == b"":
                    handle.seek(0)
                    handle.write(b" ")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            owner = self._read_owner()
            detail = f" ({owner})" if owner else ""
            raise ProcessSingletonError(
                f"another bot process already owns {self.path}{detail}"
            ) from exc

        self._handle = handle
        self.owned = True
        self._write_owner()
        atexit.register(self.release)
        return self

    def _read_owner(self) -> str:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            if not raw:
                return ""
            data = json.loads(raw)
            return f"pid={data.get('pid')} acquired_at={data.get('acquired_at')}"
        except Exception:
            return ""

    def _write_owner(self) -> None:
        if not self._handle:
            return
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "acquired_at": time.time(),
                "name": self.path.stem,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(payload)
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def release(self) -> None:
        handle = self._handle
        if not handle:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()
            self._handle = None
            self.owned = False


def acquire_process_singleton(
    name: str, directory: Optional[Path] = None
) -> ProcessSingleton:
    return ProcessSingleton(name=name, directory=directory).acquire()
