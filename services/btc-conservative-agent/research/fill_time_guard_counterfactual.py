"""Research-only fill-time direction guard counterfactuals.

This module never changes an order.  It joins an observed paper fill to only
market observations available at or before that fill and reports how candidate
momentum and order-flow gates would have changed the observed PnL.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable, Mapping


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ts(value):
    number = _num(value)
    if number is not None:
        return number
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _trade_id(row):
    return str(row.get("trade_id") or row.get("event_id") or "")


def _direction(row):
    return str(row.get("final_direction") or row.get("dir") or row.get("direction") or "").upper()


def _prior_price(tape, target):
    eligible = [row for row in tape if _ts(row.get("bucket_ts") or row.get("source_ts")) <= target]
    if not eligible:
        return None
    row = max(eligible, key=lambda item: _ts(item.get("bucket_ts") or item.get("source_ts")))
    return _num(row.get("last") or row.get("bid") or row.get("ask"))


def _blocked_summary(rows, predicate):
    blocked = [row for row in rows if predicate(row)]
    pnls = [row["pnl_usd"] for row in blocked]
    clusters = defaultdict(lambda: {"trades": 0, "blocked": 0, "pnl_usd": 0.0})
    regimes = defaultdict(lambda: {"blocked": 0, "pnl_usd": 0.0})
    for row in rows:
        cell = clusters[row["cluster_id"]]
        cell["trades"] += 1
        if row in blocked:
            cell["blocked"] += 1
            cell["pnl_usd"] += row["pnl_usd"]
            regime = row.get("regime") or "UNKNOWN"
            regimes[regime]["blocked"] += 1
            regimes[regime]["pnl_usd"] += row["pnl_usd"]
    return {
        "blocked": len(blocked),
        "blocked_winners": sum(pnl > 0 for pnl in pnls),
        "blocked_losers": sum(pnl < 0 for pnl in pnls),
        "net_saved_usd": round(-sum(pnls), 6),
        "winner_opportunity_cost_usd": round(sum(max(0.0, pnl) for pnl in pnls), 6),
        "loss_avoided_usd": round(-sum(min(0.0, pnl) for pnl in pnls), 6),
        "clusters": [
            {"cluster_id": key, **value, "pnl_usd": round(value["pnl_usd"], 6)}
            for key, value in sorted(clusters.items()) if value["blocked"]
        ],
        "regimes": [
            {"regime": key, **value, "pnl_usd": round(value["pnl_usd"], 6)}
            for key, value in sorted(regimes.items())
        ],
    }


def build_fill_time_guard_counterfactual(
    *,
    trades: Iterable[Mapping],
    executions: Iterable[Mapping],
    ai_inputs: Iterable[Mapping],
    tape_rows: Iterable[Mapping],
    source_observations: Iterable[Mapping] = (),
    epoch_id: str = "",
    momentum_horizons_sec=(30, 60, 180),
    adverse_thresholds_bps=(0.0, 1.0, 2.0, 5.0),
    flow_windows_sec=(30, 60),
    short_confirmation_max=(-0.10, 0.0, 0.10),
):
    execution_by_id = {
        _trade_id(row): row for row in executions
        if _trade_id(row) and row.get("fill_ts") is not None
    }
    ai_by_call = {
        str(row.get("trade_id") or row.get("shared_ai_call_id") or ""): row
        for row in ai_inputs
    }
    tape = sorted(
        [dict(row) for row in tape_rows if _ts(row.get("bucket_ts") or row.get("source_ts")) is not None],
        key=lambda row: _ts(row.get("bucket_ts") or row.get("source_ts")),
    )
    executable_ids = {
        str(row.get("canonical_trade_id") or "") for row in source_observations
        if str(row.get("fill_gate_verdict") or "").upper() == "EXECUTABLE"
    }
    rows, exclusions = [], defaultdict(int)
    for trade in trades:
        trade = dict(trade)
        tid = _trade_id(trade)
        execution = execution_by_id.get(tid)
        if not execution:
            exclusions["FILL_EXECUTION_RECEIPT_MISSING"] += 1
            continue
        if epoch_id and str(execution.get("epoch_id") or "") != str(epoch_id):
            exclusions["EPOCH_ID_MISMATCH"] += 1
            continue
        fill_ts = _ts(execution.get("fill_ts"))
        fill_price = _num(execution.get("fill_price"))
        direction = _direction(trade) or _direction(execution)
        if fill_ts is None or not fill_price or direction not in {"LONG", "SHORT"}:
            exclusions["FILL_IDENTITY_INCOMPLETE"] += 1
            continue
        shared = str(trade.get("shared_ai_call_id") or execution.get("shared_ai_call_id") or tid)
        ai = ai_by_call.get(shared, {})
        features = ai.get("features") or (ai.get("context") or {})
        ema9, ema21, ema200 = (_num(features.get(name)) for name in ("ema9", "ema21", "ema200"))
        plus_di, minus_di = _num(features.get("plus_di")), _num(features.get("minus_di"))
        slow_direction_confirmed = bool(
            None not in (ema9, ema21, ema200, plus_di, minus_di)
            and ((direction == "SHORT" and ema9 < ema21 <= ema200 and minus_di > plus_di)
                 or (direction == "LONG" and ema9 > ema21 >= ema200 and plus_di > minus_di))
        )
        momentum_bps = {}
        for horizon in momentum_horizons_sec:
            prior = _prior_price(tape, fill_ts - float(horizon))
            if prior:
                raw = (fill_price - prior) / prior * 10_000.0
                momentum_bps[str(horizon)] = round(raw if direction == "SHORT" else -raw, 6)
            else:
                momentum_bps[str(horizon)] = None
        flow_imbalance = {}
        for window in flow_windows_sec:
            sample = [row for row in tape if fill_ts - window <= _ts(row.get("bucket_ts") or row.get("source_ts")) <= fill_ts]
            buy = sum(_num(row.get("buy_qty"), 0.0) for row in sample)
            sell = sum(_num(row.get("sell_qty"), 0.0) for row in sample)
            denom = buy + sell
            # Positive means buy pressure. Convert to adverse-direction sign:
            # positive is adverse for SHORT, negative is adverse for LONG.
            raw = (buy - sell) / denom if denom > 0 else None
            flow_imbalance[str(window)] = None if raw is None else round(raw if direction == "SHORT" else -raw, 6)
        rows.append({
            "trade_id": tid,
            "cluster_id": shared,
            "direction": direction,
            "regime": str(trade.get("regime") or (ai.get("context") or {}).get("regime") or "UNKNOWN"),
            "pnl_usd": _num(trade.get("net_pnl_usd") or trade.get("outcome_net_pnl_usd"), 0.0),
            "fill_ts": fill_ts,
            "fill_price": fill_price,
            "conservative_fill_evidence": tid in executable_ids,
            "slow_ema_dmi_direction_confirmed": slow_direction_confirmed,
            "adx": _num(features.get("adx")),
            "momentum_adverse_bps": momentum_bps,
            "order_flow_adverse_imbalance": flow_imbalance,
        })
    momentum_grid = []
    for horizon in momentum_horizons_sec:
        for threshold in adverse_thresholds_bps:
            result = _blocked_summary(
                rows,
                lambda row, h=str(horizon), t=float(threshold):
                    row["momentum_adverse_bps"].get(h) is not None
                    and row["momentum_adverse_bps"][h] >= t,
            )
            momentum_grid.append({"horizon_sec": horizon, "adverse_threshold_bps": threshold, **result})
    flow_grid = []
    for window in flow_windows_sec:
        for maximum in short_confirmation_max:
            result = _blocked_summary(
                rows,
                lambda row, w=str(window), m=float(maximum):
                    row["order_flow_adverse_imbalance"].get(w) is None
                    or row["order_flow_adverse_imbalance"][w] > m,
            )
            flow_grid.append({"window_sec": window, "max_adverse_imbalance_for_confirmation": maximum, **result})
    independent_clusters = len({row["cluster_id"] for row in rows})
    insufficiency = []
    if independent_clusters < 30:
        insufficiency.append("FEWER_THAN_30_INDEPENDENT_CLUSTERS")
    if len({row["regime"] for row in rows}) < 3:
        insufficiency.append("REGIME_COVERAGE_INCOMPLETE")
    if any(not row["conservative_fill_evidence"] for row in rows):
        insufficiency.append("CONSERVATIVE_FILL_EVIDENCE_INCOMPLETE")
    # The current 1s tape carries price/BBO/trade flow, but not recomputed EMA
    # or DMI snapshots. Slow EMA/DMI below is therefore signal-time context;
    # the report must not present it as a fill-time indicator observation.
    if rows:
        insufficiency.append("FILL_TIME_EMA_DMI_SERIES_UNAVAILABLE_SIGNAL_TIME_CONTEXT_ONLY")
    return {
        "schema": "fill_time_guard_counterfactual_v1",
        "research_only": True,
        "changes_execution": False,
        "epoch_id": str(epoch_id or ""),
        "evidence_scope": "CURRENT_SIGNED_EPOCH_ONLY",
        "observed_trades": len(rows),
        "independent_clusters": independent_clusters,
        "exclusions": dict(sorted(exclusions.items())),
        "trade_features": rows,
        "ema_dmi_basis": "SIGNAL_TIME_AI_INPUT_CONTEXT_ONLY",
        "momentum_ema_dmi_candidates": momentum_grid,
        "microstructure_order_flow_candidates": flow_grid,
        "insufficiency": insufficiency,
        "qualification": "INSUFFICIENT_RESEARCH_ONLY" if insufficiency else "DESCRIPTIVE_RESEARCH_ONLY",
    }
