"""Trading Genome analyzer — DNA-first pipeline with persistent Genome Memory (v11)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from research.genome.clustering import build_cluster_library
from research.genome.discoveries import generate_discoveries
from research.genome.drift import detect_drift
from research.genome.fingerprints import index_by_id, outcome_fingerprint
from research.genome.hypothesis_engine import generate_hypotheses
from research.genome.library_store import GenomeLibraryStore
from research.genome.loader import load_all_layers
from research.genome.memory import merge_cluster_into_library
from research.genome.quality_score import summarize_trades
from research.genome.similarity import nearest_cluster
from research.genome.transitions import summarize_transitions
from research.genome.validation import validate_genome_integrity

GENOME_REPORT_FILE = "genome_analysis_report.json"
GENOME_LIBRARY_FILE = "genome_library.json"
GENOME_DISCOVERIES_FILE = "genome_discoveries.json"
ARCHITECTURE_FROZEN = "v11.0-genome-architecture-v1"


def _agent_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _summarize_decision_dna(layers: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    decisions = layers.get("decision") or []
    executions = layers.get("execution") or []
    approved = sum(
        1 for d in decisions
        if str(d.get("decision") or "").upper() == "APPROVE" or d.get("event_name") == "AI_APPROVED"
    )
    rejected = sum(
        1 for d in decisions
        if str(d.get("decision") or "").upper() == "REJECT" or d.get("event_name") == "AI_REJECTED"
    )
    by_event: Dict[str, int] = {}
    for ex in executions:
        name = str(ex.get("event_name") or "UNKNOWN")
        by_event[name] = by_event.get(name, 0) + 1
    return {
        "ai_approvals": approved,
        "ai_rejections": rejected,
        "approval_rate": round(approved / len(decisions), 4) if decisions else 0.0,
        "execution_events": by_event,
        "no_fill_signals": by_event.get("ORDER_EXPIRED", 0) + by_event.get("ORDER_CANCELLED", 0),
        "fills": by_event.get("ORDER_FILLED", 0),
        "chases": by_event.get("LIMIT_CHASED", 0),
        "limits_created": by_event.get("LIMIT_CREATED", 0),
        "note": "Full Decision Genome (reject/no-fill/missed) expands in Priority 4 — counts from execution layer active.",
    }


def _summarize_lifecycle_dna(layers: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    lifecycle = layers.get("lifecycle") or []
    by_event: Dict[str, int] = {}
    for row in lifecycle:
        name = str(row.get("event_name") or "UNKNOWN")
        by_event[name] = by_event.get(name, 0) + 1
    return {
        "event_counts": by_event,
        "mfe_updates": by_event.get("MFE_UPDATED", 0),
        "ladder_armed": by_event.get("LADDER_STEP_ARMED", 0),
        "ladder_hits": by_event.get("LADDER_STEP_HIT", 0),
        "thesis_changes": by_event.get("THESIS_CHANGED", 0),
        "exit_triggers": by_event.get("EXIT_TRIGGERED", 0),
    }


def _build_outcome_fingerprints(layers: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    trades = layers.get("trade") or []
    markets = index_by_id(layers.get("market") or [], "market_genome_id")
    decisions = index_by_id(layers.get("decision") or [], "decision_id")
    out: List[Dict[str, Any]] = []
    for trade in trades:
        dec = decisions.get(str(trade.get("decision_id") or ""), {})
        mkt = markets.get(str(trade.get("market_genome_id") or dec.get("market_genome_id") or ""), {})
        out.append(outcome_fingerprint(trade, mkt, dec))
    return out


def _recommendation_engine(
    cluster_match: Dict[str, Any],
    dna_summary: Dict[str, Any],
    genome_count: int,
) -> Dict[str, Any]:
    sim = float(cluster_match.get("similarity_pct") or 0)
    conf = dna_summary.get("research_confidence") or "LOW"
    n = int(dna_summary.get("sample_size") or 0)
    ci = dna_summary.get("confidence_interval_95") or {}

    if cluster_match.get("cluster_id") == "UNKNOWN" or sim < 55:
        if cluster_match.get("cluster_id") == "UNKNOWN":
            why = (
                f"No matching genome in library (similarity {sim:.1f}%) — collect only."
                if sim >= 55
                else f"Similarity {sim:.1f}% is below 55% threshold."
            )
        else:
            why = f"Similarity {sim:.1f}% is below 55% threshold."
        return {
            "action": "UNKNOWN_MARKET",
            "similarity_pct": sim,
            "detail": "Collect only — market does not match historical genomes.",
            "research_confidence": conf,
            "explanation": {
                "why": why,
                "evidence": {"sample_size": n, "confidence_interval_95": ci},
                "execution_change": "NEVER",
                "human_review_required": True,
            },
        }
    if conf == "LOW" or n < 30:
        return {
            "action": "COLLECT_MORE_DATA",
            "detail": "Insufficient sample — continue CONTINUOUS + COMBO_604 research candidate.",
            "research_confidence": conf,
            "explanation": {
                "why": f"Only {n} trades — need ≥30 for MODERATE confidence.",
                "evidence": {"sample_size": n, "confidence_interval_95": ci, "dna_quality": dna_summary.get("dna_quality")},
                "execution_change": "NEVER",
                "human_review_required": True,
            },
        }
    return {
        "action": "CONTINUE_RESEARCH_CANDIDATE",
        "similarity_pct": sim,
        "detail": "Advisory only — human decides; bot execution unchanged.",
        "research_confidence": conf,
        "genome_library_size": genome_count,
        "explanation": {
            "why": f"Known cluster {cluster_match.get('cluster_id')} at {sim:.1f}% similarity with {n} trades.",
            "evidence": {"sample_size": n, "confidence_interval_95": ci, "ev_usd": dna_summary.get("ev")},
            "compared_to": "CONTINUOUS benchmark reference in discoveries",
            "execution_change": "NEVER",
            "human_review_required": True,
        },
    }


def run_genome_analyzer(db_path: str | None = None, out_dir: str | None = None) -> dict:
    agent_root = _agent_root()
    db = db_path or os.path.join(agent_root, "research.db")
    out = out_dir or os.path.join(agent_root, "research", "genome")
    os.makedirs(out, exist_ok=True)

    validation = validate_genome_integrity(db)
    store = GenomeLibraryStore(db)
    layers = load_all_layers(db)
    trades = layers.get("trade") or []
    markets = layers.get("market") or []

    combo_trades = [t for t in trades if "604" in str(t.get("research_lane", "")).upper()]
    cont_trades = [t for t in trades if str(t.get("research_lane", "")).upper() == "CONTINUOUS"]

    outcome_fps = _build_outcome_fingerprints(layers)
    candidates = build_cluster_library(markets, trades=trades)
    existing_genomes = store.load_all_genomes()
    genome_library = merge_cluster_into_library(store, candidates, existing_genomes)

    latest_market = markets[0] if markets else {}
    cluster_match = nearest_cluster(latest_market, genome_library)
    dna_summary = summarize_trades(trades)
    bench_ev = summarize_trades(cont_trades).get("ev") or 0.0
    mid = max(1, len(trades) // 2)
    drift = detect_drift(summarize_trades(trades[:mid]), summarize_trades(trades[mid:]))

    memory_stats = store.stats()
    discoveries = generate_discoveries(outcome_fps, store=store, benchmark_ev=float(bench_ev))
    recommendation = _recommendation_engine(cluster_match, dna_summary, len(genome_library))

    report = {
        "schema": "trading_genome_analysis_v1",
        "schema_version": "1.0.0",
        "architecture_frozen": ARCHITECTURE_FROZEN,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_mode": "DNA_FIRST_MEMORY",
        "disclaimer": "Advisory knowledge only — analyzer never changes bot execution.",
        "validation": validation,
        "layer_counts": {k: len(v) for k, v in layers.items()},
        "genome_memory": {
            "persistent_genomes": memory_stats["genomes"],
            "persistent_discoveries": memory_stats["discoveries"],
            "library_status": "LEARNING" if len(genome_library) < 5 else "ACTIVE",
        },
        "dna_quality": {
            "overall": dna_summary,
            "sample_confidence": dna_summary.get("research_confidence"),
            "confidence_interval_95": dna_summary.get("confidence_interval_95"),
        },
        "decision_dna": _summarize_decision_dna(layers),
        "lifecycle_dna": _summarize_lifecycle_dna(layers),
        "current_market_cluster": cluster_match,
        "genome_library": genome_library,
        "genome_library_size": len(genome_library),
        "transitions": summarize_transitions(outcome_fps),
        "drift": drift,
        "discoveries": discoveries,
        "recommendation": recommendation,
        "hypotheses": generate_hypotheses(combo_trades, cont_trades),
        "outcome_fingerprints_sample": outcome_fps[:25],
        "benchmark_reference": {
            "combo_604": summarize_trades(combo_trades),
            "continuous": summarize_trades(cont_trades),
        },
        "migration_note": "v62 CSV reports still run in parallel until Genome reproduces all required metrics (Priority 13).",
    }

    for fname, key in (
        (GENOME_REPORT_FILE, None),
        (GENOME_LIBRARY_FILE, "genome_library"),
        (GENOME_DISCOVERIES_FILE, "discoveries"),
    ):
        path = os.path.join(out, fname)
        payload = report if key is None else (
            {"genomes": report["genome_library"], "generated_at": report["generated_at"]}
            if key == "genome_library"
            else {"discoveries": discoveries, "generated_at": report["generated_at"]}
        )
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    root_report = os.path.join(agent_root, "research", GENOME_REPORT_FILE)
    try:
        with open(root_report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    except OSError:
        pass

    try:
        from build_gpt_audit_bundle import build as build_gpt_audit

        build_gpt_audit(agent_root=agent_root)
    except Exception:
        pass

    return report


if __name__ == "__main__":
    print(json.dumps(run_genome_analyzer(), indent=2))
