"""Trading Genome analyzer — DNA-first pipeline with persistent Genome Memory.

The v11 identifier describes this research engine's frozen architecture. It is
not the running bot release, which is reported separately by the dashboards.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List

# Support both ``python -m research.genome.run_analyzer`` and the documented
# direct ``python research/genome/run_analyzer.py`` invocation.
if __package__ in (None, ""):
    _DIRECT_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _DIRECT_AGENT_ROOT not in sys.path:
        sys.path.insert(0, _DIRECT_AGENT_ROOT)

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
from research.genome.taxonomy import build_taxonomy_summary
from research.genome.transitions import summarize_transitions
from research.genome.validation import validate_genome_integrity

GENOME_REPORT_FILE = "genome_analysis_report.json"
GENOME_LIBRARY_FILE = "genome_library.json"
GENOME_DISCOVERIES_FILE = "genome_discoveries.json"
ARCHITECTURE_FROZEN = "v11.0-genome-architecture-v1"
GENOME_SOURCE_STATUS_FILE = "genome_source_status.json"
REQUIRED_SOURCE_TABLES = frozenset({
    "environment_genome", "market_genome", "decision_genome",
    "execution_genome", "lifecycle_genome", "trade_genome",
})


def _agent_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _atomic_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp",
                                     dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _source_preflight(db_path: str) -> Dict[str, Any]:
    """Inspect raw Genome evidence read-only; never initialize it in place."""
    generated_at = datetime.now(timezone.utc).isoformat()
    if not os.path.isfile(db_path):
        return {
            "schema": "genome_source_status_v1", "status": "GENOME_SOURCE_UNAVAILABLE",
            "generated_at": generated_at, "reason": "SOURCE_DB_MISSING",
            "source_label": os.path.basename(db_path), "required_tables": sorted(REQUIRED_SOURCE_TABLES),
            "available_tables": [], "missing_tables": sorted(REQUIRED_SOURCE_TABLES),
            "execution_affected": False, "other_research_pages_affected": False,
        }
    try:
        uri = f"file:{os.path.abspath(db_path)}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            available = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
    except sqlite3.Error as exc:
        return {
            "schema": "genome_source_status_v1", "status": "GENOME_SOURCE_UNAVAILABLE",
            "generated_at": generated_at, "reason": "SOURCE_DB_UNREADABLE",
            "error_type": type(exc).__name__, "source_label": os.path.basename(db_path),
            "required_tables": sorted(REQUIRED_SOURCE_TABLES), "available_tables": [],
            "missing_tables": sorted(REQUIRED_SOURCE_TABLES),
            "execution_affected": False, "other_research_pages_affected": False,
        }
    missing = REQUIRED_SOURCE_TABLES - available
    return {
        "schema": "genome_source_status_v1",
        "status": "GENOME_SOURCE_UNAVAILABLE" if missing else "AVAILABLE",
        "generated_at": generated_at,
        "reason": "REQUIRED_SOURCE_TABLES_MISSING" if missing else None,
        "source_label": os.path.basename(db_path),
        "required_tables": sorted(REQUIRED_SOURCE_TABLES),
        "available_tables": sorted(available),
        "missing_tables": sorted(missing),
        "execution_affected": False,
        "other_research_pages_affected": False,
    }


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


def _summarize_replay_capabilities(agent_root: str, layers: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Document replay layers — counterfactual, signal, lifecycle (ChatGPT audit)."""
    root = agent_root
    jsonl_files = (
        "counterfactual.jsonl",
        "signal_replay.jsonl",
        "near_miss.jsonl",
        "execution_funnel.jsonl",
        "fill_quality.jsonl",
    )
    mirrors: Dict[str, Any] = {}
    for name in jsonl_files:
        p = os.path.join(root, name)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    mirrors[name] = sum(1 for _ in fh)
            except OSError:
                mirrors[name] = "error"
    lifecycle = _summarize_lifecycle_dna(layers)
    decision = _summarize_decision_dna(layers)
    return {
        "counterfactual_replay": {
            "files": ["counterfactual.jsonl", "trade_outcome.jsonl", "shadow_outcome.jsonl"],
            "lines_on_disk": {k: mirrors.get(k) for k in mirrors if "counter" in k or "near" in k},
            "status": "ACTIVE" if mirrors.get("counterfactual.jsonl") else "PARTIAL",
        },
        "signal_replay": {
            "file": "signal_replay.jsonl",
            "lines": mirrors.get("signal_replay.jsonl"),
            "status": "ACTIVE" if mirrors.get("signal_replay.jsonl") else "NOT_FOUND",
        },
        "lifecycle_replay": {
            "status": "ACTIVE",
            "mfe_updates": lifecycle.get("mfe_updates"),
            "mae_updates": (lifecycle.get("event_counts") or {}).get("MAE_UPDATED", 0),
            "ladder_events": (lifecycle.get("ladder_armed") or 0) + (lifecycle.get("ladder_hits") or 0),
            "note": "Full trade path: entry → MFE → MAE → ladder → stop → exit in lifecycle_genome",
        },
        "roads_not_taken": {
            "ai_rejections": decision.get("ai_rejections"),
            "no_fill_signals": decision.get("no_fill_signals"),
            "status": "PARTIAL",
            "note": "Reject/no-fill/missed-opportunity genome expands in Priority 4",
        },
        "genome_replay_audit_ui": {
            "status": "NOT_STARTED",
            "note": "Priority 5 — per-genome trade/MFE/MAE/exit replay panel",
        },
    }


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
        why = cluster_match.get("reason") or (
            f"No validated cluster — {cluster_match.get('persistent_genomes', 0)} genome(s) collecting."
        )
        return {
            "action": "UNKNOWN_MARKET",
            "similarity_pct": sim,
            "detail": "Collect only — no validated cluster match yet.",
            "research_confidence": conf,
            "explanation": {
                "why": why,
                "evidence": {"sample_size": n, "confidence_interval_95": ci},
                "genome_vs_cluster": (
                    "Genome = persistent fingerprint memory. "
                    "Cluster = validated identity (≥30 trades, MODERATE+ confidence)."
                ),
                "execution_change": "NEVER",
                "human_review_required": True,
            },
        }
    if conf == "LOW" or n < 30:
        return {
            "action": "COLLECT_MORE_DATA",
            "detail": "Insufficient sample — continue CONTINUOUS + OFFSET_029_ATR_TP_25 research candidate.",
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


def run_genome_analyzer(
    db_path: str | None = None,
    out_dir: str | None = None,
    *,
    memory_db_path: str | None = None,
    publish_root_artifacts: bool = True,
) -> dict:
    agent_root = _agent_root()
    db = db_path or os.path.join(agent_root, "research.db")
    out = out_dir or os.path.join(agent_root, "research", "genome")
    os.makedirs(out, exist_ok=True)

    source_status = _source_preflight(db)
    _atomic_json(os.path.join(out, GENOME_SOURCE_STATUS_FILE), source_status)
    if source_status["status"] != "AVAILABLE":
        # Do not overwrite a prior valid analysis/library/discovery artifact.
        # The caller receives a structured, non-throwing status so unrelated
        # static, dynamic, and shadow report generation continues normally.
        return source_status

    validation = validate_genome_integrity(db)
    memory_db = memory_db_path or os.path.join(out, "genome_memory.db")
    if os.path.abspath(memory_db) == os.path.abspath(db):
        raise ValueError("Genome derived memory DB must be separate from the read-only source DB")
    store = GenomeLibraryStore(memory_db)
    layers = load_all_layers(db)
    trades = layers.get("trade") or []
    markets = layers.get("market") or []

    combo_trades = [t for t in trades if "604" in str(t.get("research_lane", "")).upper()]
    cont_trades = [t for t in trades if str(t.get("research_lane", "")).upper() == "CONTINUOUS"]

    outcome_fps = _build_outcome_fingerprints(layers)
    candidates = build_cluster_library(markets, trades=trades)
    existing_genomes = store.load_all_genomes()
    genome_library = merge_cluster_into_library(store, candidates, existing_genomes)
    taxonomy = build_taxonomy_summary(genome_library, candidates)

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
        "data_quality": validation.get("data_quality") or {},
        "layer_counts": {k: len(v) for k, v in layers.items()},
        "genome_memory": {
            "persistent_genomes": memory_stats["genomes"],
            "persistent_discoveries": memory_stats["discoveries"],
            "library_status": "LEARNING" if len(genome_library) < 5 else "ACTIVE",
        },
        "genome_taxonomy": taxonomy,
        "replay_capabilities": _summarize_replay_capabilities(agent_root, layers),
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
            "patient_chase": summarize_trades(combo_trades),
            "continuous": summarize_trades(cont_trades),
        },
        "migration_note": "v62 CSV reports still run in parallel until Genome reproduces all required metrics (Priority 13).",
        "source_status": source_status,
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

    if publish_root_artifacts:
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

        try:
            from research.genome.data_integrity_audit import run_golden_trade_audit

            run_golden_trade_audit(db_path=db)
        except Exception:
            pass

    return report


if __name__ == "__main__":
    print(json.dumps(run_genome_analyzer(), indent=2))
