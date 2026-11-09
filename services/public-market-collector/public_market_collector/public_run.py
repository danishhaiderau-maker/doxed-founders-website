"""Forward public capture. Separate process, new directory, bounded disk.

Launch with `python -B` or PYTHONDONTWRITEBYTECODE=1 so the checkout does not
gain __pycache__. The paper engine is not imported.

Free space is probed by a daemon thread. That thread only sets a stop flag.
The writer checks the flag and the floor again before each member. A stalled
write is not aborted mid-syscall.

The process asks for below-normal priority and sleeps 5 ms every 20 messages.
Commit work is incremental: only the new rows are normalised, and segment
hashes and byte totals are running counters.

Error domains:
- transport (connect, send, recv): an OSError, including ConnectionError on EOF
  or a close frame and a non-101 handshake, is a disconnect. It is journalled as
  a gap, then the process sleeps the backoff and reconnects. The backoff resets
  to the initial delay after a connection that delivered at least one message.
- disk (segment, journal, health writes): an OSError is a clean stop,
  `CAPTURE_STOP cause=write_fail detail=io_error`, exit 75. It is never treated
  as a network disconnect.

Segments roll at the first commit after they reach SEGMENT_MAX_BYTES or
SEGMENT_MAX_AGE_S. The size cap is a roll threshold: a segment can exceed it by
at most one member. A `close` record with the running whole-segment sha256 is
journalled at each roll.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from .config import CaptureStopped, StartRefused
from .output_policy import disk_free_bytes, foreign_entries, output_refusal
from .session import Session, append_startup_gap, inspect_torn_tail, last_commits_by_segment
from .ws_public import (
    BOOK_FREQ,
    BOOK_LEN,
    BOOK_PREC,
    SYMBOL,
    BadFrame,
    FrameTooLarge,
    deprioritize_process,
    open_public_socket,
    resubscribe_all_messages,
    resubscribe_book_messages,
    subscribe_payloads,
)
from .session import RESUBSCRIBE_DEADLINE_S

# Same 512 MiB floor the local paper runtime uses before it writes.
FREE_DISK_FLOOR_BYTES = 512 * 1024 * 1024
# This collector's own bytes, not a license to fill the disk.
DISK_CAP_BYTES = 8 * 1024 * 1024 * 1024
QUEUE_CAP = 64
YIELD_EVERY = 20
YIELD_SLEEP_S = 0.005
HEARTBEAT_TIMEOUT_S = 30
BACKOFF_INITIAL_S = 1
BACKOFF_CAP_S = 30
BACKOFF_JITTER_S = 0
MAX_BACKOFF_ATTEMPT = 16
# 20060 says maintenance takes up to 120 s. Liveness waits this long while paused.
MAINTENANCE_LIVENESS_S = 180
SEGMENT_MAX_BYTES = 64 * 1024 * 1024
SEGMENT_MAX_AGE_S = 3600
CLOCK_WARN_MS = 250
CLOCK_INVALID_MS = 2000
APPLIED_PRIORITY = frozenset({"posix_nice_10", "windows_below_normal"})


class Reconnect(Exception):
    def __init__(self, cause: str, *, already: bool = False, healthy: bool = False):
        super().__init__(cause)
        self.cause = cause
        self.already = already
        self.healthy = healthy


class DiskWatch:
    """Separate thread. It does not write, rename, or delete."""

    def __init__(self, session: Session, interval_s: float = 1.0):
        self.session = session
        self.interval_s = interval_s
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="pmc-disk-watch", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.interval_s):
                reason = self.session._floor_block(1)
                if reason:
                    self.session.watch_reason = reason
        except BaseException as exc:  # noqa: BLE001 - a dead watcher must stop the writer
            # The writer sees watch_reason and stops with a journalled record.
            self.session.watch_reason = f'disk_watch_failed:{type(exc).__name__}'


class _Config:
    public_capture = True
    test_only = False
    ops_approved = False
    symbol = SYMBOL
    frozen_symbol = SYMBOL
    prec = BOOK_PREC
    freq = BOOK_FREQ
    length = BOOK_LEN
    depth_n = 250
    queue_cap = QUEUE_CAP
    disk_cap_bytes = DISK_CAP_BYTES
    free_disk_floor_bytes = FREE_DISK_FLOOR_BYTES
    heartbeat_timeout_s = HEARTBEAT_TIMEOUT_S
    backoff_initial_s = BACKOFF_INITIAL_S
    backoff_cap_s = BACKOFF_CAP_S
    backoff_jitter_s = BACKOFF_JITTER_S
    health_interval_s = 30
    trade_quiet_warn_s = None
    segment_max_bytes = SEGMENT_MAX_BYTES
    segment_max_age_s = SEGMENT_MAX_AGE_S
    seq_layout = None
    seq_scope = None
    allow_root = None
    priority_result = None
    clock_warn_ms = CLOCK_WARN_MS
    clock_invalid_ms = CLOCK_INVALID_MS
    free_disk_bytes = None
    disk_free_probe = None
    repo_root = None
    resume = True
    output_root = ""
    epoch_ids = ("public-epoch-1",)
    segment_id_book = "book-0001"
    segment_id_trades = "trades-0001"
    segment_id_norm_book = "nbook-0001"
    segment_id_norm_trades = "ntrades-0001"


def allocate_segment_ids(root: Path) -> dict[str, str]:
    """Pick segment names that are not already on disk. Does not create files."""

    def nxt(relative: str, prefix: str) -> str:
        directory = root / relative
        n = 1
        while n < 100000:
            sid = f"{prefix}-{n:04d}"
            if not (directory / f"{sid}.gz").exists():
                return sid
            n += 1
        raise StartRefused("SEGMENT_ID_EXHAUSTED")

    return {
        "segment_id_book": nxt("raw/book", "book"),
        "segment_id_trades": nxt("raw/trades", "trades"),
        "segment_id_norm_book": nxt("norm/book", "nbook"),
        "segment_id_norm_trades": nxt("norm/trades", "ntrades"),
    }


def _probe_free(root: Path, probe) -> int | None:
    fn = probe or disk_free_bytes
    target = root if root.exists() else root.parent
    return fn(target)


def open_session(
    root: Path,
    *,
    resume: bool,
    repo_root: Path | None,
    allow_root: Path | None,
    disk_free_probe=None,
    priority_result: str | None = None,
    segment_max_bytes: int | None = None,
    segment_max_age_s: int | None = None,
) -> Session:
    if resume and not root.exists():
        raise StartRefused("RESUME_DIR_MISSING")
    reason = output_refusal(root, repo_root=repo_root, allow_root=allow_root, resume=resume)
    if reason:
        raise StartRefused(reason)
    if root.exists() and foreign_entries(root):
        raise StartRefused("FOREIGN_OUTPUT")
    free = _probe_free(root, disk_free_probe)
    if free is None:
        raise StartRefused("disk_free_unmeasurable")
    if int(free) <= FREE_DISK_FLOOR_BYTES:
        raise StartRefused("disk_floor")
    cfg = _Config()
    cfg.output_root = str(root)
    cfg.resume = bool(resume)
    cfg.repo_root = repo_root
    cfg.allow_root = allow_root
    cfg.disk_free_probe = disk_free_probe
    cfg.free_disk_bytes = int(free)
    cfg.priority_result = priority_result
    if segment_max_bytes is not None:
        cfg.segment_max_bytes = segment_max_bytes
    if segment_max_age_s is not None:
        cfg.segment_max_age_s = segment_max_age_s
    ids = allocate_segment_ids(root)
    cfg.segment_id_book = ids["segment_id_book"]
    cfg.segment_id_trades = ids["segment_id_trades"]
    cfg.segment_id_norm_book = ids["segment_id_norm_book"]
    cfg.segment_id_norm_trades = ids["segment_id_norm_trades"]
    return Session(cfg)


def cite_prior_segments(session: Session) -> None:
    """Read existing segments and append a startup gap. Does not rewrite them."""
    root = session.root
    journal, _stopped = session.journal_records()
    commits = last_commits_by_segment(journal)
    for relative in ("raw/book", "raw/trades", "norm/book", "norm/trades"):
        directory = root / relative
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.gz")):
            committed = commits.get(f"{relative}/{path.name}")
            report = inspect_torn_tail(path, committed=committed)
            append_startup_gap(root, path, report)


def _send_json(transport, payload: dict) -> None:
    try:
        transport.send_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    except OSError as exc:
        raise Reconnect("disconnect", already=False) from exc


def _disk(session: Session, fn, *args, **kwargs):
    """Run a session write. A disk OSError is a clean stop, not a disconnect."""
    try:
        return fn(*args, **kwargs)
    except OSError:
        session._stop("write_fail", "io_error")


def _liveness_lapsed(session: Session, now: int) -> bool:
    if session.maintenance_pause and session.last_liveness is not None:
        if now - int(session.last_liveness) <= MAINTENANCE_LIVENESS_S * 1_000_000_000:
            return False
    return _disk(session, session.poll_liveness, now)


def _resubscribe_expired(session: Session, now: int) -> bool:
    deadline = session.resubscribe_deadline_ns
    return deadline is not None and now > int(deadline) and not session.shadow.valid


def pump(session: Session, transport, *, sleep, clock_ns, mono_ns, max_messages: int | None = None) -> int:
    """Read public text messages. Yields every YIELD_EVERY so the paper engine runs."""
    # A new connection: channel ids from an earlier connection are stale.
    session.subs.clear()
    session.pending_resubscribe = False
    session.resubscribe_deadline_ns = None
    for payload in subscribe_payloads():
        _send_json(transport, payload)
    if session.last_liveness is None:
        session.last_liveness = int(clock_ns())
    count = 0
    while True:
        if session.watch_reason:
            session._stop("write_fail", session.watch_reason)
        if max_messages is not None and count >= max_messages:
            break
        try:
            text = transport.recv_text()
        except FrameTooLarge:
            session._stop("write_fail", "ws_frame_cap")
        except TimeoutError:
            now = int(clock_ns())
            if _liveness_lapsed(session, now):
                raise Reconnect("liveness", already=True, healthy=count > 0)
            if _resubscribe_expired(session, now):
                raise Reconnect("resubscribe_timeout", already=False, healthy=count > 0)
            continue
        except OSError as exc:
            # EOF and close frames arrive here as ConnectionError.
            raise Reconnect("disconnect", already=False, healthy=count > 0) from exc
        if text is None:
            raise Reconnect("closed", already=False, healthy=count > 0)
        now = int(clock_ns())
        mono = int(mono_ns())
        if isinstance(text, BadFrame):
            _disk(session, session.note_bad_json, now, mono, kind=text.kind)
            count += 1
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            _disk(session, session.note_bad_json, now, mono)
            count += 1
            continue
        code = parsed.get("code") if isinstance(parsed, dict) and parsed.get("event") == "info" else None
        _disk(session, session.handle, text, recv_utc_ns=now, recv_mono_ns=mono)
        count += 1
        if code == 20060:
            session.maintenance_pause = True
            _disk(session, session.mark_maintenance, now, mono)
        elif code == 20061:
            session.maintenance_pause = False
            # Documented advice: unsubscribe and subscribe all channels again.
            session.pending_resubscribe = False
            session.resubscribe_deadline_ns = now + RESUBSCRIBE_DEADLINE_S * 1_000_000_000
            session.resubscribe_count += 1
            for payload in resubscribe_all_messages(session.subs):
                _send_json(transport, payload)
        elif code == 20051:
            raise Reconnect("info_20051", already=False, healthy=True)
        if session.pending_resubscribe:
            session.pending_resubscribe = False
            session.resubscribe_deadline_ns = now + RESUBSCRIBE_DEADLINE_S * 1_000_000_000
            session.resubscribe_count += 1
            for payload in resubscribe_book_messages(session.subs):
                _send_json(transport, payload)
        if _liveness_lapsed(session, now):
            raise Reconnect("liveness", already=True, healthy=True)
        if _resubscribe_expired(session, now):
            raise Reconnect("resubscribe_timeout", already=False, healthy=True)
        if session.uncommitted_received >= YIELD_EVERY:
            _disk(session, session.commit)
            session.cfg.free_disk_bytes = _probe_free(session.root, session.cfg.disk_free_probe)
            if session._floor_block(200):
                session._stop("write_fail", "disk_floor")
            _disk(session, session.write_health, now)
            sleep(YIELD_SLEEP_S)
    _disk(session, session.commit)
    return count


def _recover(session: Session, clocks, monos, *, already: bool, cause: str) -> None:
    if not already:
        _disk(session, session.disconnect, recv_utc_ns=int(clocks()), recv_mono_ns=int(monos()), cause=cause)
    _disk(session, session.commit)


def _close_transport(transport) -> None:
    closer = getattr(transport, "close", None)
    if closer is None:
        return
    try:
        closer()
    except OSError:
        pass


def run_forever(
    root: Path,
    *,
    resume: bool,
    repo_root: Path | None,
    allow_root: Path | None,
    connect=None,
    sleep=None,
    clock_ns=None,
    mono_ns=None,
    deprioritize=None,
    disk_free_probe=None,
    max_messages: int | None = None,
    max_connects: int | None = None,
    watch_interval_s: float = 1.0,
) -> Session:
    """Deprioritize first, then connect. Network errors sleep and reconnect."""
    sleeper = sleep or time.sleep
    clocks = clock_ns or time.time_ns
    monos = mono_ns or time.monotonic_ns
    priority = (deprioritize or deprioritize_process)()
    if priority not in APPLIED_PRIORITY:
        raise StartRefused(f"PRIORITY_DENIED:{priority}")
    session = open_session(
        root,
        resume=resume,
        repo_root=repo_root,
        allow_root=allow_root,
        disk_free_probe=disk_free_probe,
        priority_result=priority,
    )
    watch = DiskWatch(session, watch_interval_s)
    watch.start()
    try:
        if resume:
            cite_prior_segments(session)
            session.accounted_bytes = _root_size_after_cite(session)
        attempt = 1
        connects = 0
        opener = connect or open_public_socket
        while max_connects is None or connects < max_connects:
            connects += 1
            transport = None
            cause = None
            already = False
            try:
                try:
                    transport = opener()
                except OSError:
                    # Connect, TLS, DNS, timeout, or a non-101 handshake (HandshakeRejected).
                    cause = "disconnect"
                if transport is not None:
                    try:
                        pump(
                            session,
                            transport,
                            sleep=sleeper,
                            clock_ns=clocks,
                            mono_ns=monos,
                            max_messages=max_messages,
                        )
                        attempt = 1
                        if max_messages is not None:
                            break
                    except Reconnect as exc:
                        cause = exc.cause
                        already = exc.already
                        if exc.healthy:
                            # The closed connection carried at least one message.
                            attempt = 1
            finally:
                _close_transport(transport)
            if cause is not None:
                _recover(session, clocks, monos, already=already, cause=cause)
                delay = session.plan_reconnect(attempt)
                attempt = min(attempt + 1, MAX_BACKOFF_ATTEMPT)
                sleeper(delay)
            if max_connects is not None and connects >= max_connects:
                break
        return session
    finally:
        watch.stop()
        session.release()


def _root_size_after_cite(session: Session) -> int:
    from .session import _root_size

    return _root_size(session.root)


def execute_capture(
    output: str,
    *,
    resume: bool,
    repo_root: Path,
    allow_root: str,
    runner=None,
) -> int:
    root = Path(output)
    allow = Path(allow_root)
    reason = output_refusal(root, repo_root=repo_root, allow_root=allow, resume=resume)
    if reason:
        sys.stderr.write(f"REFUSED capture: {reason}\n")
        return 78
    if runner is not None:
        return int(runner(root, resume, repo_root, allow))
    try:
        run_forever(root, resume=resume, repo_root=repo_root, allow_root=allow)
    except StartRefused as exc:
        sys.stderr.write(f"REFUSED capture: {exc}\n")
        return 78
    except CaptureStopped as exc:
        text = exc.stderr
        sys.stderr.write(text if text.endswith("\n") else text + "\n")
        return 75
    return 0
