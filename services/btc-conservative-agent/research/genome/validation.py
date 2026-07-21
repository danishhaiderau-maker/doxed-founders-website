"""Genome validation — run every analyzer cycle."""
from __future__ import annotations

import math
from typing import Any, Dict, List

from research.genome.loader import load_all_layers

MIN_FEATURE_COVERAGE = 0.80
MIN_TRADE_LINKAGE_COVERAGE = 0.95


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _feature_present(
    row: Dict[str, Any], aliases: tuple[str, ...], source_aliases: tuple[str, ...] = (),
) -> bool:
    source_values = [str(row.get(key) or "").strip().lower() for key in source_aliases if key in row]
    if source_values:
        if all(value in ("", "missing", "unknown", "none") for value in source_values):
            return False
        return any(_finite(row.get(key)) for key in aliases)
    values = [float(row.get(key)) for key in aliases if _finite(row.get(key))]
    # Legacy emitters zero-filled missing fields without provenance. Conservatively
    # mark an all-zero legacy value incomplete; new emitters provide source fields.
    return bool(values) and any(value != 0.0 for value in values)


def validate_genome_integrity(db_path: str) -> Dict[str, Any]:
    layers = load_all_layers(db_path)
    markets = {str(m.get("market_genome_id")) for m in layers.get("market") or [] if m.get("market_genome_id")}
    market_rows = layers.get("market") or []
    decisions = layers.get("decision") or []
    trades = layers.get("trade") or []

    orphans = []
    for d in decisions:
        mid = str(d.get("market_genome_id") or "")
        if mid and mid not in markets:
            orphans.append({"type": "decision_orphan_market", "id": d.get("decision_id")})

    broken_trades = []
    for t in trades:
        if not t.get("trade_id"):
            broken_trades.append({"type": "missing_trade_id"})

    ids = [str(t.get("trade_id")) for t in trades if t.get("trade_id")]
    dupes = len(ids) - len(set(ids))

    feature_aliases = {
        "adx": (("adx", "adx_at_signal", "adx_at_entry"), ("adx_source",)),
        "atr": (("atr", "volatility_atr"), ("atr_source", "volatility_source")),
        "directional_spread": (("spread", "directional_spread", "conviction_spread"), ("score_source",)),
        "structure": (("structure", "structure_score"), ("structure_source",)),
        "momentum": (("momentum",), ("momentum_source",)),
        "volatility_percentile": (("volatility_percentile",), ("volatility_percentile_source",)),
        "volume_percentile": (("volume_percentile",), ("volume_percentile_source",)),
        "long_score": (("long_score",), ("score_source",)),
        "short_score": (("short_score",), ("score_source",)),
    }
    total_markets = len(market_rows)
    feature_coverage = {}
    quality_warnings: List[Dict[str, Any]] = []
    for name, (aliases, source_aliases) in feature_aliases.items():
        present = sum(1 for row in market_rows if _feature_present(row, aliases, source_aliases))
        ratio = present / total_markets if total_markets else 0.0
        feature_coverage[name] = {
            "present": present,
            "total": total_markets,
            "ratio": round(ratio, 4),
        }
        if total_markets and ratio < MIN_FEATURE_COVERAGE:
            quality_warnings.append({
                "type": "low_feature_coverage",
                "feature": name,
                "ratio": round(ratio, 4),
                "threshold": MIN_FEATURE_COVERAGE,
            })

    decision_trade_ids = {str(d.get("trade_id")) for d in decisions if d.get("trade_id")}
    linked_trades = 0
    for trade in trades:
        source_trade_id = str(
            trade.get("source_trade_id")
            or trade.get("genome_source_trade_id")
            or trade.get("trade_id")
            or ""
        )
        if source_trade_id and source_trade_id in decision_trade_ids:
            linked_trades += 1
    linkage_ratio = linked_trades / len(trades) if trades else 1.0
    if trades and linkage_ratio < MIN_TRADE_LINKAGE_COVERAGE:
        quality_warnings.append({
            "type": "low_trade_decision_linkage",
            "linked": linked_trades,
            "total": len(trades),
            "ratio": round(linkage_ratio, 4),
            "threshold": MIN_TRADE_LINKAGE_COVERAGE,
        })

    structural_issues = list(orphans) + list(broken_trades)
    if dupes:
        structural_issues.append({"type": "duplicate_trade_ids", "count": dupes})
    if structural_issues:
        verdict = "FAIL"
    elif quality_warnings:
        verdict = "WARN"
    else:
        verdict = "PASS"
    return {
        "schema": "genome_validation_v2",
        "verdict": verdict,
        "orphan_decisions": len(orphans),
        "broken_trades": len(broken_trades),
        "duplicate_trade_ids": dupes,
        "issues": (structural_issues + quality_warnings)[:20],
        "data_quality": {
            "feature_coverage": feature_coverage,
            "trade_decision_linkage": {
                "linked": linked_trades,
                "total": len(trades),
                "ratio": round(linkage_ratio, 4),
            },
            "thresholds": {
                "minimum_feature_coverage": MIN_FEATURE_COVERAGE,
                "minimum_trade_decision_linkage": MIN_TRADE_LINKAGE_COVERAGE,
            },
            "warnings": quality_warnings,
        },
    }
