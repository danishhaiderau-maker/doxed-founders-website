"""collector_v2.2 — write-once immutable research events."""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Mapping, Optional, Sequence

from chase_offset_touch_grid import (
    CHASE_POLICIES,
    LIVE_ORIG_OFFSET_PCT,
    OFFSET_PCT_GRID,
    TTL_SEC_DEFAULT,
    candle_ts_sec,
    orig_limit_price,
    simulate_touch_fill,
)
from collector_v22_schema import (
    COLLECTOR_VERSION,
    CONTROL_TTL_SEC,
    DECISION_TREE_SCHEMA,
    EVAL_DATA_UNAVAILABLE,
    EVAL_FAIL,
    EVAL_NOT_APPLICABLE,
    EVAL_NOT_EVALUATED,
    EVAL_PASS,
    EVENT_INDEX_FILE,
    EVENT_SQLITE_INDEX_FILE,
    EVENT_SCHEMA,
    EPISODE_FALLBACK_WINDOW_SEC,
    EPISODE_SCHEMA,
    GATE_ADMIN,
    GATE_EXECUTION,
    GATE_RISK,
    GATE_STATE,
    GATE_STRATEGY,
    MAX_ENTRY_WINDOW_SEC,
    MAX_HOLD_PERIOD_SEC,
    LIFECYCLE_EVENT_FINALIZED,
    LIFECYCLE_OBSERVATION_APPEND,
    LEGACY_PREMATURE_FINALIZATION,
    OBS_COMPLETE,
    OBS_DATA_ERROR,
    OBS_FUNNEL_COMPLETE,
    OBS_INSUFFICIENT_PATH,
    OBS_PENDING,
    OBS_WAITING_120M,
    OBS_WAITING_ENTRY_WINDOW,
    PATH_ORIGIN_ACTUAL_FILL,
    PATH_ORIGIN_HYPOTHETICAL_FILL,
    PATH_ORIGIN_SIGNAL,
    PATH_WINDOW_POLICY_ID,
    POLICY_ID,
    POST_TTL_LOOKAHEAD_SEC,
    PRE_SIGNAL_CONTEXT_SCHEMA,
    PRE_SIGNAL_HORIZONS,
    PRIMARY_ACCEPTED_FILLED,
    PRIMARY_ACCEPTED_UNFILLED,
    PRIMARY_REJECTED,
    RESEARCH_EVENTS_FILE,
    RESEARCH_HORIZON_V1,
    REPLAY_INELIGIBLE,
    build_policy_identity,
)
from path_replay_v1 import (
    CONTROL_CELL,
    FEATURE_SCHEMA_VERSION,
    FILL_MODEL_IDEAL_TOUCH,
    FILL_MODELS,
    PATH_SCHEMA_VERSION,
    REPLAY_VERSION,
    raw_1m_to_ticks,
    zero_fill_costs,
)
from replay_eligibility import LEGACY_PREMATURE, validate_replay_eligibility
from policy_search_manifest import compact_search_receipt
from microstructure_tape import window_reference as microstructure_window_reference

BYTES_PER_EVENT_TYPICAL = 210_000
BYTES_PRE_SIGNAL_CONTEXT_TYPICAL = 117_000
STANDARD_RESEARCH_NOTIONAL_USD = 2_000.0

GATE_CLASS_BY_FILTER = {
    "ADX": GATE_STRATEGY,
    "RSI": GATE_STRATEGY,
    "STOCH_RSI": GATE_STRATEGY,
    "VOLATILITY": GATE_STRATEGY,
    "EDGE": GATE_STRATEGY,
    "SPREAD": GATE_EXECUTION,
    "COOLDOWN": GATE_EXECUTION,
    "TTL": GATE_EXECUTION,
    "CAPACITY": GATE_EXECUTION,
    "APPROVAL": GATE_ADMIN,
    "DAILY_DRAWDOWN": GATE_RISK,
    "LOSS_STREAK": GATE_RISK,
    "POSITION_STATE": GATE_STATE,
    "WOULD_BLOCK": GATE_STRATEGY,
    "PAUSE": GATE_STATE,
}


def make_event_id(trade_id: str, signal_ts: float) -> str:
    tid = str(trade_id or "").strip()
    if tid:
        return tid
    return f"evt-{uuid.uuid5(uuid.NAMESPACE_URL, f'{signal_ts}').hex[:16]}"


def resample_candles(
    candles_1m: Sequence[Sequence[Any]],
    *,
    bar_sec: float,
    end_ts: float,
    max_bars: int,
) -> list:
    """Bucket 1m candles into coarser bars ending at ``end_ts``."""
    buckets: dict[int, list] = {}
    for row in candles_1m or []:
        t = candle_ts_sec(row)
        if t is None or t > end_ts + 1e-9:
            continue
        key = int(t // bar_sec)
        high = float(row[2])
        low = float(row[3])
        close = float(row[4])
        vol = float(row[5]) if len(row) > 5 else 0.0
        if key not in buckets:
            buckets[key] = [key * bar_sec, close, high, low, close, vol]
        else:
            b = buckets[key]
            b[2] = max(b[2], high)
            b[3] = min(b[3], low)
            b[4] = close
            b[5] = b[5] + vol
    keys = sorted(buckets)[-max_bars:]
    return [buckets[k] for k in keys]


def build_pre_signal_context(
    candles_1m: Sequence[Sequence[Any]],
    *,
    signal_ts: float,
) -> dict:
    """Closed-candle lookback, with signal time in UTC Unix seconds.

    Input OHLCV timestamps are bar-open times (seconds or milliseconds as
    normalized by candle_ts_sec). A maturation write may receive today's
    cache for an older signal: never relabel those future bars as pre-signal.
    Closed market time alone does not prove collector availability or coverage.
    """
    series = {}
    for tf, spec in PRE_SIGNAL_HORIZONS.items():
        start = float(signal_ts) - float(spec["seconds"])
        clipped = [
            row for row in (candles_1m or [])
            if (t := candle_ts_sec(row)) is not None
            and t >= start - 1e-9 and t + 60.0 <= float(signal_ts) + 1e-9
        ]
        if tf == "1m":
            bars = clipped[-int(spec["bars"]):]
        elif tf == "5m":
            bars = resample_candles(clipped, bar_sec=300.0, end_ts=signal_ts, max_bars=int(spec["bars"]))
        elif tf == "15m":
            bars = resample_candles(clipped, bar_sec=900.0, end_ts=signal_ts, max_bars=int(spec["bars"]))
        else:
            bars = resample_candles(clipped, bar_sec=3600.0, end_ts=signal_ts, max_bars=int(spec["bars"]))
        bar_seconds = {"1m": 60.0, "5m": 300.0, "15m": 900.0, "1h": 3600.0}[tf]
        bars = [row for row in bars if (t := candle_ts_sec(row)) is not None
                and t + bar_seconds <= float(signal_ts) + 1e-9]
        series[tf] = {
            "horizon": spec["label"],
            "bars": len(bars),
            "candles": [[float(row[i]) if i < len(row) else None for i in range(min(6, len(row)))] for row in bars],
            "temporal_status": "CLOSED_CANDLES_PRESENT_COVERAGE_UNVERIFIED" if bars else "UNKNOWN_NO_CLOSED_CAUSAL_CANDLES",
            "coverage_complete": False,
        }
    return {
        "schema": PRE_SIGNAL_CONTEXT_SCHEMA,
        "horizon_id": "PRE_SIGNAL_V1",
        "signal_ts": float(signal_ts),
        "temporal_basis": "BAR_OPEN_PLUS_INTERVAL_LE_SIGNAL_UTC_SECONDS",
        "availability_time_verified": False,
        "series": series,
    }


def canonical_tape_bounds(
    *,
    signal_ts: float,
    hypothetical_fill_ts: Optional[float] = None,
    actual_fill_ts: Optional[float] = None,
) -> tuple[float, float]:
    """max(signal + entry_window, latest_valid_hyp_fill + hold)."""
    entry_end = float(signal_ts) + MAX_ENTRY_WINDOW_SEC
    latest_fill = None
    for ts in (hypothetical_fill_ts, actual_fill_ts):
        if ts is not None:
            latest_fill = float(ts) if latest_fill is None else max(latest_fill, float(ts))
    if latest_fill is not None:
        hold_end = latest_fill + MAX_HOLD_PERIOD_SEC
        return float(signal_ts) - MAX_ENTRY_WINDOW_SEC, max(entry_end, hold_end)
    return float(signal_ts) - MAX_ENTRY_WINDOW_SEC, entry_end


def assess_tape_coverage(
    candles_1m: Sequence[Sequence[Any]],
    *,
    signal_ts: float,
    required_end_ts: float,
) -> dict:
    """Independently prove chronological, duplicate-free 1m coverage.

    Maturity begins at the signal, but every row retained in the canonical tape
    is integrity checked so a pre-signal gap cannot pass here and fail replay.
    """
    raw_ts = [candle_ts_sec(row) for row in (candles_1m or [])]
    timestamps = [float(ts) for ts in raw_ts if ts is not None]
    relevant = [ts for ts in timestamps if ts < float(required_end_ts)]
    ordered = all(b > a for a, b in zip(relevant, relevant[1:]))
    duplicate = len(relevant) != len(set(relevant))
    gaps = [
        {"after_ts": a, "before_ts": b, "gap_sec": b - a}
        for a, b in zip(relevant, relevant[1:]) if b - a > 60.0 + 1.0
    ]
    future = [ts for ts in relevant if ts + 60.0 > float(signal_ts) - 1e-9]
    first = future[0] if future else None
    last_end = relevant[-1] + 60.0 if relevant else None
    starts_in_time = first is not None and first <= float(signal_ts) + 1.0 and first + 60.0 > float(signal_ts) - 1.0
    reaches_end = last_end is not None and last_end + 1.0 >= float(required_end_ts)
    eligible = bool(relevant and ordered and not duplicate and not gaps and starts_in_time and reaches_end)
    return {
        "eligible": eligible,
        "required_start_ts": float(signal_ts),
        "required_end_ts": float(required_end_ts),
        "actual_first_ts": first,
        "actual_last_end_ts": last_end,
        "ordered": ordered,
        "duplicate": duplicate,
        "gaps": gaps,
        "reason": "COMPLETE" if eligible else (
            "DUPLICATE_OR_OUT_OF_ORDER" if duplicate or not ordered else
            "CANDLE_GAP" if gaps else "INSUFFICIENT_PATH"
        ),
    }


def event_replay_eligibility(event: Mapping[str, Any]) -> dict:
    """Use the exact same fail-closed receipt consumed by replay/analyzer."""
    result = dict(validate_replay_eligibility(event))
    result["replay_status"] = result.get("status")
    if result.get("classification") == LEGACY_PREMATURE:
        result["integrity_code"] = LEGACY_PREMATURE_FINALIZATION
    return result


def slice_canonical_tape_1m(
    candles_1m: Sequence[Sequence[Any]],
    *,
    tape_start: float,
    tape_end: float,
) -> list:
    out = []
    for row in candles_1m or []:
        t = candle_ts_sec(row)
        if t is None or t + 60.0 < tape_start or t > tape_end + 1.0:
            continue
        out.append([float(row[i]) if i < len(row) else None for i in range(min(6, len(row)))])
    return out


def gate_node_v22(
    name: str,
    *,
    value: Any = None,
    threshold: Any = None,
    evaluation_status: str = EVAL_NOT_EVALUATED,
    gate_class: Optional[str] = None,
    hard_gate: bool = False,
    would_block_only: bool = False,
    note: Optional[str] = None,
) -> dict:
    return {
        "name": str(name),
        "value": value,
        "threshold": threshold,
        "evaluation_status": str(evaluation_status),
        "gate_class": gate_class or GATE_CLASS_BY_FILTER.get(str(name), GATE_STRATEGY),
        "hard_gate": bool(hard_gate),
        "would_block_only": bool(would_block_only),
        "note": note,
    }


def build_decision_tree_v22(
    *,
    reason: Optional[str] = None,
    filters: Optional[Sequence[Mapping[str, Any]]] = None,
    rsi: Any = None,
    rsi_threshold: Any = None,
    adx: Any = None,
    adx_threshold: Any = None,
    atr: Any = None,
    spread: Any = None,
    spread_threshold: Any = None,
    cooldown: Any = None,
    approval: Any = None,
    daily_drawdown: Any = None,
    loss_streak: Any = None,
    position_state: Any = None,
    would_block: Any = None,
    would_block_reason: Any = None,
    vol: Any = None,
    edge: Any = None,
    edge_threshold: Any = None,
    paused: Any = None,
    hard_reject: bool = False,
    short_circuit: bool = False,
) -> dict:
    """Immutable snapshot — STRATEGY gates computed even on short-circuit."""
    by_name = {str(row.get("name")): dict(row) for row in (filters or []) if row}
    defaults = {
        "ADX": gate_node_v22("ADX", value=adx, threshold=adx_threshold,
                            evaluation_status=EVAL_DATA_UNAVAILABLE if adx is None else EVAL_NOT_EVALUATED),
        "RSI": gate_node_v22("RSI", value=rsi, threshold=rsi_threshold,
                             evaluation_status=EVAL_DATA_UNAVAILABLE if rsi is None else EVAL_NOT_EVALUATED,
                             would_block_only=True),
        "STOCH_RSI": gate_node_v22("STOCH_RSI", evaluation_status=EVAL_NOT_EVALUATED, would_block_only=True),
        "VOLATILITY": gate_node_v22("VOLATILITY", value=vol, evaluation_status=EVAL_NOT_EVALUATED),
        "SPREAD": gate_node_v22("SPREAD", value=spread, threshold=spread_threshold, evaluation_status=EVAL_NOT_EVALUATED),
        "COOLDOWN": gate_node_v22("COOLDOWN", value=cooldown, evaluation_status=EVAL_NOT_EVALUATED),
        "APPROVAL": gate_node_v22("APPROVAL", value=approval, evaluation_status=EVAL_NOT_EVALUATED, gate_class=GATE_ADMIN),
        "DAILY_DRAWDOWN": gate_node_v22("DAILY_DRAWDOWN", value=daily_drawdown,
                                        evaluation_status=EVAL_NOT_APPLICABLE, gate_class=GATE_RISK,
                                        note="paper skips DAILY_DRAWDOWN"),
        "LOSS_STREAK": gate_node_v22("LOSS_STREAK", value=loss_streak,
                                    evaluation_status=EVAL_NOT_APPLICABLE, gate_class=GATE_RISK,
                                    note="paper skips LOSS_STREAK"),
        "POSITION_STATE": gate_node_v22("POSITION_STATE", value=position_state,
                                         evaluation_status=EVAL_NOT_EVALUATED, gate_class=GATE_STATE),
        "WOULD_BLOCK": gate_node_v22(
            "WOULD_BLOCK",
            value=would_block_reason or would_block,
            evaluation_status="would_block" if would_block else EVAL_PASS,
            would_block_only=True,
        ),
        "EDGE": gate_node_v22("EDGE", value=edge, threshold=edge_threshold, evaluation_status=EVAL_NOT_EVALUATED),
        "TTL": gate_node_v22("TTL", evaluation_status=EVAL_NOT_EVALUATED, gate_class=GATE_EXECUTION),
        "CAPACITY": gate_node_v22("CAPACITY", evaluation_status=EVAL_NOT_EVALUATED, gate_class=GATE_EXECUTION),
        "PAUSE": gate_node_v22("PAUSE", value=paused, evaluation_status=EVAL_NOT_EVALUATED, gate_class=GATE_STATE),
    }
    nodes = []
    for name, node in defaults.items():
        merged = dict(node)
        if name in by_name:
            merged.update(by_name[name])
            merged["name"] = name
        nodes.append(merged)
    if short_circuit and reason:
        for node in nodes:
            if node["name"] in ("RSI", "ADX", "STOCH_RSI", "VOLATILITY", "EDGE") and node["evaluation_status"] == EVAL_NOT_EVALUATED:
                if node.get("value") is not None:
                    node["evaluation_status"] = EVAL_PASS
    blocking = [
        n["name"] for n in nodes
        if n.get("evaluation_status") == EVAL_FAIL and n.get("hard_gate")
    ]
    return {
        "schema": DECISION_TREE_SCHEMA,
        "exact_reason": None if reason is None else str(reason),
        "hard_reject": bool(hard_reject),
        "blocking_filters": blocking,
        "filters": nodes,
        "short_circuit": bool(short_circuit),
        "note": "STRATEGY feature values persisted even when live short-circuits",
    }


def _fill_labels(fill_ts, signal_ts, ttl_sec, offset_pct, live_orig, chase_id):
    if fill_ts is None:
        return {"fill_window": "NONE", "within_policy": False, "post_ttl_observation": False}
    within_ttl = float(fill_ts) <= float(signal_ts) + float(ttl_sec) + 1e-9
    control_no_chase = abs(float(offset_pct) - float(live_orig)) < 1e-9 and str(chase_id) == "no_chase"
    if within_ttl and control_no_chase:
        return {"fill_window": "WITHIN_TTL", "within_policy": True, "post_ttl_observation": False}
    if within_ttl:
        return {"fill_window": "WITHIN_TTL", "within_policy": True, "post_ttl_observation": False}
    return {
        "fill_window": "POST_TTL",
        "within_policy": False,
        "post_ttl_observation": True,
        "note": "post-TTL touch is path observation, not fill for expired policy",
    }


def build_entry_children(
    *,
    candles_1m: Sequence[Sequence[Any]],
    signal_ts: float,
    signal_price: float,
    direction: str,
    ttl_sec: float = CONTROL_TTL_SEC,
    live_orig: float = LIVE_ORIG_OFFSET_PCT,
    ticks_1s: Optional[Sequence[Mapping[str, Any]]] = None,
) -> list:
    """Hypothetical entry children with chase_schedule metadata."""
    direction_u = str(direction or "SHORT").upper()
    children = []
    alt_ttl = float(ttl_sec) + POST_TTL_LOOKAHEAD_SEC
    for offset_pct in OFFSET_PCT_GRID:
        for policy in CHASE_POLICIES:
            chase_id = policy["id"]
            hit = simulate_touch_fill(
                candles_1m, signal_ts=signal_ts, signal_price=signal_price,
                direction=direction_u, offset_pct=offset_pct, ttl_sec=ttl_sec,
                chase=policy, ticks_1s=ticks_1s,
            )
            chosen = hit
            if not hit.get("touched"):
                chosen = simulate_touch_fill(
                    candles_1m, signal_ts=signal_ts, signal_price=signal_price,
                    direction=direction_u, offset_pct=offset_pct, ttl_sec=alt_ttl,
                    chase=policy, ticks_1s=ticks_1s,
                )
            labels = _fill_labels(
                chosen.get("touch_ts"), signal_ts, ttl_sec, offset_pct, live_orig, chase_id,
            )
            fill_ts = chosen.get("touch_ts") if labels["within_policy"] else None
            obs_ts = chosen.get("touch_ts") if labels["post_ttl_observation"] else None
            schedule = [dict(interval) for interval in (chosen.get("chase_schedule") or [])]
            children.append({
                "entry_policy_id": f"OFFSET_{offset_pct:.2f}_CHASE_{chase_id}",
                "hypothetical_order_start_ts": float(signal_ts),
                "hypothetical_order_expiry_ts": float(signal_ts) + float(ttl_sec),
                "offset_pct": float(offset_pct),
                "chase_id": chase_id,
                "chase_schedule": schedule,
                "chase_schedule_source": "SIMULATOR_ACTUAL_INTERVALS",
                "chase_schedule_policy_end_ts": float(signal_ts) + float(ttl_sec),
                "chase_schedule_note": (
                    "Intervals after policy_end_ts are counterfactual post-TTL observation only; "
                    "they do not represent an active order."
                ),
                "fill_ts": None if fill_ts is None else float(fill_ts),
                "fill_price": None if fill_ts is None else float(chosen.get("fill_price") or 0),
                "fill_model": FILL_MODEL_IDEAL_TOUCH,
                "fill_costs": zero_fill_costs(),
                "post_ttl_touch_ts": obs_ts,
                **labels,
            })
    return children


def make_event_episode(
    *,
    signal_ts: float,
    direction: str,
    symbol: str = "BTCUSD",
    shared_ai_call_id: Optional[str] = None,
) -> dict:
    """Return a stable causal cohort without altering or deduplicating signals.

    A shared upstream AI/scan call is the strongest available causal identity
    across lanes.  When it is absent, signals are grouped only when symbol,
    direction and the same fixed five-minute UTC window agree.  The fixed
    window makes IDs reproducible across restarts; raw events remain distinct.
    """
    direction_u = str(direction or "UNKNOWN").upper()
    symbol_u = str(symbol or "BTCUSD").upper()
    shared = str(shared_ai_call_id or "").strip()
    if shared:
        grouping_basis = "SHARED_AI_CALL"
        causal_key = f"shared:{symbol_u}:{direction_u}:{shared}"
        window_start_ts = None
        window_end_ts = None
    else:
        grouping_basis = "TIME_DIRECTION_SYMBOL_FALLBACK"
        window_start_ts = float(int(float(signal_ts) // EPISODE_FALLBACK_WINDOW_SEC) * EPISODE_FALLBACK_WINDOW_SEC)
        window_end_ts = window_start_ts + EPISODE_FALLBACK_WINDOW_SEC
        causal_key = f"fallback:{symbol_u}:{direction_u}:{int(window_start_ts)}"
    digest = hashlib.sha256(causal_key.encode("utf-8")).hexdigest()[:20]
    return {
        "schema": EPISODE_SCHEMA,
        "event_episode_id": f"episode-{digest}",
        "grouping_basis": grouping_basis,
        "symbol": symbol_u,
        "direction": direction_u,
        "shared_ai_call_id": shared or None,
        "fallback_window_start_ts": window_start_ts,
        "fallback_window_end_ts": window_end_ts,
        "raw_signal_preserved": True,
    }


def classify_primary_outcome(
    *,
    rejected: bool,
    submitted: bool,
    live_filled: bool,
    entry_outcome: Optional[str],
) -> str:
    if rejected or not submitted:
        return PRIMARY_REJECTED
    if live_filled or str(entry_outcome or "") == "FILLED":
        return PRIMARY_ACCEPTED_FILLED
    return PRIMARY_ACCEPTED_UNFILLED


def resolve_observation_status(
    *,
    primary_outcome: str,
    path_complete: Optional[bool],
    path_missing: bool,
    ticket_closed: bool,
    live_filled: bool,
    post_ttl_pending: bool,
    tape_end: float,
    data_horizon_ts: float,
    entry_window_complete: bool = False,
    tape_eligible: bool = False,
) -> str:
    if not entry_window_complete:
        return OBS_WAITING_ENTRY_WINDOW
    if primary_outcome in (PRIMARY_REJECTED, PRIMARY_ACCEPTED_UNFILLED):
        if post_ttl_pending or not tape_eligible:
            return OBS_WAITING_120M
        return OBS_FUNNEL_COMPLETE
    if live_filled:
        if path_missing or path_complete is not True or not tape_eligible:
            return OBS_WAITING_120M if ticket_closed else OBS_PENDING
        return OBS_COMPLETE
    return OBS_PENDING


def build_research_event(
    *,
    trade_id: str,
    epoch_id: str,
    signal_ts: float,
    signal_price: float,
    direction: str = "SHORT",
    candles_1m: Sequence[Sequence[Any]] = (),
    ticks_1s: Optional[Sequence[Mapping[str, Any]]] = None,
    live_orig: float = LIVE_ORIG_OFFSET_PCT,
    ttl_sec: float = TTL_SEC_DEFAULT,
    live_fill_ts: Optional[float] = None,
    live_fill_price: Optional[float] = None,
    ticket_closed: bool = False,
    path_complete: bool = False,
    submitted: bool = True,
    rejected: bool = False,
    decision_tree: Optional[Mapping[str, Any]] = None,
    rsi_at_signal: Any = None,
    would_block: Any = None,
    would_block_reason: Any = None,
    exact_reason: Optional[str] = None,
    atr14_pct: Optional[float] = None,
    invert_on: bool = False,
    include_ticks_1s: bool = False,
    symbol: str = "BTCUSD",
    shared_ai_call_id: Optional[str] = None,
    feature_snapshot: Optional[Mapping[str, Any]] = None,
    evaluation_ts: Optional[float] = None,
    requested_qty: Optional[float] = None,
    research_notional_usd: Optional[float] = STANDARD_RESEARCH_NOTIONAL_USD,
    market_microstructure_symbol: Optional[str] = None,
    chase_schedule: Optional[Sequence[Mapping[str, Any]]] = None,
    chase_schedule_authoritative: bool = False,
    signed_quantity_constraints: Optional[Mapping[str, Any]] = None,
    frozen_signal_snapshot_ref: Optional[Mapping[str, Any]] = None,
    snapshot_data_dir: Optional[str] = None,
) -> dict:
    """Single immutable v2.2 event envelope + canonical 1m tape."""
    direction_u = str(direction or "SHORT").upper()
    raw_direction = (
        "SHORT" if direction_u == "LONG" else "LONG" if direction_u == "SHORT" else direction_u
    ) if invert_on else direction_u
    event_id = make_event_id(trade_id, signal_ts)
    frozen_evidence = None
    if frozen_signal_snapshot_ref is not None:
        from collector_signal_snapshot import load_signal_snapshot
        frozen = load_signal_snapshot(
            frozen_signal_snapshot_ref, data_dir=snapshot_data_dir or os.getcwd(),
            event_id=event_id, epoch_id=epoch_id, signal_ts=signal_ts,
        )
        frozen_evidence = frozen["evidence"]
        feature_snapshot = frozen_evidence["feature_snapshot_at_signal"]
        decision_tree = frozen_evidence["decision_tree_snapshot"]
        rsi_at_signal = frozen_evidence["rsi_at_signal"]
        would_block = frozen_evidence["would_block"]
        would_block_reason = frozen_evidence["would_block_reason"]
        atr14_pct = frozen_evidence["atr14_pct"]
    episode = make_event_episode(
        signal_ts=signal_ts,
        # Episode identity belongs to the causal signal. Inversion is a policy
        # treatment and must not split the same opportunity into a new sample.
        direction=raw_direction,
        symbol=symbol,
        shared_ai_call_id=shared_ai_call_id,
    )
    entry_children = build_entry_children(
        candles_1m=candles_1m,
        signal_ts=signal_ts,
        signal_price=signal_price,
        direction=direction_u,
        ttl_sec=ttl_sec,
        live_orig=live_orig,
        ticks_1s=ticks_1s if include_ticks_1s else None,
    )
    valid_fill_ts = [
        c["fill_ts"] for c in entry_children if c.get("fill_ts") is not None
    ]
    latest_hyp = max(valid_fill_ts) if valid_fill_ts else None
    tape_start, tape_end = canonical_tape_bounds(
        signal_ts=signal_ts,
        hypothetical_fill_ts=latest_hyp,
        actual_fill_ts=live_fill_ts,
    )
    path_1m = slice_canonical_tape_1m(candles_1m, tape_start=tape_start, tape_end=tape_end)
    pre_signal = (frozen_evidence["pre_signal_context"] if frozen_evidence is not None
                  else build_pre_signal_context(candles_1m, signal_ts=signal_ts))
    live_filled = live_fill_ts is not None
    if live_filled and not ticket_closed:
        entry_outcome = "FILLED"
    elif live_filled:
        entry_outcome = "FILLED"
    elif ticket_closed:
        entry_outcome = "TTL_UNFILLED"
    elif rejected:
        entry_outcome = "NOT_SUBMITTED"
    else:
        entry_outcome = "PENDING"
    post_ttl_end = float(signal_ts) + float(ttl_sec) + POST_TTL_LOOKAHEAD_SEC
    horizon_ts = max((candle_ts_sec(r) or 0) + 60.0 for r in (candles_1m or [])) if candles_1m else signal_ts
    post_ttl_pending = (
        entry_outcome == "TTL_UNFILLED" and horizon_ts + 1.0 < post_ttl_end
    )
    path_missing = live_filled and not path_1m
    path_complete_flag = None
    if live_filled:
        if path_1m and path_complete:
            needed_end = float(live_fill_ts) + MAX_HOLD_PERIOD_SEC
            last_bar = max((candle_ts_sec(r) or 0) + 60.0 for r in path_1m) if path_1m else 0
            path_complete_flag = last_bar + 1.0 >= needed_end
        else:
            path_complete_flag = False
    primary = classify_primary_outcome(
        rejected=rejected,
        submitted=submitted,
        live_filled=live_filled,
        entry_outcome=entry_outcome,
    )
    required_end_ts = float(signal_ts) + MAX_ENTRY_WINDOW_SEC
    if latest_hyp is not None:
        required_end_ts = max(required_end_ts, float(latest_hyp) + MAX_HOLD_PERIOD_SEC)
    if live_fill_ts is not None:
        required_end_ts = max(required_end_ts, float(live_fill_ts) + MAX_HOLD_PERIOD_SEC)
    coverage = assess_tape_coverage(
        path_1m, signal_ts=signal_ts, required_end_ts=required_end_ts,
    )
    deadline_reached = max(horizon_ts, float(evaluation_ts or horizon_ts)) + 1.0 >= required_end_ts
    entry_window_complete = horizon_ts + 1.0 >= float(signal_ts) + MAX_ENTRY_WINDOW_SEC
    if deadline_reached and not coverage["eligible"]:
        obs = OBS_INSUFFICIENT_PATH
    elif live_filled and path_complete_flag is False and ticket_closed:
        obs = OBS_WAITING_120M
    else:
        obs = resolve_observation_status(
            primary_outcome=primary,
            path_complete=path_complete_flag,
            path_missing=path_missing,
            ticket_closed=ticket_closed,
            live_filled=live_filled,
            post_ttl_pending=post_ttl_pending,
            tape_end=tape_end,
            data_horizon_ts=horizon_ts,
            entry_window_complete=entry_window_complete,
            tape_eligible=bool(coverage["eligible"]),
        )
    path_origin_type = PATH_ORIGIN_SIGNAL
    path_origin_ts = float(signal_ts)
    if live_filled:
        path_origin_type = PATH_ORIGIN_ACTUAL_FILL
        path_origin_ts = float(live_fill_ts)
    tree = dict(decision_tree or build_decision_tree_v22(reason=exact_reason))
    control_cell = dict(CONTROL_CELL)
    control_cell["invert_on"] = bool(invert_on)
    policy_identity = build_policy_identity(
        epoch_id=str(epoch_id), control_cell=control_cell, invert_on=bool(invert_on),
    )
    exact_requested_qty = None
    try:
        exact_requested_qty = float(requested_qty) if requested_qty is not None else None
    except (TypeError, ValueError):
        exact_requested_qty = None
    if exact_requested_qty is not None and exact_requested_qty <= 0:
        exact_requested_qty = None
    standardized_notional = None
    try:
        standardized_notional = float(research_notional_usd) if research_notional_usd is not None else None
    except (TypeError, ValueError):
        standardized_notional = None
    if standardized_notional is not None and standardized_notional <= 0:
        standardized_notional = None
    if exact_requested_qty is not None:
        research_qty = exact_requested_qty
        qty_provenance = "SOURCE_TICKET_QTY"
        exchange_qty_claim = True
    elif standardized_notional is not None and float(signal_price) > 0:
        research_qty = standardized_notional / float(signal_price)
        qty_provenance = "STANDARDIZED_RESEARCH_NOTIONAL"
        exchange_qty_claim = False
    else:
        research_qty = None
        qty_provenance = "MISSING"
        exchange_qty_claim = False
    execution_basis = {
        "schema": "research_execution_basis_v1",
        "requested_qty": research_qty,
        "requested_qty_provenance": qty_provenance,
        "source_ticket_qty": exact_requested_qty,
        "standardized_notional_usd": standardized_notional if exact_requested_qty is None else None,
        "signal_price": float(signal_price),
        "market_microstructure_symbol": (
            str(market_microstructure_symbol).strip()
            if market_microstructure_symbol else None
        ),
        "exchange_qty_claim": exchange_qty_claim,
        # Preserve the signed venue receipt verbatim. Validation belongs to
        # the conservative evaluator; the collector must not repair, default,
        # or relabel missing exchange constraints.
        "signed_quantity_constraints": (
            dict(signed_quantity_constraints)
            if isinstance(signed_quantity_constraints, Mapping) else None
        ),
        "note": (
            "Exact source ticket quantity" if exchange_qty_claim else
            "Research-only standardized notional; not an exchange quantity claim"
        ),
    }
    schedule_rows = (
        chase_schedule.get("intervals") if isinstance(chase_schedule, Mapping)
        else chase_schedule
    ) or []
    schedule_is_authoritative = bool(
        chase_schedule_authoritative
        or (isinstance(chase_schedule, Mapping) and chase_schedule.get("authoritative") is True)
    )
    serialized_chase_schedule = {
        "schema": "research_chase_schedule_v1",
        "authoritative": schedule_is_authoritative,
        "intervals": [dict(row) for row in schedule_rows if isinstance(row, Mapping)],
    }
    envelope = {
        "event_id": event_id,
        "event_episode_id": episode["event_episode_id"],
        "epoch_id": str(epoch_id),
        "policy_id": POLICY_ID,
        "base_policy_id": POLICY_ID,
        "policy_signature": policy_identity["policy_signature"],
        "policy_epoch_id": policy_identity["policy_epoch_id"],
        "policy_identity": policy_identity,
        "signal_ts": float(signal_ts),
        "direction": direction_u,
        "raw_direction": raw_direction,
        "executed_direction": direction_u,
        "primary_outcome": primary,
        "observation_status": obs,
        "path_origin_type": path_origin_type,
        "path_origin_ts": path_origin_ts,
        "path_window_policy_id": PATH_WINDOW_POLICY_ID,
        "collector_version": COLLECTOR_VERSION,
        "control_cell": control_cell,
        "policy_search": compact_search_receipt(),
        "research_execution_basis": execution_basis,
        "research_chase_schedule": serialized_chase_schedule,
    }
    ticks_bounded = []
    if include_ticks_1s and ticks_1s and live_fill_ts:
        cap = float(live_fill_ts) + MAX_HOLD_PERIOD_SEC
        for tick in ticks_1s:
            t = float(tick.get("t") or 0)
            if t >= float(live_fill_ts) - 1e-9 and t <= cap + 1e-9:
                ticks_bounded.append(tick)
    record = {
        "schema": EVENT_SCHEMA,
        "collector_version": COLLECTOR_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "path_schema_version": PATH_SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "event_id": event_id,
        "event_episode_id": episode["event_episode_id"],
        "event_episode": episode,
        "trade_id": str(trade_id),
        "epoch_id": str(epoch_id),
        "base_policy_id": POLICY_ID,
        "policy_signature": policy_identity["policy_signature"],
        "policy_epoch_id": policy_identity["policy_epoch_id"],
        "policy_identity": policy_identity,
        "envelope": envelope,
        "decision_tree_snapshot": tree,
        "pre_signal_context": pre_signal,
        "research_signal_snapshot_ref": dict(frozen_signal_snapshot_ref) if frozen_signal_snapshot_ref is not None else None,
        "feature_snapshot_at_signal": dict(feature_snapshot or {}),
        "research_horizon": dict(RESEARCH_HORIZON_V1),
        "canonical_tape": {
            "path_1m": path_1m,
            "canonical_tape_start": tape_start,
            "canonical_tape_end": tape_end,
            "path_window_policy_id": PATH_WINDOW_POLICY_ID,
            "replay_status": obs,
            "ticks_1s_optional": ticks_bounded if include_ticks_1s else [],
            "ticks_1s_note": "optional bounded; replay must not require 1s",
            "coverage": coverage,
        },
        "microstructure_window": microstructure_window_reference(
            signal_ts, required_end_ts,
        ),
        "research_execution_basis": execution_basis,
        "research_chase_schedule": serialized_chase_schedule,
        "entry_children": entry_children,
        "primary_outcome": primary,
        "observation_status": obs,
        "live_fill_ts": live_fill_ts,
        "live_fill_price": live_fill_price,
        "live_orig": float(live_orig),
        "ttl_sec": float(ttl_sec),
        "invert_on": bool(invert_on),
        "rsi_at_signal": rsi_at_signal,
        "would_block": would_block,
        "would_block_reason": would_block_reason,
        "atr14_pct": atr14_pct,
        "exact_reason": exact_reason,
        "fill_model": FILL_MODEL_IDEAL_TOUCH,
        "fill_models_supported": list(FILL_MODELS),
        "lifecycle": LIFECYCLE_EVENT_FINALIZED if terminal_observation(obs) else LIFECYCLE_OBSERVATION_APPEND,
        "write_once": terminal_observation(obs),
        "immutable": terminal_observation(obs),
    }
    eligibility = event_replay_eligibility(record)
    record["replay_eligibility"] = eligibility
    record["ranking_eligible"] = bool(eligibility.get("eligible"))
    record["negative_evidence"] = obs in (OBS_INSUFFICIENT_PATH, OBS_DATA_ERROR)
    record["replay_outcomes"] = []
    return record


def _load_event_index(path: str) -> dict:
    if not os.path.isfile(path):
        return {"schema": "research_event_index_v1", "events": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict) and isinstance(raw.get("events"), dict):
            return raw
    except (json.JSONDecodeError, OSError):
        pass
    return {"schema": "research_event_index_v1", "events": {}}


_EVENT_SQLITE_SCHEMA = "research_event_identity_index_v2"


def _event_sqlite_path(root: str) -> str:
    return os.path.join(root, EVENT_SQLITE_INDEX_FILE)


def _event_index_connect(root: str) -> sqlite3.Connection:
    """Open the durable identity index; WAL keeps readers off the writer path."""
    os.makedirs(root, exist_ok=True)
    connection = sqlite3.connect(_event_sqlite_path(root), timeout=30.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS events (
               event_id TEXT PRIMARY KEY,
               generation INTEGER NOT NULL,
               generation_line_number INTEGER NOT NULL,
               global_line_number INTEGER NOT NULL,
               byte_offset INTEGER NOT NULL,
               byte_length INTEGER NOT NULL,
               row_sha256 TEXT NOT NULL,
               written_at TEXT,
               observation_status TEXT
           )"""
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.commit()
    return connection


def _index_meta(connection: sqlite3.Connection) -> dict[str, Any]:
    return {key: json.loads(value) for key, value in connection.execute(
        "SELECT key, value FROM metadata"
    )}


def _set_index_meta(connection: sqlite3.Connection, values: Mapping[str, Any]) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
        [(key, json.dumps(value, separators=(",", ":"), sort_keys=True)) for key, value in values.items()],
    )


def _event_generation_signature(root: str, events_file: str) -> list[list[Any]]:
    signature = []
    for receipt in _load_valid_event_seals(root, events_file, validate_hash=False):
        path = os.path.join(root, str(receipt["relative_path"]))
        stat = os.stat(path)
        signature.append([
            int(receipt["generation"]), str(receipt["sha256"]),
            int(stat.st_size), int(stat.st_mtime_ns),
        ])
    return signature


def _insert_index_line(
    connection: sqlite3.Connection, line: bytes, *, generation: int,
    generation_line_number: int, global_line_number: int, byte_offset: int,
) -> None:
    try:
        row = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"V22_EVENT_INDEX_JSON_INVALID:{generation}:{generation_line_number}") from exc
    if not isinstance(row, dict):
        raise RuntimeError(f"V22_EVENT_INDEX_ROW_INVALID:{generation}:{generation_line_number}")
    event_id = str(row.get("event_id") or row.get("trade_id") or "")
    if not event_id:
        raise RuntimeError(f"V22_EVENT_INDEX_ID_MISSING:{generation}:{generation_line_number}")
    row_sha = hashlib.sha256(line).hexdigest()
    existing = connection.execute(
        "SELECT row_sha256 FROM events WHERE event_id=?", (event_id,)
    ).fetchone()
    if existing:
        if existing[0] != row_sha:
            raise RuntimeError(f"V22_EVENT_ID_CONFLICT:{event_id}")
        return
    connection.execute(
        """INSERT INTO events(event_id,generation,generation_line_number,
               global_line_number,byte_offset,byte_length,row_sha256,written_at,
               observation_status) VALUES (?,?,?,?,?,?,?,?,?)""",
        (event_id, generation, generation_line_number, global_line_number,
         byte_offset, len(line), row_sha,
         json.dumps((row.get("envelope") or {}).get("signal_ts")),
         row.get("observation_status")),
    )


def _rebuild_sqlite_event_index(
    connection: sqlite3.Connection, root: str, events_file: str,
    generation_signature: list[list[Any]],
) -> dict:
    """Maintenance-only exact rebuild; normal appends never enter this path."""
    legacy_path = os.path.join(root, EVENT_INDEX_FILE)
    legacy = _load_event_index(legacy_path)
    legacy_events = legacy.get("events") or {}
    legacy_count = len(legacy_events)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DELETE FROM events")
        global_line = 0
        active_rows = 0
        active_size = 0
        for path in research_event_generation_paths(root, events_file):
            if not os.path.isfile(path):
                continue
            suffix = path[len(os.path.join(root, events_file)) + 1:] if path.startswith(os.path.join(root, events_file) + ".") else "0"
            generation = int(suffix) if suffix.isdigit() else 0
            offset = 0
            with open(path, "rb") as handle:
                for generation_line, line in enumerate(handle, start=1):
                    global_line += 1
                    _insert_index_line(
                        connection, line, generation=generation,
                        generation_line_number=generation_line,
                        global_line_number=global_line, byte_offset=offset,
                    )
                    offset += len(line)
            if generation == 0:
                active_rows = generation_line if offset else 0
                active_size = offset
        exact_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        legacy_coverage_proven = not os.path.isfile(legacy_path)
        if os.path.isfile(legacy_path) and legacy_count == exact_count:
            durable_identities = {
                event_id: row_sha for event_id, row_sha in connection.execute(
                    "SELECT event_id,row_sha256 FROM events"
                )
            }
            legacy_coverage_proven = all(
                event_id in durable_identities
                and str((metadata or {}).get("row_sha256") or "") == durable_identities[event_id]
                for event_id, metadata in legacy_events.items()
            )
        _set_index_meta(connection, {
            "schema": _EVENT_SQLITE_SCHEMA,
            "ready": True,
            "events_file": events_file,
            "generation_signature": generation_signature,
            "active_indexed_bytes": active_size,
            "active_row_count": active_rows,
            "global_row_count": global_line,
            "exact_identity_count": exact_count,
            "legacy_json_preserved": os.path.isfile(legacy_path),
            "legacy_json_event_count": legacy_count,
            "legacy_coverage_proven": legacy_coverage_proven,
        })
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return _index_meta(connection)


def _reconcile_sqlite_event_index(root: str, events_file: str = RESEARCH_EVENTS_FILE) -> tuple[sqlite3.Connection, dict]:
    """Reconcile only an unindexed ACTIVE suffix during steady operation."""
    _recover_event_rotation(root, events_file)
    signature = _event_generation_signature(root, events_file)
    connection = _event_index_connect(root)
    meta = _index_meta(connection)
    active_path = os.path.join(root, events_file)
    active_size = os.path.getsize(active_path) if os.path.isfile(active_path) else 0
    if not (
        meta.get("schema") == _EVENT_SQLITE_SCHEMA
        and meta.get("ready") is True
        and meta.get("events_file") == events_file
        and meta.get("generation_signature") == signature
        and 0 <= int(meta.get("active_indexed_bytes") or 0) <= active_size
    ):
        try:
            meta = _rebuild_sqlite_event_index(connection, root, events_file, signature)
            return connection, meta
        except Exception:
            connection.close()
            raise
    indexed = int(meta.get("active_indexed_bytes") or 0)
    if indexed == active_size:
        return connection, meta
    active_rows = int(meta.get("active_row_count") or 0)
    global_rows = int(meta.get("global_row_count") or 0)
    connection.execute("BEGIN IMMEDIATE")
    try:
        with open(active_path, "rb") as handle:
            handle.seek(indexed)
            offset = indexed
            while line := handle.readline():
                if not line.endswith(b"\n"):
                    raise RuntimeError("V22_EVENT_INDEX_ACTIVE_TAIL_INVALID")
                active_rows += 1
                global_rows += 1
                _insert_index_line(
                    connection, line, generation=0,
                    generation_line_number=active_rows,
                    global_line_number=global_rows, byte_offset=offset,
                )
                offset += len(line)
        _set_index_meta(connection, {
            "active_indexed_bytes": active_size,
            "active_row_count": active_rows,
            "global_row_count": global_rows,
            "exact_identity_count": int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
        })
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection, _index_meta(connection)


_EVENT_WRITER_LOCK = threading.RLock()
_EVENT_SEAL_CACHE_LOCK = threading.Lock()
_EVENT_SEAL_VALIDATION_CACHE: dict[str, tuple[int, int, str]] = {}
_EVENT_MAX_SEALED_GENERATIONS = 1024

_EVENT_SEAL_SCHEMA = "research_event_v22_seal_v1"
_EVENT_ROTATION_SCHEMA = "research_event_v22_rotation_v1"


def _event_seal_dir(root: str) -> str:
    return os.path.join(root, "research_events_v22.seals")


def _event_rotation_path(root: str) -> str:
    return os.path.join(_event_seal_dir(root), "rotation.pending.json")


@contextmanager
def _event_writer_exclusive(root: str):
    """The in-process writer lock plus a process-shared one-byte file lock."""
    lock_path = os.path.join(root, ".research_events_v22.writer.lock")
    os.makedirs(root, exist_ok=True)
    with _EVENT_WRITER_LOCK:
        with open(lock_path, "a+b") as lock_handle:
            lock_handle.seek(0, os.SEEK_END)
            if lock_handle.tell() == 0:
                lock_handle.write(b"0")
                lock_handle.flush()
            lock_handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock_handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _validate_active_event_ledger(path: str, prior_event_ids: set[str]) -> int:
    """Require a complete JSON-object line and globally unique identity per row."""
    seen = set()
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"V22_ROTATION_JSON_INVALID:{line_number}") from exc
            if not isinstance(row, dict):
                raise RuntimeError(f"V22_ROTATION_ROW_INVALID:{line_number}")
            event_id = str(row.get("event_id") or row.get("trade_id") or "")
            if not event_id:
                raise RuntimeError(f"V22_ROTATION_EVENT_ID_MISSING:{line_number}")
            if event_id in seen or event_id in prior_event_ids:
                raise RuntimeError(f"V22_ROTATION_EVENT_ID_DUPLICATE:{line_number}")
            seen.add(event_id)
    return len(seen)


def _atomic_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_parent(path)


def _fsync_parent(path: str) -> None:
    """Best-effort directory durability on Fly/Linux; Windows lacks this primitive."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(os.path.dirname(path) or ".", flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _load_valid_event_seals(
    root: str,
    events_file: str = RESEARCH_EVENTS_FILE,
    *,
    validate_hash: bool = True,
) -> list[dict]:
    """Return only hash-bound, canonical positive numeric sealed generations."""
    seal_dir = _event_seal_dir(root)
    if not os.path.isdir(seal_dir):
        return []
    seals = []
    matching_receipts = 0
    for name in os.listdir(seal_dir):
        if not (name.startswith("generation-") and name.endswith(".json")):
            continue
        matching_receipts += 1
        if matching_receipts > _EVENT_MAX_SEALED_GENERATIONS:
            raise RuntimeError("V22_SEAL_GENERATION_LIMIT_EXCEEDED")
        try:
            generation = int(name[len("generation-"):-len(".json")])
        except ValueError as exc:
            raise RuntimeError("V22_SEAL_RECEIPT_NAME_NONCANONICAL") from exc
        if name != f"generation-{generation}.json":
            raise RuntimeError("V22_SEAL_RECEIPT_NAME_NONCANONICAL")
        if generation <= 0:
            raise RuntimeError("V22_SEAL_RECEIPT_NAME_NONCANONICAL")
        receipt_path = os.path.join(seal_dir, name)
        try:
            with open(receipt_path, encoding="utf-8") as handle:
                receipt = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"V22_SEAL_RECEIPT_INVALID:{generation}") from exc
        expected_name = f"{events_file}.{generation}"
        sealed_path = os.path.join(root, expected_name)
        receipt_sha = str(receipt.get("sha256") or "") if isinstance(receipt, dict) else ""
        if not (
            isinstance(receipt, dict)
            and receipt.get("schema") == _EVENT_SEAL_SCHEMA
            and receipt.get("state") == "SEALED"
            and isinstance(receipt.get("generation"), int)
            and not isinstance(receipt.get("generation"), bool)
            and receipt.get("generation") == generation
            and receipt.get("relative_path") == expected_name
            and isinstance(receipt.get("row_count"), int)
            and receipt.get("row_count") > 0
            and len(receipt_sha) == 64
            and all(char in "0123456789abcdef" for char in receipt_sha)
            and os.path.isfile(sealed_path)
        ):
            raise RuntimeError(f"V22_SEAL_RECEIPT_INVALID:{generation}")
        try:
            size = os.path.getsize(sealed_path)
            if size != int(receipt.get("size_bytes") or -1):
                raise RuntimeError(f"V22_SEAL_INTEGRITY_FAILED:{generation}")
            if validate_hash:
                stat = os.stat(sealed_path)
                cache_value = (size, int(stat.st_mtime_ns), str(receipt.get("sha256") or ""))
                with _EVENT_SEAL_CACHE_LOCK:
                    cached = _EVENT_SEAL_VALIDATION_CACHE.get(sealed_path)
                if cached != cache_value:
                    if _sha256_file(sealed_path) != receipt.get("sha256"):
                        raise RuntimeError(f"V22_SEAL_INTEGRITY_FAILED:{generation}")
                    with _EVENT_SEAL_CACHE_LOCK:
                        _EVENT_SEAL_VALIDATION_CACHE[sealed_path] = cache_value
        except RuntimeError:
            raise
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"V22_SEAL_INTEGRITY_FAILED:{generation}") from exc
        seals.append(receipt)
    return sorted(seals, key=lambda row: int(row["generation"]))


def research_event_generation_paths(root: str, events_file: str = RESEARCH_EVENTS_FILE) -> list[str]:
    """Resolve authorized sealed generations oldest-first, followed by ACTIVE."""
    if os.path.isfile(_event_rotation_path(root)):
        raise RuntimeError("V22_ROTATION_IN_PROGRESS")
    paths = [os.path.join(root, str(row["relative_path"])) for row in _load_valid_event_seals(root, events_file)]
    paths.append(os.path.join(root, events_file))
    return paths


def research_event_generation_stat_signature(
    root: str, events_file: str = RESEARCH_EVENTS_FILE
) -> tuple[tuple[int, int, int], ...]:
    """O(generation-count), byte-scan-free change token for reconciliation polls."""
    rows = []
    for receipt in _load_valid_event_seals(root, events_file, validate_hash=False):
        stat = os.stat(os.path.join(root, str(receipt["relative_path"])))
        rows.append((int(receipt["generation"]), int(stat.st_size), int(stat.st_mtime_ns)))
    active = os.path.join(root, events_file)
    if os.path.isfile(active):
        stat = os.stat(active)
        rows.append((0, int(stat.st_size), int(stat.st_mtime_ns)))
    return tuple(rows)


def _recover_event_rotation(root: str, events_file: str = RESEARCH_EVENTS_FILE) -> Optional[dict]:
    """Finish or roll back the single hash-bound rotation transaction."""
    pending_path = _event_rotation_path(root)
    if not os.path.isfile(pending_path):
        return None
    try:
        with open(pending_path, encoding="utf-8") as handle:
            pending = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("V22_ROTATION_RECEIPT_INVALID") from exc
    generation = int(pending.get("generation") or 0)
    expected_name = f"{events_file}.{generation}"
    if not (
        pending.get("schema") == _EVENT_ROTATION_SCHEMA
        and generation > 0
        and pending.get("relative_path") == expected_name
    ):
        raise RuntimeError("V22_ROTATION_RECEIPT_INVALID")
    active_path = os.path.join(root, events_file)
    sealed_path = os.path.join(root, expected_name)
    expected_size = int(pending.get("size_bytes") or -1)
    expected_sha = str(pending.get("sha256") or "")
    if not os.path.exists(sealed_path):
        # The crash occurred before rename. The unchanged ACTIVE remains authoritative.
        if os.path.isfile(active_path) and os.path.getsize(active_path) == expected_size and _sha256_file(active_path) == expected_sha:
            os.remove(pending_path)
            _fsync_parent(pending_path)
            return {"state": "ROLLED_BACK", "generation": generation}
        raise RuntimeError("V22_ROTATION_SOURCE_MISSING")
    if os.path.getsize(sealed_path) != expected_size or _sha256_file(sealed_path) != expected_sha:
        raise RuntimeError("V22_ROTATION_SEALED_HASH_MISMATCH")
    if not os.path.exists(active_path):
        with open(active_path, "xb") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent(active_path)
    seal = {
        "schema": _EVENT_SEAL_SCHEMA,
        "state": "SEALED",
        "generation": generation,
        "relative_path": expected_name,
        "size_bytes": expected_size,
        "sha256": expected_sha,
        "row_count": int(pending.get("row_count") or 0),
    }
    seal_path = os.path.join(_event_seal_dir(root), f"generation-{generation}.json")
    if os.path.isfile(seal_path):
        with open(seal_path, encoding="utf-8") as handle:
            if json.load(handle) != seal:
                raise RuntimeError("V22_ROTATION_SEAL_CONFLICT")
    else:
        _atomic_json(seal_path, seal)
    os.remove(pending_path)
    _fsync_parent(pending_path)
    return seal


def rotate_research_events(*, data_dir: Optional[str] = None, failpoint: str = "") -> dict:
    """Manually seal ACTIVE under the collector's real writer lock; never deletes."""
    root = data_dir or os.getcwd()
    with _event_writer_exclusive(root):
        recovered = _recover_event_rotation(root)
        if recovered and recovered.get("state") != "ROLLED_BACK":
            return recovered
        active_path = os.path.join(root, RESEARCH_EVENTS_FILE)
        if not os.path.isfile(active_path) or os.path.getsize(active_path) <= 0:
            raise RuntimeError("V22_ROTATION_ACTIVE_EMPTY")
        with open(active_path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                raise RuntimeError("V22_ROTATION_ACTIVE_TAIL_INVALID")
        connection, index_meta = _reconcile_sqlite_event_index(root)
        try:
            prior_event_ids = {
                row[0] for row in connection.execute(
                    "SELECT event_id FROM events WHERE generation > 0"
                )
            }
            row_count = _validate_active_event_ledger(active_path, prior_event_ids)
        finally:
            connection.close()
        seals = _load_valid_event_seals(root)
        generation = (max((int(row["generation"]) for row in seals), default=0) + 1)
        sealed_name = f"{RESEARCH_EVENTS_FILE}.{generation}"
        sealed_path = os.path.join(root, sealed_name)
        if os.path.exists(sealed_path):
            raise RuntimeError("V22_ROTATION_GENERATION_OCCUPIED")
        pending = {
            "schema": _EVENT_ROTATION_SCHEMA,
            "generation": generation,
            "relative_path": sealed_name,
            "size_bytes": os.path.getsize(active_path),
            "sha256": _sha256_file(active_path),
            "row_count": row_count,
        }
        _atomic_json(_event_rotation_path(root), pending)
        if failpoint == "AFTER_PREPARED":
            raise RuntimeError("V22_ROTATION_FAILPOINT_AFTER_PREPARED")
        os.replace(active_path, sealed_path)
        _fsync_parent(sealed_path)
        if failpoint == "AFTER_RENAME":
            raise RuntimeError("V22_ROTATION_FAILPOINT_AFTER_RENAME")
        recovered = _recover_event_rotation(root)
        if failpoint == "AFTER_SEAL":
            raise RuntimeError("V22_ROTATION_FAILPOINT_AFTER_SEAL")
        # Reconciliation after the committed rename is maintenance-only. It
        # rebinds ACTIVE rows to the sealed generation and creates the empty
        # successor authority; normal appends remain O(1).
        connection, _ = _reconcile_sqlite_event_index(root)
        connection.close()
        return recovered or {}


def _scan_durable_event_rows_with_count(
    events_path: str, *, generation: int = 0, line_offset: int = 0
) -> tuple[dict, int]:
    """Rebuild event identity and count in one bounded sequential scan."""
    found = {}
    if not os.path.isfile(events_path):
        return found, 0
    line_count = 0
    with open(events_path, encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            line_count = line_number
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            event_id = str(row.get("event_id") or row.get("trade_id") or "")
            if not event_id:
                continue
            metadata = {
                "written_at": (row.get("envelope") or {}).get("signal_ts"),
                "observation_status": row.get("observation_status"),
                "bytes": len(line.encode("utf-8")),
                "line_number": line_number,
                "generation": generation,
                "generation_line_number": line_number,
                "global_line_number": line_offset + line_number,
                "row_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
            }
            existing = found.get(event_id)
            if existing and existing.get("row_sha256") != metadata["row_sha256"]:
                raise RuntimeError(f"V22_EVENT_ID_CONFLICT:{event_id}")
            found.setdefault(event_id, metadata)
    return found, line_count


def _scan_durable_event_rows(events_path: str, *, generation: int = 0, line_offset: int = 0) -> dict:
    """Compatibility wrapper returning event identity metadata only."""
    return _scan_durable_event_rows_with_count(
        events_path, generation=generation, line_offset=line_offset
    )[0]


def _reconcile_event_index(root: str, events_file: str = RESEARCH_EVENTS_FILE) -> dict:
    """Repair missing, corrupt, or stale indexes from durable JSONL rows."""
    index_path = os.path.join(root, EVENT_INDEX_FILE)
    events_path = os.path.join(root, events_file)
    _recover_event_rotation(root, events_file)
    seals = _load_valid_event_seals(root, events_file, validate_hash=False)
    durable_size = (os.path.getsize(events_path) if os.path.isfile(events_path) else 0) + sum(
        int(row["size_bytes"]) for row in seals
    )
    seal_signature = [row["sha256"] for row in seals]
    seal_stat_signature = []
    for row in seals:
        stat = os.stat(os.path.join(root, str(row["relative_path"])))
        seal_stat_signature.append(
            [int(row["generation"]), int(stat.st_size), int(stat.st_mtime_ns)]
        )
    index = _load_event_index(index_path)
    indexed_size = index.get("events_file_size")
    active_size = os.path.getsize(events_path) if os.path.isfile(events_path) else 0
    if (
        indexed_size is not None
        and int(indexed_size) == durable_size
        and int(index.get("active_file_size") or 0) == active_size
        and index.get("seal_signature") == seal_signature
        and index.get("seal_stat_signature") == seal_stat_signature
        and index.get("active_row_count") is not None
    ):
        return index
    generation_paths = research_event_generation_paths(root, events_file)
    durable = {}
    line_offset = 0
    active_row_count = 0
    for path in generation_paths:
        if not os.path.isfile(path):
            continue
        suffix = path[len(events_path) + 1:] if path.startswith(events_path + ".") else "0"
        generation = int(suffix) if suffix.isdigit() else 0
        rows, row_count = _scan_durable_event_rows_with_count(
            path, generation=generation, line_offset=line_offset
        )
        for event_id, metadata in rows.items():
            existing = durable.get(event_id)
            if existing and existing.get("row_sha256") != metadata.get("row_sha256"):
                raise RuntimeError(f"V22_EVENT_ID_CONFLICT:{event_id}")
            durable.setdefault(event_id, metadata)
        line_offset += row_count
        if generation == 0:
            active_row_count = row_count
    index = {
        "schema": "research_event_index_v1",
        "events_file_size": durable_size,
        "active_file_size": active_size,
        "seal_signature": seal_signature,
        "seal_stat_signature": seal_stat_signature,
        "active_row_count": active_row_count,
        "events": durable,
    }
    os.makedirs(root, exist_ok=True)
    _save_event_index(index_path, index)
    return index


def _save_event_index(path: str, index: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(index, handle, separators=(",", ":"), sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def event_already_written(event_id: str, *, data_dir: Optional[str] = None) -> bool:
    root = data_dir or os.getcwd()
    with _event_writer_exclusive(root):
        connection, _ = _reconcile_sqlite_event_index(root)
        try:
            return connection.execute(
                "SELECT 1 FROM events WHERE event_id=?", (str(event_id),)
            ).fetchone() is not None
        finally:
            connection.close()


def event_index_identity_count(*, data_dir: Optional[str] = None) -> int:
    """Read the durable identity count without mutating an analyzer source tree."""
    root = data_dir or os.getcwd()
    sqlite_path = _event_sqlite_path(root)
    if os.path.isfile(sqlite_path):
        connection = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        finally:
            connection.close()
    return len((_load_event_index(os.path.join(root, EVENT_INDEX_FILE)).get("events") or {}))


def write_research_event_once(
    record: Mapping[str, Any],
    *,
    data_dir: Optional[str] = None,
    events_file: str = RESEARCH_EVENTS_FILE,
) -> tuple[bool, str]:
    """Append exactly once per event_id. Returns (written, reason)."""
    root = data_dir or os.getcwd()
    event_id = str(record.get("event_id") or record.get("trade_id") or "")
    if not event_id:
        return False, "missing event_id"
    status = str(record.get("observation_status") or "")
    eligibility = event_replay_eligibility(record)
    negative_evidence = status in (OBS_INSUFFICIENT_PATH, OBS_DATA_ERROR)
    if not terminal_observation(status) or (not eligibility.get("eligible") and not negative_evidence):
        return False, "provisional or replay-ineligible event"
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    if "\n" in line:
        raise ValueError("research event must be one JSON line")
    encoded = (line + "\n").encode("utf-8")
    candidate_sha = hashlib.sha256(encoded).hexdigest()
    with _event_writer_exclusive(root):
        connection, index_meta = _reconcile_sqlite_event_index(root, events_file)
        existing = connection.execute(
            "SELECT row_sha256 FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if existing:
            connection.close()
            if existing[0] != candidate_sha:
                raise RuntimeError(f"V22_EVENT_ID_CONFLICT:{event_id}")
            return False, "duplicate event_id"
        events_path = os.path.join(root, events_file)
        os.makedirs(root, exist_ok=True)
        with open(events_path, "ab+") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.seek(0, os.SEEK_END)
                    handle.write(b"\n")
            handle.seek(0, os.SEEK_END)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            connection.execute("BEGIN IMMEDIATE")
            active_row = int(index_meta.get("active_row_count") or 0) + 1
            global_row = int(index_meta.get("global_row_count") or 0) + 1
            _insert_index_line(
                connection, encoded, generation=0,
                generation_line_number=active_row,
                global_line_number=global_row,
                byte_offset=os.path.getsize(events_path) - len(encoded),
            )
            _set_index_meta(connection, {
                "active_indexed_bytes": os.path.getsize(events_path),
                "active_row_count": active_row,
                "global_row_count": global_row,
                "exact_identity_count": int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
            })
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise
        connection.close()
        # V3 dual-write is deliberately downstream of the durable v2 append.
        # V2 remains the recovery source during migration; V3 failures are
        # surfaced in a receipt without corrupting or duplicating the source.
        try:
            from research_v3_bridge import dual_write_v22_record
            v3_receipt = dual_write_v22_record(record, data_dir=root)
            v3_reason = "v3-written" if v3_receipt.get("store_verification", {}).get("passed") else "v3-integrity-failed"
        except Exception as exc:
            v3_reason = f"v3-failed:{type(exc).__name__}:{exc}"
        return True, f"written;{v3_reason}"


def terminal_observation(status: str) -> bool:
    return status in (
        OBS_COMPLETE,
        OBS_FUNNEL_COMPLETE,
        OBS_DATA_ERROR,
        OBS_INSUFFICIENT_PATH,
    )
