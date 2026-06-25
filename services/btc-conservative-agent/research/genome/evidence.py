"""Statistical evidence, stability trends, and explainability for discoveries."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from research.genome.quality_score import summarize_trades


def _period_key(iso_ts: str) -> str:
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return f"{dt.year}-W{dt.isocalendar()[1]:02d}"
    except (TypeError, ValueError):
        return "unknown"


def statistical_evidence(pnls: List[float]) -> Dict[str, Any]:
    """Sample size, CI, effect size, approximate significance — never recommend on weak n."""
    summary = summarize_trades([{"pnl_usd": p} for p in pnls])
    n = summary["sample_size"]
    ev = summary["ev"]
    ci = summary["confidence_interval_95"]
    effect_size = round(ev / max(abs(ci["high"] - ci["low"]) / 2, 0.01), 3) if n else 0.0
    p_value = _approx_p_value_ev_gt_zero(pnls) if n >= 5 else None
    statistically_significant = (
        n >= 30
        and ci["low"] > 0
        and (p_value is None or p_value < 0.05)
    )
    return {
        "sample_size": n,
        "expected_value_usd": ev,
        "confidence_interval_95": ci,
        "win_rate_ci_95": summary.get("win_rate_ci_95"),
        "effect_size": effect_size,
        "p_value_ev_gt_zero": round(p_value, 4) if p_value is not None else None,
        "statistically_significant": statistically_significant,
        "dna_quality": summary["dna_quality"],
        "research_confidence": summary["research_confidence"],
        "recommendation_allowed": n >= 30 and summary["research_confidence"] != "LOW",
    }


def _approx_p_value_ev_gt_zero(pnls: List[float]) -> Optional[float]:
    n = len(pnls)
    if n < 5:
        return None
    mean = sum(pnls) / n
    var = sum((x - mean) ** 2 for x in pnls) / max(n - 1, 1)
    if var <= 0:
        return 0.0 if mean > 0 else 1.0
    se = math.sqrt(var / n)
    if se <= 0:
        return 0.0 if mean > 0 else 1.0
    z = mean / se
    # one-sided normal approximation
    return round(0.5 * math.erfc(z / math.sqrt(2)), 4)


def stability_from_ledger(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Trend from evidence ledger — Increasing / Stable / Decreasing."""
    if len(history) < 2:
        return {
            "trend": "INSUFFICIENT_DATA",
            "stable": False,
            "observations": len(history),
        }
    wrs = [float(h.get("win_rate") or 0) for h in history[-4:]]
    evs = [float(h.get("ev_usd") or h.get("expected_value_usd") or 0) for h in history[-4:]]
    wr_delta = wrs[-1] - wrs[0]
    ev_delta = evs[-1] - evs[0]
    if ev_delta > 0.15 and wr_delta >= 0:
        trend = "INCREASING"
    elif ev_delta < -0.15:
        trend = "DECREASING"
    else:
        trend = "STABLE"
    return {
        "trend": trend,
        "stable": trend == "STABLE" and len(history) >= 3,
        "win_rate_delta": round(wr_delta, 4),
        "ev_delta_usd": round(ev_delta, 4),
        "ledger_points": len(history),
    }


def build_explanation(
    *,
    discovery: Dict[str, Any],
    stats: Dict[str, Any],
    stability: Dict[str, Any],
    benchmark_ev: float = 0.0,
) -> Dict[str, Any]:
    fp = discovery.get("fingerprint") or {}
    return {
        "why": (
            f"Observed {stats['sample_size']} trades in "
            f"{fp.get('session')}/{fp.get('adx_bucket')}/{fp.get('spread_bucket')} "
            f"with EV ${stats['expected_value_usd']:.2f} (95% CI ${stats['confidence_interval_95']['low']:.2f}–"
            f"${stats['confidence_interval_95']['high']:.2f})."
        ),
        "evidence": stats,
        "compared_to_benchmark_ev": round(benchmark_ev, 4),
        "vs_benchmark": round(stats["expected_value_usd"] - benchmark_ev, 4),
        "how_long_observed": discovery.get("first_observed"),
        "last_seen": discovery.get("last_observed"),
        "confidence": stats.get("research_confidence"),
        "stability": stability,
        "human_review_required": True,
        "execution_change": "NEVER — advisory only",
    }


def ledger_snapshot(
    entity_type: str,
    entity_id: str,
    ts: str,
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "period_key": _period_key(ts),
        "ts": ts,
        **metrics,
    }
