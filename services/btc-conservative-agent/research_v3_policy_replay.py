"""Composable, first-hit Safe Policy Genome replay on ordered price evidence."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from research_v3_contract import validate_policy_spec


def prepare_replay_price_path(
    prices: Iterable[Mapping[str, Any]], *, fill_ts: float,
) -> dict[str, Any]:
    """Normalize one post-fill path once for a family of policy replays."""
    ordered: list[tuple[float, float]] = []
    previous = None
    for row in prices:
        ts = float(row.get("ts") or row.get("t") or 0)
        price = float(row.get("price") or row.get("mark") or row.get("close") or 0)
        if ts < fill_ts or price <= 0:
            continue
        if previous is not None and ts <= previous:
            return {"ordered": (), "error": "NON_MONOTONIC_PRICE_PATH"}
        ordered.append((ts, price))
        previous = ts
    return {"ordered": tuple(ordered), "error": None}


def _margin_return_pct(direction: str, entry: float, price: float, leverage: float) -> float:
    raw = (price - entry) / entry * 100.0
    return raw * leverage if direction == "LONG" else -raw * leverage


def replay_protected_policy(
    prices: Iterable[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    fill_ts: float,
    atr_pct_at_fill: float,
    leverage: float,
    margin_usd: float,
    policy_spec: Mapping[str, Any],
    funding_usd: float = 0.0,
    slippage_usd: float = 0.0,
    prepared_price_path: Mapping[str, Any] | None = None,
    collect_trace: bool = True,
) -> dict[str, Any]:
    """Replay ordered marks; ambiguous OHLC bars must be rejected upstream.

    All thresholds are margin-return percentages. Trading fees are exactly zero;
    funding and slippage are explicit arguments and never silently discarded.
    """
    defects = validate_policy_spec(policy_spec)
    if defects:
        return {"schema": "safe_policy_replay_v3", "status": "UNSUPPORTED", "reasons": defects, "ranking_eligible": False}
    prepared = prepared_price_path or prepare_replay_price_path(prices, fill_ts=fill_ts)
    if prepared.get("error"):
        return {"schema": "safe_policy_replay_v3", "status": "DATA_ERROR", "reasons": [str(prepared["error"])], "ranking_eligible": False}
    ordered = prepared.get("ordered") or ()
    if not ordered:
        return {"schema": "safe_policy_replay_v3", "status": "CENSORED", "reasons": ["NO_POST_FILL_PATH"], "ranking_eligible": False}

    loss = policy_spec["loss_protection"]
    profit = policy_spec["profit_protection"]
    atr_tp = profit.get("atr_tp_k")
    atr_sl = loss.get("atr_stop_k")
    tp_margin_pct = None if atr_tp is None else float(atr_pct_at_fill) * float(leverage) * float(atr_tp)
    atr_stop_margin_pct = None if atr_sl is None else float(atr_pct_at_fill) * float(leverage) * float(atr_sl)
    hard_stop = float(loss.get("hard_stop_margin_pct"))
    thesis_cut = loss.get("thesis_cut_margin_pct")
    thesis_window = float(loss.get("thesis_window_sec") or 0)
    time_stop_sec = None if loss.get("time_stop_min") is None else float(loss["time_stop_min"]) * 60.0
    be_arm = profit.get("break_even_arm_mfe_pct")
    be_arm_atr = profit.get("break_even_arm_atr_k")
    be_floor = float(profit.get("break_even_floor_pct") or 0)
    giveback_abs = profit.get("mfe_giveback_abs_pct")
    giveback_fraction = profit.get("mfe_giveback_fraction")
    mode = str(profit.get("mode") or "ATR_TARGET")
    atr_margin_pct = float(atr_pct_at_fill) * float(leverage)
    atr_trail_k = profit.get("atr_trail_k")
    chandelier_k = profit.get("chandelier_atr_k")
    trail_activation_k = float(profit.get("trail_activation_atr_k") or 0)
    partials = [tuple(map(float, rung)) for rung in (profit.get("partial_take_profits") or [])]
    ladder = [tuple(map(float, rung)) for rung in (profit.get("ladder") or [])]

    mfe = float("-inf")
    mae = float("inf")
    active_floor = None
    remaining_fraction = 1.0
    realized_margin_weighted = 0.0
    partials_taken: set[int] = set()
    favorable_observations = underwater_observations = 0
    exit_reason = "PATH_END"
    exit_ts, exit_price = ordered[-1]
    exit_margin = _margin_return_pct(direction, entry_price, exit_price, leverage)
    trace = []
    for ts, price in ordered:
        age = ts - fill_ts
        current = _margin_return_pct(direction, entry_price, price, leverage)
        mfe = max(mfe, current)
        mae = min(mae, current)
        favorable_observations += int(current > 0)
        underwater_observations += int(current < 0)
        candidate_floors = []
        if be_arm is not None and mfe >= float(be_arm):
            candidate_floors.append(be_floor)
        if be_arm_atr is not None and mfe >= float(be_arm_atr) * atr_margin_pct:
            candidate_floors.append(be_floor)
        if giveback_abs is not None and mfe > 0:
            candidate_floors.append(mfe - float(giveback_abs))
        if giveback_fraction is not None and mfe > 0:
            candidate_floors.append(mfe * (1.0 - float(giveback_fraction)))
        if mode in {"ATR_TRAIL", "HYBRID_RUNNER"} and atr_trail_k is not None and mfe >= trail_activation_k * atr_margin_pct:
            candidate_floors.append(mfe - float(atr_trail_k) * atr_margin_pct)
        if mode == "CHANDELIER" and chandelier_k is not None and mfe >= trail_activation_k * atr_margin_pct:
            candidate_floors.append(mfe - float(chandelier_k) * atr_margin_pct)
        for trigger, floor in ladder:
            if mfe >= trigger:
                candidate_floors.append(floor)
        if candidate_floors:
            new_floor = max(candidate_floors)
            active_floor = new_floor if active_floor is None else max(active_floor, new_floor)
        partial_events = []
        for index, (trigger_k, fraction) in enumerate(partials):
            if index in partials_taken or remaining_fraction <= 0:
                continue
            trigger_margin = trigger_k * atr_margin_pct
            if current >= trigger_margin:
                close_fraction = min(fraction, remaining_fraction)
                realized_margin_weighted += close_fraction * current
                remaining_fraction -= close_fraction
                partials_taken.add(index)
                partial_events.append({"trigger_atr_k": trigger_k, "fraction": close_fraction, "margin_return_pct": current})
        reason = None
        # Conservative precedence: protection/loss exits are evaluated before
        # profit target when the same ordered observation crosses both.
        if current <= -hard_stop:
            reason = "PHYSICAL_HARD_STOP"
        elif atr_stop_margin_pct is not None and current <= -atr_stop_margin_pct:
            reason = "ATR_STOP"
        elif thesis_cut is not None and age <= thesis_window and current <= float(thesis_cut):
            reason = "THESIS_FAST_CUT"
        elif active_floor is not None and current <= active_floor:
            reason = "PROFIT_PROTECTION_FLOOR"
        elif time_stop_sec is not None and age >= time_stop_sec:
            reason = "TIME_STOP"
        elif mode in {"ATR_TARGET", "HYBRID_RUNNER"} and tp_margin_pct is not None and current >= tp_margin_pct:
            reason = "ATR_TAKE_PROFIT"
        if collect_trace:
            trace.append({"ts": ts, "margin_return_pct": round(current, 8), "mfe_pct": round(mfe, 8), "active_floor_pct": active_floor, "remaining_fraction": round(remaining_fraction, 8), "partial_exits": partial_events, "exit_reason": reason})
        if reason:
            exit_reason, exit_ts, exit_price, exit_margin = reason, ts, price, current
            break
    realized_margin_weighted += remaining_fraction * exit_margin
    gross_usd = margin_usd * realized_margin_weighted / 100.0
    net_usd = gross_usd - float(funding_usd) - float(slippage_usd)
    return {
        "schema": "safe_policy_replay_v3",
        "status": "COMPLETE",
        "ranking_eligible": True,
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "exit_ts": exit_ts,
        "exit_reason": exit_reason,
        "gross_pnl_usd": round(gross_usd, 8),
        "trading_fees_usd": 0.0,
        "funding_usd": round(float(funding_usd), 8),
        "slippage_usd": round(float(slippage_usd), 8),
        "net_pnl_usd": round(net_usd, 8),
        "margin_return_pct": round(exit_margin, 8),
        "portfolio_margin_return_pct": round(realized_margin_weighted, 8),
        "mfe_pct": round(mfe, 8),
        "mae_pct": round(mae, 8),
        "profit_giveback_pct": round(max(0.0, mfe - exit_margin), 8),
        "profit_retention_ratio": round(max(0.0, realized_margin_weighted) / mfe, 8) if mfe > 0 else None,
        "underwater_observation_ratio": round(underwater_observations / len(ordered), 8),
        "favorable_observation_ratio": round(favorable_observations / len(ordered), 8),
        "partial_exit_count": len(partials_taken),
        "remaining_fraction_at_terminal": round(remaining_fraction, 8),
        "active_floor_pct": active_floor,
        "trace": trace,
    }

