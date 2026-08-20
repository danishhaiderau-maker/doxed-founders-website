"""Discrete multiverse of ONE live paper ticket.

Live ticket stays orig 0.1% / thesis −12 / 4→2 then Scenario C. This module
does not place extra orders. Each TAKEN/ORDER is one universe-anchor. We
simulate the discrete grid of that same signal's price path:

  orig 0.01%…0.30% (0.01) → touch-fill only (1m high tagged the limit)
  chase variants on actual path touches
  first-hit exits vs live 4→2+thesis−12, ATR, chandelier, structure, thesis grid, stops

TTL (~30m) is when an unfilled paper ticket COMPLETE's on the signal→expire
path. 120m is extra tape AFTER a simulated/real fill, not a delay before
scoring. COMPLETE the paper cont-* id; lab-hunter ids are not a substitute.

Storage: one compact JSON line per TAKEN in ``order_multiverse.jsonl``.
n=1 100% green is not a policy result.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional, Sequence

from chase_offset_touch_grid import (
    CHASE_POLICIES,
    DEFAULT_MARGIN_USDT,
    LIVE_ORIG_OFFSET_PCT,
    OFFSET_PCT_GRID,
    TTL_SEC_DEFAULT,
    candle_ts_sec,
    orig_limit_price,
    simulate_touch_fill,
)
from path_replay_v1 import (
    COLLECTOR_VERSION,
    CONTROL_CELL,
    FEATURE_SCHEMA_VERSION,
    FILL_MODEL_IDEAL_TOUCH,
    FILL_MODELS,
    LIVE_HARD_STOP_PCT,
    LIVE_THESIS_CUT,
    PATH_SCHEMA_VERSION,
    REPLAY_VERSION,
    mfe_mae_trajectory,
    path_exit_annotations,
    path_recovery_stats,
    raw_1m_to_ticks,
    zero_fill_costs,
)


ORDER_MULTIVERSE_SCHEMA = "order_multiverse_v1"
ORDER_MULTIVERSE_FILE = "order_multiverse.jsonl"
HOLD_SEC_DEFAULT = 7200.0
POST_TTL_LOOKAHEAD_SEC = 1800.0
SCORE_CAP = 64
LIVE_CHASE_ID = "w234_s25_i180"
LAB_OR_HUNTER_PREFIXES = ("lab-", "tbh-")
LIFECYCLE_SIGNAL = "SIGNAL"
LIFECYCLE_ENTRY_RESOLVED = "ENTRY_RESOLVED"
LIFECYCLE_FILLED = "FILLED"
LIFECYCLE_PATH_COMPLETE = "PATH_COMPLETE"
LIFECYCLE_REPLAY_COMPLETE = "REPLAY_COMPLETE"
LIFECYCLE_TTL_UNFILLED = "TTL_UNFILLED"
LIFECYCLE_FUNNEL_ONLY = "FUNNEL_ONLY"
LIFECYCLE_WAITING_120M = "WAITING_120M"
LIFECYCLE_DATA_ERROR = "DATA_ERROR"
LIFECYCLE_COMPLETE = "COMPLETE"
TERMINAL_LIFECYCLES = (LIFECYCLE_COMPLETE, LIFECYCLE_FUNNEL_ONLY, LIFECYCLE_REPLAY_COMPLETE)
KEEP_COLLECTING_LIFECYCLES = (
    LIFECYCLE_SIGNAL,
    LIFECYCLE_ENTRY_RESOLVED,
    LIFECYCLE_FILLED,
    LIFECYCLE_WAITING_120M,
    LIFECYCLE_DATA_ERROR,
)


def paper_multiverse_trade_id(*candidates: Any) -> str:
    """Paper cont-* wins. Lab-hunter ids never substitute for the paper ticket."""
    ids = [str(raw or "").strip() for raw in candidates]
    ids = [tid for tid in ids if tid]
    for tid in ids:
        if tid.startswith("cont-"):
            return tid
    for tid in ids:
        if any(tid.startswith(prefix) for prefix in LAB_OR_HUNTER_PREFIXES):
            continue
        return tid
    return ""


def resolve_lifecycle(
    *,
    signal_recorded: bool,
    entry_outcome: str,
    ticket_closed: bool,
    path_complete: Optional[bool],
    path_missing: bool,
    exit_sweep_complete: bool = False,
) -> tuple:
    """Map flags onto one lifecycle label + completeness_reason.

    SIGNAL → ENTRY_RESOLVED
      ├── FILLED → PATH_COMPLETE → REPLAY_COMPLETE  (event COMPLETE when path ready)
      └── TTL_UNFILLED → FUNNEL_ONLY
    """
    if not signal_recorded:
        return LIFECYCLE_SIGNAL, "signal not recorded; not usable"
    outcome = str(entry_outcome or "")
    if outcome == "PENDING":
        return LIFECYCLE_ENTRY_RESOLVED, "entry not resolved yet (waiting fill or TTL); not in exit stats"
    if outcome == "TTL_UNFILLED":
        return LIFECYCLE_FUNNEL_ONLY, "TTL_UNFILLED: funnel/P(fill) only; never exit-win-rate denominator"
    if outcome == "CANCELLED":
        return LIFECYCLE_FUNNEL_ONLY, "cancelled unfilled; funnel only; never exit-win-rate denominator"
    if outcome == "FILLED":
        if ticket_closed and path_missing:
            return LIFECYCLE_DATA_ERROR, "FILLED+CLOSED but path missing; DATA_ERROR not COMPLETE"
        if path_complete is not True:
            if ticket_closed:
                return LIFECYCLE_WAITING_120M, "WAITING_120M: filled but 120m path incomplete; not in exit stats"
            return LIFECYCLE_FILLED, "IN_TRADE: filled and still open; 120m path incomplete; not in exit stats"
        if exit_sweep_complete:
            return LIFECYCLE_REPLAY_COMPLETE, "FILLED path complete and query replay done"
        return LIFECYCLE_COMPLETE, "FILLED with 120m path; query-time replay; usable exit cohort"
    return LIFECYCLE_ENTRY_RESOLVED, f"unmapped entry_outcome={outcome}"


def tape_completeness(
    *,
    signal_recorded: bool,
    entry_outcome: str,
    path_complete: Optional[bool],
    path_incomplete_reason: Optional[str] = None,
    replayable: bool = False,
    exit_sweep_complete: bool = False,
    ticket_closed: bool = False,
    path_missing: bool = False,
) -> dict:
    """P0 invariant. EXIT_SWEEP_COMPLETE is query-time, never a collector duty."""
    known = entry_outcome in ("FILLED", "TTL_UNFILLED", "CANCELLED")
    lifecycle, completeness_reason = resolve_lifecycle(
        signal_recorded=signal_recorded,
        entry_outcome=entry_outcome,
        ticket_closed=ticket_closed,
        path_complete=path_complete,
        path_missing=path_missing,
        exit_sweep_complete=exit_sweep_complete,
    )
    return {
        "SIGNAL_RECORDED": bool(signal_recorded),
        "ENTRY_OUTCOME_KNOWN": known,
        "PATH_COMPLETE": path_complete,
        "PATH_INCOMPLETE_REASON": path_incomplete_reason,
        "REPLAYABLE": bool(replayable),
        "EXIT_SWEEP_COMPLETE": bool(exit_sweep_complete),
        "entry_outcome": entry_outcome,
        "exit_cohort": (
            "filled" if entry_outcome == "FILLED"
            else ("ttl_unfilled" if entry_outcome == "TTL_UNFILLED" else "pending")
        ),
        "lifecycle": lifecycle,
        "completeness_reason": completeness_reason,
    }


def allows_exit_expectancy(flags: Mapping[str, Any]) -> bool:
    """TTL-unfilled and incomplete 120m paths must not enter exit win-rate / E[PnL|fill]."""
    lifecycle = str(flags.get("lifecycle") or "")
    if lifecycle in (
        LIFECYCLE_FUNNEL_ONLY,
        LIFECYCLE_WAITING_120M,
        LIFECYCLE_DATA_ERROR,
        LIFECYCLE_SIGNAL,
        LIFECYCLE_ENTRY_RESOLVED,
        LIFECYCLE_FILLED,
        LIFECYCLE_TTL_UNFILLED,
    ):
        return False
    return (
        str(flags.get("entry_outcome") or "") == "FILLED"
        and flags.get("PATH_COMPLETE") is True
        and lifecycle in (LIFECYCLE_COMPLETE, LIFECYCLE_PATH_COMPLETE, LIFECYCLE_REPLAY_COMPLETE)
    )


def policy_reject_n1_perfect_green(n_orders: int, all_green: bool) -> bool:
    """True when a 100% green print must not be treated as a policy result."""
    return int(n_orders) < 2 or (int(n_orders) == 1 and bool(all_green))


def _offset_key(offset_pct: float) -> str:
    return f"{float(offset_pct):.2f}"


def candles_to_path_ticks(
    candles_1m: Sequence[Sequence[Any]],
    *,
    direction: str,
    start_ts: float,
    end_ts: float,
    as_of_ts: Optional[float] = None,
) -> list:
    """1m high tagged: SHORT sees high then low then close (touch, not blind shadow)."""
    return raw_1m_to_ticks(
        candles_1m,
        direction=direction,
        start_ts=start_ts,
        end_ts=end_ts,
        as_of_ts=as_of_ts,
    )


def _compact_score(
    *,
    orig: float,
    chase_id: str,
    exit_id: str,
    sim: Mapping[str, Any],
    first_hit: Optional[str] = None,
    invert_on: bool = False,
) -> dict:
    pnl = sim.get("net_pnl_usd")
    hit = first_hit or sim.get("exit_reason")
    green = sim.get("green")
    if green is None and pnl is not None:
        green = float(pnl) > 0
    return {
        "orig": round(float(orig), 2),
        "chase": chase_id,
        "exit": exit_id,
        "pnl": None if pnl is None else round(float(pnl), 4),
        "first_hit": hit,
        "green": bool(green),
        "invert_on": bool(invert_on),
        "mfe_pct": sim.get("mfe_pct"),
        "mae_pct": sim.get("mae_pct"),
    }


def _path_horizon_ts(candles_1m, ticks_1s, signal_ts: float) -> float:
    last = float(signal_ts)
    for row in candles_1m or []:
        t = candle_ts_sec(row)
        if t is not None:
            last = max(last, t + 60.0)
    for tick in ticks_1s or []:
        try:
            last = max(last, float(tick.get("t") or 0))
        except (TypeError, ValueError):
            continue
    return last


def _score_priority(row: Mapping[str, Any], live_orig: float) -> tuple:
    orig = float(row.get("orig") or 0)
    chase = str(row.get("chase") or "")
    exit_id = str(row.get("exit") or "")
    live = 0 if abs(orig - float(live_orig)) < 1e-9 else 1
    chase_rank = 0 if chase in ("no_chase", LIVE_CHASE_ID) else 1
    always = 0 if exit_id in ("live_4_2_t12", "live_c_t12") else 1
    return (live, chase_rank, always, exit_id)


def cap_chase_exit_scores(
    scores: Sequence[Mapping[str, Any]],
    *,
    live_orig: float = LIVE_ORIG_OFFSET_PCT,
    cap: int = SCORE_CAP,
) -> list:
    """Keep live baseline + ladder comparison; then best/worst; drop the rest."""
    rows = [dict(row) for row in scores]
    if len(rows) <= int(cap):
        return rows
    must = []
    rest = []
    for row in rows:
        orig = float(row.get("orig") or 0)
        chase = str(row.get("chase") or "")
        exit_id = str(row.get("exit") or "")
        keep = (
            abs(orig - float(live_orig)) < 1e-9
            and chase in ("no_chase", LIVE_CHASE_ID)
            and (
                exit_id in ("live_4_2_t12", "live_c_t12")
                or exit_id.startswith("atr_")
                or exit_id.startswith("chand_")
                or exit_id.startswith("struct_")
                or exit_id.startswith("fh_")
                or exit_id.startswith("thesis_")
                or exit_id.startswith("stop_")
                or exit_id.startswith("ladder_")
                or exit_id.startswith("time_")
                or exit_id.startswith("combo_")
            )
        ) or (
            exit_id in ("live_4_2_t12", "live_c_t12")
            and chase in ("no_chase", LIVE_CHASE_ID)
        )
        if keep:
            must.append(row)
        else:
            rest.append(row)
    seen = set()
    out = []
    for row in must:
        key = (row.get("orig"), row.get("chase"), row.get("exit"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    rest_sorted = sorted(
        rest,
        key=lambda row: (
            abs(float(row["pnl"])) if row.get("pnl") is not None else -1.0,
            0 if row.get("green") else 1,
        ),
        reverse=True,
    )
    for row in rest_sorted:
        if len(out) >= int(cap):
            break
        key = (row.get("orig"), row.get("chase"), row.get("exit"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=lambda row: _score_priority(row, live_orig))
    return out[: int(cap)]


def build_order_multiverse(
    *,
    trade_id: str,
    signal_price: float,
    signal_ts: float,
    direction: str = "SHORT",
    candles_1m: Sequence[Sequence[Any]] = (),
    ticks_1s: Optional[Sequence[Mapping[str, Any]]] = None,
    live_orig: float = LIVE_ORIG_OFFSET_PCT,
    ttl_sec: float = TTL_SEC_DEFAULT,
    hold_sec: float = HOLD_SEC_DEFAULT,
    atr14_pct: Optional[float] = None,
    donchian_high: Optional[float] = None,
    donchian_low: Optional[float] = None,
    support_price: Optional[float] = None,
    resistance_price: Optional[float] = None,
    margin_usdt: float = DEFAULT_MARGIN_USDT,
    path_complete: bool = False,
    invert_on: bool = False,
    live_fill_ts: Optional[float] = None,
    live_fill_price: Optional[float] = None,
    ticket_closed: bool = False,
    rsi_at_signal: Optional[float] = None,
    would_block: Optional[bool] = None,
    would_block_reason: Optional[str] = None,
    decision_tree: Optional[Mapping[str, Any]] = None,
    submitted: bool = True,
    exact_reason: Optional[str] = None,
    post_ttl_lookahead_sec: float = POST_TTL_LOOKAHEAD_SEC,
) -> dict:
    direction_u = str(direction or "SHORT").upper()
    ticks_abs = list(ticks_1s or [])
    touches = {}
    alternative_touches = {}
    fills = []
    alt_ttl = float(ttl_sec) + float(post_ttl_lookahead_sec)
    ttl_end_for_labels = float(signal_ts) + float(ttl_sec)

    def _fill_labels(fill_ts, offset_pct, chase_id):
        if fill_ts is None:
            return {
                "original_order_fill": False,
                "alternative_entry_fill": False,
                "fill_window": "NONE",
                "record_kind": "unfilled_order",
            }
        within_ttl = float(fill_ts) <= ttl_end_for_labels + 1e-9
        control_no_chase = (
            abs(float(offset_pct) - float(live_orig)) < 1e-9
            and str(chase_id) == "no_chase"
        )
        if within_ttl and control_no_chase:
            return {
                "original_order_fill": True,
                "alternative_entry_fill": False,
                "fill_window": "WITHIN_TTL",
                "record_kind": "actual_trade",
            }
        window = "WITHIN_TTL" if within_ttl else "POST_TTL"
        return {
            "original_order_fill": False,
            "alternative_entry_fill": True,
            "fill_window": window,
            "record_kind": "hypothetical_fill",
            "note": None if within_ttl else "no lookahead into the original TTL order",
        }

    for offset_pct in OFFSET_PCT_GRID:
        hit = simulate_touch_fill(
            candles_1m,
            signal_ts=signal_ts,
            signal_price=signal_price,
            direction=direction_u,
            offset_pct=offset_pct,
            ttl_sec=ttl_sec,
            chase={"no_chase": True},
            ticks_1s=ticks_abs or None,
        )
        hit_alt = simulate_touch_fill(
            candles_1m,
            signal_ts=signal_ts,
            signal_price=signal_price,
            direction=direction_u,
            offset_pct=offset_pct,
            ttl_sec=alt_ttl,
            chase={"no_chase": True},
            ticks_1s=ticks_abs or None,
        )
        touches[_offset_key(offset_pct)] = hit.get("touch_ts")
        if not hit.get("touched") and hit_alt.get("touched"):
            alternative_touches[_offset_key(offset_pct)] = hit_alt.get("touch_ts")
        else:
            alternative_touches[_offset_key(offset_pct)] = None
        for policy in CHASE_POLICIES:
            chase_hit = simulate_touch_fill(
                candles_1m,
                signal_ts=signal_ts,
                signal_price=signal_price,
                direction=direction_u,
                offset_pct=offset_pct,
                ttl_sec=ttl_sec,
                chase=policy,
                ticks_1s=ticks_abs or None,
            )
            chosen = chase_hit
            if not chase_hit.get("touched"):
                chosen = simulate_touch_fill(
                    candles_1m,
                    signal_ts=signal_ts,
                    signal_price=signal_price,
                    direction=direction_u,
                    offset_pct=offset_pct,
                    ttl_sec=alt_ttl,
                    chase=policy,
                    ticks_1s=ticks_abs or None,
                )
            if not chosen.get("touched"):
                continue
            labels = _fill_labels(chosen.get("touch_ts"), offset_pct, policy["id"])
            fills.append({
                "orig": float(offset_pct),
                "chase_id": policy["id"],
                "fill_ts": float(chosen["touch_ts"]),
                "hyp_fill_ts": float(chosen["touch_ts"]),
                "fill_price": float(chosen["fill_price"]),
                **labels,
            })
    live_key = _offset_key(live_orig)
    if live_fill_ts is not None:
        touches[live_key] = float(live_fill_ts)
        fill_px = float(
            live_fill_price
            or orig_limit_price(signal_price, direction_u, live_orig)
        )
        have = {(round(float(row["orig"]), 2), row["chase_id"]) for row in fills}
        if (round(float(live_orig), 2), "no_chase") not in have:
            labels = _fill_labels(live_fill_ts, live_orig, "no_chase")
            fills.append({
                "orig": float(live_orig),
                "chase_id": "no_chase",
                "fill_ts": float(live_fill_ts),
                "hyp_fill_ts": float(live_fill_ts),
                "fill_price": fill_px,
                **labels,
            })

    horizon = _path_horizon_ts(candles_1m, ticks_abs, signal_ts)
    ttl_end = float(signal_ts) + float(ttl_sec)
    ttl_reached = horizon + 1.0 >= ttl_end or bool(path_complete)
    live_filled = live_fill_ts is not None
    # Open filled tickets stay collecting until 120m. Do not mix TTL-unfilled into exit WR.
    if live_filled and not ticket_closed:
        entry_outcome = "FILLED"
    elif live_filled and ticket_closed:
        entry_outcome = "FILLED"
    elif ticket_closed or ttl_reached:
        entry_outcome = "CANCELLED" if ticket_closed and not live_filled else "TTL_UNFILLED"
        if ticket_closed and not live_filled and ttl_reached:
            entry_outcome = "TTL_UNFILLED"
    else:
        entry_outcome = "PENDING"

    live_path_ticks = []
    recovery = None
    trajectory = None
    path_complete_flag = None
    path_incomplete_reason = None
    path_missing = False
    if live_filled:
        fill_ts = float(live_fill_ts)
        live_path_ticks = [tick for tick in ticks_abs if float(tick.get("t") or 0) >= fill_ts - 1e-9]
        if not live_path_ticks:
            live_path_ticks = candles_to_path_ticks(
                candles_1m, direction=direction_u, start_ts=fill_ts,
                end_ts=fill_ts + float(hold_sec),
            )
        path_missing = not bool(live_path_ticks)
        if live_path_ticks:
            recovery = path_recovery_stats(
                live_path_ticks,
                direction=direction_u,
                entry_price=float(live_fill_price or orig_limit_price(signal_price, direction_u, live_orig)),
                fill_t=fill_ts,
            )
            trajectory = mfe_mae_trajectory(
                live_path_ticks,
                direction=direction_u,
                entry_price=float(live_fill_price or orig_limit_price(signal_price, direction_u, live_orig)),
                fill_t=fill_ts,
            )
        if trajectory and trajectory.get("path_complete_120m") and not path_missing:
            path_complete_flag = True
        else:
            path_complete_flag = False
            if path_missing:
                path_incomplete_reason = "MISSING_PATH"
            else:
                path_incomplete_reason = "WAITING_120M" if ticket_closed else "IN_TRADE"
    elif entry_outcome in ("TTL_UNFILLED", "CANCELLED"):
        path_complete_flag = None
        path_incomplete_reason = "NO_FILL"

    lifecycle, _reason = resolve_lifecycle(
        signal_recorded=True,
        entry_outcome=entry_outcome,
        ticket_closed=ticket_closed,
        path_complete=path_complete_flag,
        path_missing=path_missing,
        exit_sweep_complete=False,
    )
    pending = lifecycle in KEEP_COLLECTING_LIFECYCLES
    post_ttl_end = float(signal_ts) + float(ttl_sec) + float(post_ttl_lookahead_sec)
    post_ttl_pending = (
        entry_outcome in ("TTL_UNFILLED", "CANCELLED")
        and horizon + 1.0 < post_ttl_end
    )
    replayable = bool(live_path_ticks) and lifecycle in (
        LIFECYCLE_COMPLETE, LIFECYCLE_PATH_COMPLETE, LIFECYCLE_REPLAY_COMPLETE,
    )
    completeness = tape_completeness(
        signal_recorded=True,
        entry_outcome=entry_outcome,
        path_complete=path_complete_flag,
        path_incomplete_reason=path_incomplete_reason,
        replayable=replayable,
        exit_sweep_complete=False,
        ticket_closed=ticket_closed,
        path_missing=path_missing,
    )
    event = completeness["lifecycle"]
    if event == LIFECYCLE_FILLED:
        event = "PENDING"
    elif event == LIFECYCLE_ENTRY_RESOLVED:
        event = "PENDING"
    elif event == LIFECYCLE_PATH_COMPLETE:
        event = LIFECYCLE_COMPLETE
    completeness_reason = completeness["completeness_reason"]
    path_end = (
        float(live_fill_ts) + float(hold_sec) if live_filled
        else float(signal_ts) + max(float(ttl_sec), float(hold_sec))
    )
    path_1m = []
    pre_sec = 3600.0
    for row in candles_1m or []:
        t = candle_ts_sec(row)
        if t is None or t + 60.0 < float(signal_ts) - pre_sec or t > path_end + 1.0:
            continue
        path_1m.append([float(row[i]) if i < len(row) else None for i in range(min(6, len(row)))])

    for fill in fills:
        fill.setdefault("fill_model", FILL_MODEL_IDEAL_TOUCH)
        fill.setdefault("fill_costs", zero_fill_costs())

    exit_notes = path_exit_annotations(recovery, None)
    mae_mfe = {
        "MFE": None if not recovery else recovery.get("MFE"),
        "MFE_time": None if not recovery else recovery.get("MFE_time"),
        "MAE": None if not recovery else recovery.get("MAE"),
        "MAE_time": None if not recovery else recovery.get("MAE_time"),
    }
    raw_path = {
        "schema": PATH_SCHEMA_VERSION,
        "path_1m": path_1m,
        "tick_n": len(live_path_ticks),
    }
    derived_features = {
        "schema": FEATURE_SCHEMA_VERSION,
        "recovery": recovery,
        "mfe_mae_trajectory": trajectory,
        **mae_mfe,
        "atr14_pct": atr14_pct,
        "donchian_high": donchian_high,
        "donchian_low": donchian_low,
        "support_price": support_price,
        "resistance_price": resistance_price,
        **exit_notes,
    }
    replay_results = {
        "schema": REPLAY_VERSION,
        "chase_exit_scores": [],
        "note": "query-time from raw_path; collector does not precompute exit WR",
    }

    return {
        "schema": ORDER_MULTIVERSE_SCHEMA,
        "collector_version": COLLECTOR_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "path_schema_version": PATH_SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "event": event,
        "lifecycle": completeness["lifecycle"],
        "pending": pending,
        "completeness_reason": completeness_reason,
        "trade_id": str(trade_id),
        "signal_px": float(signal_price),
        "signal_ts": float(signal_ts),
        "direction": direction_u,
        "invert_on": bool(invert_on),
        "live_orig": float(live_orig),
        "live_fill_ts": None if live_fill_ts is None else float(live_fill_ts),
        "live_fill_price": None if live_fill_price is None else float(live_fill_price),
        "live_ticket_unchanged": True,
        "live_thesis_cut": LIVE_THESIS_CUT,
        "live_hard_stop_pct": LIVE_HARD_STOP_PCT,
        "live_ladder": "4->2 then Scenario C",
        "control_cell": dict(CONTROL_CELL),
        "LIVE_CELL": True,
        "fill_model": FILL_MODEL_IDEAL_TOUCH,
        "fill_models_supported": list(FILL_MODELS),
        "fill_costs": zero_fill_costs(),
        "atr14_pct": atr14_pct,
        "donchian_high": donchian_high,
        "donchian_low": donchian_low,
        "support_price": support_price,
        "resistance_price": resistance_price,
        "touches": touches,
        "alternative_touches": alternative_touches,
        "original_order_fill": bool(live_filled),
        "alternative_entry_fill": any(ts is not None for ts in alternative_touches.values()),
        "post_ttl_pending": bool(post_ttl_pending),
        "post_ttl_end": post_ttl_end,
        "cohort": (
            "REJECTED_SIGNAL" if not submitted
            else ("ACTUAL_FILLED" if live_filled else "SUBMITTED_UNFILLED")
        ),
        "record_kind": (
            "rejected_opportunity" if not submitted
            else ("actual_trade" if live_filled else "unfilled_order")
        ),
        "rsi_at_signal": rsi_at_signal,
        "would_block": would_block,
        "would_block_reason": would_block_reason,
        "decision_tree": dict(decision_tree) if decision_tree else None,
        "exact_reason": exact_reason,
        "envelope": {
            "schema": "opportunity_capture_v2.1",
            "collector_version": COLLECTOR_VERSION,
            "trade_id": str(trade_id),
            "signal_ts": float(signal_ts),
            "signal_price": float(signal_price),
            "direction": direction_u,
            "path_from": "signal_ts",
            "branch": (
                "REJECTED_BLOCKED" if not submitted
                else ("ACCEPTED_FILL" if live_filled else "ACCEPTED_NEVER_FILL")
            ),
            "cohort": (
                "REJECTED_SIGNAL" if not submitted
                else ("ACTUAL_FILLED" if live_filled else "SUBMITTED_UNFILLED")
            ),
            "record_kind": (
                "rejected_opportunity" if not submitted
                else ("actual_trade" if live_filled else "unfilled_order")
            ),
            "submitted": bool(submitted),
            "live_filled": bool(live_filled),
            "entry_outcome": entry_outcome,
            "original_order_fill": bool(live_filled),
            "alternative_entry_fill": any(ts is not None for ts in alternative_touches.values()),
            "exact_reason": exact_reason,
            "decision_tree": dict(decision_tree) if decision_tree else None,
            "rsi_at_signal": rsi_at_signal,
            "would_block": would_block,
            "would_block_reason": would_block_reason,
            "control_cell": dict(CONTROL_CELL),
            "fill_model": FILL_MODEL_IDEAL_TOUCH,
            "fill_models_supported": list(FILL_MODELS),
            "units": "unrealized_pct_on_100x_margin",
            "live_knobs_unchanged": True,
        },
        "hypothetical_fills": fills,
        "n_touched": sum(1 for ts in touches.values() if ts is not None),
        "n_missed": sum(1 for ts in touches.values() if ts is None),
        "path_1m": path_1m,
        "raw_path": raw_path,
        "derived_features": derived_features,
        "replay_results": replay_results,
        "chase_exit_scores": [],
        "recovery": recovery,
        "mfe_mae_trajectory": trajectory,
        "MFE": mae_mfe["MFE"],
        "MFE_time": mae_mfe["MFE_time"],
        "MAE": mae_mfe["MAE"],
        "MAE_time": mae_mfe["MAE_time"],
        "completeness": completeness,
        "n_green": 0,
        "n_red": 0,
        "n": 0,
        "policy_reject_n1_100_green": policy_reject_n1_perfect_green(1, True),
        "note": "collector stores tape+touches; Stage-1 exit sweep is query-time; n=1 100% green is not policy",
    }


def compact_json_line(record: Mapping[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=True)


def write_order_multiverse(path: str, record: Mapping[str, Any]) -> str:
    line = compact_json_line(record)
    if line.count("\n"):
        raise ValueError("order_multiverse row must be one JSON line")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return line
