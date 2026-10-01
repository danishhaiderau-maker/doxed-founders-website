"""Analyzer view of order-multiverse collection health and its quarantine.

Rows finalized with an empty ``canonical_tape.path_1m`` were produced by the
cache-only path source (200 in-memory bars) and carry no replayable path.
They stay on disk untouched; this report marks them
``MULTIVERSE_EMPTY_PATH_COLLECTION_DEFECT`` so no cohort consumes them, and
measures the post-fix stream (rows with ``canonical_tape.path_source``):
empty-path rate, terminal status mix, entry-grid dedupe integrity and
discovery touch-grid coverage of tile calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from typing import Any, Iterable, Iterator, Mapping, Optional

from multiverse_entry_grid import GRID_FILE, rotation_family

SCHEMA = "multiverse_collection_health_v1"
REPORT_FILE = "multiverse_collection_health_report.json"
MULTIVERSE_FILE = "order_multiverse.jsonl"
TOUCH_GRID_FILE = "chase_offset_touch_grid.jsonl"
DEFECT_REASON = "MULTIVERSE_EMPTY_PATH_COLLECTION_DEFECT"
NEVER_RECORDED_REASON = "PATH_SOURCE_NEVER_RECORDED"
NEVER_RECORDED_QUARANTINE_REASON = "MULTIVERSE_PATH_SOURCE_NEVER_RECORDED"
LATE_MATURATION_REASON = "MULTIVERSE_LATE_MATURATION_BACKFILL"
LATE_MATURATION_SEC = 3600.0
EMPTY_PATH_ALARM_RATE = 0.20
TOUCH_GRID_COVERAGE_FLOOR = 0.90
LEGACY_ADMISSION_BASIS = "AI_APPROVE_LEGACY"
_GRID_DIGEST_RE = re.compile(r'"grid_sha256":\s*"([0-9a-f]{64})"')
_CHILDREN_KEY = ', "entry_children": '
_AFTER_CHILDREN_KEY = ', "primary_outcome": '
_FILE_MEMO: dict[tuple, Any] = {}


def _signature(path: str) -> Optional[tuple]:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(path), stat.st_size, stat.st_mtime_ns)


def _lines(path: str) -> Iterator[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    yield line
    except OSError:
        return


def _parse_without_children(line: str) -> Optional[dict]:
    """Parse a v2.2 row, skipping the 300-child array when it can be spliced out."""
    start = line.find(_CHILDREN_KEY)
    if start >= 0:
        end = line.find(_AFTER_CHILDREN_KEY, start)
        if end > start:
            try:
                row = json.loads(line[:start] + line[end:])
            except ValueError:
                row = None
            if isinstance(row, dict) and "canonical_tape" in row and "observation_status" in row \
                    and "entry_children" not in row:
                row["_inline_children"] = True
                return row
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if isinstance(row, dict):
        row["_inline_children"] = isinstance(row.get("entry_children"), list) and bool(row["entry_children"])
        row.pop("entry_children", None)
    return row if isinstance(row, dict) else None


def _compact_row(row: Mapping[str, Any], line_bytes: int) -> dict:
    tape = row.get("canonical_tape") if isinstance(row.get("canonical_tape"), Mapping) else {}
    envelope = row.get("envelope") if isinstance(row.get("envelope"), Mapping) else {}
    episode = row.get("event_episode") if isinstance(row.get("event_episode"), Mapping) else {}
    anchor = row.get("entry_grid_anchor") if isinstance(row.get("entry_grid_anchor"), Mapping) else {}
    ref = row.get("entry_children_ref") if isinstance(row.get("entry_children_ref"), Mapping) else {}
    source = tape.get("path_source") if isinstance(tape.get("path_source"), Mapping) else None
    return {
        "trade_id": str(row.get("trade_id") or ""),
        "epoch_id": str(row.get("epoch_id") or envelope.get("epoch_id") or ""),
        "obs": str(row.get("observation_status") or row.get("lifecycle") or ""),
        "never_recorded": (tape.get("coverage") or {}).get("reason") == NEVER_RECORDED_REASON
        if isinstance(tape.get("coverage"), Mapping) else False,
        "signal_ts": envelope.get("signal_ts"),
        "empty_path": not tape.get("path_1m"),
        "post_fix": source is not None,
        "window_state": (source or {}).get("window_state"),
        "tape_bars": (source or {}).get("tape_bars"),
        "maturation_lag_sec": _maturation_lag(source),
        "late_backfill_marked": bool((source or {}).get("late_backfill")),
        "call_id": str(anchor.get("shared_ai_call_id") or episode.get("shared_ai_call_id") or ""),
        "inline_children": bool(row.get("_inline_children")),
        "grid_ref": str(ref.get("grid_sha256") or "") or None,
        "bytes": int(line_bytes),
    }


def _maturation_lag(source: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Seconds between the path window closing and the row being matured.

    Rows written since the stall fix carry it; older rows derive it from the
    tape head observed at write time (an upper-bound proxy).
    """
    if not source:
        return None
    lag = source.get("maturation_lag_sec")
    if isinstance(lag, (int, float)):
        return float(lag)
    head, end = source.get("tape_latest_bucket_ts"), source.get("window_end_ts")
    if isinstance(head, (int, float)) and isinstance(end, (int, float)):
        return max(0.0, float(head) - float(end))
    return None


def _file_rows(path: str) -> list:
    sig = _signature(path)
    if sig is None:
        return []
    key = ("mv",) + sig
    if key not in _FILE_MEMO:
        rows = []
        for line in _lines(path):
            row = _parse_without_children(line)
            if row is not None:
                rows.append(_compact_row(row, len(line.encode("utf-8", "replace"))))
        _FILE_MEMO[key] = rows
    return _FILE_MEMO[key]


def _file_grid_digests(path: str) -> set:
    sig = _signature(path)
    if sig is None:
        return set()
    key = ("grid",) + sig
    if key not in _FILE_MEMO:
        digests = set()
        for line in _lines(path):
            match = _GRID_DIGEST_RE.search(line)
            if match:
                digests.add(match.group(1))
        _FILE_MEMO[key] = digests
    return _FILE_MEMO[key]


def _file_discovery_calls(path: str) -> dict:
    """shared_ai_call_id -> (tile_admission_basis, signal_ts) for discovery-grid arm rows."""
    sig = _signature(path)
    if sig is None:
        return {}
    key = ("touch",) + sig
    if key not in _FILE_MEMO:
        calls: dict[str, tuple] = {}
        for line in _lines(path):
            if '"discovery_shadow_only": true' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            call = str(row.get("shared_ai_call_id") or "")
            if call:
                signal_ts = row.get("signal_ts")
                calls.setdefault(call, (
                    str(row.get("tile_admission_basis") or LEGACY_ADMISSION_BASIS),
                    float(signal_ts) if isinstance(signal_ts, (int, float)) else None,
                ))
        _FILE_MEMO[key] = calls
    return _FILE_MEMO[key]


def _latest_by_trade(rows: Iterable[dict]) -> list:
    latest: dict[str, dict] = {}
    for row in rows:
        if row["trade_id"]:
            latest[row["trade_id"]] = row
    return list(latest.values())


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def _cohort(rows: list) -> dict:
    written = len(rows)
    empty = sum(1 for row in rows if row["empty_path"])
    return {
        "rows": written,
        "empty_path_rows": empty,
        "empty_path_rate": _rate(empty, written),
        "status_counts": dict(Counter(row["obs"] for row in rows)),
    }


def build_multiverse_collection_report(data_dir: str, *, epoch_id: Optional[str] = None) -> dict:
    rows = []
    for path in rotation_family(data_dir, MULTIVERSE_FILE):
        rows.extend(_file_rows(path))
    rows = _latest_by_trade(rows)
    if epoch_id:
        rows = [row for row in rows if row["epoch_id"] == str(epoch_id)]
    grid_digests: set = set()
    for path in rotation_family(data_dir, GRID_FILE):
        grid_digests |= _file_grid_digests(path)
    discovery: dict[str, str] = {}
    arm_fix_ts: Optional[float] = None
    for path in rotation_family(data_dir, TOUCH_GRID_FILE):
        for call, (basis, signal_ts) in _file_discovery_calls(path).items():
            discovery.setdefault(call, basis)
            if basis != LEGACY_ADMISSION_BASIS and signal_ts is not None:
                arm_fix_ts = signal_ts if arm_fix_ts is None else min(arm_fix_ts, signal_ts)

    defects = [row for row in rows if row["empty_path"] and row["obs"] == "INSUFFICIENT_PATH"]
    defect_ts = [float(row["signal_ts"]) for row in defects if isinstance(row["signal_ts"], (int, float))]
    defect_ids = sorted(row["trade_id"] for row in defects)
    never_recorded = [row for row in rows if row["never_recorded"]]
    post_fix = [row for row in rows if row["post_fix"] and not row["never_recorded"]]
    legacy = [row for row in rows if not row["post_fix"]]
    ref_rows = [row for row in rows if row["grid_ref"]]
    missing_refs = sum(1 for row in ref_rows if row["grid_ref"] not in grid_digests)

    def coverage(cohort: list) -> dict:
        calls = {row["call_id"] for row in cohort if row["call_id"]}
        armed = calls & set(discovery)
        return {
            "tile_calls": len(calls),
            "discovery_grid_calls": len(armed),
            "coverage": _rate(len(armed), len(calls)),
        }

    # Coverage is a property of the call, not of when its row matured: rows
    # finalized after the fix can belong to calls armed by the old code.
    def armed_after_fix(row: dict) -> bool:
        ts = row["signal_ts"]
        return arm_fix_ts is not None and isinstance(ts, (int, float)) and float(ts) >= arm_fix_ts

    coverage_rows = [row for row in rows if not row["never_recorded"]]
    pre_arm_coverage = coverage([row for row in coverage_rows if not armed_after_fix(row)])
    post_cohort = _cohort(post_fix)
    post_coverage = coverage([row for row in coverage_rows if armed_after_fix(row)])
    alarms = []
    if post_cohort["rows"] >= 5 and (post_cohort["empty_path_rate"] or 0) > EMPTY_PATH_ALARM_RATE:
        alarms.append("MULTIVERSE_EMPTY_PATH_RATE_HIGH")
    if missing_refs:
        alarms.append("MULTIVERSE_ENTRY_GRID_REFERENCE_MISSING")
    if post_coverage["tile_calls"] >= 3 and (post_coverage["coverage"] or 0) < TOUCH_GRID_COVERAGE_FLOOR:
        alarms.append("TOUCH_GRID_COVERAGE_LOW")
    return {
        "schema": SCHEMA,
        "epoch_id": epoch_id,
        "status": "ALARM" if alarms else ("NO_POST_FIX_ROWS" if not post_fix else "OK"),
        "alarms": alarms,
        "rows_total": len(rows),
        "rows_by_epoch": dict(Counter(row["epoch_id"] or "UNKNOWN" for row in rows)),
        "legacy_cache_only_rows": _cohort(legacy),
        "post_fix_rows": post_cohort,
        "post_fix_path_source_states": dict(Counter(str(row["window_state"]) for row in post_fix)),
        "post_fix_rows_with_tape_bars": sum(1 for row in post_fix if (row["tape_bars"] or 0) > 0),
        "entry_grid": {
            "inline_rows": sum(1 for row in rows if row["inline_children"]),
            "referenced_rows": len(ref_rows),
            "distinct_grids_referenced": len({row["grid_ref"] for row in ref_rows}),
            "grid_rows_available": len(grid_digests),
            "missing_grid_references": missing_refs,
            "mean_row_bytes_inline": _mean_bytes(row for row in rows if row["inline_children"]),
            "mean_row_bytes_referenced": _mean_bytes(ref_rows),
        },
        "touch_grid_coverage": {
            "cohort_basis": "CALL_SIGNAL_TS_VS_FIRST_ADMISSION_BASIS_ARM",
            "arm_fix_signal_ts": arm_fix_ts,
            "legacy": pre_arm_coverage,
            "post_fix": post_coverage,
            "discovery_calls_by_admission_basis": dict(Counter(discovery.values())),
        },
        "quarantine": {
            "reason": DEFECT_REASON,
            "rows": len(defects),
            "interval_signal_ts": [min(defect_ts), max(defect_ts)] if defect_ts else None,
            "trade_ids_sha256": hashlib.sha256("\n".join(defect_ids).encode("utf-8")).hexdigest()
            if defect_ids else None,
            "trade_ids_sample": defect_ids[:20],
            "action": "EXCLUDED_FROM_COHORTS_SOURCE_ROWS_UNMODIFIED",
        },
        "source_never_recorded": {
            "reason": NEVER_RECORDED_QUARANTINE_REASON,
            "rows": len(never_recorded),
            "status_counts": dict(Counter(row["obs"] for row in never_recorded)),
            "basis": "signal window ended before the 1s tape began; finalized DATA_ERROR, never a research outcome",
            "action": "EXCLUDED_FROM_COHORTS_SOURCE_ROWS_UNMODIFIED",
        },
        "late_maturation": _late_maturation(post_fix),
        "quarantined_trade_ids": defect_ids + sorted(row["trade_id"] for row in never_recorded),
    }


def _late_maturation(rows: list) -> dict:
    late = [
        row for row in rows
        if row["late_backfill_marked"]
        or (row["maturation_lag_sec"] is not None and row["maturation_lag_sec"] > LATE_MATURATION_SEC)
    ]
    late_ts = [float(row["signal_ts"]) for row in late if isinstance(row["signal_ts"], (int, float))]
    ids = sorted(row["trade_id"] for row in late)
    return {
        "reason": LATE_MATURATION_REASON,
        "threshold_sec": LATE_MATURATION_SEC,
        "rows": len(late),
        "marked_rows": sum(1 for row in late if row["late_backfill_marked"]),
        "derived_rows": sum(1 for row in late if not row["late_backfill_marked"]),
        "max_lag_sec": max((row["maturation_lag_sec"] or 0.0) for row in late) if late else None,
        "interval_signal_ts": [min(late_ts), max(late_ts)] if late_ts else None,
        "trade_ids_sample": ids[:20],
        "basis": "path rebuilt from the retained 1s tape after the collector maturation worker stalled; "
                 "content identical to on-time maturation, only its latency differs",
        "action": "LABELLED_NOT_EXCLUDED",
    }


def _mean_bytes(rows: Iterable[dict]) -> Optional[int]:
    sizes = [row["bytes"] for row in rows]
    return int(sum(sizes) / len(sizes)) if sizes else None


def write_report(report: Mapping[str, Any], path: str = REPORT_FILE) -> None:
    payload = {key: value for key, value in report.items() if key != "quarantined_trade_ids"}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
