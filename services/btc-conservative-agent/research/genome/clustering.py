"""Cluster library builder — learns centroids as sample grows."""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import Any, Dict, List

from research.genome.fingerprints import fingerprint_key, market_fingerprint
from research.genome.quality_score import summarize_trades
from research.genome.similarity import FEATURE_KEYS

MIN_CLUSTER_TRADES = 5
MIN_MARKETS_FOR_LIBRARY = 15


def _finite(value: Any):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _centroid(rows: List[Dict[str, Any]]) -> tuple[Dict[str, Any], Dict[str, Dict[str, float]]]:
    """Average present values only; missing observations must never become zero."""
    out: Dict[str, Any] = {}
    coverage: Dict[str, Dict[str, float]] = {}
    for key in FEATURE_KEYS:
        vals = [value for value in (_finite(r.get(key)) for r in rows) if value is not None]
        out[key] = round(sum(vals) / len(vals), 4) if vals else None
        coverage[key] = {
            "present": len(vals),
            "total": len(rows),
            "ratio": round(len(vals) / len(rows), 4) if rows else 0.0,
        }
    return out, coverage


def _representative(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Prefer the newest row with the most usable similarity features."""
    def rank(row: Dict[str, Any]):
        present = sum(_finite(row.get(key)) is not None for key in FEATURE_KEYS)
        return present, str(row.get("ts") or "")

    return market_fingerprint(max(rows, key=rank))


def _source_watermark(
    rows: List[Dict[str, Any]], linked_trades: List[Dict[str, Any]] | None = None,
) -> str:
    identities = sorted(
        str(row.get("market_genome_id") or row.get("ts") or "") for row in rows
    )
    identities.extend(sorted(
        "TRADE|" + "|".join([
            str(row.get("trade_id") or ""),
            str(row.get("pnl_usd") if row.get("pnl_usd") is not None else ""),
            str(row.get("exit_reason") or ""),
        ])
        for row in (linked_trades or [])
    ))
    return hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()


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
        centroid, centroid_coverage = _centroid(rows)
        linked_pnls: List[float] = []
        linked_trades: List[Dict[str, Any]] = []
        for row in rows:
            mid = str(row.get("market_genome_id") or "")
            for t in trade_by_mkt.get(mid, []):
                linked_pnls.append(float(t.get("pnl_usd") or 0))
                linked_trades.append(t)
        if linked_pnls and len(linked_pnls) < MIN_CLUSTER_TRADES:
            continue
        summary = summarize_trades([{"pnl_usd": p} for p in linked_pnls]) if linked_pnls else {}
        clusters.append({
            "cluster_id": f"GENOME-{cid:03d}",
            "fingerprint_key": key,
            "market_observations": len(rows),
            "source_watermark": _source_watermark(rows, linked_trades),
            "trade_count": len(linked_pnls),
            "centroid": centroid,
            "centroid_coverage": centroid_coverage,
            "representative": _representative(rows),
            "ev_usd": summary.get("ev"),
            "win_rate": summary.get("win_rate"),
            "dna_quality": summary.get("dna_quality"),
            "research_confidence": summary.get("research_confidence"),
        })
        cid += 1
        if cid > max(k, 12):
            break
    return clusters
