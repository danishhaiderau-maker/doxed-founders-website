"""Runtime data hygiene on the Fly volume: archive, never hard delete.

Pure module (no bot.py, Flask or network). Three jobs, all moving files into
``research_archive/`` under the runtime root. That directory name is in
``research_segment_selection.EXCLUDED_DIR_NAMES``, so nothing moved there is
re-shipped as a new segment, and every move is recorded in a receipt.

1. ``archive_pre_epoch_ledgers`` (#420, epoch start). When a clean data epoch
   opens, plain append-only ledgers whose *mtime predates the epoch start* hold
   only pre-epoch rows; they are moved to
   ``research_archive/pre_epoch/<epoch_id>/`` so the new epoch's files start
   empty. A ledger written after the epoch start (mtime >= start) is MIXED and
   is left in place for the analyzer epoch guard; nothing is row-filtered.
   Hash-sealed / restart-recovery stores (v22 sealed generations, v3 ledgers,
   paper lifecycle, relay outbox, provisional store) and market tapes are
   never moved. One receipt per epoch makes the step idempotent.
2. ``compact_handoff_journal`` (cancellation/fill evidence handoffs). The
   append-only pending/result journals were never rotated (199 MB / 48 MB on
   4 Oct). Compaction renames the journal into ``research_archive/handoffs/``
   and writes back only the unresolved pending rows (first pending row per
   receipt id, original order), under the caller's append lock, so replay
   semantics are unchanged.
3. ``sweep_orphan_tmp`` (boot). Atomic-write temp files left by a crashed
   writer (``.research_events_v22.provisional.json.<pid>.tmp``,
   ``paper_lifecycle_v1.json.<pid>.<tid>.tmp``,
   ``signal_snapshot.jsonl.<pid>.<tid>.tmp``) are moved to
   ``research_archive/orphan_tmp/`` only when older than 1 h, not owned by
   this process or any live pid, and the in-process writer lock is free.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

ARCHIVE_DIR = "research_archive"
PRE_EPOCH_SUBDIR = "pre_epoch"
HANDOFF_SUBDIR = "handoffs"
ORPHAN_TMP_SUBDIR = "orphan_tmp"
RECEIPT_DIR = "data_epoch_boundary"  # kept by the wipe's chain-state guard
PRE_EPOCH_RECEIPT_SCHEMA = "pre_epoch_ledger_archive_v1"
HANDOFF_RECEIPT_SCHEMA = "handoff_journal_compaction_v1"
ORPHAN_RECEIPT_SCHEMA = "orphan_tmp_sweep_v1"

# Plain append-only, epoch-scoped, bot-owned ledgers (no hash chain, no seal
# receipt, not read back for restart recovery). Keep this list explicit.
PRE_EPOCH_LEDGERS = (
    "trades_3factor.csv", "expired_orders_3factor.csv", "decisions_3factor.csv",
    "blocked_signals_3factor.csv", "ai_tranche_log.csv", "setup_log_3factor.csv",
    "pipeline_events_3factor.csv", "ai_errors_3factor.csv",
    "trade_outcome.jsonl", "fill_markouts.jsonl", "execution_funnel.jsonl",
    "signal_replay.jsonl", "post_exit_replay.jsonl", "shadow_outcome.jsonl", "shadow_exit_paths.jsonl",
    "taker_signal_counterfactuals.jsonl", "adaptive_entry_decisions.jsonl",
)
# Restart-recovery or hash-sealed state: never moved by this module.
NEVER_MOVE = frozenset({
    "paper_lifecycle_v1.json", "research_events_v22.jsonl", "research_events_v22.provisional.json",
    "data_epoch.json", "lane_pnl_ledger.json", "cancellation_evidence_handoffs.jsonl",
    "fill_evidence_handoffs.jsonl", "market_microstructure_1s.jsonl", "cross_venue_tape_1m.jsonl",
    "market_context_1m.jsonl", "liquidations.jsonl",
})

# Per-ledger sidecars that describe one exact file; they move with it.
LEDGER_SIDECAR_SUFFIXES = (".validation.json", ".malformed_rows.jsonl")

ORPHAN_TMP_PATTERNS = (
    re.compile(r"^\.research_events_v22\.provisional\.json\.(?P<pid>\d+)\.tmp$"),
    re.compile(r"^paper_lifecycle_v1\.json\.(?P<pid>\d+)\.\d+\.tmp$"),
    re.compile(r"^signal_snapshot\.jsonl\.(?P<pid>\d+)\.\d+\.tmp$"),
)
ORPHAN_MIN_AGE_SEC = 3600.0

HANDOFF_COMPACT_MIN_BYTES = 16 * 1024 * 1024
HANDOFF_COMPACT_MAX_AGE_SEC = 24 * 3600.0


def utc_stamp(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json_atomic(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".hyg-{os.getpid()}")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _unique_target(directory: Path, name: str) -> Path:
    target = directory / name
    n = 1
    while target.exists():
        target = directory / f"{name}.{n}"
        n += 1
    return target


# ------------------------------------------------------------------ 1. pre-epoch ledgers

def pre_epoch_receipt_path(runtime_root: str | os.PathLike, epoch_id: str) -> Path:
    return Path(runtime_root) / RECEIPT_DIR / f"{epoch_id}.pre_epoch_archive.json"


def archive_pre_epoch_ledgers(runtime_root: str | os.PathLike, manifest: dict | None, *,
                              names: Iterable[str] = PRE_EPOCH_LEDGERS, now: float | None = None) -> dict | None:
    """Move whole pre-epoch ledgers out of the clean epoch; idempotent per epoch."""
    if not manifest or not manifest.get("epoch_id"):
        return None
    root = Path(runtime_root)
    epoch_id = str(manifest["epoch_id"])
    receipt = pre_epoch_receipt_path(root, epoch_id)
    try:
        done = json.loads(receipt.read_text(encoding="utf-8"))
        if isinstance(done, dict) and done.get("epoch_id") == epoch_id:
            return done
    except (OSError, ValueError):
        pass
    started = float(manifest["started_at_ts"])
    target_dir = root / ARCHIVE_DIR / PRE_EPOCH_SUBDIR / epoch_id
    files: dict[str, dict] = {}
    for name in names:
        if name in NEVER_MOVE or "/" in name or name.startswith("."):
            files[name] = {"status": "REFUSED_PROTECTED"}
            continue
        src = root / name
        try:
            st = src.stat()
        except FileNotFoundError:
            files[name] = {"status": "ABSENT"}
            continue
        except OSError as exc:
            files[name] = {"status": "STAT_ERROR", "error": str(exc)[:200]}
            continue
        entry: dict[str, Any] = {"bytes": st.st_size, "mtime": st.st_mtime}
        if st.st_size == 0:
            files[name] = {**entry, "status": "EMPTY"}
            continue
        if st.st_mtime >= started:
            # Rows after the epoch start may be current: never split a file here.
            files[name] = {**entry, "status": "MIXED_LEFT_IN_PLACE"}
            continue
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_target(target_dir, name)
            os.rename(src, target)  # same volume: atomic, no copy
        except OSError as exc:
            files[name] = {**entry, "status": "MOVE_FAILED", "error": str(exc)[:200]}
            continue
        sidecars = []
        for suffix in LEDGER_SIDECAR_SUFFIXES:
            side = root / (name + suffix)
            if side.is_file():
                try:
                    side_target = _unique_target(target_dir, side.name)
                    os.rename(side, side_target)
                    sidecars.append(str(side_target.relative_to(root)))
                except OSError:
                    pass  # a stale sidecar never matches the new file's signature
        files[name] = {**entry, "status": "ARCHIVED", "archived_to": str(target.relative_to(root)),
                       "sidecars": sidecars}
    archived = [n for n, f in files.items() if f["status"] == "ARCHIVED"]
    doc = {
        "schema": PRE_EPOCH_RECEIPT_SCHEMA, "epoch_id": epoch_id,
        "epoch_started_at_ts": started, "decided_at_utc": utc_stamp(now),
        "archive_dir": str(target_dir.relative_to(root)), "deletion_invoked": False,
        "archived": archived, "archived_bytes": sum(files[n]["bytes"] for n in archived),
        "mixed_left_in_place": [n for n, f in files.items() if f["status"] == "MIXED_LEFT_IN_PLACE"],
        "files": files,
    }
    _write_json_atomic(receipt, doc)
    return doc


# ------------------------------------------------------------------ 2. handoff journals

def compact_handoff_journal(path: str | os.PathLike, *, lock: Any, pending_schema: str, result_schema: str,
                            terminal_statuses: Iterable[str], runtime_root: str | os.PathLike,
                            min_bytes: int = HANDOFF_COMPACT_MIN_BYTES,
                            max_age_sec: float = HANDOFF_COMPACT_MAX_AGE_SEC,
                            force: bool = False, now: float | None = None) -> dict:
    """Archive the journal and keep only unresolved pending rows; replay-equivalent."""
    src = Path(path)
    root = Path(runtime_root)
    now = time.time() if now is None else float(now)
    terminal_statuses = frozenset(terminal_statuses)
    state_path = root / RECEIPT_DIR / f"{src.name}.compaction.json"
    try:
        last = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    with lock:
        try:
            size = src.stat().st_size
        except FileNotFoundError:
            return {"status": "ABSENT"}
        last_ts = float(last.get("compacted_at_ts") or 0)
        due = force or size >= min_bytes or (size > 0 and last_ts and now - last_ts >= max_age_sec)
        if not due:
            return {"status": "NOT_DUE", "bytes": size}
        pending: dict[str, bytes] = {}
        terminal: set[str] = set()
        rows = 0
        with open(src, "rb") as handle:
            for raw in handle:
                rows += 1
                try:
                    row = json.loads(raw)
                except (TypeError, ValueError):
                    continue  # torn/malformed bytes stay in the archive copy only
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("receipt_id") or "")
                if not rid:
                    continue
                if row.get("schema") == pending_schema:
                    if rid not in pending:
                        pending[rid] = raw if raw.endswith(b"\n") else raw + b"\n"
                elif row.get("schema") == result_schema and str(row.get("status") or "") in terminal_statuses:
                    terminal.add(rid)
        keep = [line for rid, line in pending.items() if rid not in terminal]
        archive_dir = root / ARCHIVE_DIR / HANDOFF_SUBDIR
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_target(archive_dir, f"{src.name}.{utc_stamp(now)}")
        tmp = src.with_name(f".{src.name}.compact-{os.getpid()}")
        with open(tmp, "wb") as out:
            try:
                os.chmod(tmp, 0o600)  # same mode the durable handoff writer uses
            except OSError:
                pass
            out.writelines(keep)
            out.flush()
            os.fsync(out.fileno())
        os.rename(src, target)
        os.replace(tmp, src)
    doc = {
        "schema": HANDOFF_RECEIPT_SCHEMA, "journal": src.name, "compacted_at_ts": now,
        "compacted_at_utc": utc_stamp(now), "archived_to": str(target.relative_to(root)),
        "archived_bytes": size, "rows_read": rows, "pending_ids": len(pending),
        "terminal_ids": len(terminal), "unresolved_kept": len(keep), "deletion_invoked": False,
    }
    _write_json_atomic(state_path, doc)
    return {"status": "COMPACTED", **doc}


# ------------------------------------------------------------------ 3. orphaned temp files

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _try_lock(lock: Any):
    if lock is None:
        yield True
        return
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def sweep_orphan_tmp(runtime_root: str | os.PathLike, *, writer_lock: Any = None, now: float | None = None,
                     min_age_sec: float = ORPHAN_MIN_AGE_SEC, current_pid: int | None = None,
                     pid_alive: Callable[[int], bool] = _pid_alive) -> dict:
    """Quarantine (move, never delete) orphaned atomic-write temp files in the runtime root."""
    root = Path(runtime_root)
    now = time.time() if now is None else float(now)
    current_pid = os.getpid() if current_pid is None else int(current_pid)
    moved, skipped = [], []
    with _try_lock(writer_lock) as free:
        if not free:
            return {"schema": ORPHAN_RECEIPT_SCHEMA, "status": "WRITER_LOCK_HELD", "moved": [], "skipped": []}
        try:
            names = sorted(os.listdir(root))
        except OSError as exc:
            return {"schema": ORPHAN_RECEIPT_SCHEMA, "status": "LIST_FAILED", "error": str(exc)[:200]}
        target_dir = root / ARCHIVE_DIR / ORPHAN_TMP_SUBDIR / utc_stamp(now)
        for name in names:
            match = next((m for m in (p.match(name) for p in ORPHAN_TMP_PATTERNS) if m), None)
            if not match:
                continue
            src = root / name
            try:
                st = src.stat()
            except OSError:
                continue
            pid = int(match.group("pid"))
            reason = None
            if now - st.st_mtime < min_age_sec:
                reason = "YOUNGER_THAN_MIN_AGE"
            elif pid == current_pid:
                reason = "OWNED_BY_THIS_PROCESS"
            elif pid_alive(pid):
                reason = "OWNER_PID_ALIVE"
            if reason:
                skipped.append({"name": name, "reason": reason})
                continue
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                os.rename(src, _unique_target(target_dir, name))
            except OSError as exc:
                skipped.append({"name": name, "reason": f"MOVE_FAILED:{exc}"[:200]})
                continue
            moved.append({"name": name, "bytes": st.st_size})
    doc = {
        "schema": ORPHAN_RECEIPT_SCHEMA, "status": "OK", "swept_at_utc": utc_stamp(now),
        "archive_dir": str(target_dir.relative_to(root)) if moved else None,
        "moved": moved, "moved_count": len(moved), "moved_bytes": sum(m["bytes"] for m in moved), "skipped": skipped,
        "deletion_invoked": False,
    }
    if moved:
        _write_json_atomic(root / RECEIPT_DIR / f"orphan_tmp_sweep.{utc_stamp(now)}.json", doc)
    return doc
