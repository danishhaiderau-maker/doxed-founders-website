"""Hypothesis engine — supported / invalidated hypotheses each analyzer cycle."""
from __future__ import annotations

from typing import Any, Dict, List

from research.genome.quality_score import summarize_trades


def generate_hypotheses(
    combo_604_trades: List[Dict[str, Any]],
    continuous_trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    c604 = summarize_trades(combo_604_trades)
    bench = summarize_trades(continuous_trades)
    supported: List[Dict[str, Any]] = []
    invalidated: List[Dict[str, Any]] = []

    if c604["sample_size"] >= 30 and c604["ev"] > 0:
        supported.append({
            "hypothesis": "COMBO_604 shows positive EV in current sample",
            "evidence": f"n={c604['sample_size']} EV={c604['ev']}",
            "dna_quality": c604["dna_quality"],
            "confidence": c604["research_confidence"],
            "recommended_action": "Continue collection — advisory only",
        })
    elif c604["sample_size"] >= 30:
        invalidated.append({
            "hypothesis": "COMBO_604 shows positive EV",
            "evidence": f"n={c604['sample_size']} EV={c604['ev']}",
            "recommended_action": "Monitor kill criteria",
        })

    if c604["sample_size"] >= 50 and bench["sample_size"] >= 50:
        if c604["ev"] > bench["ev"]:
            supported.append({
                "hypothesis": "COMBO_604 beats CONTINUOUS benchmark",
                "evidence": f"604 EV={c604['ev']} vs CONT={bench['ev']}",
                "confidence": c604["research_confidence"],
                "recommended_action": "Continue collection — not promotion-ready until all criteria met",
            })
        else:
            invalidated.append({
                "hypothesis": "COMBO_604 beats CONTINUOUS benchmark",
                "evidence": f"604 EV={c604['ev']} vs CONT={bench['ev']}",
                "recommended_action": "Review kill criteria",
            })

    return {
        "supported": supported,
        "invalidated": invalidated,
        "disclaimer": "Recommendations are advisory only — analyzer never changes execution",
    }
