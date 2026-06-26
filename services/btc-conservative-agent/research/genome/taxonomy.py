"""Genome vs Cluster taxonomy — persistent fingerprint vs validated market identity."""
from __future__ import annotations

from typing import Any, Dict, List

# Validated cluster gates (ChatGPT: don't label Cluster until sample supports it)
VALIDATED_MIN_TRADES = 30
VALIDATED_MIN_OBSERVATIONS = 20
VALIDATED_CONFIDENCE = frozenset({"MODERATE", "HIGH"})


def genome_validation_status(genome: Dict[str, Any]) -> str:
    """COLLECTING → CANDIDATE → VALIDATED."""
    trades = int(genome.get("trade_count") or 0)
    obs = int(genome.get("observations") or 0)
    conf = str(genome.get("research_confidence") or "LOW").upper()
    if (
        trades >= VALIDATED_MIN_TRADES
        and obs >= VALIDATED_MIN_OBSERVATIONS
        and conf in VALIDATED_CONFIDENCE
    ):
        return "VALIDATED"
    if trades >= 10 or obs >= 10:
        return "CANDIDATE"
    return "COLLECTING"


def annotate_genome(genome: Dict[str, Any]) -> Dict[str, Any]:
    status = genome_validation_status(genome)
    genome["validation_status"] = status
    genome["is_validated_cluster"] = status == "VALIDATED"
    genome.setdefault("cluster_id", genome.get("genome_id"))
    return genome


def validated_clusters(genomes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [g for g in genomes if genome_validation_status(g) == "VALIDATED"]


def build_taxonomy_summary(
    genomes: List[Dict[str, Any]],
    cluster_candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Explain Genome (persistent) vs Cluster (validated identity) on dashboard."""
    validated = validated_clusters(genomes)
    return {
        "definitions": {
            "genome": (
                "Persistent fingerprint entity in genome_library — remembers every "
                "observed DNA bucket across analyzer cycles (living memory)."
            ),
            "cluster": (
                "Validated recurring market identity — a genome promoted only after "
                f"≥{VALIDATED_MIN_TRADES} trades, ≥{VALIDATED_MIN_OBSERVATIONS} observations, "
                "and MODERATE/HIGH research confidence."
            ),
            "unknown_market": (
                "Correct when validated_clusters=0 — high similarity to today's fingerprint "
                "does NOT mean a validated historical cluster exists yet."
            ),
        },
        "persistent_genomes": len(genomes),
        "cluster_candidates_this_cycle": len(cluster_candidates),
        "validated_clusters": len(validated),
        "collecting_genomes": sum(1 for g in genomes if genome_validation_status(g) == "COLLECTING"),
        "candidate_genomes": sum(1 for g in genomes if genome_validation_status(g) == "CANDIDATE"),
        "validated_genome_ids": [g.get("genome_id") for g in validated],
    }
