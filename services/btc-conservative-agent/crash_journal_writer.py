"""Bounded cooperative crash-journal locking; never acquires business locks.

The sibling lock file is permanent: deleting/replacing it defeats coordination.
Legacy writers do not honor this protocol. Repair those only before startup or
after independently proving every writer stopped, not merely trading paused.
"""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import time


def _unlinked_path(path):
    target = Path(path).absolute()
    if target.is_symlink() or target.resolve() != target:
        raise ValueError("crash journal linked path")
    return target


@contextmanager
def crash_journal_lock(path, *, timeout_seconds=0.25):
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not 0 <= timeout_seconds <= 30 or not math.isfinite(timeout_seconds)):
        raise ValueError("invalid lock timeout")
    path = _unlinked_path(path)
    lock_path = _unlinked_path(str(path) + ".lock")
    # Never unlink this inode, including on timeout.
    with open(lock_path, "a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b"\0")
                lock.flush()
            def acquire():
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                import errno
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("crash journal lock timeout") from exc
                time.sleep(min(0.01, remaining))
        try:
            yield
        finally:
            release()


def append_crash_snapshot(path, snapshot, *, timeout_seconds=0.25):
    """Append one strict JSON object; return persisted byte count.

    Serialization happens before lock acquisition. This cannot guarantee a full
    record across power loss; failed/partial writes are surfaced, never retried
    implicitly. Existing interior damage requires the separate forensic repair.
    """
    if not isinstance(snapshot, dict):
        raise TypeError("snapshot must be an object")
    payload = (json.dumps(snapshot, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > 1024 * 1024:
        raise ValueError("crash snapshot exceeds 1 MiB")
    with crash_journal_lock(path, timeout_seconds=timeout_seconds):
        path = _unlinked_path(path)
        with open(path, "a+b", buffering=0) as journal:
            size = os.fstat(journal.fileno()).st_size
            if size:
                journal.seek(-1, os.SEEK_END)
                if journal.read(1) != b"\n":
                    raise ValueError("crash journal has an unterminated tail")
            # O_APPEND remains set; partial writes continue under the same lock.
            written = 0
            while written < len(payload):
                count = journal.write(payload[written:])
                if not count:
                    raise OSError("crash journal append made no progress")
                written += count
            os.fsync(journal.fileno())
    return written
