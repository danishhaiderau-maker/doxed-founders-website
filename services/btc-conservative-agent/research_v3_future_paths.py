"""Asynchronous all-opportunity future market-path maturation.

This module is evidence-only.  It never evaluates a policy, changes a decision,
or waits on the AI/order path.  It joins immutable V3 opportunity and decision
identities to the continuously collected one-second market tape, then writes a
content-addressed segment after the declared horizon has matured.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from research_v3_contract import canonical_json
from research_v3_store import V3EvidenceStore


FUTURE_PATH_SCHEMA = "all_opportunity_future_path_v1"
DEFAULT_REQUIRED_HORIZONS_SEC = (60, 300, 900, 1800, 3600, 7200)
DEFAULT_MAX_BATCH = 64
MATURATION_SETTLE_SEC = 30
SOURCE_TAPE_FILE = "market_microstructure_1s.jsonl"
MAX_TAPE_READ_BYTES = 16 * 1024 * 1024
MAX_TAPE_TOTAL_READ_BYTES = 24 * 1024 * 1024


def _timestamp(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        try:
            number = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return None
    return number if math.isfinite(number) else None


def _contained(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("FUTURE_PATH_OUTSIDE_CANONICAL_ROOT") from exc
    return resolved


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.endswith("\n"):
                    raise ValueError(f"TRUNCATED_JSONL_LINE:{path.name}:{line_no}")
                row = json.loads(line)
                if isinstance(row, Mapping):
                    rows.append(dict(row))
    except FileNotFoundError:
        pass
    return rows


def _owner_key(epoch_id: str, opportunity_id: str, decision_id: str | None) -> str:
    material = canonical_json({
        "epoch_id": str(epoch_id),
        "opportunity_id": str(opportunity_id),
        "decision_id": str(decision_id or "OPPORTUNITY_ONLY"),
    })
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _market_row(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    ts = _timestamp(raw.get("bucket_ts") or raw.get("ts") or raw.get("t"))
    try:
        price = float(raw.get("last") or raw.get("price") or raw.get("mark"))
    except (TypeError, ValueError, OverflowError):
        return None
    if ts is None or not math.isfinite(price) or price <= 0:
        return None
    row = dict(raw)
    row["ts"] = ts
    row["price"] = price
    return row


def _bounded_tape_tail(path: Path, *, max_bytes: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read only a fixed recent tail, aligned to complete JSONL records.

    The production tape is append-only and chronological.  Reading it from
    byte zero every five minutes is unbounded CPU/disk work on the single VM.
    A tail read keeps runtime evidence maturation independent of total history;
    older intervals remain explicitly UNKNOWN for offline/canonical backfill.
    """
    size = path.stat().st_size
    budget = max(1, int(max_bytes))
    start_offset = max(0, size - budget)
    rows: list[dict[str, Any]] = []
    parse_errors = 0
    bytes_read = 0
    with path.open("rb") as handle:
        handle.seek(start_offset)
        if start_offset:
            # Count the discarded partial record too: the receipt's byte bound
            # describes actual disk work, not only successfully parsed lines.
            bytes_read += len(handle.readline())
        aligned_offset = handle.tell()
        for raw_line in handle:
            bytes_read += len(raw_line)
            try:
                raw = json.loads(raw_line.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                parse_errors += 1
                continue
            if not isinstance(raw, Mapping):
                continue
            row = _market_row(raw)
            if row is not None:
                rows.append(row)
    return rows, {
        "schema": "bounded_tape_tail_read_v1",
        "file_size_bytes": size,
        "max_read_bytes": budget,
        "start_offset": aligned_offset,
        "bytes_read": bytes_read,
        "truncated_to_recent_tail": aligned_offset > 0,
        "parse_errors": parse_errors,
        "observed_start_ts": rows[0]["ts"] if rows else None,
        "observed_end_ts": rows[-1]["ts"] if rows else None,
    }


def _rotated_tape_paths(active: Path) -> list[Path]:
    """Return newest-to-oldest tape files without escaping the tape directory."""
    rotated: list[tuple[int, Path]] = []
    for candidate in active.parent.glob(active.name + ".*"):
        suffix = candidate.name[len(active.name) + 1:]
        if suffix.isdigit() and candidate.is_file():
            rotated.append((int(suffix), _contained(active.parent, candidate)))
    # Rotation suffixes increase monotonically; the greatest suffix is newest.
    return ([active] if active.is_file() else []) + [
        path for _suffix, path in sorted(rotated, reverse=True)
    ]


def _bounded_tape_window(
    active: Path, *, required_start_ts: float, max_bytes: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a bounded active+rotated tape horizon, newest to oldest.

    A rotation must not turn otherwise retained evidence into UNKNOWN.  Reads
    stop as soon as the requested lower boundary is present, or at the strict
    aggregate byte ceiling.  Rows are then returned chronologically.
    """
    budget = max(1, int(max_bytes))
    rows_by_ts: dict[float, dict[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    bytes_read = 0
    parse_errors = 0
    boundary_reached = False
    paths = _rotated_tape_paths(active)
    for path in paths:
        remaining = budget - bytes_read
        if remaining <= 0:
            break
        rows, receipt = _bounded_tape_tail(path, max_bytes=remaining)
        receipt = {**receipt, "source_name": path.name}
        receipts.append(receipt)
        bytes_read += int(receipt["bytes_read"])
        parse_errors += int(receipt["parse_errors"])
        for row in rows:
            rows_by_ts[float(row["ts"])] = row
        observed_start = receipt.get("observed_start_ts")
        if observed_start is not None and float(observed_start) <= required_start_ts + 2.0:
            boundary_reached = True
            break
        # A partial tail of this shard cannot safely skip farther backwards:
        # the remaining prefix is older and must be read before an older shard.
        if receipt.get("truncated_to_recent_tail"):
            break
    ordered = [rows_by_ts[key] for key in sorted(rows_by_ts)]
    return ordered, {
        "schema": "bounded_rotated_tape_window_v1",
        "max_read_bytes": budget,
        "bytes_read": bytes_read,
        "parse_errors": parse_errors,
        "source_files_considered": len(paths),
        "source_files_read": len(receipts),
        "source_receipts": receipts,
        "requested_start_boundary_reached": boundary_reached,
        "observed_start_ts": ordered[0]["ts"] if ordered else None,
        "observed_end_ts": ordered[-1]["ts"] if ordered else None,
        "truncated_to_recent_tail": bool(
            not boundary_reached and (
                any(receipt.get("truncated_to_recent_tail") for receipt in receipts)
                or len(receipts) < len(paths) or bytes_read >= budget
            )
        ),
    }


def _coverage(rows: list[dict[str, Any]], start_ts: float, end_ts: float) -> dict[str, Any]:
    rows = sorted(rows, key=lambda row: float(row["ts"]))
    times = [float(row["ts"]) for row in rows]
    gaps = [right - left for left, right in zip(times, times[1:])]
    bbo = 0
    depth = 0
    for row in rows:
        try:
            bid, ask = float(row.get("bid")), float(row.get("ask"))
        except (TypeError, ValueError):
            continue
        if bid <= 0 or ask <= 0 or ask < bid:
            continue
        bbo += 1
        try:
            bid_qty = float(row.get("bid_qty") or row.get("bid_size"))
            ask_qty = float(row.get("ask_qty") or row.get("ask_size"))
        except (TypeError, ValueError):
            continue
        if bid_qty > 0 and ask_qty > 0:
            depth += 1
    complete = bool(times) and times[0] <= start_ts + 2.0 and times[-1] >= end_ts - 2.0
    max_gap = max(gaps) if gaps else None
    return {
        "schema": "all_opportunity_future_path_coverage_v1",
        "requested_start_ts": start_ts,
        "requested_end_ts": end_ts,
        "observed_start_ts": times[0] if times else None,
        "observed_end_ts": times[-1] if times else None,
        "row_count": len(rows),
        "max_gap_sec": max_gap,
        "requested_bounds_complete": complete,
        "two_second_or_better": bool(rows) and (max_gap is None or max_gap <= 2.0),
        "bbo_row_count": bbo,
        "depth_row_count": depth,
        "all_rows_have_valid_bbo": bool(rows) and bbo == len(rows),
        "all_rows_have_visible_depth": bool(rows) and depth == len(rows),
        "conservative_bbo_depth_eligible": bool(
            complete and (max_gap is None or max_gap <= 2.0)
            and bbo == len(rows) and depth == len(rows)
        ),
    }


def _candidates(root: Path, epoch_id: str) -> list[dict[str, Any]]:
    ledgers = _contained(root, root / "v3" / "ledgers")
    opportunities = [
        row for row in _read_jsonl(ledgers / "opportunity.jsonl")
        if str(row.get("epoch_id") or "") == str(epoch_id)
    ]
    decisions_by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(ledgers / "decision.jsonl"):
        if str(row.get("epoch_id") or "") == str(epoch_id):
            decisions_by_episode[str(row.get("episode_id") or "")].append(row)
    market_rows = _read_jsonl(ledgers / "market_segment.jsonl")
    def terminal_or_already_retried(row: Mapping[str, Any]) -> bool:
        status = row.get("future_path_status")
        if status == "COMPLETE":
            return True
        if status != "UNKNOWN":
            return False
        # One bounded migration retry is allowed for the exact historical bug:
        # active-file-only reads could not see a retained rotated interval.
        # A row emitted by the rotated reader is final even when still UNKNOWN.
        return not (
            row.get("unknown_reason") == "SOURCE_INTERVAL_OUTSIDE_BOUNDED_TAIL"
            and (row.get("bounded_tape_read") or {}).get("schema")
            != "bounded_rotated_tape_window_v1"
        )

    completed = {
        str(row.get("future_path_owner_key") or "")
        for row in market_rows
        if terminal_or_already_retried(row)
    }
    requested = {
        str(row.get("future_path_owner_key") or "")
        for row in market_rows
        if row.get("future_path_status") == "PENDING"
    }
    result: list[dict[str, Any]] = []
    seen_decisions: set[str] = set()
    for opportunity in opportunities:
        opportunity_id = str(opportunity.get("record_id") or opportunity.get("opportunity_id") or "")
        episode_id = str(opportunity.get("episode_id") or "")
        signal_ts = _timestamp(opportunity.get("signal_ts"))
        if not opportunity_id or not episode_id:
            continue
        decisions = decisions_by_episode.get(episode_id) or [None]
        for decision in decisions:
            decision_id = str((decision or {}).get("record_id") or "") or None
            if decision_id:
                seen_decisions.add(decision_id)
            owner = _owner_key(epoch_id, opportunity_id, decision_id)
            if owner in completed:
                continue
            result.append({
                "owner_key": owner,
                "epoch_id": str(epoch_id),
                "opportunity_id": opportunity_id,
                "episode_id": episode_id,
                "decision_id": decision_id,
                "event_id": (decision or {}).get("event_id"),
                "shared_ai_call_id": opportunity.get("shared_ai_call_id"),
                "symbol": str(opportunity.get("symbol") or "BTCUSD"),
                "signal_ts": signal_ts,
                "decision_outcome": (decision or {}).get("primary_outcome"),
                "request_exists": owner in requested,
                "identity_unknown_reason": (
                    "SIGNAL_TIMESTAMP_MISSING" if signal_ts is None else None
                ),
            })
    for decisions in decisions_by_episode.values():
        for decision in decisions:
            decision_id = str(decision.get("record_id") or "")
            if not decision_id or decision_id in seen_decisions:
                continue
            owner = _owner_key(epoch_id, "UNKNOWN", decision_id)
            if owner in completed:
                continue
            result.append({
                "owner_key": owner,
                "epoch_id": str(epoch_id),
                "opportunity_id": None,
                "episode_id": decision.get("episode_id"),
                "decision_id": decision_id,
                "event_id": decision.get("event_id"),
                "shared_ai_call_id": None,
                "symbol": "BTCUSD",
                "signal_ts": None,
                "decision_outcome": decision.get("primary_outcome"),
                "request_exists": owner in requested,
                "identity_unknown_reason": "OPPORTUNITY_IDENTITY_MISSING",
            })
    return sorted(result, key=lambda row: (
        row.get("signal_ts") is None,
        float(row.get("signal_ts") or 0),
        row["owner_key"],
    ))


def _cursor_path(root: Path) -> Path:
    return _contained(root, root / "v3" / "receipts" / "future-path-cursor.json")


def _load_cursor(root: Path, epoch_id: str) -> int:
    try:
        payload = json.loads(_cursor_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return 0
    if str(payload.get("epoch_id") or "") != str(epoch_id):
        return 0
    try:
        return max(0, int(payload.get("cursor") or 0))
    except (TypeError, ValueError):
        return 0


def _store_cursor(root: Path, epoch_id: str, cursor: int) -> None:
    target = _cursor_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json({
        "schema": "all_opportunity_future_path_cursor_v1",
        "epoch_id": str(epoch_id),
        "cursor": max(0, int(cursor)),
    }).encode("utf-8")
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def mature_future_market_paths(
    *, data_dir: str | Path, epoch_id: str, now_ts: float,
    required_horizons_sec: Iterable[int] = DEFAULT_REQUIRED_HORIZONS_SEC,
    max_batch: int = DEFAULT_MAX_BATCH,
    max_tape_read_bytes: int = MAX_TAPE_TOTAL_READ_BYTES,
) -> dict[str, Any]:
    """Mature a bounded batch; absent evidence remains PENDING/UNKNOWN."""
    root = Path(data_dir).resolve()
    tape = _contained(root, root / SOURCE_TAPE_FILE)
    horizons = tuple(sorted({int(value) for value in required_horizons_sec if int(value) > 0}))
    if not horizons:
        raise ValueError("FUTURE_PATH_HORIZONS_REQUIRED")
    maximum = horizons[-1]
    candidates = _candidates(root, str(epoch_id))
    store = V3EvidenceStore(root, epoch_id=str(epoch_id))
    identity_unknown = [row for row in candidates if row.get("identity_unknown_reason")]
    valid_candidates = [row for row in candidates if not row.get("identity_unknown_reason")]
    mature = [
        row for row in valid_candidates
        if float(now_ts) >= row["signal_ts"] + maximum + MATURATION_SETTLE_SEC
    ]
    batch_size = max(1, int(max_batch))
    cursor = _load_cursor(root, str(epoch_id))
    if mature:
        # Candidates are oldest-first.  Always drain the oldest retained
        # horizons first: an index cursor is unsafe because the candidate list
        # shrinks after every terminal write and can skip unprocessed rows.
        selected = mature[:batch_size]
        next_cursor = cursor + len(selected)
    else:
        selected = []
        next_cursor = 0
    request_writes: list[dict[str, Any]] = []
    # Requests are durable before maturation.  They make the denominator and
    # declared horizon explicit even if the process restarts or the tape never
    # becomes available.  Repeated scans are idempotent by record_id.
    request_candidates = []
    request_seen: set[str] = set()
    for item in selected + [row for row in valid_candidates if not row.get("request_exists")]:
        if item["owner_key"] in request_seen or item.get("request_exists"):
            continue
        request_candidates.append(item)
        request_seen.add(item["owner_key"])
        if len(request_candidates) >= batch_size:
            break
    for item in request_candidates:
        request_writes.append(store.append("market_segment", {
            "record_id": f"future-path-request:{item['owner_key']}",
            "episode_id": item["episode_id"],
            "event_id": item["event_id"],
            "opportunity_id": item["opportunity_id"],
            "decision_id": item["decision_id"],
            "shared_ai_call_id": item["shared_ai_call_id"],
            "future_path_owner_key": item["owner_key"],
            "segment_role": "SIGNAL_TO_120M_FUTURE_PATH_REQUEST",
            "requested_horizons_sec": list(horizons),
            "requested_start_ts": item["signal_ts"],
            "requested_end_ts": item["signal_ts"] + maximum,
            "future_path_status": "PENDING",
            "evidence_only": True,
        }))
    pending_count = len(valid_candidates) - len(mature)
    windows = {
        row["owner_key"]: (row["signal_ts"], row["signal_ts"] + maximum)
        for row in selected
    }
    selected_rows: dict[str, dict[float, dict[str, Any]]] = {
        key: {} for key in windows
    }
    source_exists = bool(_rotated_tape_paths(tape))
    parse_errors = 0
    tail_read = {
        "schema": "bounded_tape_tail_read_v1",
        "file_size_bytes": 0,
        "max_read_bytes": int(max_tape_read_bytes),
        "start_offset": 0,
        "bytes_read": 0,
        "truncated_to_recent_tail": False,
        "parse_errors": 0,
        "observed_start_ts": None,
        "observed_end_ts": None,
    }
    if source_exists and windows:
        lower = min(start for start, _ in windows.values())
        upper = max(end for _, end in windows.values())
        tape_rows, tail_read = _bounded_tape_window(
            tape, required_start_ts=lower, max_bytes=max_tape_read_bytes,
        )
        parse_errors = int(tail_read["parse_errors"])
        for row in tape_rows:
            ts = float(row["ts"])
            if ts < lower or ts > upper:
                continue
            for key, (start, end) in windows.items():
                if start <= ts <= end:
                    selected_rows[key][ts] = row
    complete = unknown = 0
    writes: list[dict[str, Any]] = []
    # A shared opportunity commonly has one decision per paper family. Reuse
    # the identical immutable window calculation/object rather than hashing
    # and serializing the same 120-minute tape dozens of times.
    window_cache: dict[tuple[str, float, float], tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]] = {}
    for item in identity_unknown[:batch_size]:
        writes.append(store.append("market_segment", {
            "record_id": f"future-path:{item['owner_key']}:unknown",
            "episode_id": item["episode_id"],
            "event_id": item["event_id"],
            "opportunity_id": item["opportunity_id"],
            "decision_id": item["decision_id"],
            "future_path_owner_key": item["owner_key"],
            "segment_role": "SIGNAL_TO_120M_FUTURE_PATH",
            "requested_horizons_sec": list(horizons),
            "requested_start_ts": None,
            "requested_end_ts": None,
            "future_path_status": "UNKNOWN",
            "unknown_reason": item["identity_unknown_reason"],
            "segment_ref": None,
            "evidence_only": True,
        }))
        unknown += 1
    for item in selected:
        start_ts, end_ts = windows[item["owner_key"]]
        cache_key = (item["symbol"], start_ts, end_ts)
        cached = window_cache.get(cache_key)
        if cached is None:
            rows = [selected_rows[item["owner_key"]][key] for key in sorted(selected_rows[item["owner_key"]])]
            coverage = _coverage(rows, start_ts, end_ts)
            coverage["parse_errors"] = parse_errors
            coverage["required_horizons_sec"] = list(horizons)
            coverage["horizon_mature"] = True
            cached = (rows, coverage, None)
            window_cache[cache_key] = cached
        rows, coverage, cached_segment_ref = cached
        status = "COMPLETE" if coverage["requested_bounds_complete"] else "UNKNOWN"
        reason = None
        segment_ref = None
        if status == "COMPLETE":
            segment_ref = cached_segment_ref
            if segment_ref is None:
                segment_ref = store.put_market_segment(
                    source="LIVE_1S_ALL_OPPORTUNITY_FUTURE_PATH",
                    symbol=item["symbol"], timeframe="1s",
                    start_ts=start_ts, end_ts=end_ts, rows=rows,
                )
                window_cache[cache_key] = (rows, coverage, segment_ref)
            complete += 1
        else:
            tail_start = tail_read.get("observed_start_ts")
            reason = (
                "SOURCE_TAPE_MISSING" if not source_exists
                else "SOURCE_INTERVAL_OUTSIDE_BOUNDED_TAIL"
                if tail_read.get("truncated_to_recent_tail") and tail_start is not None
                and start_ts < float(tail_start) - 2.0
                else "REQUESTED_HORIZON_INCOMPLETE"
            )
            unknown += 1
        suffix = segment_ref["sha256"] if segment_ref else "unknown"
        write = store.append("market_segment", {
            "record_id": f"future-path:{item['owner_key']}:{suffix}",
            "episode_id": item["episode_id"],
            "event_id": item["event_id"],
            "opportunity_id": item["opportunity_id"],
            "decision_id": item["decision_id"],
            "shared_ai_call_id": item["shared_ai_call_id"],
            "future_path_owner_key": item["owner_key"],
            "segment_role": "SIGNAL_TO_120M_FUTURE_PATH",
            "requested_horizons_sec": list(horizons),
            "requested_start_ts": start_ts,
            "requested_end_ts": end_ts,
            "future_path_status": status,
            "unknown_reason": reason,
            "decision_outcome": item["decision_outcome"],
            "segment_ref": segment_ref,
            "coverage": coverage,
            "bounded_tape_read": tail_read,
            "evidence_only": True,
        })
        writes.append(write)
    _store_cursor(root, str(epoch_id), next_cursor)
    return {
        "schema": FUTURE_PATH_SCHEMA,
        "epoch_id": str(epoch_id),
        "now_ts": float(now_ts),
        "required_horizons_sec": list(horizons),
        "candidate_count": len(candidates),
        "pending_count": pending_count,
        "mature_selected": len(selected),
        "cursor": next_cursor,
        "complete_count": complete,
        "unknown_count": unknown,
        "source_tape_present": source_exists,
        "bounded_tape_read": tail_read,
        "request_writes": request_writes,
        "writes": writes,
    }
