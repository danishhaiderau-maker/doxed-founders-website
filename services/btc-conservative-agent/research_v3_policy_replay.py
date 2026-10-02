"""Composable, first-hit Safe Policy Genome replay on ordered price evidence."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import numpy as np

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


class PreparedReplayArrays:
    """One post-fill path shared by every protection replayed from one fill.

    Holds the margin-return series, its running extrema and per-profit-rule
    floor series, so each policy resolves its exit without rescanning the path.
    """

    __slots__ = ("ts", "price", "cur", "mfe", "mae", "neg_mae", "underwater", "favorable", "age",
                 "age_sorted", "n", "floors", "seg_start", "seg_mfe", "seg_min_cur")

    def __init__(self, ordered: Iterable[tuple[float, float]], *, direction: str,
                 entry_price: float, leverage: float, fill_ts: float) -> None:
        pairs = np.asarray(ordered if isinstance(ordered, np.ndarray) else tuple(ordered), dtype=np.float64).reshape(-1, 2)
        self.ts = pairs[:, 0]
        self.price = pairs[:, 1]
        raw = (self.price - entry_price) / entry_price * 100.0
        self.cur = raw * leverage if direction == "LONG" else -raw * leverage
        self.mfe = np.maximum.accumulate(self.cur) if len(self.cur) else self.cur
        self.mae = np.minimum.accumulate(self.cur) if len(self.cur) else self.cur
        self.neg_mae = -self.mae
        self.underwater = np.cumsum(self.cur < 0)
        self.favorable = np.cumsum(self.cur > 0)
        self.age = self.ts - fill_ts
        self.age_sorted = bool(np.all(np.diff(self.age) >= 0))
        self.n = len(self.cur)
        self.floors: dict[tuple[Any, ...], tuple[np.ndarray, np.ndarray, int | None]] = {}
        # Running MFE is a step function; every floor rule reads only MFE, so
        # floors are constant between new highs. Segment k starts at a new high.
        if self.n:
            self.seg_start = np.flatnonzero(np.concatenate(([True], self.mfe[1:] != self.mfe[:-1])))
            self.seg_mfe = self.mfe[self.seg_start]
            self.seg_min_cur = np.minimum.reduceat(self.cur, self.seg_start)
        else:
            self.seg_start = self.seg_mfe = self.seg_min_cur = np.empty(0)


class ReplayPlan:
    """Numeric fields of one validated policy spec, parsed once per protection."""

    __slots__ = ("hard_stop", "atr_tp", "atr_sl", "thesis_cut", "thesis_window", "time_stop_sec",
                 "be_arm", "be_arm_atr", "be_floor", "giveback_abs", "giveback_fraction", "mode",
                 "atr_trail_k", "chandelier_k", "trail_activation_k", "partials", "ladder", "floor_key")

    def __init__(self, policy_spec: Mapping[str, Any]) -> None:
        loss = policy_spec["loss_protection"]
        profit = policy_spec["profit_protection"]
        self.atr_tp = None if profit.get("atr_tp_k") is None else float(profit["atr_tp_k"])
        self.atr_sl = None if loss.get("atr_stop_k") is None else float(loss["atr_stop_k"])
        self.hard_stop = float(loss.get("hard_stop_margin_pct"))
        self.thesis_cut = None if loss.get("thesis_cut_margin_pct") is None else float(loss["thesis_cut_margin_pct"])
        self.thesis_window = float(loss.get("thesis_window_sec") or 0)
        self.time_stop_sec = None if loss.get("time_stop_min") is None else float(loss["time_stop_min"]) * 60.0
        self.be_arm = None if profit.get("break_even_arm_mfe_pct") is None else float(profit["break_even_arm_mfe_pct"])
        self.be_arm_atr = None if profit.get("break_even_arm_atr_k") is None else float(profit["break_even_arm_atr_k"])
        self.be_floor = float(profit.get("break_even_floor_pct") or 0)
        self.giveback_abs = None if profit.get("mfe_giveback_abs_pct") is None else float(profit["mfe_giveback_abs_pct"])
        self.giveback_fraction = None if profit.get("mfe_giveback_fraction") is None else float(profit["mfe_giveback_fraction"])
        self.mode = str(profit.get("mode") or "ATR_TARGET")
        self.atr_trail_k = None if profit.get("atr_trail_k") is None else float(profit["atr_trail_k"])
        self.chandelier_k = None if profit.get("chandelier_atr_k") is None else float(profit["chandelier_atr_k"])
        self.trail_activation_k = float(profit.get("trail_activation_atr_k") or 0)
        self.partials = tuple(tuple(map(float, rung)) for rung in (profit.get("partial_take_profits") or []))
        self.ladder = tuple(tuple(map(float, rung)) for rung in (profit.get("ladder") or []))
        self.floor_key = (self.be_arm, self.be_arm_atr, self.be_floor, self.giveback_abs, self.giveback_fraction,
                          self.mode, self.atr_trail_k, self.chandelier_k, self.trail_activation_k, self.ladder)


def _first_at_least(running: np.ndarray, value: float, end: int) -> int | None:
    """First index < end where a non-decreasing series reaches value."""
    index = int(np.searchsorted(running, value, side="left"))
    return index if index < end else None


def _first_true(mask: np.ndarray) -> int | None:
    if not len(mask):
        return None
    index = int(np.argmax(mask))
    return index if mask[index] else None


def _floor_series(path: PreparedReplayArrays, plan: ReplayPlan,
                  atr_margin_pct: float) -> tuple[np.ndarray, np.ndarray, int | None]:
    """Per-segment armed/active floor and the first path index touching it.

    Each floor term is an elementwise function of MFE, and MFE is constant
    within a segment, so evaluating the terms once per segment and
    accumulating across segments reproduces the per-second series exactly.
    """
    key = (plan.floor_key, atr_margin_pct)
    cached = path.floors.get(key)
    if cached is not None:
        return cached
    mfe = path.seg_mfe
    floor = np.full(len(mfe), -np.inf)
    armed = np.zeros(len(mfe), dtype=bool)

    def add(condition: np.ndarray, value: Any) -> None:
        nonlocal floor, armed
        floor = np.maximum(floor, np.where(condition, value, -np.inf))
        armed |= condition

    if plan.be_arm is not None:
        add(mfe >= plan.be_arm, plan.be_floor)
    if plan.be_arm_atr is not None:
        add(mfe >= plan.be_arm_atr * atr_margin_pct, plan.be_floor)
    if plan.giveback_abs is not None:
        add(mfe > 0, mfe - plan.giveback_abs)
    if plan.giveback_fraction is not None:
        add(mfe > 0, mfe * (1.0 - plan.giveback_fraction))
    if plan.mode in {"ATR_TRAIL", "HYBRID_RUNNER"} and plan.atr_trail_k is not None:
        add(mfe >= plan.trail_activation_k * atr_margin_pct, mfe - plan.atr_trail_k * atr_margin_pct)
    if plan.mode == "CHANDELIER" and plan.chandelier_k is not None:
        add(mfe >= plan.trail_activation_k * atr_margin_pct, mfe - plan.chandelier_k * atr_margin_pct)
    for trigger, value in plan.ladder:
        add(mfe >= trigger, value)
    armed = np.logical_or.accumulate(armed)
    active = np.maximum.accumulate(floor)
    first = None
    segment = _first_true(armed & (path.seg_min_cur <= active))
    if segment is not None:
        start = int(path.seg_start[segment])
        stop = int(path.seg_start[segment + 1]) if segment + 1 < len(path.seg_start) else path.n
        first = start + int(np.argmax(path.cur[start:stop] <= active[segment]))
    cached = path.floors[key] = (armed, active, first)
    return cached


def _replay_exit(path: PreparedReplayArrays, plan: ReplayPlan, atr_pct_at_fill: float,
                 leverage: float) -> tuple[int, str, float, int, float, float | None]:
    """Exit index, reason, realised margin, partial count, remaining fraction, floor."""
    n = path.n
    cur, mfe = path.cur, path.mfe
    atr_margin_pct = float(atr_pct_at_fill) * float(leverage)
    # Precedence order of the scalar replay; a later rule wins only strictly
    # earlier. Every rule except the floor is the first crossing of a running
    # extreme (or of monotone age), so a binary search equals a full scan.
    best, exit_reason = n, "PATH_END"
    hit = _first_at_least(path.neg_mae, plan.hard_stop, n)
    if hit is not None:
        best, exit_reason = hit, "PHYSICAL_HARD_STOP"
    if best > 0 and plan.atr_sl is not None:
        hit = _first_at_least(path.neg_mae, atr_margin_pct * plan.atr_sl, best)
        if hit is not None:
            best, exit_reason = hit, "ATR_STOP"
    if best > 0 and plan.thesis_cut is not None:
        if path.age_sorted:
            window_end = min(best, int(np.searchsorted(path.age, plan.thesis_window, side="right")))
            hit = _first_at_least(path.neg_mae, -plan.thesis_cut, window_end)
        else:
            hit = _first_true((path.age[:best] <= plan.thesis_window) & (cur[:best] <= plan.thesis_cut))
        if hit is not None:
            best, exit_reason = hit, "THESIS_FAST_CUT"
    armed, active, floor_first = _floor_series(path, plan, atr_margin_pct)
    if best > 0 and floor_first is not None and floor_first < best:
        best, exit_reason = floor_first, "PROFIT_PROTECTION_FLOOR"
    if best > 0 and plan.time_stop_sec is not None:
        if path.age_sorted:
            hit = _first_at_least(path.age, plan.time_stop_sec, best)
        else:
            hit = _first_true(path.age[:best] >= plan.time_stop_sec)
        if hit is not None:
            best, exit_reason = hit, "TIME_STOP"
    if best > 0 and plan.mode in {"ATR_TARGET", "HYBRID_RUNNER"} and plan.atr_tp is not None:
        hit = _first_at_least(mfe, atr_margin_pct * plan.atr_tp, best)
        if hit is not None:
            best, exit_reason = hit, "ATR_TAKE_PROFIT"
    exit_index = best if best < n else n - 1
    remaining_fraction = 1.0
    realized_margin_weighted = 0.0
    taken = []
    for index, (trigger_k, fraction) in enumerate(plan.partials):
        hit = _first_at_least(mfe, trigger_k * atr_margin_pct, exit_index + 1)
        if hit is not None:
            taken.append((hit, index, fraction))
    partials_taken = 0
    for hit, _index, fraction in sorted(taken):
        if remaining_fraction <= 0:
            continue
        close_fraction = min(fraction, remaining_fraction)
        realized_margin_weighted += close_fraction * float(cur[hit])
        remaining_fraction -= close_fraction
        partials_taken += 1
    realized_margin_weighted += remaining_fraction * float(cur[exit_index])
    segment = int(np.searchsorted(path.seg_start, exit_index, side="right")) - 1
    active_floor = float(active[segment]) if armed[segment] else None
    return exit_index, exit_reason, realized_margin_weighted, partials_taken, remaining_fraction, active_floor


def replay_cell(path: PreparedReplayArrays, plan: ReplayPlan, *, atr_pct_at_fill: float,
                leverage: float, margin_usd: float) -> dict[str, Any]:
    """The screen's stored fields of ``replay_protected_policy_arrays``, identically rounded."""
    n = path.n
    if not n:
        return {"status": "CENSORED", "exit_reason": None, "net_pnl_usd": None,
                "profit_retention_ratio": None, "profit_giveback_pct": None,
                "underwater_observation_ratio": None}
    exit_index, exit_reason, realized, _partials, _remaining, _floor = _replay_exit(
        path, plan, atr_pct_at_fill, leverage)
    terminal_mfe = float(path.mfe[exit_index])
    gross_usd = margin_usd * realized / 100.0
    return {
        "status": "COMPLETE",
        "exit_reason": exit_reason,
        "net_pnl_usd": round(gross_usd - 0.0 - 0.0, 8),
        "profit_retention_ratio": round(max(0.0, realized) / terminal_mfe, 8) if terminal_mfe > 0 else None,
        "profit_giveback_pct": round(max(0.0, terminal_mfe - float(path.cur[exit_index])), 8),
        "underwater_observation_ratio": round(int(path.underwater[exit_index]) / n, 8),
    }


def replay_protected_policy_arrays(
    path: PreparedReplayArrays,
    *,
    direction: str,
    entry_price: float,
    atr_pct_at_fill: float,
    leverage: float,
    margin_usd: float,
    policy_spec: Mapping[str, Any],
    funding_usd: float = 0.0,
    slippage_usd: float = 0.0,
    spec_validated: bool = False,
    plan: ReplayPlan | None = None,
) -> dict[str, Any]:
    """Vectorised twin of ``replay_protected_policy`` without a trace.

    Same precedence, floors, partial realisation and rounding, so every field
    equals the canonical scalar replay on the same ordered path.
    """
    if not spec_validated:
        defects = validate_policy_spec(policy_spec)
        if defects:
            return {"schema": "safe_policy_replay_v3", "status": "UNSUPPORTED", "reasons": defects, "ranking_eligible": False}
    n = path.n
    if not n:
        return {"schema": "safe_policy_replay_v3", "status": "CENSORED", "reasons": ["NO_POST_FILL_PATH"], "ranking_eligible": False}
    exit_index, exit_reason, realized_margin_weighted, partials_taken, remaining_fraction, active_floor = _replay_exit(
        path, plan or ReplayPlan(policy_spec), atr_pct_at_fill, leverage)
    exit_margin = float(path.cur[exit_index])
    gross_usd = margin_usd * realized_margin_weighted / 100.0
    net_usd = gross_usd - float(funding_usd) - float(slippage_usd)
    terminal_mfe = float(path.mfe[exit_index])
    return {
        "schema": "safe_policy_replay_v3",
        "status": "COMPLETE",
        "ranking_eligible": True,
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": float(path.price[exit_index]),
        "exit_ts": float(path.ts[exit_index]),
        "exit_reason": exit_reason,
        "gross_pnl_usd": round(gross_usd, 8),
        "trading_fees_usd": 0.0,
        "funding_usd": round(float(funding_usd), 8),
        "slippage_usd": round(float(slippage_usd), 8),
        "net_pnl_usd": round(net_usd, 8),
        "margin_return_pct": round(exit_margin, 8),
        "portfolio_margin_return_pct": round(realized_margin_weighted, 8),
        "mfe_pct": round(terminal_mfe, 8),
        "mae_pct": round(float(path.mae[exit_index]), 8),
        "profit_giveback_pct": round(max(0.0, terminal_mfe - exit_margin), 8),
        "profit_retention_ratio": round(max(0.0, realized_margin_weighted) / terminal_mfe, 8) if terminal_mfe > 0 else None,
        "underwater_observation_ratio": round(int(path.underwater[exit_index]) / n, 8),
        "favorable_observation_ratio": round(int(path.favorable[exit_index]) / n, 8),
        "partial_exit_count": partials_taken,
        "remaining_fraction_at_terminal": round(remaining_fraction, 8),
        "active_floor_pct": active_floor,
        "trace": [],
    }
