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
    current = [row for row in rows if str(
        row.get("epoch_id") or (row.get("envelope") or {}).get("epoch_id") or ""
    ) == epoch_id] if epoch_id else []
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
    if missing_episode_ids:
        blockers.append("EVENT_EPISODE_ID_MISSING")
    for name, count in outcome_coverage.items():
        if count == 0:
            blockers.append(f"{name}_COVERAGE_MISSING")
    if str(oos.get("epoch_id") or "") != epoch_id:
        blockers.append("OOS_REPORT_EPOCH_MISMATCH")
    if not oos.get("independent_oos_qualified"):
        blockers.append("INDEPENDENT_OOS_EVIDENCE_MISSING")
    if not gates or not all(value is True for value in gates.values()):
        blockers.append("QUALIFICATION_GATES_INCOMPLETE")
    candidate = oos.get("candidate") or oos.get("current_candidate")
    if not candidate:
        blockers.append("QUALIFIED_CANDIDATE_MISSING")
    blockers = sorted(set(blockers))
    qualified = bool(str(oos.get("status") or "").upper() == "QUALIFIED" and not blockers)
    report = {
        "schema": "best_policy_research_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "epoch_id": epoch_id or None,
        "status": "QUALIFIED" if qualified else "NO QUALIFIED POLICY",
        "independent_oos_qualified": qualified,
        "current_candidate": candidate if qualified else None,
        "qualification_gates": gates,
        "evidence": {
            "current_epoch_events": len(current),
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
