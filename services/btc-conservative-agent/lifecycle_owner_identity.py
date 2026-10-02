"""Process identity and stable, lifetime ownership lock (never unlink lock file)."""
import math
import os
from pathlib import Path


def identity(pid):
    if os.name != "posix":
        return None  # Windows retains conservative PID liveness plus OS lock.
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        ticks = int(stat[stat.rfind(")") + 2:].split()[19])
        return {"boot_id": boot, "start_ticks": ticks}
    except (OSError, ValueError, IndexError):
        return None


def boot_time():
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def stale(existing, current_identity, alive, observed_identity, boot_unix):
    if not alive:
        return True
    saved = existing.get("process_identity")
    if (isinstance(saved, dict) and isinstance(saved.get("boot_id"), str) and saved["boot_id"]
            and type(saved.get("start_ticks")) is int and saved["start_ticks"] > 0):
        if current_identity and saved["boot_id"] != current_identity["boot_id"]:
            return True
        return bool(observed_identity and
                    (saved["boot_id"], saved["start_ticks"]) !=
                    (observed_identity["boot_id"], observed_identity["start_ticks"]))
    if saved is not None:
        return False
    created = existing.get("created_unix")
    return bool(type(created) in (int, float) and math.isfinite(created)
                and created > 0 and boot_unix is not None and created < boot_unix - 1)


def acquire(path):
    handle = Path(path).open("a+b")
    try:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def release(handle):
    if handle is not None:
        handle.close()
