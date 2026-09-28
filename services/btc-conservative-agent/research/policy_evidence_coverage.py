"""Read-only policy evidence coverage manifest.

This module audits whether already-persisted episode rows contain the exact
identities needed by a later conservative replay.  It never joins evidence by
timestamp, evaluates a fill, or assigns a trading outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$", re.IGNORECASE)


def _text(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None


def _identity(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("receipt_identity") or row.get("identity") or {}
    return value if isinstance(value, Mapping) else {}


def _first(row: Mapping[str, Any], identity: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, "", []):
            return value
        value = identity.get(name)
        if value not in (None, "", []):
            return value
    return None


def _content_addressed(value: Any) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value).strip()))


def episode_coverage(row: Mapping[str, Any]) -> dict[str, Any]:
    """Classify one persisted episode without deriving or joining evidence."""
    identity = _identity(row)
    tape_ids = _first(row, identity, "tape_ids") or []
    if not isinstance(tape_ids, (list, tuple)):
        tape_ids = []
    tape_ids = sorted({_text(item) for item in tape_ids if _text(item)})
    schedule_sha256 = _text(_first(row, identity, "schedule_sha256"))

    fields = {
        "epoch_id": _text(_first(row, identity, "epoch_id")),
        "event_id": _text(_first(row, identity, "event_id")),
        "episode_id": _text(_first(row, identity, "episode_id", "event_episode_id")),
        "opportunity_id": _text(_first(row, identity, "opportunity_id")),
        "policy_signature": _text(
            _first(row, identity, "candidate_policy_signature", "policy_signature")
        ),
    }
    reasons: list[str] = []
    for name, value in fields.items():
        if not value:
            reasons.append(f"UNKNOWN_{name.upper()}_MISSING")
    if not tape_ids:
        reasons.append("UNKNOWN_TAPE_IDS_MISSING")
    elif not all(_content_addressed(value) for value in tape_ids):
        reasons.append("UNKNOWN_TAPE_IDS_NOT_CONTENT_ADDRESSED")
    if not schedule_sha256:
        reasons.append("UNKNOWN_SCHEDULE_SHA256_MISSING")
    elif not _content_addressed(schedule_sha256):
        reasons.append("UNKNOWN_SCHEDULE_SHA256_INVALID")
    if row.get("required_entry_horizons_complete") is not True:
        reasons.append("UNKNOWN_REQUIRED_ENTRY_HORIZONS_INCOMPLETE")
    if row.get("required_post_exit_horizons_complete") is not True:
        reasons.append("UNKNOWN_REQUIRED_POST_EXIT_HORIZONS_INCOMPLETE")

    return {
        "schema": "policy_evidence_episode_coverage_v1",
        **fields,
        "tape_ids": tape_ids,
        "schedule_sha256": schedule_sha256,
        "exact_binding_complete": not reasons,
        "coverage_status": "EXACTLY_BOUND" if not reasons else "UNKNOWN_UNVERIFIABLE",
        "unknown_reason_codes": sorted(set(reasons)),
        "conservative_outcome": None,
        "note": "Coverage only; no fill or PnL outcome was evaluated.",
    }


def build_policy_evidence_coverage_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    episodes = [episode_coverage(row) for row in rows]
    episodes.sort(key=lambda item: tuple(item.get(key) or "" for key in (
        "epoch_id", "opportunity_id", "episode_id", "event_id", "policy_signature"
    )))
    reasons = Counter(
        reason for episode in episodes for reason in episode["unknown_reason_codes"]
    )
    complete = sum(item["exact_binding_complete"] for item in episodes)
    material = json.dumps(episodes, sort_keys=True, separators=(",", ":"))
    return {
        "schema": "policy_evidence_coverage_report_v1",
        "classification": "DERIVED_READ_ONLY_COVERAGE",
        "outcome_evaluation_performed": False,
        "timestamp_join_performed": False,
        "episode_count": len(episodes),
        "exactly_bound_episode_count": complete,
        "unknown_unverifiable_episode_count": len(episodes) - complete,
        "unknown_reason_counts": dict(sorted(reasons.items())),
        "episodes_sha256": hashlib.sha256(material.encode("utf-8")).hexdigest(),
        "episodes": episodes,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"ROW_NOT_OBJECT:{number}")
            rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit exact policy evidence bindings")
    parser.add_argument("input", type=Path, help="Existing episode JSONL")
    parser.add_argument("--output", type=Path, help="Optional derived JSON report path")
    args = parser.parse_args(argv)
    report = build_policy_evidence_coverage_report(_read_jsonl(args.input))
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
