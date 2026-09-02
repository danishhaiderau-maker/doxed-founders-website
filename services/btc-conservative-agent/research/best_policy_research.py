"""Build the fail-closed current-epoch policy-readiness artifact.

This adapter intentionally does not search or rank policies. It joins matured
collector evidence to a separately produced chronological OOS qualification
receipt and refuses to expose a candidate when either side is incomplete.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from collector_v22_schema import RESEARCH_EVENTS_FILE
from collector_v22 import research_event_generation_paths
from replay_eligibility import validate_replay_eligibility
from policy_search_manifest import POLICY_SEARCH_MANIFEST

BEST_POLICY_RESEARCH_REPORT_FILE = "best_policy_research_report.json"
POLICY_CANDIDATE_OOS_REPORT_FILE = "policy_candidate_oos_report.json"
POLICY_SEARCH_MANIFEST_FILE = "policy_search_manifest.json"
QUALIFICATION_GATE_SCHEMA = "best_policy_qualification_gates_v2"
REQUIRED_QUALIFICATION_GATES = (
    "chronological_untouched_oos",
    "cost_adjusted_positive_expectancy",
    "acceptable_drawdown",
    "minimum_independent_episodes",
    "parameter_neighborhood_stability",
    "conservative_execution",
    "regime_diversity",
    "no_data_integrity_defects",
    "control_benchmark_comparison",
    "purged_walk_forward_validation",
    "sealed_holdout_receipt",
    "measured_execution_costs",
    "liquidation_buffer",
    "detailed_regime_support",
    "baseline_replay_coverage",
)

QUALIFICATION_GATE_LABELS = {
    "chronological_untouched_oos": "Chronological untouched OOS",
    "cost_adjusted_positive_expectancy": "Positive cost-adjusted expectancy",
    "acceptable_drawdown": "Acceptable drawdown and CVaR",
    "minimum_independent_episodes": "Adequate independent sample",
    "parameter_neighborhood_stability": "Stable neighbouring parameters",
    "conservative_execution": "Conservative execution evidence",
    "regime_diversity": "Multiple regimes represented",
    "no_data_integrity_defects": "No data-integrity defects",
    "control_benchmark_comparison": "Control and benchmark comparison",
    "purged_walk_forward_validation": "Purged walk-forward and embargoed folds",
    "sealed_holdout_receipt": "Single-use sealed holdout receipt",
    "measured_execution_costs": "Measured fees, funding, latency and slippage",
    "liquidation_buffer": "Verified liquidation buffer",
    "detailed_regime_support": "Detailed regime and liquidity support",
    "baseline_replay_coverage": "Market, no-chase, 13m and 30m baseline replay",
}


def qualification_gate_details(gates, evidence=None, *, current_generation_available=True) -> list[dict]:
    """Project mandatory gates without turning absent evidence into a pass.

    Boolean gates remain in the report for existing clients.  This richer view
    distinguishes an observed failure from an unmeasured gate and from a report
    that is not bound to the current generation.
    """
    values = gates if isinstance(gates, dict) else {}
    receipts = evidence if isinstance(evidence, dict) else {}
    rows = []
    for gate in REQUIRED_QUALIFICATION_GATES:
        receipt = receipts.get(gate)
        receipt = receipt if isinstance(receipt, dict) else {}
        if not current_generation_available:
            status = "UNAVAILABLE"
            blocker = "CURRENT_GENERATION_UNAVAILABLE"
        elif values.get(gate) is True:
            status = "PASS"
            blocker = None
        elif gate in values and values.get(gate) is False:
            status = "FAIL"
            blocker = str(receipt.get("blocker") or f"QUALIFICATION_GATE_FAILED:{gate}")
        else:
            status = "UNKNOWN"
            blocker = str(receipt.get("blocker") or f"QUALIFICATION_GATE_EVIDENCE_MISSING:{gate}")
        rows.append({
            "gate": gate,
            "label": QUALIFICATION_GATE_LABELS.get(gate, gate.replace("_", " ").title()),
            "status": status,
            "blocker": blocker,
            "evidence": receipt.get("evidence"),
            "receipt_id": receipt.get("receipt_id"),
            "source": receipt.get("source"),
        })
    return rows


def qualification_gate_blockers(gates) -> list[str]:
    if not isinstance(gates, dict):
        return ["QUALIFICATION_GATES_INVALID"]
    blockers = []
    for gate in REQUIRED_QUALIFICATION_GATES:
        if gates.get(gate) is not True:
            blockers.append(f"QUALIFICATION_GATE_FAILED:{gate}")
    return blockers


def candidate_contract_blockers(candidate) -> list[str]:
    if not isinstance(candidate, dict):
        return ["QUALIFIED_CANDIDATE_MISSING"]
    blockers = []
    kind = str(candidate.get("kind") or "").upper()
    if kind not in ("STATIC", "DYNAMIC"):
        return ["CANDIDATE_KIND_INVALID"]
    for field in ("policy_id", "policy_signature"):
        if not candidate.get(field):
            blockers.append(f"CANDIDATE_{field.upper()}_MISSING")
    if kind == "STATIC":
        if not isinstance(candidate.get("policy_spec"), dict) or not candidate["policy_spec"]:
            blockers.append("STATIC_POLICY_SPEC_MISSING")
    else:
        classifier = candidate.get("regime_classifier")
        if not isinstance(classifier, dict):
            blockers.append("DYNAMIC_REGIME_CLASSIFIER_MISSING")
        else:
            for field in ("id", "version", "feature_schema"):
                if not classifier.get(field):
                    blockers.append(f"DYNAMIC_CLASSIFIER_{field.upper()}_MISSING")
        mapping = candidate.get("regime_policy_map")
        if not isinstance(mapping, dict) or not mapping:
            blockers.append("DYNAMIC_REGIME_POLICY_MAP_MISSING")
        if candidate.get("fallback") not in ("CONTROL", "NO_TRADE"):
            blockers.append("DYNAMIC_FALLBACK_INVALID")
        if candidate.get("drift_action") not in ("CONTROL", "NO_TRADE"):
            blockers.append("DYNAMIC_DRIFT_ACTION_INVALID")
        for field in ("training_cutoff", "supported_domain"):
            if not candidate.get(field):
                blockers.append(f"DYNAMIC_{field.upper()}_MISSING")
        per_regime = candidate.get("per_regime_oos")
        if not isinstance(per_regime, dict) or not per_regime:
            blockers.append("DYNAMIC_PER_REGIME_OOS_MISSING")
        elif isinstance(mapping, dict):
            for regime in mapping:
                receipt = per_regime.get(regime)
                if not isinstance(receipt, dict) or int(receipt.get("independent_episodes") or 0) <= 0:
                    blockers.append(f"DYNAMIC_REGIME_OOS_MISSING:{regime}")
    return blockers


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _events(path: Path) -> list[dict]:
    rows = []
    paths = research_event_generation_paths(str(path.parent)) if path.name == RESEARCH_EVENTS_FILE else [str(path)]
    for candidate in paths:
        try:
            with Path(candidate).open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
            pass
    return rows


def build_best_policy_research_report(data_dir=".", report_dir=".", *, events=None, cycle_snapshot=None, microstructure_evidence=None, genome=None) -> dict:
    data_root = Path(data_dir)
    report_root = Path(report_dir)
    from research.v3_policy_report_adapter import has_v3_evidence, load_or_build_genome, load_v3_cycle_snapshot

    if has_v3_evidence(data_dir):
        genome = genome if genome is not None else load_or_build_genome(data_dir, report_dir)
        snapshot = cycle_snapshot or load_v3_cycle_snapshot(data_dir)
        oos = _json(report_root / POLICY_CANDIDATE_OOS_REPORT_FILE)
        collection = genome.get("collection") or {}
        identities = collection.get("effective_paper_execution_identities") or []
        identity = identities[0] if len(identities) == 1 else {}
        gates = oos.get("qualification_gates") or {}
        gate_receipts = oos.get("qualification_gate_evidence") or {}
        generation_available = bool(
            genome.get("epoch_id")
            and str(oos.get("epoch_id") or "") == str(genome.get("epoch_id") or "")
        )
        candidate = oos.get("candidate") or oos.get("current_candidate")
        blockers = sorted(set(list(oos.get("blockers") or []) + qualification_gate_blockers(gates) + candidate_contract_blockers(candidate)))
        qualified = bool(str(oos.get("status") or "").upper() == "QUALIFIED" and candidate and not blockers)
        evidence = oos.get("evidence") or {}
        report = {
            "schema": "best_policy_research_v3_1_adapter_v1",
            "cycle_snapshot": snapshot,
            "conservative_microstructure_evidence": microstructure_evidence,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "epoch_id": genome.get("epoch_id"),
            "policy_epoch_id": identity.get("policy_epoch_id"),
            "evidence_policy_signature": identity.get("policy_signature"),
            "status": "QUALIFIED" if qualified else "NO QUALIFIED POLICY",
            "independent_oos_qualified": qualified,
            "current_candidate": candidate if qualified else None,
            "descriptive_challenger": oos.get("descriptive_challenger"),
            "qualification_gates": gates,
            "qualification_gate_details": qualification_gate_details(
                gates, gate_receipts, current_generation_available=generation_available
            ),
            "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
            "evidence": {
                "current_epoch_events": int(evidence.get("current_events") or 0),
                "collection_epoch_events": int(evidence.get("current_events") or 0),
                "collection_policy_epoch_count": len({row.get("policy_epoch_id") for row in identities if row.get("policy_epoch_id")}),
                "completed_paths": int(evidence.get("eligible_events") or 0),
                "replay_eligible_execution_rows": int(
                    evidence.get("eligible_events") or 0
                ),
                "replay_eligible_events": int(evidence.get("eligible_events") or 0),
                "replay_ineligible_events": int(evidence.get("excluded_events") or 0),
                "independent_episode_count": int(evidence.get("independent_episodes") or 0),
                "events_missing_episode_id": 0,
                "qualified_oos_episodes": int(evidence.get("qualified_oos_episodes") or 0),
                "terminal_lifecycles": int(evidence.get("terminal_lifecycles") or 0),
                "provisional_lifecycles": int(evidence.get("provisional_lifecycles") or 0),
                "market_segments": int(evidence.get("market_segments") or 0),
                "order_intents": int(evidence.get("order_intents") or 0),
            },
            "blockers": blockers,
            "source_oos_report": POLICY_CANDIDATE_OOS_REPORT_FILE,
            "source_report": "safe_policy_genome_v3_report.json",
            "source_schema": genome.get("schema"),
            "research_design": {"search": genome.get("search"), "ranking": (genome.get("contract") or {}).get("ranking")},
            "live_policy_change_allowed": False,
        }
        report_root.mkdir(parents=True, exist_ok=True)
        target = report_root / BEST_POLICY_RESEARCH_REPORT_FILE
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temp.replace(target)
        return report
    rows = list(events) if events is not None else _events(data_root / RESEARCH_EVENTS_FILE)

    def signal_ts(row):
        try:
            return float((row.get("envelope") or {}).get("signal_ts") or row.get("signal_ts") or 0)
        except (TypeError, ValueError):
            return 0.0

    latest = max(rows, key=signal_ts, default={})
    epoch_id = str(latest.get("epoch_id") or (latest.get("envelope") or {}).get("epoch_id") or "")
    current_policy_epoch = str(
        latest.get("policy_epoch_id") or (latest.get("envelope") or {}).get("policy_epoch_id") or ""
    )
    current_policy_signature = str(
        latest.get("policy_signature") or (latest.get("envelope") or {}).get("policy_signature") or ""
    )
    current = [row for row in rows if str(
        row.get("epoch_id") or (row.get("envelope") or {}).get("epoch_id") or ""
    ) == epoch_id and str(
        row.get("policy_epoch_id") or (row.get("envelope") or {}).get("policy_epoch_id") or ""
    ) == current_policy_epoch] if epoch_id and current_policy_epoch else []
    outcome_coverage = {"ACCEPTED_FILLED": 0, "ACCEPTED_UNFILLED": 0, "REJECTED": 0}
    episodes = set()
    missing_episode_ids = 0
    eligible = 0
    for row in current:
        outcome = str(row.get("primary_outcome") or (row.get("envelope") or {}).get("primary_outcome") or "")
        if outcome in outcome_coverage:
            outcome_coverage[outcome] += 1
        episode_id = row.get("event_episode_id") or (row.get("envelope") or {}).get("event_episode_id")
        if episode_id:
            episodes.add(str(episode_id))
        else:
            missing_episode_ids += 1
        eligible += int(bool(validate_replay_eligibility(row).get("eligible")))

    oos = _json(report_root / POLICY_CANDIDATE_OOS_REPORT_FILE)
    gates = oos.get("qualification_gates") or {}
    gate_receipts = oos.get("qualification_gate_evidence") or {}
    blockers = list(oos.get("blockers") or [])
    if cycle_snapshot and (
        (oos.get("cycle_snapshot") or {}).get("snapshot_id")
        != cycle_snapshot.get("snapshot_id")
    ):
        blockers.append("POLICY_CYCLE_SNAPSHOT_MISMATCH")
    if not epoch_id:
        blockers.append("NO_CURRENT_V22_EPOCH")
    if eligible != len(current):
        blockers.append("REPLAY_INELIGIBLE_PATHS_PRESENT")
    collection_epoch_rows = [row for row in rows if str(
        row.get("epoch_id") or (row.get("envelope") or {}).get("epoch_id") or ""
    ) == epoch_id]
    collection_policy_epochs = {
        str(row.get("policy_epoch_id") or (row.get("envelope") or {}).get("policy_epoch_id") or "")
        for row in collection_epoch_rows
    }
    if "" in collection_policy_epochs:
        blockers.append("POLICY_IDENTITY_MISSING")
    if missing_episode_ids:
        blockers.append("EVENT_EPISODE_ID_MISSING")
    for name, count in outcome_coverage.items():
        if count == 0:
            blockers.append(f"{name}_COVERAGE_MISSING")
    if str(oos.get("epoch_id") or "") != epoch_id:
        blockers.append("OOS_REPORT_EPOCH_MISMATCH")
    if str(oos.get("policy_epoch_id") or "") != current_policy_epoch:
        blockers.append("OOS_REPORT_POLICY_EPOCH_MISMATCH")
    if str(oos.get("evidence_policy_signature") or "") != current_policy_signature:
        blockers.append("OOS_REPORT_POLICY_SIGNATURE_MISMATCH")
    if not oos.get("independent_oos_qualified"):
        blockers.append("INDEPENDENT_OOS_EVIDENCE_MISSING")
    if oos.get("qualification_gate_schema") != QUALIFICATION_GATE_SCHEMA:
        blockers.append("QUALIFICATION_GATE_SCHEMA_MISMATCH")
    blockers.extend(qualification_gate_blockers(gates))
    candidate = oos.get("candidate") or oos.get("current_candidate")
    blockers.extend(candidate_contract_blockers(candidate))
    blockers = sorted(set(blockers))
    qualified = bool(str(oos.get("status") or "").upper() == "QUALIFIED" and not blockers)
    report = {
        "schema": "best_policy_research_v1",
        "cycle_snapshot": cycle_snapshot,
        "conservative_microstructure_evidence": microstructure_evidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "epoch_id": epoch_id or None,
        "policy_epoch_id": current_policy_epoch or None,
        "evidence_policy_signature": current_policy_signature or None,
        "status": "QUALIFIED" if qualified else "NO QUALIFIED POLICY",
        "independent_oos_qualified": qualified,
        "current_candidate": candidate if qualified else None,
        "descriptive_challenger": oos.get("descriptive_challenger"),
        "qualification_gates": gates,
        "qualification_gate_details": qualification_gate_details(
            gates,
            gate_receipts,
            current_generation_available=bool(
                epoch_id
                and str(oos.get("epoch_id") or "") == epoch_id
                and str(oos.get("policy_epoch_id") or "") == current_policy_epoch
                and str(oos.get("evidence_policy_signature") or "") == current_policy_signature
            ),
        ),
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "evidence": {
            "current_epoch_events": len(current),
            "collection_epoch_events": len(collection_epoch_rows),
            "collection_policy_epoch_count": len(collection_policy_epochs - {""}),
            "completed_paths": eligible,
            "replay_eligible_execution_rows": eligible,
            "replay_eligible_events": eligible,
            "replay_ineligible_events": len(current) - eligible,
            "independent_episode_count": len(episodes),
            "events_missing_episode_id": missing_episode_ids,
            "qualified_oos_episodes": int((oos.get("evidence") or {}).get("qualified_oos_episodes") or 0),
            "outcome_coverage": outcome_coverage,
        },
        "blockers": blockers,
        "source_oos_report": POLICY_CANDIDATE_OOS_REPORT_FILE,
        "research_design": {
            "search_manifest_schema": POLICY_SEARCH_MANIFEST["schema"],
            "search_manifest_version": POLICY_SEARCH_MANIFEST["version"],
            "search_manifest_signature": POLICY_SEARCH_MANIFEST["signature"],
            "counts": POLICY_SEARCH_MANIFEST["counts"],
            "indicator_families": POLICY_SEARCH_MANIFEST["indicator_families"],
            "causal_regime_features": POLICY_SEARCH_MANIFEST["causal_regime_features"],
            "static_vs_dynamic": {
                "required": True,
                "static_candidate": "one frozen policy across all supported regimes",
                "dynamic_candidate": "frozen causal regime classifier and regime-policy map",
                "winner_rule": "untouched chronological OOS expectancy and drawdown after execution costs",
                "fallback": "CONTROL or NO_TRADE for unknown or drifting regimes",
            },
        },
    }
    report_root.mkdir(parents=True, exist_ok=True)
    manifest_target = report_root / POLICY_SEARCH_MANIFEST_FILE
    manifest_temp = manifest_target.with_suffix(manifest_target.suffix + ".tmp")
    manifest_temp.write_text(json.dumps(POLICY_SEARCH_MANIFEST, indent=2), encoding="utf-8")
    manifest_temp.replace(manifest_target)
    target = report_root / BEST_POLICY_RESEARCH_REPORT_FILE
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temp.replace(target)
    return report
