"""Cluster library builder — learns centroids as sample grows."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from research.genome.fingerprints import fingerprint_key, market_fingerprint
from research.genome.quality_score import summarize_trades
from research.genome.similarity import FEATURE_KEYS

MIN_CLUSTER_TRADES = 5
MIN_MARKETS_FOR_LIBRARY = 15


def _centroid(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key in FEATURE_KEYS:
        vals = [float(r.get(key) or 0) for r in rows]
        out[key] = round(sum(vals) / len(vals), 4) if vals else 0.0
    return out


def build_cluster_library(
    market_rows: List[Dict[str, Any]],
    trades: List[Dict[str, Any]] | None = None,
    k: int = 8,
) -> List[Dict[str, Any]]:
    """
    Build DNA cluster library from market genomes.
    Uses fingerprint buckets until sample supports richer clustering.
    """
    if len(market_rows) < MIN_MARKETS_FOR_LIBRARY:
        return []

    trade_by_mkt: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    if trades:
        for t in trades:
            mid = str(t.get("market_genome_id") or "")
            if mid:
                trade_by_mkt[mid].append(t)

    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in market_rows:
        fp = market_fingerprint(row)
        buckets[fingerprint_key(fp)].append(row)

    clusters: List[Dict[str, Any]] = []
    cid = 1
    for key, rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        linked_pnls: List[float] = []
        for row in rows:
            mid = str(row.get("market_genome_id") or "")
            for t in trade_by_mkt.get(mid, []):
                linked_pnls.append(float(t.get("pnl_usd") or 0))
        if linked_pnls and len(linked_pnls) < MIN_CLUSTER_TRADES:
            continue
        summary = summarize_trades([{"pnl_usd": p} for p in linked_pnls]) if linked_pnls else {}
        clusters.append({
            "cluster_id": f"GENOME-{cid:03d}",
            "fingerprint_key": key,
            "market_observations": len(rows),
            "trade_count": len(linked_pnls),
            "centroid": _centroid(rows),
            "representative": market_fingerprint(rows[0]),
            "ev_usd": summary.get("ev"),
            "dna_quality": summary.get("dna_quality"),
            "research_confidence": summary.get("research_confidence"),
        })
        cid += 1
        if cid > max(k, 12):
            break
    return clusters
