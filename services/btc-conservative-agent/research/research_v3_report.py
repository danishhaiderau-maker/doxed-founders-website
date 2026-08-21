"""Analyzer-facing Safe Policy Genome V3 status and ranking report."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from policy_search_manifest import POLICY_SEARCH_MANIFEST
from research_v3_contract import SAFE_POLICY_GENOME_CONTRACT
from research_v3_candidates import evaluate_protection_screen, load_candidate_inputs
from research_v3_ranking import rank_safe_policies
from research_v3_search import build_search_plan, search_progress
from research_v3_store import V3EvidenceStore

REPORT_FILE = "safe_policy_genome_v3_report.json"


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def build_safe_policy_genome_v3_report(data_dir=".", report_dir=".", *, candidates=None) -> dict[str, Any]:
    v3_root = Path(data_dir) / "v3"
    epoch_ids = set()
    for path in (v3_root / "ledgers").glob("*.jsonl"):
        for row in _read_ledger(path):
            if row.get("epoch_id"):
                epoch_ids.add(str(row["epoch_id"]))
    epoch_id = sorted(epoch_ids)[-1] if epoch_ids else "V3_NOT_STARTED"
    store = V3EvidenceStore(data_dir, epoch_id=epoch_id)
    verification = store.verify()
    opportunities = _read_ledger(store.ledger_path("opportunity"))
    decisions = _read_ledger(store.ledger_path("decision"))
    lifecycles = _read_ledger(store.ledger_path("lifecycle"))
    terminal_lifecycles = [row for row in lifecycles if row.get("terminal") is True]
    executions = _read_ledger(store.ledger_path("execution"))
    outcome_counts = Counter(str(row.get("outcome_state") or "UNKNOWN") for row in terminal_lifecycles)
    decision_outcomes = Counter(str(row.get("primary_outcome") or "UNKNOWN") for row in decisions)
    search = build_search_plan({
        "entry_offset_pct": list((POLICY_SEARCH_MANIFEST.get("dimensions") or {}).get("entry_offset_pct") or []),
        "entry_ttl_min": list((POLICY_SEARCH_MANIFEST.get("dimensions") or {}).get("entry_ttl_min") or []),
        "chase_policy_id": list((POLICY_SEARCH_MANIFEST.get("dimensions") or {}).get("chase_policy_id") or []),
    })
    candidate_screen = None
    if candidates is None:
        candidate_screen = evaluate_protection_screen(load_candidate_inputs(data_dir))
        candidates = candidate_screen["candidates"]
    ranking = rank_safe_policies(candidates or [])
    progress_receipts = []
    if candidate_screen is not None:
        progress_receipts.append({
            "unique_policies_evaluated": candidate_screen.get("unique_policies_evaluated", 0),
            "independent_episodes": len({row.get("episode_id") for row in opportunities if row.get("episode_id")}),
        })
    report = {
        "schema": "safe_policy_genome_v3_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "V3_INTEGRITY_FAILED" if not verification["passed"] else "V3_COLLECTING" if opportunities else "V3_READY_FOR_FRESH_EPOCH",
        "live_policy_change_allowed": False,
        "real_bitfinex_trading_allowed": False,
        "epoch_id": epoch_id,
        "contract": SAFE_POLICY_GENOME_CONTRACT,
        "integrity": verification,
        "collection": {
            "independent_opportunities": len({row.get("episode_id") for row in opportunities if row.get("episode_id")}),
            "decision_branches": len(decisions),
            "execution_rows": len(executions),
            "terminal_lifecycles": len(terminal_lifecycles),
            "provisional_lifecycles": len(lifecycles) - len(terminal_lifecycles),
            "decision_outcomes": dict(sorted(decision_outcomes.items())),
            "outcome_states": dict(sorted(outcome_counts.items())),
            "ledger_counts": verification["ledger_counts"],
            "market_segments": verification["market_segment_count"],
        },
        "search": search,
        "search_progress": search_progress(search, progress_receipts),
        "candidate_screen": candidate_screen or {
            "schema": "externally_supplied_safe_policy_candidates_v3",
            "unique_policies_evaluated": len(candidates or []),
            "descriptive_top_100": [],
        },
        "safe_policy_ranking": ranking,
        "number_one_strategy": ranking["number_one"],
        "qualification": ranking["qualification"],
        "blockers": (["V3_DATA_INTEGRITY_FAILED"] if not verification["passed"] else []) + (["NO_SAFE_QUALIFIED_POLICY"] if not ranking["number_one"] else []),
        "note": "Number one is selected only among policies passing every integrity, conservative-execution, sealed-OOS, drawdown, CVaR, liquidation, stability, multiple-testing and regime gate.",
    }
    _atomic_json(Path(report_dir) / REPORT_FILE, report)
    return report
