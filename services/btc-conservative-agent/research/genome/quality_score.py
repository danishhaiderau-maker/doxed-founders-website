"""DNA Quality score and 95% confidence intervals."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple


def wilson_ci(wins: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def ev_confidence_interval(pnls: List[float]) -> Dict[str, float]:
    n = len(pnls)
    if n == 0:
        return {"low": 0.0, "high": 0.0, "ev": 0.0}
    ev = sum(pnls) / n
    if n == 1:
        return {"low": ev, "high": ev, "ev": ev}
    var = sum((x - ev) ** 2 for x in pnls) / (n - 1)
    se = math.sqrt(var / n)
    margin = 1.96 * se
    return {"low": ev - margin, "high": ev + margin, "ev": ev}


def research_confidence_label(n: int) -> str:
    if n >= 100:
        return "HIGH"
    if n >= 30:
        return "MODERATE"
    return "LOW"


def dna_quality(
    sample_size: int,
    win_rate: float,
    ev: float,
    stability: float = 1.0,
) -> float:
    """Composite 0–100 score — sample-size gated."""
    if sample_size <= 0:
        return 0.0
    size_factor = min(1.0, sample_size / 100.0)
    wr_factor = max(0.0, min(1.0, win_rate))
    ev_factor = max(0.0, min(1.0, (ev + 1.0) / 2.0))
    score = 100.0 * size_factor * wr_factor * ev_factor * max(0.0, min(1.0, stability))
    return round(score, 1)


def summarize_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnls = [float(t.get("pnl_usd") or 0) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    n = len(pnls)
    wr = wins / n if n else 0.0
    ci = ev_confidence_interval(pnls)
    wr_lo, wr_hi = wilson_ci(wins, n)
    return {
        "sample_size": n,
        "win_rate": round(wr, 4),
        "ev": round(ci["ev"], 4),
        "confidence_interval_95": {"low": round(ci["low"], 4), "high": round(ci["high"], 4)},
        "win_rate_ci_95": {"low": round(wr_lo, 4), "high": round(wr_hi, 4)},
        "dna_quality": dna_quality(n, wr, ci["ev"]),
        "research_confidence": research_confidence_label(n),
    }
