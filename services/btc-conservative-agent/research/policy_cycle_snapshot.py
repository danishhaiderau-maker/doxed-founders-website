"""Immutable input boundary shared by one analyzer policy-report cycle."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from collector_v22_schema import RESEARCH_EVENTS_FILE
from microstructure_tape import FILE_NAME as MICROSTRUCTURE_FILE, validate_window


def _load_microstructure_snapshot(data_dir=".") -> dict:
    path = Path(data_dir) / MICROSTRUCTURE_FILE
    rows = []
    digest = hashlib.sha256()
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                frozen = json.loads(json.dumps(row, sort_keys=True, separators=(",", ":")))
                rows.append(frozen)
                digest.update(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode())
                digest.update(b"\n")
    except OSError:
        pass
    bounds = [int(row["bucket_ts"]) for row in rows if isinstance(row.get("bucket_ts"), (int, float))]
    receipt = {
        "schema": "market_microstructure_snapshot_v1",
        "source_file": MICROSTRUCTURE_FILE,
        "snapshot_sha256": digest.hexdigest(),
        "row_count": len(rows),
        "first_bucket_ts": min(bounds) if bounds else None,
        "last_bucket_ts": max(bounds) if bounds else None,
    }
    return {"rows": tuple(rows), "receipt": receipt}


def _microstructure_evidence(events, tape_snapshot) -> dict:
    rows = tape_snapshot["rows"]
    rows_by_bucket = defaultdict(list)
    for row in rows:
        try:
            rows_by_bucket[int(row.get("bucket_ts"))].append(row)
        except (TypeError, ValueError, AttributeError):
            continue
    referenced = complete = incomplete = 0
    complete_ids = []
    for event in events:
        reference = event.get("microstructure_window")
        if reference is None:
            continue
        referenced += 1
        valid_reference = bool(
            isinstance(reference, dict)
            and reference.get("schema") == "microstructure_window_reference_v1"
            and reference.get("source_file") == MICROSTRUCTURE_FILE
        )
        if not valid_reference:
            incomplete += 1
            continue
        result = validate_window(rows_by_bucket, reference)
        if result.get("eligible") is True:
            complete += 1
            complete_ids.append(str(event.get("event_id") or ""))
        else:
            incomplete += 1
    return {
        "schema": "conservative_microstructure_evidence_v1",
        "tape_snapshot": tape_snapshot["receipt"],
        "events_evaluated": len(events),
        "referenced_events": referenced,
        "complete_windows": complete,
        "incomplete_windows": incomplete,
        "unreferenced_events": len(events) - referenced,
        "conservative_evidence_event_ids": complete_ids,
        "cohort_status": "AVAILABLE" if complete else "NO_COMPLETE_CONSERVATIVE_EVIDENCE",
        "qualification_effect": "SEPARATE_EVIDENCE_ONLY",
    }


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
    tape_snapshot = _load_microstructure_snapshot(data_dir)
    return {
        "events": tuple(events), "receipt": receipt,
        "microstructure": _microstructure_evidence(events, tape_snapshot),
    }


def build_policy_cycle_reports(data_dir=".", report_dir=".", between_builders_hook=None) -> dict:
    """Generate candidate then best from one pinned event tuple."""
    from research.policy_candidate_oos import build_policy_candidate_oos_report
    from research.best_policy_research import build_best_policy_research_report

    snapshot = load_policy_cycle_snapshot(data_dir)
    candidate = build_policy_candidate_oos_report(
        data_dir=data_dir, report_dir=report_dir,
        events=snapshot["events"], cycle_snapshot=snapshot["receipt"],
        microstructure_evidence=snapshot["microstructure"],
    )
    if between_builders_hook:
        between_builders_hook()
    best = build_best_policy_research_report(
        data_dir=data_dir, report_dir=report_dir,
        events=snapshot["events"], cycle_snapshot=snapshot["receipt"],
        microstructure_evidence=snapshot["microstructure"],
    )
    return {
        "candidate": candidate, "best": best, "cycle_snapshot": snapshot["receipt"],
        "microstructure": snapshot["microstructure"],
    }
