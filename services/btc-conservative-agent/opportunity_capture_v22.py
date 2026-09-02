"""collector_v2.2 analyzer — three primaries only + capacity projection."""
from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any, Iterable, Mapping, Optional

from collector_storage import project_capacity
from collector_v22 import BYTES_PER_EVENT_TYPICAL, RESEARCH_EVENTS_FILE, research_event_generation_paths
from collector_v22_schema import (
    COLLECTOR_VERSION,
    PRIMARY_ACCEPTED_FILLED,
    PRIMARY_ACCEPTED_UNFILLED,
    PRIMARY_REJECTED,
)
from replay_eligibility import validate_replay_eligibility


def _load_jsonl(path: str) -> list:
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _version_ok(row: Mapping[str, Any], min_version: str = COLLECTOR_VERSION) -> bool:
    ver = str(row.get("collector_version") or "")
    if not ver.startswith("collector_v2."):
        return False
    if min_version == COLLECTOR_VERSION:
        return ver >= "collector_v2.2"
    return ver >= min_version


def analyze_v22_events(
    *,
    data_dir: Optional[str] = None,
    events_path: Optional[str] = None,
    min_collector_version: str = COLLECTOR_VERSION,
) -> dict:
    root = data_dir or os.getcwd()
    path = events_path or os.path.join(root, RESEARCH_EVENTS_FILE)
    paths = [path] if events_path else research_event_generation_paths(root)
    rows = [r for candidate in paths for r in _load_jsonl(candidate) if _version_ok(r, min_collector_version)]
    filled = [r for r in rows if r.get("primary_outcome") == PRIMARY_ACCEPTED_FILLED]
    unfilled = [r for r in rows if r.get("primary_outcome") == PRIMARY_ACCEPTED_UNFILLED]
    rejected = [r for r in rows if r.get("primary_outcome") == PRIMARY_REJECTED]
    reasons = Counter(str(r.get("exact_reason") or "UNKNOWN") for r in rejected)
    eligibility_receipts = [validate_replay_eligibility(row) for row in rows]
    replay_eligible = sum(1 for receipt in eligibility_receipts if receipt.get("eligible"))
    integrity_blockers = Counter(
        reason
        for receipt in eligibility_receipts if not receipt.get("eligible")
        for reason in (receipt.get("reasons") or ["UNKNOWN_REPLAY_BLOCKER"])
    )
    observation_statuses = Counter(str(row.get("observation_status") or "UNKNOWN") for row in rows)
    events_per_day = max(5.0, len(rows)) if len(rows) < 7 else len(rows) / 7.0
    capacity = project_capacity(
        data_dir=root,
        bytes_per_event_typical=BYTES_PER_EVENT_TYPICAL,
        events_per_day=events_per_day,
    )
    return {
        "schema": "collector_v22_analyzer_report_v1",
        "collector_version": COLLECTOR_VERSION,
        "filter": f"collector_version>={min_collector_version}",
        "empty_epoch": not rows,
        "primaries": {
            PRIMARY_ACCEPTED_FILLED: {
                "n": len(filled),
                "note": "CONTROL actual fills only — hypotheticals are children, not a fourth cohort",
            },
            PRIMARY_ACCEPTED_UNFILLED: {
                "n": len(unfilled),
                "note": "entry funnel only; never exit win-rate denominator",
            },
            PRIMARY_REJECTED: {
                "n": len(rejected),
                "reasons": dict(reasons),
                "note": "first-class rejected with decision tree snapshot",
            },
        },
        "capacity_projection": capacity,
        "replay_integrity": {
            "eligible_events": replay_eligible,
            "ineligible_events": len(rows) - replay_eligible,
            "observation_statuses": dict(observation_statuses),
            "blockers": dict(integrity_blockers),
            "ranking_denominator": replay_eligible,
            "note": "INSUFFICIENT_PATH/DATA_ERROR are immutable negative evidence, never scored outcomes",
        },
        "bytes_per_event_budget": {
            "typical_om_write_once_kb": round(BYTES_PER_EVENT_TYPICAL / 1024, 1),
            "pre_signal_context_kb": 117,
            "note": "write-once ~210 KB/event; no path_replay 1s on volume by default",
        },
        "live_knobs_unchanged": True,
        "no_best_strategy_claim": True,
    }


def write_v22_report(report: Mapping[str, Any], path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return path
