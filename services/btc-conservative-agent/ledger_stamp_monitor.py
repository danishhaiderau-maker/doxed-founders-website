"""HM monitor (R16) ``v3_ledger_unstamped_since``: V3 ledger rows written since ``since_ts`` without a countable stamp.

Reads every generation of each ledger (``{name}.jsonl`` then sealed ``{name}.jsonl.N``, newest first) from the END
backwards. M1 (HM 25 Oct): the ONLY boundary is ``write_stamp.written_at_ts`` < ``since_ts``; legacy top-level keys
(ts/decision_ts/...) are never trusted. A row with no stamp or no written_at_ts is UNSTAMPED and is never skipped by
the scan window (fail closed): every unstamped row newer than the boundary, or newer than any stamped row, counts.
M3: unstamped rows that lie strictly BELOW the oldest stamped row of the stream (append order proves they were
written before stamping began) are reported as ``legacy_unstamped_before_first_stamp`` and are not RED. A generation
whose file mtime is before ``since_ts`` provably holds nothing written since (no read needed). A stream with no
stamped row and no write since ``since_ts`` that is retired by code is reported IDLE with its reason (never GREEN).
Rows whose sha256 is in the exclusion list (RK list) are reported separately and not counted.

Status: RED when count > 0, a stream is unreadable (fail closed), or a retired stream was written after retirement;
AMBER while the cached incremental scan has not yet reached EOF (it resumes on the next call); IDLE when a ledger is idle (reason given); GREEN only when none of these apply.
CLI: ``python ledger_stamp_monitor.py --data-dir DIR --since TS [--exclusion-list F]``; exit 0 GREEN, 2 RED, 3 other.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterator

import ledger_write_stamp as lws

CHECK_ID = "v3_ledger_unstamped_since"
LEDGERS = ("opportunity", "decision", "order_intent", "execution", "lifecycle", "market_segment")
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
_CHUNK = 1 << 20


def _reverse_lines(path: Path, max_bytes: int) -> Iterator[tuple[bytes, bool]]:
    """(line, exhausted_budget) newest first; the trailing partial line (no newline) is skipped."""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        read = 0
        buf = b""
        first = True
        while pos > 0:
            if read >= max_bytes:
                yield b"", True
                return
            step = min(_CHUNK, pos, max(1, max_bytes - read))
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
            read += step
            parts = buf.split(b"\n")
            buf = parts[0]
            tail = parts[1:]
            if first:
                first = False
                if tail and tail[-1] != b"":
                    tail = tail[:-1]  # incomplete last row: being written, not yet a row
            for line in reversed(tail):
                if line.strip():
                    yield line, False
        if buf.strip():
            yield buf, False


# HM R2 #872: explicit {stream: reason} allowlist of retired streams. A stream is IDLE only when it is
# listed here AND every writer module's retirement flag is True. Any other stream with no write since the
# cutoff is RED (NO_WRITE_SINCE_NOT_ALLOWLISTED); any write since the cutoff to a retired stream is RED
# (WRITE_TO_RETIRED_STREAM), stamped or not.
RETIRED_STREAM_ALLOWLIST: dict[str, str] = {"market_segment": "MARKET_SEGMENTS_RETIRED_R17"}
RETIREMENT_FLAGS: dict[str, tuple[tuple[str, str], ...]] = {
    "market_segment": (
        ("research_v3_bridge", "MARKET_SEGMENT_WRITES_RETIRED"),
        ("research_v3_future_paths", "SEGMENT_WRITES_RETIRED_R17"),
        ("recover_archived_future_paths", "SEGMENT_WRITES_RETIRED_R17"),
        ("qualification_horizon_index", "SEGMENT_WRITES_RETIRED_R17"),
    ),
}


def retirement_flags(name: str) -> dict[str, bool | None]:
    """Each writer's flag (None = import failed or flag absent)."""
    import importlib
    out: dict[str, bool | None] = {}
    for module, attr in RETIREMENT_FLAGS.get(name, ()):
        try:
            value = getattr(importlib.import_module(module), attr, None)
        except Exception:  # noqa: BLE001
            value = None
        out[f"{module}.{attr}"] = value if isinstance(value, bool) else None
    return out


def _retired_reason(name: str) -> str | None:
    """Allowlisted AND all writer flags True; anything else = not retired (fail closed)."""
    reason = RETIRED_STREAM_ALLOWLIST.get(name)
    flags = retirement_flags(name)
    if reason is None or not flags or not all(v is True for v in flags.values()):
        return None
    return reason


def generation_paths(ledger_dir: Path, name: str) -> list[Path]:
    """M2: active ``{name}.jsonl`` first, then sealed ``{name}.jsonl.N`` newest (highest N) first."""
    out: list[Path] = []
    active = ledger_dir / f"{name}.jsonl"
    if active.exists():
        out.append(active)
    sealed = []
    prefix = f"{name}.jsonl."
    for cand in ledger_dir.glob(f"{prefix}*"):
        suffix = cand.name[len(prefix):]
        if suffix.isdigit() and int(suffix) > 0 and suffix == str(int(suffix)):
            sealed.append((int(suffix), cand))
    out.extend(p for _, p in sorted(sealed, reverse=True))
    return out


def load_exclusions(path: str | os.PathLike | None) -> set[str]:
    out: set[str] = set()
    if not path:
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                sha = json.loads(line).get("row_sha256")
                if sha:
                    out.add(sha)
    return out


def _new_info() -> dict:
    return {"status": None, "unstamped": 0, "excluded_listed": 0, "stamped": 0, "boundary_reached": False,
            "boundary": None, "legacy_unstamped_before_first_stamp": 0, "unclassified_at_cap": 0,
            "generations_scanned": [], "idle_reason": None, "red_reason": None, "reasons": {}, "sample_row_ids": [],
            "warnings": [], "last_write_ts": None}


def _last_written_at(path: Path):
    """written_at_ts of the file's last row (None if absent/unparseable)."""
    try:
        for line, _ in _reverse_lines(path, 4 * 1024 * 1024):
            row = json.loads(line)
            stamp = row.get(lws.STAMP_KEY) if isinstance(row, dict) else None
            value = stamp.get("written_at_ts") if isinstance(stamp, dict) else None
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    except (OSError, ValueError):
        return None
    return None


# --- Incremental forward scan (HM 25 Oct 06:30 live check) -----------------------------------------------------
# The old reverse scan re-read every unstamped row on every call (market_segment: 18,322 rows; order_intent hit the
# 256 MB cap). The scan is now a forward fold cached per (ledger_dir, stream, since): sealed generations are
# immutable and the active file is append-only, so each call reads only bytes appended since the cached byte offset.
# A call stops at ``time_budget_s`` (~2 s at the route) and resumes on the next call; until the fold reaches EOF the
# stream is AMBER (SCAN_INCOMPLETE_RESUMING), never GREEN. A file rewrite/rotation (inode, head or tail fingerprint
# changes, or size shrinks) drops the cache and refolds. Fold semantics equal the old reverse scan:
#   * a stamped row with written_at_ts < since (or a generation whose mtime < since) is a boundary: reset counts;
#   * unstamped rows before the stream's first stamped row are legacy (only if a stamped row/boundary follows);
#   * unstamped rows after a stamped row, and stamped non-countable rows, count.
import copy
import threading as _threading
import time as _time

DEFAULT_TIME_BUDGET_S = 2.0
_FP_BYTES = 4096
_SCAN_CACHE: dict[tuple, dict] = {}
_SCAN_CACHE_LOCK = _threading.Lock()
_SCAN_CACHE_MAX = 64

# HM 06:30: retired streams split rows written BEFORE retirement (IDLE/RETIRED, reported as pre_retirement_rows)
# from writes after it (RED WRITE_TO_RETIRED_STREAM). market_segment's last row was written 24 Oct 19:20 AEDT,
# before retirement took effect; boundary 2026-10-24T19:21:00+11:00. Unstamped rows carry no write time, so the
# file mtime decides: a generation touched at/after the retirement ts makes every unstamped row in it RED.
RETIRED_SINCE_TS: dict[str, float] = {"market_segment": 1792830060.0}


def _fold_new() -> dict:
    return {"seen_stamped": False, "boundary": None, "pre": [0, {}, []], "cnt": [0, {}, []], "stamped": 0,
            "excluded": 0, "max_written": None}


def _bucket_add(bucket, reason, rid):
    bucket[0] += 1
    bucket[1][reason] = bucket[1].get(reason, 0) + 1
    if len(bucket[2]) < 5:
        bucket[2].append(rid)


def _fold_reset(fold: dict, boundary: str) -> None:
    fold.update(seen_stamped=True, boundary=boundary, pre=[0, {}, []], cnt=[0, {}, []], stamped=0, excluded=0)


def _fold_line(fold: dict, line: bytes, since_ts: float, exclusions: set[str]) -> None:
    try:
        row = json.loads(line)
    except ValueError:
        row = None
    stamp = row.get(lws.STAMP_KEY) if isinstance(row, dict) else None
    written = stamp.get("written_at_ts") if isinstance(stamp, dict) else None
    if isinstance(written, bool) or not isinstance(written, (int, float)):
        written = None
    if written is not None:
        fold["max_written"] = written if fold["max_written"] is None else max(fold["max_written"], written)
        if written < since_ts:
            _fold_reset(fold, "WRITTEN_AT_TS")
            return
        if not fold["seen_stamped"]:
            fold["seen_stamped"] = True
    rid = row.get("record_id") if isinstance(row, dict) else None
    reason = lws.row_non_countable_reason(row)
    if reason is None and written is None:
        reason = "WRITE_STAMP_WRITTEN_AT_TS_MISSING"  # M1
    if reason is None:
        fold["stamped"] += 1
        return
    if exclusions and hashlib.sha256(line.rstrip(b"\r")).hexdigest() in exclusions:
        fold["excluded"] += 1
        return
    _bucket_add(fold["cnt"] if (written is not None or fold["seen_stamped"]) else fold["pre"], reason, rid)


def _fingerprint(path: Path, st, offset: int) -> tuple:
    with path.open("rb") as fh:
        head = hashlib.sha256(fh.read(_FP_BYTES)).hexdigest()
        tail = ""
        if offset > 0:
            fh.seek(max(0, offset - 256))
            tail = hashlib.sha256(fh.read(min(256, offset))).hexdigest()
    return (st.st_dev, st.st_ino, head, tail)


def _plan(name: str, gens: list[Path], since_ts: float, info: dict, unverified: list[str]):
    """Oldest-first list of generations to fold, plus whether an mtime boundary precedes them."""
    stats = []
    for path in gens:
        st = path.stat()  # OSError propagates: unreadable
        stats.append((path, st))
        if st.st_mtime < since_ts:
            tail = _last_written_at(path)
            if tail is None:
                info["warnings"].append(f"MTIME_BOUNDARY_UNCONFIRMED_BY_STAMP:{path.name}")
            elif tail >= since_ts:
                info["warnings"].append(f"MTIME_BOUNDARY_CONTRADICTED_BY_STAMP:{path.name}")
                unverified.append(f"{name}:MTIME_BOUNDARY_AMBIGUOUS:{path.name}")
            return list(reversed(stats[:-1])), True, stats
    return list(reversed(stats)), False, stats


def _scan_ledger(name: str, ledger_dir: Path, since_ts: float, exclusions: set[str], max_bytes: int,
                 unverified: list[str], deadline: float | None = None) -> dict:
    info = _new_info()
    info["scan"] = {"complete": False, "bytes_read_this_call": 0, "cached_offset": 0, "resumed_from_cache": False}
    info["pre_retirement_rows"] = 0
    info["retired_since_ts"] = RETIRED_SINCE_TS.get(name)
    retired = _retired_reason(name)
    try:
        gens = generation_paths(ledger_dir, name)
    except OSError as exc:
        info["red_reason"] = "STREAM_UNREADABLE"  # fail closed
        unverified.append(f"{name}:UNREADABLE:{type(exc).__name__}")
        return info
    if not gens:
        info.update(boundary_reached=True, boundary="NO_FILE")
        info["scan"]["complete"] = True
        if retired:
            info["idle_reason"] = retired
        else:
            info["red_reason"] = "NO_WRITE_SINCE_NOT_ALLOWLISTED"
        return info
    try:
        todo, mtime_boundary, stats = _plan(name, gens, since_ts, info, unverified)
    except OSError as exc:
        info["red_reason"] = "STREAM_UNREADABLE"  # fail closed: unreadable is RED
        unverified.append(f"{name}:UNREADABLE:{type(exc).__name__}")
        return info
    info["generations_scanned"] = [p.name for p, _ in stats]
    newest_mtime = stats[0][1].st_mtime
    info["last_write_ts"] = newest_mtime
    key = (str(ledger_dir), name, float(since_ts), tuple(sorted(exclusions)) if exclusions else ())
    budget = max_bytes
    read_total = 0
    try:
        with _SCAN_CACHE_LOCK:
            cached = _SCAN_CACHE.get(key)
        # cache entry: {"gens": [(name, fingerprint, done_offset)], "fold": {...}, "mtime_boundary": bool}
        names = [p.name for p, _ in todo]
        fold = None
        progress: list = []
        if cached and cached["mtime_boundary"] == mtime_boundary and [g[0] for g in cached["gens"]] == names[:len(cached["gens"])]:
            ok = True
            for (gname, fp, off), (path, st) in zip(cached["gens"], todo):
                if st.st_size < off or _fingerprint(path, st, off) != fp:
                    ok = False
                    break
            if ok:
                fold = copy.deepcopy(cached["fold"])
                progress = [list(g) for g in cached["gens"]]
                info["scan"]["resumed_from_cache"] = True
        if fold is None:
            fold = _fold_new()
            if mtime_boundary:
                _fold_reset(fold, "FILE_MTIME_BEFORE_SINCE")
            progress = []
        complete = True
        for idx, (path, st) in enumerate(todo):
            start = progress[idx][2] if idx < len(progress) else 0
            if idx < len(progress) - 1 and start >= st.st_size:
                continue
            offset = start
            with path.open("rb") as fh:
                fh.seek(offset)
                buf = b""
                while True:
                    if budget <= 0 or (deadline is not None and _time.monotonic() >= deadline):
                        complete = False
                        break
                    chunk = fh.read(min(_CHUNK, budget))
                    if not chunk:
                        break
                    budget -= len(chunk)
                    read_total += len(chunk)
                    buf += chunk
                    lines = buf.split(b"\n")
                    buf = lines.pop()
                    for line in lines:
                        offset += len(line) + 1
                        if line.strip():
                            _fold_line(fold, line, since_ts, exclusions)
                if complete and buf.strip() and idx < len(todo) - 1:
                    # sealed generation without trailing newline: its last row is complete
                    offset += len(buf)
                    _fold_line(fold, buf, since_ts, exclusions)
            entry = [path.name, _fingerprint(path, st, offset), offset]
            if idx < len(progress):
                progress[idx] = entry
            else:
                progress.append(entry)
            if not complete:
                break
        with _SCAN_CACHE_LOCK:
            if key not in _SCAN_CACHE and len(_SCAN_CACHE) >= _SCAN_CACHE_MAX:
                _SCAN_CACHE.pop(next(iter(_SCAN_CACHE)))
            _SCAN_CACHE[key] = {"gens": [tuple(g) for g in progress], "fold": fold, "mtime_boundary": mtime_boundary}
    except OSError as exc:
        info["red_reason"] = "STREAM_UNREADABLE"
        unverified.append(f"{name}:UNREADABLE:{type(exc).__name__}")
        return info
    info["scan"].update(complete=complete, bytes_read_this_call=read_total,
                        cached_offset=progress[-1][2] if progress else 0)
    if fold["boundary"]:
        info.update(boundary_reached=True, boundary=fold["boundary"])
    info["stamped"] = fold["stamped"]
    info["excluded_listed"] = fold["excluded"]
    counted = fold["cnt"]
    legacy = fold["pre"]
    if complete and not fold["seen_stamped"]:
        reasons = dict(legacy[1])
        for k, v in counted[1].items():
            reasons[k] = reasons.get(k, 0) + v
        counted = [counted[0] + legacy[0], reasons, (legacy[2] + counted[2])[:5]]
        legacy = [0, {}, []]
    if not complete:
        info["unclassified_at_cap"] = legacy[0]
        legacy = [0, {}, []]
        unverified.append(f"{name}:SCAN_INCOMPLETE_RESUMING")
    elif not fold["boundary"]:
        info.update(boundary_reached=True, boundary="END_OF_OLDEST_GENERATION")
    info["legacy_unstamped_before_first_stamp"] = legacy[0]
    retired_since = RETIRED_SINCE_TS.get(name)
    if retired and retired_since is not None and not complete:
        return info  # AMBER (resuming); never RED/IDLE on a partial fold of a retired stream
    if retired and retired_since is not None:
        # Split: rows with written_at_ts < retirement, or unstamped rows in files untouched since retirement, are
        # pre-retirement history (IDLE). Any newer stamp, or file touched at/after retirement, is a post-retirement write.
        max_w = fold["max_written"]
        post = (max_w is not None and max_w >= retired_since) or newest_mtime >= retired_since
        if not post:
            info["pre_retirement_rows"] = counted[0] + info["stamped"] + info["legacy_unstamped_before_first_stamp"]
            info["idle_reason"] = retired
            return info
        info["red_reason"] = "WRITE_TO_RETIRED_STREAM"
        _apply_counted(info, counted)
        return info
    _apply_counted(info, counted)
    wrote_since = info["stamped"] > 0 or info["unstamped"] > 0 or newest_mtime >= since_ts
    if retired and wrote_since and complete:
        info["red_reason"] = "WRITE_TO_RETIRED_STREAM"
    elif not wrote_since and complete:
        if retired:
            info["idle_reason"] = retired
        else:
            info["red_reason"] = "NO_WRITE_SINCE_NOT_ALLOWLISTED"
    return info


def _apply_counted(info: dict, counted) -> None:
    info["unstamped"] = counted[0]
    info["reasons"] = dict(counted[1])
    info["sample_row_ids"] = list(counted[2])


def check(data_dir: str | os.PathLike, since_ts: float, *, exclusions: set[str] | None = None,
          max_bytes: int = DEFAULT_MAX_BYTES, time_budget_s: float | None = None) -> dict:
    exclusions = exclusions or set()
    ledger_dir = Path(data_dir) / "v3" / "ledgers"
    per: dict[str, dict] = {}
    unverified: list[str] = []
    t0 = _time.monotonic()
    deadline = t0 + time_budget_s if time_budget_s is not None else None
    for name in LEDGERS:
        n_before = len(unverified)
        info = _scan_ledger(name, ledger_dir, float(since_ts), exclusions, max_bytes, unverified, deadline)
        info["retirement_flags"] = retirement_flags(name)
        if info["unstamped"] > 0 or info["red_reason"]:
            info["status"] = "RED"
        elif len(unverified) > n_before:
            info["status"] = "AMBER"
        elif info["idle_reason"]:
            info["status"] = "IDLE"
        else:
            info["status"] = "GREEN"
        per[name] = info
    count = sum(v["unstamped"] for v in per.values())
    idle = {k: v["idle_reason"] for k, v in per.items() if v["status"] == "IDLE"}
    red_streams = {k: v["red_reason"] for k, v in per.items() if v["red_reason"]}
    status = "RED" if count > 0 or red_streams else ("AMBER" if unverified else ("IDLE" if idle else "GREEN"))
    return {"check": CHECK_ID, "since_ts": float(since_ts), "status": status, "alert": status in ("RED", "AMBER"),
            "unstamped_rows_since": count, "unverified": unverified, "idle_ledgers": idle, "red_streams": red_streams,
            "retired_streams_allowlist": dict(RETIRED_STREAM_ALLOWLIST),
            "retired_since_ts": dict(RETIRED_SINCE_TS),
            "pre_retirement_rows": {k: v["pre_retirement_rows"] for k, v in per.items() if v["pre_retirement_rows"]},
            "scan_complete": all(v["scan"]["complete"] for v in per.values()),
            "scan_elapsed_s": round(_time.monotonic() - t0, 3),
            "warnings": [w for v in per.values() for w in v["warnings"]],
            "legacy_unstamped_before_first_stamp": sum(v["legacy_unstamped_before_first_stamp"] for v in per.values()),
            "boundary_key": "write_stamp.written_at_ts", "ledgers": per}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--since", type=float, required=True)
    ap.add_argument("--exclusion-list")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--time-budget-s", type=float, default=None)
    a = ap.parse_args(argv)
    result = check(a.data_dir, a.since, exclusions=load_exclusions(a.exclusion_list), max_bytes=a.max_bytes, time_budget_s=a.time_budget_s)
    print(json.dumps(result, sort_keys=True))
    return {"GREEN": 0, "RED": 2}.get(result["status"], 3)


if __name__ == "__main__":
    sys.exit(main())
