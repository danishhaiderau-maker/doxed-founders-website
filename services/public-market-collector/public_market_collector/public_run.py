"""Forward public capture. Separate process, new directory, bounded disk.

The paper engine is not imported and is not asked to store this book. This
process lowers its priority and sleeps between commits so a full-depth book
does not sit on the paper engine's lock or fill the laptop disk.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .config import CaptureStopped, StartRefused
from .output_policy import disk_free_bytes, foreign_entries, output_refusal
from .session import Session, append_startup_gap, inspect_torn_tail
from .ws_public import (
    BOOK_FREQ,
    BOOK_LEN,
    BOOK_PREC,
    SYMBOL,
    FrameTooLarge,
    deprioritize_process,
    open_public_socket,
    subscribe_payloads,
)

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
    clock_warn_ms = None
    clock_invalid_ms = None
    trade_quiet_warn_s = None
    segment_max_bytes = None
    seq_layout = None
    seq_scope = None
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


def open_session(root: Path, *, resume: bool, repo_root: Path | None, disk_free_probe=None) -> Session:
    reason = output_refusal(root, repo_root=repo_root)
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
    cfg.disk_free_probe = disk_free_probe
    cfg.free_disk_bytes = int(free)
    ids = allocate_segment_ids(root)
    cfg.segment_id_book = ids["segment_id_book"]
    cfg.segment_id_trades = ids["segment_id_trades"]
    cfg.segment_id_norm_book = ids["segment_id_norm_book"]
    cfg.segment_id_norm_trades = ids["segment_id_norm_trades"]
    return Session(cfg)


def cite_prior_segments(session: Session) -> None:
    """Read existing segments and append a startup gap. Does not rewrite them."""
    root = session.root
    for relative in ("raw/book", "raw/trades", "norm/book", "norm/trades"):
        directory = root / relative
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.gz")):
            report = inspect_torn_tail(path)
            append_startup_gap(root, path, report)


def pump(session: Session, transport, *, sleep, clock_ns, mono_ns, max_messages: int | None = None) -> int:
    """Read public text messages. Yields every YIELD_EVERY so the paper engine runs."""
    for payload in subscribe_payloads():
        transport.send_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    count = 0
    while True:
        if max_messages is not None and count >= max_messages:
            break
        try:
            text = transport.recv_text()
        except FrameTooLarge:
            session._stop("write_fail", "ws_frame_cap")
        if text is None:
            raise ConnectionError("closed")
        now = int(clock_ns())
        mono = int(mono_ns())
        session.handle(text, recv_utc_ns=now, recv_mono_ns=mono)
        count += 1
        if session.uncommitted_received >= YIELD_EVERY:
            session.commit()
            session.cfg.free_disk_bytes = _probe_free(session.root, session.cfg.disk_free_probe)
            if session._floor_block(200):
                session._stop("write_fail", "disk_floor")
            session.write_health(now)
            sleep(YIELD_SLEEP_S)
    session.commit()
    return count


def run_forever(
    root: Path,
    *,
    resume: bool,
    repo_root: Path | None,
    connect=None,
    sleep=None,
    clock_ns=None,
    mono_ns=None,
    deprioritize=None,
    disk_free_probe=None,
    max_messages: int | None = None,
    max_connects: int | None = None,
) -> Session:
    """Deprioritize first, then connect. Reconnect sleeps the backoff."""
    sleeper = sleep or time.sleep
    clocks = clock_ns or time.time_ns
    monos = mono_ns or time.monotonic_ns
    (deprioritize or deprioritize_process)()
    session = open_session(root, resume=resume, repo_root=repo_root, disk_free_probe=disk_free_probe)
    try:
        if resume:
            cite_prior_segments(session)
        connects = 0
        opener = connect or open_public_socket
        while max_connects is None or connects < max_connects:
            connects += 1
            try:
                transport = opener()
                pump(
                    session,
                    transport,
                    sleep=sleeper,
                    clock_ns=clocks,
                    mono_ns=monos,
                    max_messages=max_messages,
                )
                if max_messages is not None:
                    break
            except ConnectionError:
                delay = session.plan_reconnect(connects)
                session.disconnect(recv_utc_ns=int(clocks()), recv_mono_ns=int(monos()), cause="disconnect")
                session.commit()
                sleeper(delay)
                if max_connects is not None and connects >= max_connects:
                    break
        return session
    finally:
        session.release()


def execute_capture(output: str, *, resume: bool, repo_root: Path, runner=None) -> int:
    root = Path(output)
    reason = output_refusal(root, repo_root=repo_root)
    if reason:
        sys.stderr.write(f"REFUSED capture: {reason}\n")
        return 78
    if runner is not None:
        return int(runner(root, resume, repo_root))
    try:
        run_forever(root, resume=resume, repo_root=repo_root)
    except StartRefused as exc:
        sys.stderr.write(f"REFUSED capture: {exc}\n")
        return 78
    except CaptureStopped as exc:
        text = exc.stderr
        sys.stderr.write(text if text.endswith("\n") else text + "\n")
        return 75
    return 0
