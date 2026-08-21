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


def _fresh_cutoff(data_dir: str | Path) -> float | None:
    try:
        session = json.loads((Path(data_dir) / "research_session.json").read_text(encoding="utf-8"))
        return float(session.get("fresh_collection_start_time") or session.get("bot_start_time"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _select_current_epoch(opportunities: list[dict[str, Any]], cutoff: float | None) -> str | None:
    eligible = []
    for row in opportunities:
        epoch_id = str(row.get("epoch_id") or "")
        try:
            signal_ts = float(row.get("signal_ts"))
        except (TypeError, ValueError):
            signal_ts = 0.0
            if cutoff is not None:
                continue
        if epoch_id and (cutoff is None or signal_ts >= cutoff):
            eligible.append((signal_ts, epoch_id))
    return max(eligible)[1] if eligible else None


def _exclude_identity_aliases(opportunities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prefer the causal shared-ID row when an enrichment retry minted a fallback alias."""
    grouped: dict[tuple[float, str, str], list[dict[str, Any]]] = {}
    for row in opportunities:
        try:
            signal_ts = float(row.get("signal_ts"))
        except (TypeError, ValueError):
            signal_ts = -1.0
        key = (
            signal_ts,
            str(row.get("symbol") or "").upper(),
            str(row.get("raw_direction") or "").upper(),
        )
        grouped.setdefault(key, []).append(row)
    kept, excluded = [], []
    for rows in grouped.values():
        rows = sorted(
            rows,
            key=lambda row: (
                0 if str(row.get("grouping_basis") or "") == "SHARED_AI_CALL" else 1,
                str(row.get("episode_id") or ""),
            ),
        )
        kept.append(rows[0])
        excluded.extend(rows[1:])
    return kept, excluded


def build_safe_policy_genome_v3_report(data_dir=".", report_dir=".", *, candidates=None) -> dict[str, Any]:
    v3_root = Path(data_dir) / "v3"
    all_opportunities = _read_ledger(v3_root / "ledgers" / "opportunity.jsonl")
    cutoff = _fresh_cutoff(data_dir)
    selected_epoch = _select_current_epoch(all_opportunities, cutoff)
    epoch_id = selected_epoch or "V3_NOT_STARTED"
    store = V3EvidenceStore(data_dir, epoch_id=epoch_id)
    verification = store.verify()
    def scoped(rows):
        return [row for row in rows if selected_epoch is not None and str(row.get("epoch_id") or "") == selected_epoch]

    opportunities = scoped(all_opportunities)
    if cutoff is not None:
        opportunities = [row for row in opportunities if float(row.get("signal_ts") or 0) >= cutoff]
    opportunities, identity_aliases = _exclude_identity_aliases(opportunities)
    allowed_episodes = {str(row.get("episode_id") or "") for row in opportunities}
    decisions = [row for row in scoped(_read_ledger(store.ledger_path("decision"))) if str(row.get("episode_id") or "") in allowed_episodes]
    lifecycles = [row for row in scoped(_read_ledger(store.ledger_path("lifecycle"))) if str(row.get("episode_id") or "") in allowed_episodes]
    terminal_lifecycles = [row for row in lifecycles if row.get("terminal") is True]
    executions = [row for row in scoped(_read_ledger(store.ledger_path("execution"))) if str(row.get("episode_id") or "") in allowed_episodes]
    observed_epochs = sorted({str(row.get("epoch_id")) for row in all_opportunities if row.get("epoch_id")})
    excluded_opportunities = len(all_opportunities) - len(opportunities)
    contamination = bool(excluded_opportunities or identity_aliases or len(observed_epochs) > 1)
    outcome_counts = Counter(str(row.get("outcome_state") or "UNKNOWN") for row in terminal_lifecycles)
    decision_outcomes = Counter(str(row.get("primary_outcome") or "UNKNOWN") for row in decisions)
    search = build_search_plan({
        "entry_offset_pct": list((POLICY_SEARCH_MANIFEST.get("dimensions") or {}).get("entry_offset_pct") or []),
        "entry_ttl_min": list((POLICY_SEARCH_MANIFEST.get("dimensions") or {}).get("entry_ttl_min") or []),
        "chase_policy_id": list((POLICY_SEARCH_MANIFEST.get("dimensions") or {}).get("chase_policy_id") or []),
    })
    candidate_screen = None
    if candidates is None:
        candidate_screen = evaluate_protection_screen(load_candidate_inputs(
            data_dir,
            epoch_id=selected_epoch,
            minimum_signal_ts=cutoff,
        ))
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
        "status": "V3_INTEGRITY_FAILED" if not verification["passed"] else "V3_EPOCH_CONTAMINATION_BLOCKED" if contamination else "V3_COLLECTING" if opportunities else "V3_READY_FOR_FRESH_EPOCH",
        "live_policy_change_allowed": False,
        "real_bitfinex_trading_allowed": False,
        "epoch_id": epoch_id,
        "contract": SAFE_POLICY_GENOME_CONTRACT,
        "integrity": verification,
        "epoch_scope": {
            "selected_epoch_id": selected_epoch,
            "fresh_cutoff_ts": cutoff,
            "observed_opportunity_epochs": observed_epochs,
            "active_opportunity_rows": len(all_opportunities),
            "included_fresh_rows": len(opportunities),
            "excluded_stale_or_foreign_rows": excluded_opportunities,
            "excluded_identity_alias_rows": len(identity_aliases),
            "identity_alias_episode_ids": sorted(str(row.get("episode_id") or "") for row in identity_aliases),
            "contamination_detected": contamination,
        },
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
        "blockers": (["V3_DATA_INTEGRITY_FAILED"] if not verification["passed"] else []) + (["MIXED_OR_PRE_CUTOFF_V3_EVIDENCE_EXCLUDED"] if excluded_opportunities or len(observed_epochs) > 1 else []) + (["CAUSAL_IDENTITY_ALIAS_EXCLUDED"] if identity_aliases else []) + (["NO_SAFE_QUALIFIED_POLICY"] if not ranking["number_one"] else []),
        "note": "Number one is selected only among policies passing every integrity, conservative-execution, sealed-OOS, drawdown, CVaR, liquidation, stability, multiple-testing and regime gate.",
    }
    _atomic_json(Path(report_dir) / REPORT_FILE, report)
    return report
