"""Deterministic, descriptive-only summary of canonical shadow-lane outcomes.

This report intentionally keeps each policy lane separate.  Two lanes may replay
the same shared AI call, so their PnL must never be summed as independent trades
or merged with executed-trade PnL.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable


REPORT_NAME = "shadow_lane_comprehensive_report.json"
OUTCOME_NAME = "shadow_lane_outcome.jsonl"
DECISION_NAME = "type_b_adx_v3_shadow_decisions.jsonl"
EVENT_NAME = "research_events_v22.jsonl"
SESSION_NAME = "research_session.json"
PROVISIONAL_EXIT_REASONS = {"BUFFER_TRUNCATED"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _iso_key(value: Any) -> str:
    return str(value or "")


def _latest_by(rows: Iterable[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in key_fields)
        if not any(key):
            continue
        prior = latest.get(key)
        if prior is None or _iso_key(row.get("ts")) >= _iso_key(prior.get("ts")):
            latest[key] = row
    return [latest[key] for key in sorted(latest)]


def _classification(row: dict[str, Any]) -> str:
    if bool(row.get("policy_entered")):
        return "POLICY_ENTERED_ACCEPTED"
    if str(row.get("research_lane") or "") == "TYPE_B_HUNTER_ADX_V3_SHADOW":
        return "POLICY_REJECTED_COUNTERFACTUAL"
    return "CALIBRATION_COUNTERFACTUAL"


def _regime(row: dict[str, Any]) -> str:
    features = row.get("entry_features") if isinstance(row.get("entry_features"), dict) else {}
    return str(features.get("regime") or features.get("regime_label") or row.get("regime") or "UNKNOWN")


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [row for row in rows if bool(row.get("filled"))]
    provisional = [row for row in filled if str(row.get("exit_reason") or "") in PROVISIONAL_EXIT_REASONS]
    completed = [
        row for row in filled
        if row not in provisional and row.get("net_pnl_usd") is not None
    ]
    pnls = [float(row.get("net_pnl_usd") or 0.0) for row in completed]
    return {
        "records": len(rows),
        "independent_shared_ai_episodes": len({str(row.get("source_trade_id") or row.get("shared_ai_call_id") or "") for row in rows if row.get("source_trade_id") or row.get("shared_ai_call_id")}),
        "fills": len(filled),
        "no_fills": len(rows) - len(filled),
        "completed_terminal_fills": len(completed),
        "provisional_excluded": len(provisional),
        "wins": sum(value > 0 for value in pnls),
        "losses": sum(value < 0 for value in pnls),
        "flats": sum(value == 0 for value in pnls),
        "net_pnl_usd": round(sum(pnls), 6),
        "ev_per_completed_fill_usd": round(sum(pnls) / len(pnls), 6) if pnls else None,
        "exit_reasons": dict(sorted(Counter(str(row.get("exit_reason") or "UNKNOWN") for row in rows).items())),
        "regimes": dict(sorted(Counter(_regime(row) for row in rows).items())),
    }


def _selected_epoch(data_root: Path, events: list[dict[str, Any]]) -> str | None:
    session_path = data_root / SESSION_NAME
    if session_path.is_file():
        try:
            session = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            session = {}
        if isinstance(session, dict) and session.get("collector_v22_epoch_id"):
            return str(session["collector_v22_epoch_id"])
    epoch_counts = Counter(str(row.get("epoch_id") or "") for row in events if row.get("epoch_id"))
    return epoch_counts.most_common(1)[0][0] if epoch_counts else None


def _build_cohorts(
    rows: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    qualification: str,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("research_lane") or "UNKNOWN"), _classification(row))].append(row)
    cohorts = []
    for (lane, classification), cohort_rows in sorted(grouped.items()):
        summary = _summarize(cohort_rows)
        blockers = Counter()
        if classification == "POLICY_REJECTED_COUNTERFACTUAL":
            for row in cohort_rows:
                decision = decisions.get(str(row.get("study_id") or ""), {})
                blockers[str(decision.get("block_reason") or "UNKNOWN_REJECTION_REASON")] += 1
        cohorts.append({
            "research_lane": lane,
            "classification": classification,
            **summary,
            "policy_signatures": sorted({str(row.get("policy_signature")) for row in cohort_rows if row.get("policy_signature")}),
            "policy_epoch_ids": sorted({str(row.get("policy_epoch_id")) for row in cohort_rows if row.get("policy_epoch_id")}),
            "blockers": dict(sorted(blockers.items())),
            "qualification": qualification,
        })
    return cohorts


def build_shadow_lane_comprehensive_report(data_dir: str = ".", report_dir: str = ".") -> dict[str, Any]:
    data_root = Path(data_dir)
    report_root = Path(report_dir)
    outcome_rows = _latest_by(
        _read_jsonl(data_root / OUTCOME_NAME),
        ("study_id", "research_lane"),
    )
    decisions = {
        str(row.get("study_id")): row
        for row in _latest_by(_read_jsonl(data_root / DECISION_NAME), ("study_id",))
    }
    events = _read_jsonl(data_root / EVENT_NAME)
    epoch_id = _selected_epoch(data_root, events)
    cutoff = max((_iso_key(row.get("ts")) for row in outcome_rows), default=None)

    current_rows = []
    legacy_unscoped_rows = []
    foreign_rows = []
    malformed_identity_rows = []
    for row in outcome_rows:
        row_epoch = str(row.get("collection_epoch_id") or row.get("epoch_id") or "")
        signature = str(row.get("policy_signature") or "")
        policy_epoch = str(row.get("policy_epoch_id") or "")
        if not row_epoch:
            legacy_unscoped_rows.append(row)
        elif row_epoch != str(epoch_id or ""):
            foreign_rows.append(row)
        elif not signature or not policy_epoch:
            malformed_identity_rows.append(row)
        else:
            current_rows.append(row)

    cohorts = _build_cohorts(current_rows, decisions, "CURRENT_SIGNED_DESCRIPTIVE_ONLY")
    legacy_cohorts = _build_cohorts(
        legacy_unscoped_rows, decisions, "LEGACY_UNSCOPED_DESCRIPTIVE_ONLY"
    )

    episode_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in current_rows:
        episode = str(row.get("source_trade_id") or row.get("shared_ai_call_id") or row.get("study_id") or "")
        if episode:
            episode_rows[episode].append(row)
    paired = sum(len({str(row.get("research_lane") or "") for row in rows}) > 1 for rows in episode_rows.values())

    report = {
        "schema": "shadow_lane_comprehensive_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_epoch_id": epoch_id,
        "current_cutoff_ts": cutoff,
        "selection": {
            "source": OUTCOME_NAME,
            "dedupe_key": ["study_id", "research_lane"],
            "episode_key": "source_trade_id_or_shared_ai_call_id",
            "row_epoch_note": "Only rows with exact collection_epoch_id, policy_signature, and policy_epoch_id enter current cohorts.",
        },
        "epoch_scope": {
            "selected_epoch_id": epoch_id,
            "current_signed_rows": len(current_rows),
            "legacy_unscoped_rows": len(legacy_unscoped_rows),
            "foreign_epoch_rows": len(foreign_rows),
            "malformed_current_identity_rows": len(malformed_identity_rows),
            "qualification_blocked": bool(foreign_rows or malformed_identity_rows),
            "blockers": sorted(set(
                (["FOREIGN_EPOCH_SHADOW_ROWS"] if foreign_rows else [])
                + (["MALFORMED_CURRENT_SHADOW_IDENTITY"] if malformed_identity_rows else [])
            )),
        },
        "safety": {
            "status": "DESCRIPTIVE_ONLY_NEVER_RELAY_ELIGIBLE",
            "live_policy_change_allowed": False,
            "executed_pnl_merged": False,
            "paired_lane_pnl_additive": False,
            "warning": "Policy lanes can replay the same shared AI call. Compare lanes within episodes; never sum them as independent trades.",
        },
        "coverage": {
            "deduped_lane_records": len(current_rows),
            "all_preserved_lane_records": len(outcome_rows),
            "independent_shared_ai_episodes": len(episode_rows),
            "paired_multi_lane_episodes": paired,
            "single_lane_episodes": len(episode_rows) - paired,
            "provisional_exit_reasons": sorted(PROVISIONAL_EXIT_REASONS),
        },
        "cohorts": cohorts,
        "legacy_unscoped_cohorts": legacy_cohorts,
    }
    report_root.mkdir(parents=True, exist_ok=True)
    target = report_root / REPORT_NAME
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, target)
    return report


__all__ = ["build_shadow_lane_comprehensive_report"]
