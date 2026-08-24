"""Analyzer-facing Safe Policy Genome V3 status and ranking report."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from policy_search_manifest import POLICY_SEARCH_MANIFEST
from research_v3_contract import SAFE_POLICY_GENOME_CONTRACT, normalize_lifecycle_outcome
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
    parents = list(range(len(opportunities)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parents[right] = left

    first_by_shared: dict[str, int] = {}
    first_by_fingerprint: dict[tuple[float, str, str], int] = {}
    for index, row in enumerate(opportunities):
        shared = str(row.get("shared_ai_call_id") or "").strip()
        if shared:
            if shared in first_by_shared:
                union(index, first_by_shared[shared])
            else:
                first_by_shared[shared] = index
        try:
            signal_ts = float(row.get("signal_ts"))
        except (TypeError, ValueError):
            signal_ts = -1.0
        fingerprint = (
            signal_ts,
            str(row.get("symbol") or "").upper(),
            str(row.get("raw_direction") or "").upper(),
        )
        if fingerprint in first_by_fingerprint:
            union(index, first_by_fingerprint[fingerprint])
        else:
            first_by_fingerprint[fingerprint] = index
    grouped: dict[int, list[dict[str, Any]]] = {}
    for index, row in enumerate(opportunities):
        grouped.setdefault(find(index), []).append(row)
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
    order_intents = [row for row in scoped(_read_ledger(store.ledger_path("order_intent"))) if str(row.get("episode_id") or "") in allowed_episodes]
    lifecycles = [row for row in scoped(_read_ledger(store.ledger_path("lifecycle"))) if str(row.get("episode_id") or "") in allowed_episodes]
    terminal_lifecycles = [row for row in lifecycles if row.get("terminal") is True]
    executions = [row for row in scoped(_read_ledger(store.ledger_path("execution"))) if str(row.get("episode_id") or "") in allowed_episodes]
    observed_epochs = sorted({str(row.get("epoch_id")) for row in all_opportunities if row.get("epoch_id")})
    excluded_opportunities = len(all_opportunities) - len(opportunities)
    policy_ids_by_signature: dict[str, set[str]] = {}
    signatures_by_episode_policy: dict[tuple[str, str], set[str]] = {}
    missing_policy_identity_rows = 0
    pending_policy_identity_rows = 0
    immediate_lane_decisions = [
        row for row in decisions
        if str(row.get("decision_stage") or "") == "LANE_POLICY_VERDICT"
    ]
    now_ts = datetime.now(timezone.utc).timestamp()
    reconciliation_deadlines_by_episode: dict[str, list[float]] = {}
    for decision in immediate_lane_decisions:
        if decision.get("order_intent_expected") is True:
            reconciliation_deadlines_by_episode.setdefault(
                str(decision.get("episode_id") or ""), []
            ).append(float(decision.get("resolution_deadline_ts") or 0))

    def identity_is_still_reconciling(row: dict[str, Any]) -> bool:
        deadlines = reconciliation_deadlines_by_episode.get(
            str(row.get("episode_id") or ""), []
        )
        return bool(deadlines and max(deadlines) > now_ts)
    policy_attributable_lifecycles = [
        row for row in lifecycles
        if str(row.get("observation_status") or "") in {
            "PAPER_POSITION_OPEN", "PAPER_POSITION_CLOSED",
        }
    ]
    for row in immediate_lane_decisions:
        signature = str(row.get("policy_signature") or "").strip()
        policy_id = str(row.get("policy_id") or "").strip()
        policy_epoch_id = str(row.get("policy_epoch_id") or "").strip()
        if not signature or not policy_id or not policy_epoch_id:
            missing_policy_identity_rows += 1
            continue
        policy_ids_by_signature.setdefault(signature, set()).add(policy_id)
        signatures_by_episode_policy.setdefault(
            (str(row.get("episode_id") or ""), policy_id), set()
        ).add(signature)
    for row in order_intents:
        signature = str(row.get("policy_signature") or "").strip()
        policy_id = str(row.get("policy_id") or "").strip()
        policy_epoch_id = str(row.get("policy_epoch_id") or "").strip()
        if not signature or not policy_id or not policy_epoch_id:
            if identity_is_still_reconciling(row):
                pending_policy_identity_rows += 1
            else:
                missing_policy_identity_rows += 1
            continue
        policy_ids_by_signature.setdefault(signature, set()).add(policy_id)
        signatures_by_episode_policy.setdefault(
            (str(row.get("episode_id") or ""), policy_id), set()
        ).add(signature)
    for row in [*executions, *policy_attributable_lifecycles]:
        signature = str(row.get("policy_signature") or "").strip()
        policy_id = str(row.get("policy_id") or "").strip()
        policy_epoch_id = str(row.get("policy_epoch_id") or "").strip()
        research_lane = str(row.get("research_lane") or "").strip()
        shared_ai_call_id = str(row.get("shared_ai_call_id") or "").strip()
        if not all((signature, policy_id, policy_epoch_id, research_lane, shared_ai_call_id)):
            if identity_is_still_reconciling(row):
                pending_policy_identity_rows += 1
            else:
                missing_policy_identity_rows += 1
            continue
        policy_ids_by_signature.setdefault(signature, set()).add(policy_id)
        signatures_by_episode_policy.setdefault(
            (str(row.get("episode_id") or ""), policy_id), set()
        ).add(signature)
    policy_signature_collisions = {
        signature: sorted(policy_ids)
        for signature, policy_ids in policy_ids_by_signature.items()
        if len(policy_ids) > 1
    }
    policy_signature_divergence = {
        f"{episode_id}:{policy_id}": sorted(signatures)
        for (episode_id, policy_id), signatures in signatures_by_episode_policy.items()
        if len(signatures) > 1
    }
    paper_world_contradiction_rows = []
    policy_provenance_rows = [
        *immediate_lane_decisions,
        *order_intents,
        *executions,
        *policy_attributable_lifecycles,
    ]
    for row in policy_provenance_rows:
        if str(row.get("policy_execution_scope") or "") != "PAPER_RESEARCH_ONLY":
            continue
        spec = row.get("paper_policy_spec")
        spec_paper_only = spec.get("paper_only") if isinstance(spec, dict) else None
        if row.get("paper_only") is not False and spec_paper_only is not False:
            continue
        paper_world_contradiction_rows.append({
            "record_id": str(row.get("record_id") or ""),
            "episode_id": str(row.get("episode_id") or ""),
            "policy_id": str(row.get("policy_id") or ""),
            "top_level_paper_only": row.get("paper_only"),
            "spec_paper_only": spec_paper_only,
        })
    policy_identity_contamination = bool(
        policy_signature_collisions or policy_signature_divergence
        or missing_policy_identity_rows or paper_world_contradiction_rows
    )
    contamination = bool(
        excluded_opportunities or identity_aliases or len(observed_epochs) > 1
        or policy_identity_contamination
    )
    outcome_counts = Counter(normalize_lifecycle_outcome(
        row.get("outcome_state"), net_pnl_usd=row.get("net_pnl_usd")
    ) for row in terminal_lifecycles)
    decision_outcomes = Counter(str(
        row.get("primary_outcome")
        or row.get("outcome_state")
        or row.get("policy_decision")
        or "UNKNOWN"
    ) for row in decisions)
    decision_dispositions = Counter(str(
        row.get("execution_disposition") or "LEGACY_TERMINAL_DECISION"
    ) for row in decisions)
    lane_decision_outcomes: dict[str, Counter] = {}
    for row in immediate_lane_decisions:
        lane = str(row.get("research_lane") or "UNKNOWN")
        outcome = str(row.get("outcome_state") or row.get("policy_decision") or "UNKNOWN")
        lane_decision_outcomes.setdefault(lane, Counter())[outcome] += 1
    def resolution_key(row):
        return (
            str(row.get("episode_id") or ""),
            str(row.get("policy_signature") or ""),
            str(row.get("research_lane") or "").upper(),
        )
    expected_order_decisions = [
        row for row in immediate_lane_decisions if row.get("order_intent_expected") is True
    ]
    intent_keys = {resolution_key(row) for row in order_intents}
    entry_resolutions: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in lifecycles:
        if row.get("resolution_scope") == "LANE_ENTRY":
            entry_resolutions.setdefault(resolution_key(row), []).append(row)
    entry_resolution_counts = Counter()
    orphan_expected_orders = []
    for decision in expected_order_decisions:
        key = resolution_key(decision)
        rows = entry_resolutions.get(key, [])
        states = {str(row.get("entry_resolution") or "") for row in rows}
        if key in intent_keys or "ORDER_SUBMITTED" in states:
            entry_resolution_counts["submitted"] += 1
        elif "NO_ORDER" in states:
            entry_resolution_counts["terminal_no_order"] += 1
        else:
            deadline = float(decision.get("resolution_deadline_ts") or 0)
            awaiting_deadlines = [float(row.get("resolution_deadline_ts") or 0) for row in rows if row.get("entry_resolution") == "AWAITING"]
            deadline = max([deadline, *awaiting_deadlines])
            if deadline > now_ts:
                entry_resolution_counts["awaiting_within_deadline"] += 1
            else:
                entry_resolution_counts["overdue_orphan"] += 1
                orphan_expected_orders.append({
                    "episode_id": key[0], "policy_signature": key[1],
                    "research_lane": key[2], "resolution_deadline_ts": deadline or None,
                })
    entry_resolution_integrity = {
        "expected": len(expected_order_decisions),
        "submitted": entry_resolution_counts["submitted"],
        "terminal_no_order": entry_resolution_counts["terminal_no_order"],
        "awaiting_within_deadline": entry_resolution_counts["awaiting_within_deadline"],
        "overdue_orphan": entry_resolution_counts["overdue_orphan"],
        "orphan_expected_orders": orphan_expected_orders,
        "passed": entry_resolution_counts["overdue_orphan"] == 0,
    }
    effective_paper_execution_identities = []
    seen_effective_identities = set()
    for row in [*order_intents, *executions, *policy_attributable_lifecycles]:
        signature = str(row.get("policy_signature") or "").strip()
        if not signature or signature in seen_effective_identities:
            continue
        seen_effective_identities.add(signature)
        spec = row.get("paper_policy_spec") or {}
        relay_capable = bool(spec.get("relay_eligible", row.get("relay_eligible", False)))
        effective_paper_execution_identities.append({
            "policy_signature": signature,
            "policy_epoch_id": row.get("policy_epoch_id"),
            "policy_id": row.get("policy_id"),
            "research_lane": row.get("research_lane"),
            "effective_execution_mode": "PAPER_OBSERVED",
            "live_relay_capable": relay_capable,
            "relay_capability_note": (
                "Capability metadata only; paper evidence does not authorize live relay."
            ),
        })
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
    if not entry_resolution_integrity["passed"]:
        # Preserve descriptive rows, but never surface a qualified winner from
        # a cohort whose expected entry outcomes are still missing.
        ranking = dict(ranking)
        ranking["number_one"] = None
        ranking["qualification"] = "BLOCKED_ORDER_RESOLUTION_INTEGRITY"
    # The replay engine may assess tens of thousands of complete policies.  The
    # full candidate rows are working memory, not a report contract: persisting
    # them under both candidate_screen.candidates and ranking.blocked made a
    # small V3.1 cohort produce a ~200 MB artifact and held the scheduled
    # analyzer in JSON serialization for minutes.  Keep the auditable counts,
    # blocker distribution, bounded leaderboards and at most the public top 100
    # qualified policies.  This is also the dashboard's documented exposure.
    persisted_candidate_screen = dict(candidate_screen or {
        "schema": "externally_supplied_safe_policy_candidates_v3",
        "unique_policies_evaluated": len(candidates or []),
        "descriptive_top_100": [],
        "profit_capture_leaders": {},
        "drawdown_control_leaders": [],
        "dynamic_regime_leaders": {},
    })
    persisted_candidate_screen.pop("candidates", None)
    blocked_rows = list(ranking.get("blocked") or [])
    blocker_counts = Counter(
        blocker
        for row in blocked_rows
        for blocker in (row.get("ranking_blockers") or [])
    )
    persisted_ranking = dict(ranking)
    persisted_ranking.pop("blocked", None)
    persisted_ranking["ranked"] = list(ranking.get("ranked") or [])[:100]
    persisted_ranking["blocked_policy_count"] = len(blocked_rows)
    persisted_ranking["blocked_gate_counts"] = dict(sorted(blocker_counts.items()))
    progress_receipts = []
    if candidate_screen is not None:
        progress_receipts.append({
            "unique_policies_evaluated": candidate_screen.get("unique_policies_evaluated", 0),
            "independent_episodes": len({row.get("episode_id") for row in opportunities if row.get("episode_id")}),
        })
    report = {
        "schema": "safe_policy_genome_v3_1_report_v1",
        "extension": "ADAPTIVE_EXIT_AND_DRAWDOWN_LAB_V3_1",
        "data_scope": "FRESH-COLLECTION" if selected_epoch is not None else "SESSION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "V3_INTEGRITY_FAILED" if not verification["passed"] else "V3_ORDER_RESOLUTION_INTEGRITY_FAILED" if not entry_resolution_integrity["passed"] else "V3_EPOCH_CONTAMINATION_BLOCKED" if contamination else "V3_COLLECTING" if opportunities else "V3_READY_FOR_FRESH_EPOCH",
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
            "missing_policy_identity_rows": missing_policy_identity_rows,
            "pending_policy_identity_rows": pending_policy_identity_rows,
            "policy_signature_collisions": policy_signature_collisions,
            "policy_signature_divergence": policy_signature_divergence,
            "paper_world_contradiction_count": len(paper_world_contradiction_rows),
            "paper_world_contradiction_rows": paper_world_contradiction_rows,
            "contamination_detected": contamination,
        },
        "collection": {
            "independent_opportunities": len({row.get("episode_id") for row in opportunities if row.get("episode_id")}),
            "decision_branches": len(decisions),
            "execution_rows": len(executions),
            "terminal_lifecycles": len(terminal_lifecycles),
            "provisional_lifecycles": len(lifecycles) - len(terminal_lifecycles),
            "decision_outcomes": dict(sorted(decision_outcomes.items())),
            "decision_dispositions": dict(sorted(decision_dispositions.items())),
            "lane_decision_outcomes": {
                lane: dict(sorted(counts.items()))
                for lane, counts in sorted(lane_decision_outcomes.items())
            },
            "outcome_states": dict(sorted(outcome_counts.items())),
            "ledger_counts": verification["ledger_counts"],
            "market_segments": verification["market_segment_count"],
            "entry_resolution_integrity": entry_resolution_integrity,
            "effective_paper_execution_identities": effective_paper_execution_identities,
        },
        "search": search,
        "search_progress": search_progress(search, progress_receipts),
        "candidate_screen": persisted_candidate_screen,
        "safe_policy_ranking": persisted_ranking,
        "number_one_strategy": ranking["number_one"],
        "qualification": ranking["qualification"],
        "blockers": (["V3_DATA_INTEGRITY_FAILED"] if not verification["passed"] else []) + (["ORPHAN_EXPECTED_ORDER"] if not entry_resolution_integrity["passed"] else []) + (["MIXED_OR_PRE_CUTOFF_V3_EVIDENCE_EXCLUDED"] if excluded_opportunities or len(observed_epochs) > 1 else []) + (["CAUSAL_IDENTITY_ALIAS_EXCLUDED"] if identity_aliases else []) + (["POLICY_IDENTITY_CONTAMINATION"] if policy_identity_contamination else []) + (["NO_SAFE_QUALIFIED_POLICY"] if not ranking["number_one"] else []),
        "note": "Number one is selected only among policies passing every integrity, conservative-execution, sealed-OOS, drawdown, CVaR, liquidation, stability, multiple-testing and regime gate.",
    }
    _atomic_json(Path(report_dir) / REPORT_FILE, report)
    return report
