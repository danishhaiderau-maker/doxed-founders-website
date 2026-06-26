"""Genome validation — run every analyzer cycle."""
from __future__ import annotations

from typing import Any, Dict, List

from research.genome.loader import load_all_layers


def validate_genome_integrity(db_path: str) -> Dict[str, Any]:
    layers = load_all_layers(db_path)
    markets = {str(m.get("market_genome_id")) for m in layers.get("market") or [] if m.get("market_genome_id")}
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

    ok = not orphans and not broken_trades and dupes == 0
    return {
        "schema": "genome_validation_v1",
        "verdict": "PASS" if ok else "WARN",
        "orphan_decisions": len(orphans),
        "broken_trades": len(broken_trades),
        "duplicate_trade_ids": dupes,
        "issues": (orphans + broken_trades)[:20],
    }
