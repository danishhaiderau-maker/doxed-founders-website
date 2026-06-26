"""Pre-collection data integrity audit — golden trade validation gates."""
from __future__ import annotations

import json
import os
import random
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List

from research.genome.loader import load_all_layers
from research.genome.validation import validate_genome_integrity


def _agent_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _link_chain_ok(trade: Dict[str, Any], layers: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    issues: List[str] = []
    tid = str(trade.get("trade_id") or "")
    if not tid:
        issues.append("missing trade_id")
    for key, id_field, table in (
        ("market", "market_genome_id", "market"),
        ("decision", "decision_id", "decision"),
        ("execution", "execution_id", "execution"),
    ):
        ref = str(trade.get(id_field) or "")
        if not ref:
            issues.append(f"missing {id_field}")
            continue
        rows = layers.get(table) or []
        if not any(str(r.get(id_field) or r.get(f"{table}_genome_id") or "") == ref for r in rows):
            issues.append(f"orphan {id_field}={ref}")
    lifecycle = layers.get("lifecycle") or []
    life_for_trade = [r for r in lifecycle if str(r.get("trade_id") or "") == tid]
    if not life_for_trade:
        issues.append("no lifecycle events")
    else:
        names = {str(r.get("event_name") or "") for r in life_for_trade}
        if "POSITION_OPENED" not in names:
            issues.append("missing POSITION_OPENED")
        if "POSITION_CLOSED" not in names and trade.get("pnl_usd") is not None:
            issues.append("missing POSITION_CLOSED")
    return issues


def run_golden_trade_audit(
    db_path: str | None = None,
    sample_size: int = 10,
) -> Dict[str, Any]:
    """Random sample audit — prove recorders + ID chain before long collection."""
    root = _agent_root()
    db = db_path or os.path.join(root, "research.db")
    validation = validate_genome_integrity(db)
    layers = load_all_layers(db)
    trades = layers.get("trade") or []

    sample: List[Dict[str, Any]] = []
    if trades:
        picks = random.sample(trades, min(sample_size, len(trades)))
        for t in picks:
            issues = _link_chain_ok(t, layers)
            sample.append({
                "trade_id": t.get("trade_id"),
                "research_lane": t.get("research_lane"),
                "pnl_usd": t.get("pnl_usd"),
                "issues": issues,
                "pass": len(issues) == 0,
            })

    layer_counts = {k: len(v) for k, v in layers.items()}
    passes = sum(1 for s in sample if s["pass"])
    report = {
        "schema": "genome_data_integrity_audit_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "architecture_frozen": "v11.0-genome-architecture-v1",
        "validation": validation,
        "layer_counts": layer_counts,
        "trade_count": len(trades),
        "golden_sample_size": len(sample),
        "golden_pass": passes,
        "golden_fail": len(sample) - passes,
        "ready_for_long_collection": (
            validation.get("verdict") == "PASS"
            and passes == len(sample)
            and len(trades) >= 1
        ),
        "samples": sample,
        "checklist": {
            "genome_layers_recorded": validation.get("verdict") == "PASS",
            "id_chain_validated": passes == len(sample) if sample else False,
            "lifecycle_present": all(
                "no lifecycle events" not in (s.get("issues") or []) for s in sample
            ) if sample else False,
            "replay_ui": "NOT_STARTED",
            "note": "Ladder/config changes are forward-compatible — no data wipe required.",
        },
    }
    out_dir = os.path.join(root, "research", "genome")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "data_integrity_audit.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return report
