"""Pure, fail-closed eligibility checks for collector v2.2 event replay."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from collector_v22_schema import (
    COLLECTOR_VERSION,
    MAX_ENTRY_WINDOW_SEC,
    MAX_HOLD_PERIOD_SEC,
    OBS_COMPLETE,
    OBS_FUNNEL_COMPLETE,
    OBS_PATH_COMPLETE,
)


REPLAY_ELIGIBLE = "REPLAY_ELIGIBLE"
REPLAY_INELIGIBLE = "REPLAY_INELIGIBLE"
LEGACY_PREMATURE = "LEGACY_V22_PREMATURE_FINALIZATION"
_TERMINAL_OBSERVATIONS = {OBS_COMPLETE, OBS_FUNNEL_COMPLETE, OBS_PATH_COMPLETE}
_ONE_MINUTE_SEC = 60.0
_TIMESTAMP_TOLERANCE_SEC = 1e-6


def _finite_timestamp(value: Any) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(timestamp):
        return None
    # Canonical candles currently use milliseconds; tolerate seconds for fixtures
    # and future storage adapters.
    return timestamp / 1000.0 if abs(timestamp) >= 100_000_000_000.0 else timestamp


def _candle_timestamp(row: Any) -> float | None:
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or not row:
        return None
    return _finite_timestamp(row[0])


def _event_signal_timestamp(event: Mapping[str, Any]) -> float | None:
    envelope = event.get("envelope")
    value = envelope.get("signal_ts") if isinstance(envelope, Mapping) else None
    if value is None:
        value = event.get("signal_ts")
    return _finite_timestamp(value)


def _fill_timestamps(event: Mapping[str, Any]) -> tuple[list[float], list[str]]:
    fills: list[float] = []
    reasons: list[str] = []
    live_fill = event.get("live_fill_ts")
    if live_fill is not None:
        parsed = _finite_timestamp(live_fill)
        if parsed is None:
            reasons.append("INVALID_LIVE_FILL_TIMESTAMP")
        else:
            fills.append(parsed)
    children = event.get("entry_children") or []
    if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
        return fills, reasons + ["INVALID_ENTRY_CHILDREN"]
    for index, child in enumerate(children):
        if not isinstance(child, Mapping) or child.get("fill_ts") is None:
            continue
        parsed = _finite_timestamp(child.get("fill_ts"))
        if parsed is None:
            reasons.append(f"INVALID_HYPOTHETICAL_FILL_TIMESTAMP:{index}")
        else:
            fills.append(parsed)
    return fills, reasons


def validate_replay_eligibility(event: Mapping[str, Any]) -> dict:
    """Derive replay eligibility from the tape itself, never lifecycle labels."""
    reasons: list[str] = []
    signal_ts = _event_signal_timestamp(event)
    if signal_ts is None:
        reasons.append("INVALID_SIGNAL_TIMESTAMP")

    tape = event.get("canonical_tape") or {}
    path = tape.get("path_1m") if isinstance(tape, Mapping) else None
    if not isinstance(path, Sequence) or isinstance(path, (str, bytes)) or not path:
        path = []
        reasons.append("MISSING_1M_TAPE")

    timestamps: list[float] = []
    for index, row in enumerate(path):
        timestamp = _candle_timestamp(row)
        if timestamp is None:
            reasons.append(f"INVALID_CANDLE_TIMESTAMP:{index}")
        else:
            timestamps.append(timestamp)

    for index, (previous, current) in enumerate(zip(timestamps, timestamps[1:]), start=1):
        delta = current - previous
        if abs(delta) <= _TIMESTAMP_TOLERANCE_SEC:
            reasons.append(f"DUPLICATE_CANDLE:{index}")
        elif delta < 0:
            reasons.append(f"OUT_OF_ORDER_CANDLE:{index}")
        elif abs(delta - _ONE_MINUTE_SEC) > _TIMESTAMP_TOLERANCE_SEC:
            reasons.append(f"CANDLE_GAP:{index}:{delta:g}s")

    fills, fill_reasons = _fill_timestamps(event)
    reasons.extend(fill_reasons)
    required_end = signal_ts + MAX_ENTRY_WINDOW_SEC if signal_ts is not None else None
    if signal_ts is not None:
        for fill_ts in fills:
            if fill_ts < signal_ts - _TIMESTAMP_TOLERANCE_SEC:
                reasons.append("FILL_BEFORE_SIGNAL")
            if fill_ts > signal_ts + MAX_ENTRY_WINDOW_SEC + _TIMESTAMP_TOLERANCE_SEC:
                reasons.append("FILL_OUTSIDE_SUPPORTED_ENTRY_WINDOW")
            required_end = max(required_end, fill_ts + MAX_HOLD_PERIOD_SEC)

    actual_start = timestamps[0] if timestamps else None
    actual_end = timestamps[-1] + _ONE_MINUTE_SEC if timestamps else None
    if signal_ts is not None and actual_start is not None:
        if actual_start > signal_ts + _TIMESTAMP_TOLERANCE_SEC:
            reasons.append("TAPE_STARTS_AFTER_SIGNAL")
        if actual_end + _TIMESTAMP_TOLERANCE_SEC < signal_ts + MAX_ENTRY_WINDOW_SEC:
            reasons.append("ENTRY_WINDOW_INCOMPLETE")
        for fill_ts in fills:
            if actual_end + _TIMESTAMP_TOLERANCE_SEC < fill_ts + MAX_HOLD_PERIOD_SEC:
                reasons.append(f"HOLD_WINDOW_INCOMPLETE:{fill_ts:g}")

    # Stable ordering makes receipts and golden tests deterministic.
    reasons = list(dict.fromkeys(reasons))
    observation_status = str(event.get("observation_status") or "")
    legacy_premature = bool(
        reasons
        and str(event.get("collector_version") or "") == COLLECTOR_VERSION
        and observation_status in _TERMINAL_OBSERVATIONS
    )
    return {
        "status": REPLAY_INELIGIBLE if reasons else REPLAY_ELIGIBLE,
        "eligible": not reasons,
        "classification": LEGACY_PREMATURE if legacy_premature else None,
        "reasons": reasons,
        "signal_ts": signal_ts,
        "required_entry_end_ts": None if signal_ts is None else signal_ts + MAX_ENTRY_WINDOW_SEC,
        "latest_fill_ts": max(fills) if fills else None,
        "required_tape_end_ts": required_end,
        "actual_first_candle_ts": actual_start,
        "actual_tape_end_ts": actual_end,
        "candle_count": len(timestamps),
    }
