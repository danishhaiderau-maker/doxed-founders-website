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
    *,
    validated_only: bool = True,
) -> Dict[str, Any]:
    """Match current market to library. UNKNOWN until validated clusters exist."""
    pool = clusters
    if validated_only:
        pool = [
            c for c in clusters
            if c.get("is_validated_cluster") or c.get("validation_status") == "VALIDATED"
        ]
    if not pool:
        closest_sim = 0.0
        closest_id = None
        if clusters:
            vec = _vector(market_row)
            for cluster in clusters:
                centroid = cluster.get("centroid") or {}
                sim = cosine_similarity(vec, _vector(centroid))
                if sim > closest_sim:
                    closest_sim = sim
                    closest_id = str(
                        cluster.get("genome_id")
                        or cluster.get("cluster_id")
                        or cluster.get("id")
                        or "UNKNOWN"
                    )
        return {
            "cluster_id": "UNKNOWN",
            "similarity_pct": round(closest_sim * 100.0, 1) if clusters else 0.0,
            "closest_genome_id": closest_id,
            "validated_clusters_available": 0,
            "persistent_genomes": len(clusters),
            "recommendation": "Collect only — no validated clusters in library yet",
            "reason": (
                f"{len(clusters)} persistent genome(s) collecting data; "
                "0 validated clusters (need ≥30 trades + MODERATE confidence)."
            ),
        }
    vec = _vector(market_row)
    best_id = "UNKNOWN"
    best_sim = 0.0
    for cluster in pool:
        centroid = cluster.get("centroid") or {}
        sim = cosine_similarity(vec, _vector(centroid))
        if sim > best_sim:
            best_sim = sim
            best_id = str(
                cluster.get("genome_id")
                or cluster.get("cluster_id")
                or cluster.get("id")
                or "UNKNOWN"
            )
    sim_pct = round(best_sim * 100.0, 1)
    if sim_pct < UNKNOWN_SIMILARITY_THRESHOLD:
        return {
            "cluster_id": "UNKNOWN",
            "similarity_pct": sim_pct,
            "closest_genome_id": best_id,
            "validated_clusters_available": len(pool),
            "recommendation": "Collect only — insufficient similarity to validated clusters",
            "reason": f"Similarity {sim_pct:.1f}% below {UNKNOWN_SIMILARITY_THRESHOLD}% threshold.",
        }
    match = next(
        (c for c in pool if str(c.get("genome_id") or c.get("cluster_id")) == best_id),
        {},
    )
    return {
        "cluster_id": best_id,
        "cluster_identity": match.get("identity"),
        "similarity_pct": sim_pct,
        "validated_clusters_available": len(pool),
        "recommendation": "Advisory analysis only — no execution changes",
        "reason": f"Matched validated cluster {best_id} at {sim_pct:.1f}% similarity.",
    }
