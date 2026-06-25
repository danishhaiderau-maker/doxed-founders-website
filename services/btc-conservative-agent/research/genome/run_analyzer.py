"""Trading Genome analyzer — DNA-first pipeline (v11)."""
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
from research.genome.loader import load_all_layers
from research.genome.quality_score import summarize_trades
from research.genome.similarity import nearest_cluster
from research.genome.transitions import summarize_transitions

GENOME_REPORT_FILE = "genome_analysis_report.json"
GENOME_LIBRARY_FILE = "genome_cluster_library.json"
GENOME_DISCOVERIES_FILE = "genome_discoveries.json"


def _agent_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _summarize_decision_dna(layers: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    decisions = layers.get("decision") or []
    executions = layers.get("execution") or []
    approved = sum(1 for d in decisions if str(d.get("decision") or "").upper() == "APPROVE"
                   or d.get("event_name") == "AI_APPROVED")
    rejected = sum(1 for d in decisions if str(d.get("decision") or "").upper() == "REJECT"
                     or d.get("event_name") == "AI_REJECTED")
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


def run_genome_analyzer(db_path: str | None = None, out_dir: str | None = None) -> dict:
    agent_root = _agent_root()
    db = db_path or os.path.join(agent_root, "research.db")
    out = out_dir or os.path.join(agent_root, "research", "genome")
    os.makedirs(out, exist_ok=True)

    layers = load_all_layers(db)
    trades = layers.get("trade") or []
    markets = layers.get("market") or []

    # Lane summaries kept for benchmark comparison only — DNA layer is primary.
    combo_trades = [t for t in trades if "604" in str(t.get("research_lane", "")).upper()]
    cont_trades = [t for t in trades if str(t.get("research_lane", "")).upper() == "CONTINUOUS"]

    outcome_fps = _build_outcome_fingerprints(layers)
    cluster_library = build_cluster_library(markets, trades=trades)
    latest_market = markets[0] if markets else {}
    cluster_match = nearest_cluster(latest_market, cluster_library)
    discoveries = generate_discoveries(outcome_fps)

    dna_summary = summarize_trades(trades)
    decision_dna = _summarize_decision_dna(layers)
    lifecycle_dna = _summarize_lifecycle_dna(layers)

    # Baseline for drift: first half vs second half of closed trades
    mid = max(1, len(trades) // 2)
    baseline = summarize_trades(trades[mid:])
    current = summarize_trades(trades[:mid])
    drift = detect_drift(current, baseline)

    report = {
        "schema": "trading_genome_analysis_v1",
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_mode": "DNA_FIRST",
        "disclaimer": "Advisory discoveries only — analyzer never changes bot execution.",
        "layer_counts": {k: len(v) for k, v in layers.items()},
        "dna_quality": {
            "overall": dna_summary,
            "sample_confidence": dna_summary.get("research_confidence"),
            "confidence_interval_95": dna_summary.get("confidence_interval_95"),
        },
        "decision_dna": decision_dna,
        "lifecycle_dna": lifecycle_dna,
        "current_market_cluster": cluster_match,
        "cluster_library_size": len(cluster_library),
        "cluster_library_status": (
            "LEARNING" if len(cluster_library) < 3 else "ACTIVE"
        ),
        "transitions": summarize_transitions(outcome_fps),
        "drift": drift,
        "discoveries": discoveries,
        "hypotheses": generate_hypotheses(combo_trades, cont_trades),
        "outcome_fingerprints_sample": outcome_fps[:25],
        "benchmark_reference": {
            "combo_604": summarize_trades(combo_trades),
            "continuous": summarize_trades(cont_trades),
        },
    }

    report_path = os.path.join(out, GENOME_REPORT_FILE)
    library_path = os.path.join(out, GENOME_LIBRARY_FILE)
    discoveries_path = os.path.join(out, GENOME_DISCOVERIES_FILE)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    with open(library_path, "w", encoding="utf-8") as fh:
        json.dump({"clusters": cluster_library, "generated_at": report["generated_at"]}, fh, indent=2)
    with open(discoveries_path, "w", encoding="utf-8") as fh:
        json.dump({"discoveries": discoveries, "generated_at": report["generated_at"]}, fh, indent=2)

    # Mirror to agent root for manifest / dashboard
    root_report = os.path.join(agent_root, "research", GENOME_REPORT_FILE)
    try:
        with open(root_report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    except OSError:
        pass

    return report


if __name__ == "__main__":
    print(json.dumps(run_genome_analyzer(), indent=2))
