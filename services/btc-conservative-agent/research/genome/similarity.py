"""Cluster similarity engine with UNKNOWN detection."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

UNKNOWN_SIMILARITY_THRESHOLD = 55.0

FEATURE_KEYS = (
    "adx", "atr", "volatility_percentile", "volume_percentile",
    "spread", "bull_score", "bear_score", "momentum", "structure",
)

FEATURE_SCALES = {
    "adx": 100.0,
    "atr": 1000.0,
    "volatility_percentile": 100.0,
    "volume_percentile": 100.0,
    "spread": 10.0,
    "bull_score": 10.0,
    "bear_score": 10.0,
    "momentum": 1.0,
    "structure": 10.0,
}
MIN_SHARED_FEATURES = 3


def _vector(row: Dict[str, Any]) -> List[float]:
    return [float(row.get(k) or 0.0) for k in FEATURE_KEYS]


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalized_feature_similarity(
    a: Dict[str, Any], b: Dict[str, Any], minimum_shared: int = MIN_SHARED_FEATURES,
) -> Tuple[Optional[float], int]:
    """Missing-aware distance similarity over normalized shared dimensions."""
    distances = []
    for key in FEATURE_KEYS:
        av = _finite(a.get(key))
        bv = _finite(b.get(key))
        if av is None or bv is None:
            continue
        scale = max(float(FEATURE_SCALES.get(key) or 1.0), 1e-9)
        distances.append(min(1.0, abs(av - bv) / scale))
    if len(distances) < minimum_shared:
        return None, len(distances)
    return max(0.0, 1.0 - (sum(distances) / len(distances))), len(distances)


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
                sim_value, _ = normalized_feature_similarity(market_row, centroid)
                sim = sim_value or 0.0
                if sim_value is not None and (closest_id is None or sim > closest_sim):
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
    best_id = "UNKNOWN"
    best_sim = 0.0
    best_shared = 0
    for cluster in pool:
        centroid = cluster.get("centroid") or {}
        sim_value, shared = normalized_feature_similarity(market_row, centroid)
        sim = sim_value or 0.0
        if sim_value is not None and (best_id == "UNKNOWN" or sim > best_sim):
            best_sim = sim
            best_shared = shared
            best_id = str(
                cluster.get("genome_id")
                or cluster.get("cluster_id")
                or cluster.get("id")
                or "UNKNOWN"
            )
    sim_pct = round(best_sim * 100.0, 1)
    if best_shared < MIN_SHARED_FEATURES:
        return {
            "cluster_id": "UNKNOWN",
            "similarity_pct": 0.0,
            "closest_genome_id": best_id if best_id != "UNKNOWN" else None,
            "shared_features": best_shared,
            "required_shared_features": MIN_SHARED_FEATURES,
            "validated_clusters_available": len(pool),
            "recommendation": "Collect only - insufficient populated features",
            "reason": (
                f"Only {best_shared} shared feature(s); need at least "
                f"{MIN_SHARED_FEATURES} for a market match."
            ),
        }
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
