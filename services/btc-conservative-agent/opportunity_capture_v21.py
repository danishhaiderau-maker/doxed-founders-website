"""collector_v2.1 Opportunity Capture — four cohorts, never mixed.

Every candidate (accepted or rejected) leaves a counterfactual envelope:

  SIGNAL → ACCEPTED (fill | never fill) | REJECTED/BLOCKED
           + exact reason + filter tree + subsequent market path from signal_ts.

Cohorts (do not mix denominators):
  A ACTUAL_FILLED        — real paper positions (CONTROL performance)
  B SUBMITTED_UNFILLED   — order submitted, TTL/never filled (entry funnel)
  C REJECTED_SIGNAL      — signal existed, bot did not submit
  D HYPOTHETICAL_FILLED  — alternative offset/chase WOULD have filled on tape

Terminology (never mix):
  actual_trade / hypothetical_fill / counterfactual_position /
  rejected_opportunity / unfilled_order
"""
from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any, Iterable, Mapping, Optional, Sequence

from chase_offset_touch_grid import (
    LIVE_ORIG_OFFSET_PCT,
    orig_limit_price,
    simulate_touch_fill,
)
from order_multiverse import (
    HOLD_SEC_DEFAULT,
    ORDER_MULTIVERSE_FILE,
    POST_TTL_LOOKAHEAD_SEC,
    allows_exit_expectancy,
    candles_to_path_ticks,
)
from path_replay_v1 import (
    COLLECTOR_VERSION,
    CONTROL_CELL,
    FEATURE_SCHEMA_VERSION,
    FILL_MODEL_IDEAL_TOUCH,
    FILL_MODELS,
    LIVE_THESIS_CUT,
    PATH_SCHEMA_VERSION,
    REPLAY_VERSION,
    mfe_mae_trajectory,
    path_recovery_stats,
    stage1_replay,
    zero_fill_costs,
)


OPPORTUNITY_CAPTURE_SCHEMA = "opportunity_capture_v2.1"
OPPORTUNITY_CAPTURE_FILE = "opportunity_capture.jsonl"
OPPORTUNITY_CAPTURE_REPORT_FILE = "collector_v21_opportunity_capture.json"

COHORT_ACTUAL_FILLED = "ACTUAL_FILLED"
COHORT_SUBMITTED_UNFILLED = "SUBMITTED_UNFILLED"
COHORT_REJECTED_SIGNAL = "REJECTED_SIGNAL"
COHORT_HYPOTHETICAL_FILLED = "HYPOTHETICAL_FILLED"
COHORTS = (
    COHORT_ACTUAL_FILLED,
    COHORT_SUBMITTED_UNFILLED,
    COHORT_REJECTED_SIGNAL,
    COHORT_HYPOTHETICAL_FILLED,
)

KIND_ACTUAL_TRADE = "actual_trade"
KIND_UNFILLED_ORDER = "unfilled_order"
KIND_REJECTED_OPPORTUNITY = "rejected_opportunity"
KIND_HYPOTHETICAL_FILL = "hypothetical_fill"
KIND_COUNTERFACTUAL_POSITION = "counterfactual_position"

FILTER_NAMES = (
    "ADX",
    "RSI",
    "STOCH_RSI",
    "VOLATILITY",
    "SPREAD",
    "COOLDOWN",
    "APPROVAL",
    "DAILY_DRAWDOWN",
    "LOSS_STREAK",
    "POSITION_STATE",
    "WOULD_BLOCK",
    "EDGE",
    "TTL",
    "CAPACITY",
    "PAUSE",
)


def empty_cohort_stats() -> dict:
    return {
        "n": 0,
        "n_would_fill": 0,
        "p_would_fill": None,
        "e_pnl_if_hyp_fill": None,
        "n_scored_hyp": 0,
        "reasons": {},
        "note": "empty epoch — zeros, not a crash",
    }


def filter_node(
    name: str,
    *,
    value: Any = None,
    threshold: Any = None,
    result: str = "not_evaluated",
    hard_gate: bool = False,
    would_block_only: bool = False,
) -> dict:
    """One evaluated filter. result: pass | fail | would_block | skipped | not_evaluated."""
    return {
        "name": str(name),
        "value": value,
        "threshold": threshold,
        "result": str(result),
        "hard_gate": bool(hard_gate),
        "would_block_only": bool(would_block_only),
    }


def build_decision_tree(
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
) -> dict:
    """Persist every named filter even when not evaluated — replay needs the tree."""
    by_name = {str(row.get("name")): dict(row) for row in (filters or []) if row}
    defaults = {
        "ADX": filter_node("ADX", value=adx, threshold=adx_threshold),
        "RSI": filter_node("RSI", value=rsi, threshold=rsi_threshold, would_block_only=True),
        "STOCH_RSI": filter_node("STOCH_RSI", would_block_only=True),
        "VOLATILITY": filter_node("VOLATILITY", value=vol),
        "SPREAD": filter_node("SPREAD", value=spread, threshold=spread_threshold),
        "COOLDOWN": filter_node("COOLDOWN", value=cooldown),
        "APPROVAL": filter_node("APPROVAL", value=approval),
        "DAILY_DRAWDOWN": {**filter_node("DAILY_DRAWDOWN", value=daily_drawdown, result="skipped"), "note": "paper skips DAILY_DRAWDOWN"},
        "LOSS_STREAK": {**filter_node("LOSS_STREAK", value=loss_streak, result="skipped"), "note": "paper skips LOSS_STREAK"},
        "POSITION_STATE": filter_node("POSITION_STATE", value=position_state),
        "WOULD_BLOCK": filter_node(
            "WOULD_BLOCK",
            value=would_block_reason or would_block,
            result="would_block" if would_block else "pass",
            hard_gate=False,
            would_block_only=True,
        ),
        "EDGE": filter_node("EDGE", value=edge, threshold=edge_threshold),
        "TTL": filter_node("TTL"),
        "CAPACITY": filter_node("CAPACITY"),
        "PAUSE": filter_node("PAUSE", value=paused),
    }
    nodes = []
    for name in FILTER_NAMES:
        node = dict(defaults[name])
        if name in by_name:
            node.update(by_name[name])
            node["name"] = name
        nodes.append(node)
    blocking = [
        node["name"] for node in nodes
        if node.get("result") == "fail" and node.get("hard_gate")
    ]
    return {
        "schema": "decision_tree_v2.1",
        "exact_reason": None if reason is None else str(reason),
        "hard_reject": bool(hard_reject),
        "blocking_filters": blocking,
        "filters": nodes,
        "rsi_live_veto": False,
        "adx_live_veto": False,
        "atr_live_close": False,
        "note": "RSI/ADX/ATR are persisted; WOULD_BLOCK is log-only unless already a hard gate",
    }


def classify_cohort(
    *,
    submitted: bool,
    live_filled: bool,
    entry_outcome: Optional[str] = None,
    rejected: bool = False,
) -> str:
    if rejected or (not submitted):
        return COHORT_REJECTED_SIGNAL
    outcome = str(entry_outcome or "")
    if live_filled or outcome == "FILLED":
        return COHORT_ACTUAL_FILLED
    return COHORT_SUBMITTED_UNFILLED


def counterfactual_envelope(
    *,
    trade_id: str,
    signal_ts: float,
    signal_price: float,
    direction: str = "SHORT",
    submitted: bool = False,
    live_filled: bool = False,
    entry_outcome: Optional[str] = None,
    rejected: bool = False,
    exact_reason: Optional[str] = None,
    decision_tree: Optional[Mapping[str, Any]] = None,
    original_order_fill: Optional[bool] = None,
    alternative_entry_fill: Optional[bool] = None,
    rsi_at_signal: Any = None,
    would_block: Any = None,
    would_block_reason: Any = None,
    path_from: str = "signal_ts",
) -> dict:
    cohort = classify_cohort(
        submitted=submitted,
        live_filled=live_filled,
        entry_outcome=entry_outcome,
        rejected=rejected,
    )
    if cohort == COHORT_ACTUAL_FILLED:
        kind = KIND_ACTUAL_TRADE
        branch = "ACCEPTED_FILL"
    elif cohort == COHORT_SUBMITTED_UNFILLED:
        kind = KIND_UNFILLED_ORDER
        branch = "ACCEPTED_NEVER_FILL"
    else:
        kind = KIND_REJECTED_OPPORTUNITY
        branch = "REJECTED_BLOCKED"
    return {
        "schema": OPPORTUNITY_CAPTURE_SCHEMA,
        "collector_version": COLLECTOR_VERSION,
        "trade_id": str(trade_id),
        "signal_ts": float(signal_ts),
        "signal_price": float(signal_price),
        "direction": str(direction or "SHORT").upper(),
        "path_from": path_from,
        "branch": branch,
        "cohort": cohort,
        "record_kind": kind,
        "submitted": bool(submitted),
        "live_filled": bool(live_filled),
        "entry_outcome": entry_outcome,
        "original_order_fill": original_order_fill,
        "alternative_entry_fill": alternative_entry_fill,
        "exact_reason": exact_reason,
        "decision_tree": dict(decision_tree or build_decision_tree(reason=exact_reason)),
        "rsi_at_signal": rsi_at_signal,
        "would_block": would_block,
        "would_block_reason": would_block_reason,
        "control_cell": dict(CONTROL_CELL),
        "fill_model": FILL_MODEL_IDEAL_TOUCH,
        "fill_models_supported": list(FILL_MODELS),
        "units": "unrealized_pct_on_100x_margin",
        "live_knobs_unchanged": True,
    }


def label_fill_window(
    *,
    fill_ts: Optional[float],
    signal_ts: float,
    ttl_sec: float,
    is_control_orig: bool,
    chase_id: str,
) -> dict:
    """CONTROL orig within TTL is original_order_fill. Post-TTL is alternative only."""
    if fill_ts is None:
        return {
            "original_order_fill": False,
            "alternative_entry_fill": False,
            "fill_window": "NONE",
            "record_kind": KIND_UNFILLED_ORDER,
        }
    within_ttl = float(fill_ts) <= float(signal_ts) + float(ttl_sec) + 1e-9
    control_no_chase = bool(is_control_orig) and str(chase_id) == "no_chase"
    if within_ttl and control_no_chase:
        return {
            "original_order_fill": True,
            "alternative_entry_fill": False,
            "fill_window": "WITHIN_TTL",
            "record_kind": KIND_ACTUAL_TRADE,
        }
    if within_ttl:
        return {
            "original_order_fill": False,
            "alternative_entry_fill": True,
            "fill_window": "WITHIN_TTL",
            "record_kind": KIND_HYPOTHETICAL_FILL,
        }
    return {
        "original_order_fill": False,
        "alternative_entry_fill": True,
        "fill_window": "POST_TTL",
        "record_kind": KIND_HYPOTHETICAL_FILL,
        "note": "no lookahead into the original TTL order; post-TTL touch is alternative_entry_fill only",
    }


def build_rejected_capture(
    *,
    trade_id: str,
    signal_price: float,
    signal_ts: float,
    direction: str = "SHORT",
    reason: str = "REJECTED",
    decision_tree: Optional[Mapping[str, Any]] = None,
    candles_1m: Sequence[Sequence[Any]] = (),
    ticks_1s: Optional[Sequence[Mapping[str, Any]]] = None,
    ttl_sec: float = 1800.0,
    hold_sec: float = HOLD_SEC_DEFAULT,
    live_orig: float = LIVE_ORIG_OFFSET_PCT,
    rsi_at_signal: Any = None,
    would_block: Any = None,
    would_block_reason: Any = None,
    atr14_pct: Optional[float] = None,
    submitted: bool = False,
) -> dict:
    """First-class REJECTED_SIGNAL row with path from signal_ts and hyp fill grid."""
    direction_u = str(direction or "SHORT").upper()
    tree = dict(decision_tree or build_decision_tree(
        reason=reason,
        hard_reject=not submitted,
        rsi=rsi_at_signal,
        would_block=would_block,
        would_block_reason=would_block_reason,
    ))
    ttl_end = float(signal_ts) + float(ttl_sec)
    alt_end = ttl_end + float(POST_TTL_LOOKAHEAD_SEC)
    path_end = float(signal_ts) + max(float(ttl_sec) + float(POST_TTL_LOOKAHEAD_SEC), float(hold_sec))
    hyp_fills = []
    original_order_fill = False
    alternative_entry_fill = False
    for offset_pct in (0.05, 0.10, 0.20, 0.30):
        hit_ttl = simulate_touch_fill(
            candles_1m,
            signal_ts=signal_ts,
            signal_price=signal_price,
            direction=direction_u,
            offset_pct=offset_pct,
            ttl_sec=ttl_sec,
            chase={"no_chase": True},
            ticks_1s=ticks_1s,
        )
        hit_alt = simulate_touch_fill(
            candles_1m,
            signal_ts=signal_ts,
            signal_price=signal_price,
            direction=direction_u,
            offset_pct=offset_pct,
            ttl_sec=ttl_sec + POST_TTL_LOOKAHEAD_SEC,
            chase={"no_chase": True},
            ticks_1s=ticks_1s,
        )
        fill_ts = hit_ttl.get("touch_ts") or (
            None if hit_ttl.get("touched") else hit_alt.get("touch_ts")
        )
        labels = label_fill_window(
            fill_ts=fill_ts,
            signal_ts=signal_ts,
            ttl_sec=ttl_sec,
            is_control_orig=abs(float(offset_pct) - float(live_orig)) < 1e-9,
            chase_id="no_chase",
        )
        if labels["original_order_fill"]:
            original_order_fill = True
        if labels["alternative_entry_fill"]:
            alternative_entry_fill = True
        if fill_ts is not None:
            hyp_fills.append({
                "orig": float(offset_pct),
                "chase_id": "no_chase",
                "fill_ts": float(fill_ts),
                "hyp_fill_ts": float(fill_ts),
                "fill_price": float(hit_ttl.get("fill_price") or hit_alt.get("fill_price") or orig_limit_price(signal_price, direction_u, offset_pct)),
                "fill_model": FILL_MODEL_IDEAL_TOUCH,
                "fill_costs": zero_fill_costs(),
                **labels,
            })
    path_ticks = list(ticks_1s or [])
    if not path_ticks:
        path_ticks = candles_to_path_ticks(
            candles_1m, direction=direction_u, start_ts=signal_ts, end_ts=path_end,
        )
    envelope = counterfactual_envelope(
        trade_id=trade_id,
        signal_ts=signal_ts,
        signal_price=signal_price,
        direction=direction_u,
        submitted=submitted,
        live_filled=False,
        entry_outcome="NOT_SUBMITTED",
        rejected=True,
        exact_reason=reason,
        decision_tree=tree,
        original_order_fill=original_order_fill,
        alternative_entry_fill=alternative_entry_fill,
        rsi_at_signal=rsi_at_signal,
        would_block=would_block,
        would_block_reason=would_block_reason,
        path_from="signal_ts",
    )
    return {
        "schema": OPPORTUNITY_CAPTURE_SCHEMA,
        "collector_version": COLLECTOR_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "path_schema_version": PATH_SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "event": "REJECTED_SIGNAL",
        "lifecycle": "REJECTED_SIGNAL",
        "pending": False,
        "trade_id": str(trade_id),
        "signal_px": float(signal_price),
        "signal_ts": float(signal_ts),
        "direction": direction_u,
        "cohort": COHORT_REJECTED_SIGNAL,
        "record_kind": KIND_REJECTED_OPPORTUNITY,
        "live_ticket_unchanged": True,
        "live_orig": float(live_orig),
        "live_thesis_cut": LIVE_THESIS_CUT,
        "control_cell": dict(CONTROL_CELL),
        "LIVE_CELL": True,
        "fill_model": FILL_MODEL_IDEAL_TOUCH,
        "fill_models_supported": list(FILL_MODELS),
        "hypothetical_fills": hyp_fills,
        "path_1m": _compact_path_1m(candles_1m, signal_ts, path_end),
        "raw_path": {"schema": PATH_SCHEMA_VERSION, "path_1m": _compact_path_1m(candles_1m, signal_ts, path_end), "tick_n": len(path_ticks)},
        "ttl_end": ttl_end,
        "post_ttl_end": alt_end,
        "envelope": envelope,
        "decision_tree": tree,
        "exact_reason": str(reason),
        "rsi_at_signal": rsi_at_signal,
        "would_block": would_block,
        "would_block_reason": would_block_reason,
        "atr14_pct": atr14_pct,
        "note": "rejected is first-class; path is from signal_ts; not an exit-WR row",
    }


def _compact_path_1m(candles_1m, start_ts: float, end_ts: float) -> list:
    from chase_offset_touch_grid import candle_ts_sec
    out = []
    for row in candles_1m or []:
        t = candle_ts_sec(row)
        if t is None or t + 60.0 < float(start_ts) or t > float(end_ts) + 1.0:
            continue
        out.append([float(row[i]) if i < len(row) else None for i in range(min(6, len(row)))])
    return out


def write_opportunity_capture(path: str, record: Mapping[str, Any]) -> str:
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    if line.count("\n"):
        raise ValueError("opportunity_capture row must be one JSON line")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return line


def _load_jsonl(path: str) -> list:
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _latest_by_trade_id(rows: Iterable[Mapping[str, Any]]) -> list:
    latest = {}
    order = []
    for row in rows:
        tid = str(row.get("trade_id") or "")
        if not tid:
            continue
        if tid not in latest:
            order.append(tid)
        latest[tid] = dict(row)
    return [latest[tid] for tid in order]


def _hyp_pnl_from_path(row: Mapping[str, Any], fill: Mapping[str, Any]) -> Optional[float]:
    fill_ts = fill.get("hyp_fill_ts") or fill.get("fill_ts")
    fill_px = fill.get("fill_price")
    if fill_ts is None or fill_px is None:
        return None
    candles = (row.get("raw_path") or {}).get("path_1m") or row.get("path_1m") or []
    if not candles:
        return None
    ticks = candles_to_path_ticks(
        candles,
        direction=str(row.get("direction") or "SHORT"),
        start_ts=float(fill_ts),
        end_ts=float(fill_ts) + float(HOLD_SEC_DEFAULT),
    )
    if not ticks:
        return None
    sweep = stage1_replay(
        ticks,
        direction=str(row.get("direction") or "SHORT"),
        entry_price=float(fill_px),
        fill_t=float(fill_ts),
        invert_on=bool(row.get("invert_on")),
        atr14_pct=row.get("atr14_pct"),
    )
    scores = sweep.get("chase_exit_scores") or []
    live = next((item for item in scores if item.get("exit") in ("live_4_2_t12", "thesis_m12")), None)
    if live and live.get("pnl") is not None:
        return float(live["pnl"])
    if scores and scores[0].get("pnl") is not None:
        return float(scores[0]["pnl"])
    if recovery and recovery.get("mfe_pct") is not None:
        return float(recovery.get("mae_pct") if (recovery.get("mae_pct") or 0) < -12 else recovery.get("mfe_pct"))
    return None


def _mean(values: Sequence[float]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 4)


def analyze_opportunity_capture(
    *,
    data_dir: Optional[str] = None,
    multiverse_path: Optional[str] = None,
    capture_path: Optional[str] = None,
) -> dict:
    """Default 30-min analyzer report. Empty epoch returns zeros, does not crash."""
    root = data_dir or os.getcwd()
    mv_path = multiverse_path or os.path.join(root, ORDER_MULTIVERSE_FILE)
    cap_path = capture_path or os.path.join(root, OPPORTUNITY_CAPTURE_FILE)
    mv_rows = _latest_by_trade_id(_load_jsonl(mv_path))
    cap_rows = _latest_by_trade_id(_load_jsonl(cap_path))

    actual = []
    unfilled = []
    rejected = [
        row for row in cap_rows
        if str(row.get("event") or row.get("lifecycle") or "") == "REJECTED_SIGNAL"
        and not row.get("would_block_only")
    ]
    would_block_candidates = [
        row for row in cap_rows
        if row.get("would_block_only") or str(row.get("event") or "") == "WOULD_BLOCK_CANDIDATE"
    ]
    hyp_only = []
    waiting = 0
    data_error = 0
    funnel_in_exit = 0
    reasons: Counter = Counter()
    rsi_rows = []
    mae_mfe = []
    control_pnls = []
    stage1_notes = []

    for row in mv_rows:
        lifecycle = str(row.get("lifecycle") or row.get("event") or "")
        flags = row.get("completeness") or {}
        if lifecycle in ("WAITING_120M", "DATA_ERROR"):
            if lifecycle == "WAITING_120M":
                waiting += 1
            else:
                data_error += 1
            continue
        if allows_exit_expectancy(flags):
            actual.append(row)
            if row.get("derived_features") or row.get("mfe_mae_trajectory"):
                mae_mfe.append(row.get("mfe_mae_trajectory") or (row.get("derived_features") or {}).get("mfe_mae_trajectory"))
            rec = row.get("recovery") or (row.get("derived_features") or {}).get("recovery") or {}
            if rec.get("last_unreal_pct") is not None:
                control_pnls.append(float(rec["last_unreal_pct"]))
        elif lifecycle == "FUNNEL_ONLY" or str(flags.get("entry_outcome") or "") == "TTL_UNFILLED":
            unfilled.append(row)
            if allows_exit_expectancy(flags):
                funnel_in_exit += 1
        env = row.get("envelope") or {}
        if env.get("rsi_at_signal") is not None or row.get("rsi_at_signal") is not None:
            rsi_rows.append({
                "trade_id": row.get("trade_id"),
                "rsi_at_signal": env.get("rsi_at_signal") if env.get("rsi_at_signal") is not None else row.get("rsi_at_signal"),
                "would_block": env.get("would_block") or row.get("would_block"),
                "would_block_reason": env.get("would_block_reason") or row.get("would_block_reason"),
                "cohort": env.get("cohort") or row.get("cohort"),
            })
        for fill in row.get("hypothetical_fills") or []:
            if fill.get("alternative_entry_fill") or fill.get("record_kind") == KIND_HYPOTHETICAL_FILL:
                hyp_only.append((row, fill))

    for row in rejected:
        reasons[str(row.get("exact_reason") or (row.get("envelope") or {}).get("exact_reason") or "UNKNOWN")] += 1
        env = row.get("envelope") or {}
        if env.get("rsi_at_signal") is not None or row.get("rsi_at_signal") is not None:
            rsi_rows.append({
                "trade_id": row.get("trade_id"),
                "rsi_at_signal": row.get("rsi_at_signal") if row.get("rsi_at_signal") is not None else env.get("rsi_at_signal"),
                "would_block": row.get("would_block") or env.get("would_block"),
                "would_block_reason": row.get("would_block_reason") or env.get("would_block_reason"),
                "cohort": COHORT_REJECTED_SIGNAL,
            })
        for fill in row.get("hypothetical_fills") or []:
            hyp_only.append((row, fill))

    hyp_pnls = []
    n_would_fill = 0
    for row, fill in hyp_only:
        n_would_fill += 1
        pnl = _hyp_pnl_from_path(row, fill)
        if pnl is not None:
            hyp_pnls.append(pnl)

    rejected_would = 0
    rejected_pnls = []
    for row in rejected:
        fills = row.get("hypothetical_fills") or []
        if fills:
            rejected_would += 1
            for fill in fills:
                pnl = _hyp_pnl_from_path(row, fill)
                if pnl is not None:
                    rejected_pnls.append(pnl)

    n_rejected = len(rejected)
    p_fill_rejected = None if n_rejected == 0 else round(rejected_would / n_rejected, 4)

    report = {
        "schema": "collector_v21_analyzer_report_v1",
        "collector_version": COLLECTOR_VERSION,
        "empty_epoch": not (mv_rows or cap_rows),
        "units": "unrealized_pct_on_100x_margin_100x",
        "fill_model": FILL_MODEL_IDEAL_TOUCH,
        "live_knobs_unchanged": True,
        "control_cell": dict(CONTROL_CELL),
        "cohorts": {
            COHORT_ACTUAL_FILLED: {
                "n": len(actual),
                "e_pnl": _mean(control_pnls),
                "note": "CONTROL paper fills only; FUNNEL_ONLY/WAITING_120M/DATA_ERROR excluded from exit WR",
            },
            COHORT_SUBMITTED_UNFILLED: {
                "n": len(unfilled),
                "p_fill_control_orig": None if not unfilled else 0.0,
                "note": "FUNNEL_ONLY — entry funnel only; never exit win-rate",
            },
            COHORT_REJECTED_SIGNAL: {
                "n": n_rejected,
                "reasons": dict(reasons),
                "p_would_fill": p_fill_rejected,
                "e_pnl_if_hyp_fill": _mean(rejected_pnls),
                "n_hyp_scored": len(rejected_pnls),
                "note": "first-class rejected rows; path from signal_ts; not CONTROL performance",
            },
            COHORT_HYPOTHETICAL_FILLED: {
                "n": n_would_fill,
                "e_pnl": _mean(hyp_pnls),
                "n_scored": len(hyp_pnls),
                "note": "scored from hyp_fill_ts on stored path; IDEAL_TOUCH; not actual_trade",
            },
        },
        "stage1": {
            "thesis_grid": True,
            "ladder": True,
            "atr_tp": True,
            "atr_sl": True,
            "chase": True,
            "orig_offsets": True,
            "note": "query-time on stored path; not live closes",
        },
        "exclusions": {
            "FUNNEL_ONLY_in_exit_wr": funnel_in_exit,
            "WAITING_120M": waiting,
            "DATA_ERROR": data_error,
        },
        "rsi": {
            "n_stamped": len(rsi_rows),
            "live_veto": False,
            "would_block_candidates": len(would_block_candidates),
            "would_block_vs_tape": rsi_rows[:32],
            "note": "RSI persisted; simulate_rsi is not a live veto",
        },
        "mae_mfe": {
            "n_trajectories": len([row for row in mae_mfe if row]),
            "note": "from filled 120m paths",
        },
        "waiting_120m_out_of_exit_stats": True,
        "data_error_out_of_exit_stats": True,
        "ttl_never_in_exit_wr": funnel_in_exit == 0,
        "gaps": [
            "CONSERVATIVE_TOUCH and ACTUAL_EXECUTION are schema-only; live fills still labeled IDEAL_TOUCH unless an exchange fill is stored",
            "Rejected path quality depends on 1m candles available at persist/sync time",
            "n=1 100% green is not a policy result",
            "No live RSI/ADX/ATR veto added",
        ],
        "notes": stage1_notes,
    }
    if report["empty_epoch"]:
        report["cohorts"] = {key: empty_cohort_stats() for key in COHORTS}
        report["cohorts"][COHORT_ACTUAL_FILLED]["note"] = "CONTROL paper fills only; empty epoch"
        report["message"] = "empty post-wipe epoch — zeros, not a crash"
    return report


def write_opportunity_capture_report(report: Mapping[str, Any], path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return path
