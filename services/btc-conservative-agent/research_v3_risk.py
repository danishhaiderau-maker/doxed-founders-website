"""Chronological portfolio risk metrics for Safe Policy Genome."""
from __future__ import annotations

import math
from statistics import mean
from typing import Iterable


def portfolio_risk_metrics(pnls: Iterable[float], *, starting_equity_usd: float) -> dict:
    values = [float(value) for value in pnls]
    if starting_equity_usd <= 0:
        raise ValueError("starting_equity_usd must be positive")
    equity = float(starting_equity_usd)
    peak = equity
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    current_underwater = 0
    max_underwater = 0
    recovery_lengths = []
    drawdown_squares = []
    longest_loss_streak = current_loss_streak = 0
    gross_profit = gross_loss = 0.0
    for pnl in values:
        equity += pnl
        if pnl > 0:
            gross_profit += pnl
            current_loss_streak = 0
        elif pnl < 0:
            gross_loss += abs(pnl)
            current_loss_streak += 1
            longest_loss_streak = max(longest_loss_streak, current_loss_streak)
        if equity >= peak:
            if current_underwater:
                recovery_lengths.append(current_underwater)
            peak = equity
            current_underwater = 0
        else:
            current_underwater += 1
            max_underwater = max(max_underwater, current_underwater)
        dd_usd = equity - peak
        dd_pct = 100.0 * dd_usd / peak if peak else 0.0
        max_dd_usd = min(max_dd_usd, dd_usd)
        max_dd_pct = min(max_dd_pct, dd_pct)
        drawdown_squares.append(dd_pct * dd_pct)
    if current_underwater:
        recovery_lengths.append(current_underwater)
    sorted_losses = sorted((value for value in values if value < 0))
    tail_n = max(1, math.ceil(len(values) * 0.05)) if values else 0
    cvar95 = mean(sorted(values)[:tail_n]) if tail_n else None
    net = sum(values)
    return {
        "schema": "portfolio_risk_metrics_v1",
        "episodes": len(values),
        "starting_equity_usd": round(starting_equity_usd, 6),
        "ending_equity_usd": round(equity, 6),
        "net_pnl_usd": round(net, 6),
        "max_drawdown_usd": round(max_dd_usd, 6),
        "max_drawdown_pct": round(max_dd_pct, 6),
        "ulcer_index_pct": round(math.sqrt(mean(drawdown_squares)), 6) if drawdown_squares else None,
        "cvar95_usd": round(cvar95, 6) if cvar95 is not None else None,
        "worst_trade_usd": round(min(values), 6) if values else None,
        "longest_loss_streak": longest_loss_streak,
        "max_underwater_episodes": max_underwater,
        "max_recovery_episodes": max(recovery_lengths, default=0),
        "profit_factor": None if gross_loss == 0 else round(gross_profit / gross_loss, 6),
        "recovery_factor": None if max_dd_usd == 0 else round(net / abs(max_dd_usd), 6),
        "wins": sum(value > 0 for value in values),
        "losses": len(sorted_losses),
        "realized_zero": sum(value == 0 for value in values),
    }


def drawdown_budget_gate(metrics: dict, *, max_drawdown_usd: float, max_drawdown_pct: float, min_cvar95_usd: float) -> dict:
    reasons = []
    if abs(float(metrics.get("max_drawdown_usd") or 0)) > float(max_drawdown_usd):
        reasons.append("MAX_DRAWDOWN_USD_EXCEEDED")
    if abs(float(metrics.get("max_drawdown_pct") or 0)) > float(max_drawdown_pct):
        reasons.append("MAX_DRAWDOWN_PCT_EXCEEDED")
    cvar = metrics.get("cvar95_usd")
    if cvar is None or float(cvar) < float(min_cvar95_usd):
        reasons.append("CVAR95_BUDGET_FAILED")
    return {"passed": not reasons, "reasons": reasons}

