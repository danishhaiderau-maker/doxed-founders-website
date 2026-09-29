"""Hypothesis engine — supported / invalidated hypotheses each analyzer cycle.

The tile roster comes from the canonical registry (combo_pathway_config);
Continuous is an analytical comparison label only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    ACTIVE_TILE_REGISTRY,
    COMPARISON_BENCHMARK_LANE,
)
from research.genome.quality_score import summarize_trades


def trades_by_active_tile(trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group trade rows by registry lane, in registry display order."""
    grouped: Dict[str, List[Dict[str, Any]]] = {lane: [] for lane in ACTIVE_TILE_ORDER}
    for trade in trades or []:
        lane = str(trade.get("research_lane") or "").upper()
        if lane in grouped:
            grouped[lane].append(trade)
    return grouped


def generate_hypotheses(
    tile_trades: Mapping[str, List[Dict[str, Any]]],
    comparison_trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    bench = summarize_trades(comparison_trades)
    supported: List[Dict[str, Any]] = []
    invalidated: List[Dict[str, Any]] = []

    for lane in ACTIVE_TILE_ORDER:
        label = ACTIVE_TILE_REGISTRY[lane]["label"]
        tile = summarize_trades(list(tile_trades.get(lane) or []))
        if tile["sample_size"] >= 30 and tile["ev"] > 0:
            supported.append({
                "lane": lane,
                "hypothesis": f"{label} shows positive EV in current sample",
                "evidence": f"n={tile['sample_size']} EV={tile['ev']}",
                "dna_quality": tile["dna_quality"],
                "confidence": tile["research_confidence"],
                "recommended_action": "Continue collection — advisory only",
            })
        elif tile["sample_size"] >= 30:
            invalidated.append({
                "lane": lane,
                "hypothesis": f"{label} shows positive EV",
                "evidence": f"n={tile['sample_size']} EV={tile['ev']}",
                "recommended_action": "Monitor kill criteria",
            })

        if tile["sample_size"] >= 50 and bench["sample_size"] >= 50:
            row = {
                "lane": lane,
                "hypothesis": f"{label} beats {COMPARISON_BENCHMARK_LANE} analytical comparison",
                "evidence": f"TILE EV={tile['ev']} vs COMPARISON EV={bench['ev']}",
            }
            if tile["ev"] > bench["ev"]:
                supported.append({
                    **row,
                    "confidence": tile["research_confidence"],
                    "recommended_action": "Continue collection — not promotion-ready until all criteria met",
                })
            else:
                invalidated.append({**row, "recommended_action": "Review kill criteria"})

    return {
        "supported": supported,
        "invalidated": invalidated,
        "disclaimer": "Recommendations are advisory only — analyzer never changes execution",
    }
