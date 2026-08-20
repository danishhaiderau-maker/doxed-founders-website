"""Immutable input boundary shared by one analyzer policy-report cycle."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from collector_v22_schema import RESEARCH_EVENTS_FILE


def load_policy_cycle_snapshot(data_dir=".") -> dict:
    path = Path(data_dir) / RESEARCH_EVENTS_FILE
    events = []
    digest = hashlib.sha256()
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    # A concurrent append may expose an incomplete final line.
                    # It belongs to the next cycle, never this snapshot.
                    continue
                if not isinstance(row, dict):
                    continue
                frozen = json.loads(json.dumps(row, sort_keys=True, separators=(",", ":")))
                events.append(frozen)
                digest.update(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                digest.update(b"\n")
    except OSError:
        pass
    last = events[-1] if events else {}
    envelope = last.get("envelope") or {}
    receipt = {
        "schema": "policy_cycle_snapshot_v1",
        "snapshot_id": "policy-snapshot-" + digest.hexdigest()[:24],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_file": RESEARCH_EVENTS_FILE,
        "row_count": len(events),
        "last_event_id": last.get("event_id"),
        "last_signal_ts": envelope.get("signal_ts") or last.get("signal_ts"),
        "epoch_id": last.get("epoch_id") or envelope.get("epoch_id"),
        "policy_epoch_id": last.get("policy_epoch_id") or envelope.get("policy_epoch_id"),
        "policy_signature": last.get("policy_signature") or envelope.get("policy_signature"),
    }
    return {"events": tuple(events), "receipt": receipt}


def build_policy_cycle_reports(data_dir=".", report_dir=".", between_builders_hook=None) -> dict:
    """Generate candidate then best from one pinned event tuple."""
    from research.policy_candidate_oos import build_policy_candidate_oos_report
    from research.best_policy_research import build_best_policy_research_report

    snapshot = load_policy_cycle_snapshot(data_dir)
    candidate = build_policy_candidate_oos_report(
        data_dir=data_dir, report_dir=report_dir,
        events=snapshot["events"], cycle_snapshot=snapshot["receipt"],
    )
    if between_builders_hook:
        between_builders_hook()
    best = build_best_policy_research_report(
        data_dir=data_dir, report_dir=report_dir,
        events=snapshot["events"], cycle_snapshot=snapshot["receipt"],
    )
    return {"candidate": candidate, "best": best, "cycle_snapshot": snapshot["receipt"]}
