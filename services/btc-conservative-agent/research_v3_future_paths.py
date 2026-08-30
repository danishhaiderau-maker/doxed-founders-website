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
DEFAULT_MAX_BATCH = 8
MATURATION_SETTLE_SEC = 30
SOURCE_TAPE_FILE = "market_microstructure_1s.jsonl"


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
    completed = {
        str(row.get("future_path_owner_key") or "")
        for row in _read_jsonl(ledgers / "market_segment.jsonl")
        if row.get("future_path_status") in {"COMPLETE", "UNKNOWN"}
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
        start = cursor % len(mature)
        selected = (mature[start:] + mature[:start])[:batch_size]
        next_cursor = (start + len(selected)) % len(mature)
    else:
        selected = []
        next_cursor = 0
    request_writes: list[dict[str, Any]] = []
    # Requests are durable before maturation.  They make the denominator and
    # declared horizon explicit even if the process restarts or the tape never
    # becomes available.  Repeated scans are idempotent by record_id.
    request_candidates = selected or valid_candidates[:batch_size]
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
    source_exists = tape.is_file()
    parse_errors = 0
    if source_exists and windows:
        lower = min(start for start, _ in windows.values())
        upper = max(end for _, end in windows.values())
        with tape.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    raw = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    parse_errors += 1
                    continue
                if not isinstance(raw, Mapping):
                    continue
                row = _market_row(raw)
                if row is None:
                    continue
                ts = float(row["ts"])
                if ts < lower or ts > upper:
                    continue
                for key, (start, end) in windows.items():
                    if start <= ts <= end:
                        selected_rows[key][ts] = row
    complete = unknown = 0
    writes: list[dict[str, Any]] = []
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
        rows = [selected_rows[item["owner_key"]][key] for key in sorted(selected_rows[item["owner_key"]])]
        coverage = _coverage(rows, start_ts, end_ts)
        coverage["parse_errors"] = parse_errors
        coverage["required_horizons_sec"] = list(horizons)
        coverage["horizon_mature"] = True
        status = "COMPLETE" if coverage["requested_bounds_complete"] else "UNKNOWN"
        reason = None
        segment_ref = None
        if status == "COMPLETE":
            segment_ref = store.put_market_segment(
                source="LIVE_1S_ALL_OPPORTUNITY_FUTURE_PATH",
                symbol=item["symbol"], timeframe="1s",
                start_ts=start_ts, end_ts=end_ts, rows=rows,
            )
            complete += 1
        else:
            reason = "SOURCE_TAPE_MISSING" if not source_exists else "REQUESTED_HORIZON_INCOMPLETE"
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
        "request_writes": request_writes,
        "writes": writes,
    }
