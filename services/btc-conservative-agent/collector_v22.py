"""collector_v2.2 — write-once immutable research events."""
from __future__ import annotations

import json
import os
import uuid
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
    EVENT_SCHEMA,
    GATE_ADMIN,
    GATE_EXECUTION,
    GATE_RISK,
    GATE_STATE,
    GATE_STRATEGY,
    MAX_ENTRY_WINDOW_SEC,
    MAX_HOLD_PERIOD_SEC,
    OBS_COMPLETE,
    OBS_DATA_ERROR,
    OBS_FUNNEL_COMPLETE,
    OBS_INSUFFICIENT_PATH,
    OBS_PENDING,
    OBS_WAITING_120M,
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

BYTES_PER_EVENT_TYPICAL = 210_000
BYTES_PRE_SIGNAL_CONTEXT_TYPICAL = 117_000

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
    """Multi-TF lookback: 1m/24h, 5m/7d, 15m/30d, 1h/90d."""
    series = {}
    for tf, spec in PRE_SIGNAL_HORIZONS.items():
        start = float(signal_ts) - float(spec["seconds"])
        clipped = [
            row for row in (candles_1m or [])
            if (t := candle_ts_sec(row)) is not None and t >= start - 1e-9
        ]
        if tf == "1m":
            bars = clipped[-int(spec["bars"]):]
        elif tf == "5m":
            bars = resample_candles(clipped, bar_sec=300.0, end_ts=signal_ts, max_bars=int(spec["bars"]))
        elif tf == "15m":
            bars = resample_candles(clipped, bar_sec=900.0, end_ts=signal_ts, max_bars=int(spec["bars"]))
        else:
            bars = resample_candles(clipped, bar_sec=3600.0, end_ts=signal_ts, max_bars=int(spec["bars"]))
        series[tf] = {
            "horizon": spec["label"],
            "bars": len(bars),
            "candles": [[float(row[i]) if i < len(row) else None for i in range(min(6, len(row)))] for row in bars],
        }
    return {
        "schema": PRE_SIGNAL_CONTEXT_SCHEMA,
        "horizon_id": "PRE_SIGNAL_V1",
        "signal_ts": float(signal_ts),
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
            schedule = [{
                "chase_step_index": 0,
                "active_from_ts": float(signal_ts),
                "active_until_ts": float(signal_ts) + float(ttl_sec),
                "limit_price": orig_limit_price(signal_price, direction_u, offset_pct),
                "offset_pct": float(offset_pct),
            }]
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
            children.append({
                "entry_policy_id": f"OFFSET_{offset_pct:.2f}_CHASE_{chase_id}",
                "hypothetical_order_start_ts": float(signal_ts),
                "hypothetical_order_expiry_ts": float(signal_ts) + float(ttl_sec),
                "offset_pct": float(offset_pct),
                "chase_id": chase_id,
                "chase_schedule": schedule,
                "fill_ts": None if fill_ts is None else float(fill_ts),
                "fill_price": None if fill_ts is None else float(chosen.get("fill_price") or 0),
                "fill_model": FILL_MODEL_IDEAL_TOUCH,
                "fill_costs": zero_fill_costs(),
                "post_ttl_touch_ts": obs_ts,
                **labels,
            })
    return children


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
) -> str:
    if primary_outcome == PRIMARY_REJECTED:
        if data_horizon_ts + 1.0 >= tape_end:
            return OBS_FUNNEL_COMPLETE
        return OBS_PENDING
    if primary_outcome == PRIMARY_ACCEPTED_UNFILLED:
        if post_ttl_pending:
            return OBS_PENDING
        return OBS_FUNNEL_COMPLETE
    if live_filled:
        if path_missing:
            return OBS_DATA_ERROR
        if path_complete is not True:
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
) -> dict:
    """Single immutable v2.2 event envelope + canonical 1m tape."""
    direction_u = str(direction or "SHORT").upper()
    event_id = make_event_id(trade_id, signal_ts)
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
    pre_signal = build_pre_signal_context(candles_1m, signal_ts=signal_ts)
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
    if live_filled and path_complete_flag is False and ticket_closed:
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
        )
    path_origin_type = PATH_ORIGIN_SIGNAL
    path_origin_ts = float(signal_ts)
    if live_filled:
        path_origin_type = PATH_ORIGIN_ACTUAL_FILL
        path_origin_ts = float(live_fill_ts)
    tree = dict(decision_tree or build_decision_tree_v22(reason=exact_reason))
    envelope = {
        "event_id": event_id,
        "epoch_id": str(epoch_id),
        "policy_id": POLICY_ID,
        "signal_ts": float(signal_ts),
        "direction": direction_u,
        "primary_outcome": primary,
        "observation_status": obs,
        "path_origin_type": path_origin_type,
        "path_origin_ts": path_origin_ts,
        "path_window_policy_id": PATH_WINDOW_POLICY_ID,
        "collector_version": COLLECTOR_VERSION,
        "control_cell": dict(CONTROL_CELL),
    }
    ticks_bounded = []
    if include_ticks_1s and ticks_1s and live_fill_ts:
        cap = float(live_fill_ts) + MAX_HOLD_PERIOD_SEC
        for tick in ticks_1s:
            t = float(tick.get("t") or 0)
            if t >= float(live_fill_ts) - 1e-9 and t <= cap + 1e-9:
                ticks_bounded.append(tick)
    return {
        "schema": EVENT_SCHEMA,
        "collector_version": COLLECTOR_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "path_schema_version": PATH_SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "event_id": event_id,
        "trade_id": str(trade_id),
        "epoch_id": str(epoch_id),
        "envelope": envelope,
        "decision_tree_snapshot": tree,
        "pre_signal_context": pre_signal,
        "research_horizon": dict(RESEARCH_HORIZON_V1),
        "canonical_tape": {
            "path_1m": path_1m,
            "canonical_tape_start": tape_start,
            "canonical_tape_end": tape_end,
            "path_window_policy_id": PATH_WINDOW_POLICY_ID,
            "replay_status": obs,
            "ticks_1s_optional": ticks_bounded if include_ticks_1s else [],
            "ticks_1s_note": "optional bounded; replay must not require 1s",
        },
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
        "write_once": True,
        "immutable": True,
    }


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


def _save_event_index(path: str, index: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(index, handle, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def event_already_written(event_id: str, *, data_dir: Optional[str] = None) -> bool:
    root = data_dir or os.getcwd()
    index_path = os.path.join(root, EVENT_INDEX_FILE)
    index = _load_event_index(index_path)
    return str(event_id) in (index.get("events") or {})


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
    index_path = os.path.join(root, EVENT_INDEX_FILE)
    index = _load_event_index(index_path)
    if event_id in (index.get("events") or {}):
        return False, "duplicate event_id"
    events_path = os.path.join(root, events_file)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    if "\n" in line:
        raise ValueError("research event must be one JSON line")
    os.makedirs(root, exist_ok=True)
    with open(events_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
    index.setdefault("events", {})[event_id] = {
        "written_at": record.get("envelope", {}).get("signal_ts"),
        "observation_status": record.get("observation_status"),
        "bytes": len(line.encode("utf-8")),
    }
    _save_event_index(index_path, index)
    return True, "written"


def terminal_observation(status: str) -> bool:
    return status in (
        OBS_COMPLETE,
        OBS_FUNNEL_COMPLETE,
        OBS_DATA_ERROR,
        OBS_INSUFFICIENT_PATH,
    )
