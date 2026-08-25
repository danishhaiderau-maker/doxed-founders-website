"""Hypothesis engine — supported / invalidated hypotheses each analyzer cycle."""
from __future__ import annotations

from typing import Any, Dict, List

from research.genome.quality_score import summarize_trades


def generate_hypotheses(
    patient_chase_trades: List[Dict[str, Any]],
    continuous_trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    patient = summarize_trades(patient_chase_trades)
    bench = summarize_trades(continuous_trades)
    supported: List[Dict[str, Any]] = []
    invalidated: List[Dict[str, Any]] = []

    if patient["sample_size"] >= 30 and patient["ev"] > 0:
        supported.append({
            "hypothesis": "OFFSET_029_ATR_TP_25 shows positive EV in current sample",
            "evidence": f"n={patient['sample_size']} EV={patient['ev']}",
            "dna_quality": patient["dna_quality"],
            "confidence": patient["research_confidence"],
            "recommended_action": "Continue collection — advisory only",
        })
    elif patient["sample_size"] >= 30:
        invalidated.append({
            "hypothesis": "OFFSET_029_ATR_TP_25 shows positive EV",
            "evidence": f"n={patient['sample_size']} EV={patient['ev']}",
            "recommended_action": "Monitor kill criteria",
        })

    if patient["sample_size"] >= 50 and bench["sample_size"] >= 50:
        if patient["ev"] > bench["ev"]:
            supported.append({
                "hypothesis": "OFFSET_029_ATR_TP_25 beats CONTINUOUS benchmark",
                "evidence": f"PATIENT EV={patient['ev']} vs CONT={bench['ev']}",
                "confidence": patient["research_confidence"],
                "recommended_action": "Continue collection — not promotion-ready until all criteria met",
            })
        else:
            invalidated.append({
                "hypothesis": "OFFSET_029_ATR_TP_25 beats CONTINUOUS benchmark",
                "evidence": f"PATIENT EV={patient['ev']} vs CONT={bench['ev']}",
                "recommended_action": "Review kill criteria",
            })

    return {
        "supported": supported,
        "invalidated": invalidated,
        "disclaimer": "Recommendations are advisory only — analyzer never changes execution",
    }
