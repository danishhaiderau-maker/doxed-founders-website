"""Explicit daily public-data runtime. No scheduler, credentials or execution path.

One directory per UTC receive day; reconnect/snapshot at rollover. A boundary
frame may be omitted, never silently assigned to the old day. Runtime journals
report partial days, reconnect/rollover gaps and *observed* minute coverage, not
continuous coverage or Stage-F VALID judgments. Day sealing is a separate task.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .config import CaptureStopped, StartRefused
from .framing import AppendJournal, read_journal
from .market import classify_message
from .output_policy import allow_root_refusal, disk_free_bytes
from .public_run import FREE_DISK_FLOOR_BYTES, ConnectPacer, run_forever
from .relaunch import LaunchJournal, supervise

WINDOWS_ROOT = r"C:\DoxxedCrypto\data\collector"
STATUS_INTERVAL_NS = 30_000_000_000
MINUTE_NS = 60_000_000_000
DAY_NS = 1440 * MINUTE_NS
_METADATA = frozenset({"runtime.lock", "launch.journal", "runtime.journal"})


def utc_day(ns: int) -> str:
    return datetime.fromtimestamp(ns // 1_000_000_000, timezone.utc).strftime("%Y-%m-%d")


def default_root(platform: str | None = None) -> str | None:
    return WINDOWS_ROOT if (sys.platform if platform is None else platform) == "win32" else None


def validate_root(root: Path, repo_root: Path | None) -> None:
    reason = allow_root_refusal(root, repo_root=repo_root)
    if reason:
        raise StartRefused(reason)
    for child in root.iterdir():
        if child.is_symlink():
            raise StartRefused("RUNTIME_SYMLINK")
        if child.name in _METADATA and child.is_file():
            continue
        try:
            day = datetime.strptime(child.name, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            day = None
        if day != child.name or not child.is_dir():
            raise StartRefused("FOREIGN_RUNTIME_ROOT")


class RuntimeLock:
    """OS advisory lock released on death, leaving the file in place. No unlink.
    All entrypoints to this runtime hold it through waits, capture and cleanup.
    Windows uses a nonblocking byte lock; POSIX uses flock.
    """
    def __init__(self, path: Path):
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600)
        self.windows = os.name == "nt"
        try:
            if self.windows:
                import msvcrt
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b"\0")
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            raise StartRefused("RUNTIME_ALREADY_RUNNING") from exc

    def close(self):
        os.close(self.fd)


class Coverage:
    def __init__(self, day: str, started_ns: int):
        self.day = day
        self.start_ns = started_ns
        self.day_start_ns = (started_ns // DAY_NS) * DAY_NS
        self.minutes = {"trades_feed": set(), "valid_book_feed": set(), "data": set()}

    def observe(self, session, now_ns: int, parsed):
        if utc_day(now_ns) != self.day or not isinstance(parsed, list) or not parsed:
            return
        sub = session.subs.get(parsed[0])
        if not isinstance(sub, dict):
            return
        minute = (now_ns - self.day_start_ns) // MINUTE_NS
        classified = classify_message(parsed, sub, None)["channel"]
        if sub.get("channel") == "trades" and classified in {"heartbeat", "trade"}:
            self.minutes["trades_feed"].add(minute)
        if sub.get("channel") == "book" and session.shadow.valid and classified in {"heartbeat", "book_snapshot", "book_delta", "checksum"}:
            self.minutes["valid_book_feed"].add(minute)
        if classified in {"trade", "book_snapshot", "book_delta", "checksum"}:
            self.minutes["data"].add(minute)

    def record(self, session, now_ns, *, final=False):
        return {
            "kind": "coverage" if final else "heartbeat",
            "schema_v": "pmc_runtime_v1", "day_utc": self.day,
            "utc_ns": int(now_ns), "run_start_ns": self.start_ns,
            "window_end_ns": min(int(now_ns), self.day_start_ns + DAY_NS),
            "final_for_run": final, "sealed": False,
            "coverage_basis": "UTC_receive_minute_observed_not_continuous_coverage",
            "scope": "this_run_only_no_stage_f_validity_claim",
            "observed_minutes": {key: sorted(value) for key, value in self.minutes.items()},
            "unobserved_minutes": {key: 1440 - len(value) for key, value in self.minutes.items()},
            "book_valid": session.shadow.valid, "received_count": session.received,
            "durable_count": session.durable, "gaps": session.gap_count,
            "checksum_faults": session.checksum_fail_count,
        }


def run_daily(root: Path, *, repo_root=None, stop=None, clock_ns=None, mono_ns=None,
              sleep=None, connect=None, disk_free_probe=None, deprioritize=None,
              floor_bytes=FREE_DISK_FLOOR_BYTES, max_days=None) -> int:
    """Run until stop, terminal failure, or synthetic max_days. Lock/launcher are
    supplied by execute_daily. Dependency hooks permit socket-free tests only.
    """
    clocks = clock_ns or time.time_ns
    monos = mono_ns or time.monotonic_ns
    stopped = stop or (lambda: False)
    sleeper = sleep or time.sleep
    free = disk_free_probe or disk_free_bytes
    floor = max(FREE_DISK_FLOOR_BYTES, int(floor_bytes))
    pacing = ConnectPacer()  # preserved across midnight
    prior, torn = read_journal(root / "runtime.journal")
    if torn:
        raise StartRefused("RUNTIME_JOURNAL_TORN")
    previous_ns = prior[-1].get("utc_ns") if prior else None
    journal = AppendJournal(root / "runtime.journal", deque(maxlen=8), exclusive=False)
    active = None
    coverage = None
    last_status = None

    def write(row):
        size = len(json.dumps(row).encode("utf-8")) + 512
        available = free(root)
        if available is None or int(available) - size < floor:
            raise CaptureStopped("disk_floor", "CAPTURE_STOP cause=write_fail detail=disk_floor\n")
        journal.append_obj(row, label="runtime")

    def observe(session, now_ns, parsed):
        nonlocal active, last_status
        active = session
        coverage.observe(session, now_ns, parsed)
        if last_status is None or now_ns - last_status >= STATUS_INTERVAL_NS:
            # Flush raw data even on an idle/heartbeat-only connection. Health is
            # durable and includes invalid-book status, not just socket liveness.
            session.commit()
            session.cfg.free_disk_bytes = free(root)
            session.write_health(now_ns)
            write(coverage.record(session, now_ns))
            last_status = now_ns

    def wait(seconds):
        # At most 1 s between heartbeat/stop checks during reconnect backoff.
        remaining = float(seconds)
        while remaining > 0 and not stopped():
            chunk = min(remaining, 1.0)
            sleeper(chunk)
            remaining -= chunk
            if active is not None:
                observe(active, int(clocks()), None)

    count = 0
    try:
        while not stopped() and (max_days is None or count < max_days):
            active = None
            now = int(clocks())
            day = utc_day(now)
            coverage = Coverage(day, now)
            last_status = None
            write({"kind": "day_start", "schema_v": "pmc_runtime_v1", "day_utc": day,
                   "utc_ns": now, "resume": (root / day).exists(),
                   "gap_note": "unobserved_before_start_or_between_runs; no_book_backfill",
                   "observation_gap_start_ns": previous_ns if previous_ns is not None else (now // DAY_NS) * DAY_NS,
                   "observation_gap_end_ns": now,
                   "rollover_policy": "reconnect_and_fresh_snapshot; boundary_frame_may_be_omitted"})
            session = run_forever(
                root / day, resume=(root / day).exists(), allow_root=root,
                repo_root=repo_root, stop=lambda: stopped() or utc_day(int(clocks())) != day,
                connect=connect, sleep=wait, clock_ns=clocks, mono_ns=monos,
                deprioritize=deprioritize, disk_free_probe=free, pacer=pacing,
                free_disk_floor_bytes=floor, observe=observe,
            )
            active = None  # released session must not be used by backoff heartbeat
            end = int(clocks())
            write(coverage.record(session, end, final=True))
            write({"kind": "day_end", "schema_v": "pmc_runtime_v1", "day_utc": day,
                   "utc_ns": end, "reason": "graceful_stop" if stopped() else "utc_rollover",
                   "book_backfill": False})
            previous_ns = end
            count += 1
        return 0
    finally:
        journal.close()


def execute_daily(root: str, *, repo_root=None, stop_event=None, runner=None,
                  clock_ns=None, sleep=None, disk_free_probe=None, max_runs=None) -> int:
    """Explicit launcher. Does not install autostart or alter any bot configuration."""
    path = Path(root)
    event = stop_event or threading.Event()
    clocks = clock_ns or time.time_ns
    free = disk_free_probe or disk_free_bytes
    lock = None
    previous = {}

    def disk_guard():
        available = free(path)
        if available is None or int(available) < FREE_DISK_FLOOR_BYTES + 4096:
            raise CaptureStopped("disk_floor", "CAPTURE_STOP cause=write_fail detail=disk_floor\n")

    def once():
        try:
            if runner is not None:
                return int(runner(path, event))
            return run_daily(path, repo_root=repo_root, stop=event.is_set, sleep=event.wait)
        except StartRefused:
            return 78
        except (CaptureStopped, OSError):
            return 75
        except Exception as exc:
            # No exception text (may include untrusted transport payloads).
            sys.stderr.write(f"COLLECTOR_UNEXPECTED {type(exc).__name__}\n")
            return 1

    try:
        validate_root(path, repo_root)
        disk_guard()
        lock = RuntimeLock(path / "runtime.lock")
        # Check metadata integrity before any new append or socket.
        _rows, torn = read_journal(path / "runtime.journal")
        if torn:
            raise StartRefused("RUNTIME_JOURNAL_TORN")
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous[sig] = signal.getsignal(sig)
                signal.signal(sig, lambda *_args: event.set())
        return supervise(once, journal=LaunchJournal(path / "launch.journal", disk_guard),
                         clock_ns=clocks, sleep=sleep or event.wait,
                         stop=event.is_set, max_runs=max_runs)
    except StartRefused as exc:
        sys.stderr.write(f"REFUSED daily: {exc.reason}\n")
        return 78
    except (CaptureStopped, OSError):
        return 75
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if lock is not None:
            lock.close()
