"""Cluster similarity engine with UNKNOWN detection."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

UNKNOWN_SIMILARITY_THRESHOLD = 55.0

FEATURE_KEYS = (
    "adx", "atr", "volatility_percentile", "volume_percentile",
    "spread", "bull_score", "bear_score", "momentum", "structure",
)


def _vector(row: Dict[str, Any]) -> List[float]:
    return [float(row.get(k) or 0.0) for k in FEATURE_KEYS]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def nearest_cluster(
    market_row: Dict[str, Any],
    clusters: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not clusters:
        return {
            "cluster_id": "UNKNOWN",
            "similarity_pct": 0.0,
            "recommendation": "Collect only — no cluster library",
        }
    vec = _vector(market_row)
    best_id = "UNKNOWN"
    best_sim = 0.0
    for cluster in clusters:
        centroid = cluster.get("centroid") or {}
        sim = cosine_similarity(vec, _vector(centroid))
        if sim > best_sim:
            best_sim = sim
            best_id = str(cluster.get("cluster_id") or cluster.get("id") or "UNKNOWN")
    sim_pct = round(best_sim * 100.0, 1)
    if sim_pct < UNKNOWN_SIMILARITY_THRESHOLD:
        return {
            "cluster_id": "UNKNOWN",
            "similarity_pct": sim_pct,
            "closest_cluster_id": best_id,
            "recommendation": "Collect only — insufficient similarity to historical clusters",
        }
    return {
        "cluster_id": best_id,
        "similarity_pct": sim_pct,
        "recommendation": "Advisory analysis only — no execution changes",
    }
