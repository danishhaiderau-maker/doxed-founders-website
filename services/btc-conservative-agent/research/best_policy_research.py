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
from replay_eligibility import validate_replay_eligibility

BEST_POLICY_RESEARCH_REPORT_FILE = "best_policy_research_report.json"
POLICY_CANDIDATE_OOS_REPORT_FILE = "policy_candidate_oos_report.json"
QUALIFICATION_GATE_SCHEMA = "best_policy_qualification_gates_v1"
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
)


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
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
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


def build_best_policy_research_report(data_dir=".", report_dir=".") -> dict:
    data_root = Path(data_dir)
    report_root = Path(report_dir)
    rows = _events(data_root / RESEARCH_EVENTS_FILE)

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
    blockers = list(oos.get("blockers") or [])
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "epoch_id": epoch_id or None,
        "policy_epoch_id": current_policy_epoch or None,
        "evidence_policy_signature": current_policy_signature or None,
        "status": "QUALIFIED" if qualified else "NO QUALIFIED POLICY",
        "independent_oos_qualified": qualified,
        "current_candidate": candidate if qualified else None,
        "qualification_gates": gates,
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "evidence": {
            "current_epoch_events": len(current),
            "collection_epoch_events": len(collection_epoch_rows),
            "collection_policy_epoch_count": len(collection_policy_epochs - {""}),
            "completed_paths": eligible,
            "replay_eligible_events": eligible,
            "replay_ineligible_events": len(current) - eligible,
            "independent_episode_count": len(episodes),
            "events_missing_episode_id": missing_episode_ids,
            "qualified_oos_episodes": int((oos.get("evidence") or {}).get("qualified_oos_episodes") or 0),
            "outcome_coverage": outcome_coverage,
        },
        "blockers": blockers,
        "source_oos_report": POLICY_CANDIDATE_OOS_REPORT_FILE,
    }
    report_root.mkdir(parents=True, exist_ok=True)
    target = report_root / BEST_POLICY_RESEARCH_REPORT_FILE
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temp.replace(target)
    return report
