"""Deterministic Phase 3/4 evidence coverage and triage reporting.

This read-only report composes the canonical binding index, evaluated library
rows, and archive/recovery receipts.  It does not infer a terminal outcome from
absence, timestamps, or archive presence: missing proof always remains UNKNOWN.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.policy_evidence_schema import canonical_json


SCHEMA = "evidence_coverage_triage_report_v1"
UNRESOLVED_EPISODE_ID = "episode-914e64c269e23d9db99f"
TERMINAL = frozenset({"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN"})
ARCHIVE_VERIFICATION_SCHEMA = "legacy_archive_verification_index_v1"
SOURCE_COUNT_NAMES = (
    "opportunities", "decisions", "order_intents", "executions",
    "lifecycles", "market_segments",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive_receipts(archive_root: str | Path | None) -> dict[str, Any]:
    """Verify retained payloads named by non-destructive archive receipts."""
    if archive_root is None:
        return {"archive_session_count": 0, "verified_session_count": 0,
                "unverifiable_session_count": 0, "invalid_session_count": 0,
                "retained_file_count": 0,
                "retained_unique_checksum_count": 0, "sessions": []}
    root = Path(archive_root).resolve()
    sessions = []
    checksums: set[str] = set()
    retained = 0
    for receipt_path in sorted(root.glob("session_*/archive_meta.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        errors: list[str] = []
        verified_files = 0
        for item in receipt.get("source_inventory") or []:
            if not isinstance(item, Mapping):
                errors.append("ARCHIVE_INVENTORY_ROW_INVALID")
                continue
            relative = Path(str(item.get("preserved_path") or ""))
            candidate = (receipt_path.parent / relative).resolve()
            try:
                candidate.relative_to(receipt_path.parent.resolve())
            except ValueError:
                errors.append("ARCHIVE_PATH_OUTSIDE_SESSION")
                continue
            expected = str(item.get("preserved_sha256") or "").lower()
            if not candidate.is_file():
                errors.append("ARCHIVE_PAYLOAD_MISSING")
            elif len(expected) != 64 or _sha256(candidate) != expected:
                errors.append("ARCHIVE_CHECKSUM_MISMATCH")
            else:
                verified_files += 1
                checksums.add(expected)
        retained += verified_files
        receipt_verified = not errors and bool(receipt.get("integrity", {}).get("verified"))
        verification_status = (
            "INVALID" if errors else "VERIFIED" if receipt_verified else "UNVERIFIABLE"
        )
        sessions.append({
            "session_id": receipt_path.parent.name,
            "receipt_sha256": _sha256(receipt_path),
            "verified": receipt_verified,
            "verification_status": verification_status,
            "verified_file_count": verified_files,
            "error_codes": sorted(set(errors)),
            "raw_payloads_retained": receipt.get("raw_payloads_retained") is True,
        })
    valid = sum(row["verification_status"] == "VERIFIED" for row in sessions)
    unverifiable = sum(row["verification_status"] == "UNVERIFIABLE" for row in sessions)
    invalid = sum(row["verification_status"] == "INVALID" for row in sessions)
    return {
        "archive_session_count": len(sessions),
        "verified_session_count": valid,
        "unverifiable_session_count": unverifiable,
        "invalid_session_count": invalid,
        "retained_file_count": retained,
        "retained_unique_checksum_count": len(checksums),
        "sessions": sessions,
    }


def load_archive_verification_index(path: str | Path) -> dict[str, Any]:
    """Load a completed checksum-bound archive index without rehashing its payloads."""
    source = Path(path).resolve()
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if payload.get("schema") != ARCHIVE_VERIFICATION_SCHEMA:
        raise ValueError("ARCHIVE_VERIFICATION_INDEX_SCHEMA_MISMATCH")
    expected = str(payload.get("index_payload_sha256") or "").lower()
    stable = dict(payload)
    stable.pop("generated_at", None)
    stable.pop("index_payload_sha256", None)
    actual = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if expected != actual:
        raise ValueError("ARCHIVE_VERIFICATION_INDEX_CHECKSUM_MISMATCH")
    if payload.get("complete") is not True or int(payload.get("pending_session_count") or 0):
        raise ValueError("ARCHIVE_VERIFICATION_INDEX_INCOMPLETE")
    if int(payload.get("invalid_session_count") or 0):
        raise ValueError("ARCHIVE_VERIFICATION_INDEX_INVALID")
    return {
        "archive_session_count": int(payload.get("archive_session_count") or 0),
        "verified_session_count": int(payload.get("verified_session_count") or 0),
        "unverifiable_session_count": int(payload.get("unverifiable_session_count") or 0),
        "invalid_session_count": int(payload.get("invalid_session_count") or 0),
        "retained_file_count": int(payload.get("verified_file_count") or 0),
        "retained_unique_checksum_count": int(payload.get("verified_unique_checksum_count") or 0),
        "verification_index_sha256": expected,
        "sessions": list(payload.get("sessions") or []),
    }


def _episode_id(row: Mapping[str, Any]) -> str:
    return str(row.get("episode_id") or "").strip()


def build_evidence_coverage_triage_report(
    binding_report: Mapping[str, Any],
    library_rows: Iterable[Mapping[str, Any]] = (),
    *,
    archive_summary: Mapping[str, Any] | None = None,
    source_counts: Mapping[str, Any] | None = None,
    unresolved_episode_id: str = UNRESOLVED_EPISODE_ID,
    input_artifacts: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build a stable goal-shaped report without inventing missing evidence."""
    bindings = [dict(row) for row in binding_report.get("bindings") or []]
    results = [dict(row) for row in library_rows]
    by_episode: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"bindings": [], "results": []}
    )
    for row in bindings:
        by_episode[_episode_id(row)]["bindings"].append(row)
    for row in results:
        by_episode[_episode_id(row)]["results"].append(row)

    episode_rows = []
    missing_reasons: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    exact = reconstructed = unknown = 0
    for episode_id in sorted(by_episode):
        material = by_episode[episode_id]
        episode_bindings = material["bindings"]
        episode_results = material["results"]
        reasons = {
            str(reason)
            for row in episode_bindings
            for reason in (row.get("unknown_reason_codes") or [])
        }
        if episode_bindings and any(
            row.get("exact_binding_complete") is not True for row in episode_bindings
        ) and not reasons:
            reasons.add("UNKNOWN_CANONICAL_BINDING_INCOMPLETE")
        if not episode_id:
            reasons.add("UNKNOWN_EPISODE_ID_MISSING")
        if not episode_bindings:
            reasons.add("UNKNOWN_CANONICAL_BINDING_MISSING")
        classifications = []
        explicit_reconstruction = False
        for result in episode_results:
            reasons.update(str(reason) for reason in (result.get("unknown_reason_codes") or []))
            classification = str(result.get("classification") or "UNKNOWN").upper()
            if classification == "UNSUPPORTED" or classification not in TERMINAL:
                classification = "UNKNOWN"
            if result.get("supported") is not True:
                classification = "UNKNOWN"
                reasons.add("UNKNOWN_TERMINAL_OUTCOME_UNSUPPORTED")
            classifications.append(classification)
            outcomes[classification] += 1
            origin = str(result.get("evidence_origin") or result.get("reconstruction_status") or "").upper()
            explicit_reconstruction = explicit_reconstruction or origin in {
                "RECONSTRUCTED", "CHECKSUM_VERIFIED_RECONSTRUCTION"
            }
        if not episode_results:
            reasons.add("UNKNOWN_TERMINAL_OUTCOME_MISSING")
        if "UNKNOWN" in classifications:
            reasons.add("UNKNOWN_TERMINAL_OUTCOME_UNSUPPORTED")

        all_exact = bool(episode_bindings) and all(
            row.get("exact_binding_complete") is True for row in episode_bindings
        )
        if episode_id == unresolved_episode_id:
            status = "UNKNOWN"
            reasons.add("UNKNOWN_UNRESOLVED_ORPHAN_LIFECYCLE")
        elif not reasons and all_exact and explicit_reconstruction:
            status = "RECONSTRUCTED"
        elif not reasons and all_exact:
            status = "EXACT"
        else:
            status = "UNKNOWN"
        exact += status == "EXACT"
        reconstructed += status == "RECONSTRUCTED"
        unknown += status == "UNKNOWN"
        missing_reasons.update(reasons)
        episode_rows.append({
            "episode_id": episode_id or None,
            "coverage_status": status,
            "decision_count": len(episode_bindings),
            "schedule_reference_count": len({
                str(row.get("schedule_id")) for row in episode_bindings if row.get("schedule_id")
            }),
            "complete_schedule_count": len({
                str(row.get("schedule_id")) for row in episode_bindings
                if row.get("schedule_id") and row.get("schedule_status") == "EXACT"
            }),
            "market_path_count": len({tape for row in episode_bindings for tape in (row.get("tape_ids") or [])}),
            "terminal_outcome_count": len(episode_results),
            "terminal_outcomes": sorted(classifications),
            "unknown_reason_codes": sorted(reasons),
        })

    normalized_source_counts = {}
    for name in SOURCE_COUNT_NAMES:
        value = (source_counts or {}).get(name)
        normalized_source_counts[name] = (
            int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else "UNKNOWN"
        )
    unique_complete_schedules = {
        str(row.get("schedule_id")) for row in bindings
        if row.get("schedule_id") and row.get("schedule_status") == "EXACT"
    }
    unique_market_paths = {
        str(tape) for row in bindings for tape in (row.get("tape_ids") or []) if tape
    }
    unresolved_rows = [row for row in episode_rows if row.get("episode_id") == unresolved_episode_id]
    report = {
        "schema": SCHEMA,
        "classification": "DERIVED_READ_ONLY_TRIAGE",
        "outcome_inference_performed": False,
        "missing_evidence_defaults_to": "UNKNOWN",
        "authoritative_source_record_counts": normalized_source_counts,
        "totals": {
            "opportunities": normalized_source_counts["opportunities"],
            "episodes": len(episode_rows),
            "decisions": normalized_source_counts["decisions"],
            "order_intents": normalized_source_counts["order_intents"],
            "executions": normalized_source_counts["executions"],
            "lifecycles": normalized_source_counts["lifecycles"],
            "market_segments": normalized_source_counts["market_segments"],
            "complete_schedules": len(unique_complete_schedules),
            "market_paths": len(unique_market_paths),
            "terminal_outcomes": len(results),
            "exact_episodes": exact,
            "reconstructed_episodes": reconstructed,
            "unknown_episodes": unknown,
        },
        "terminal_outcome_counts": dict(sorted(outcomes.items())),
        "missing_evidence_reason_counts": dict(sorted(missing_reasons.items())),
        "unresolved_episode": {
            "episode_id": unresolved_episode_id,
            "separate_from_general_triage": True,
            "status": "UNKNOWN",
            "present_in_inputs": bool(unresolved_rows),
            "rows": unresolved_rows,
        },
        "archive_recovery_retention": dict(archive_summary or verify_archive_receipts(None)),
        "input_artifacts": sorted((dict(row) for row in input_artifacts), key=lambda row: str(row.get("path") or "")),
        "episodes": episode_rows,
    }
    report["report_payload_sha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return report


def verify_report_checksum(report: Mapping[str, Any]) -> bool:
    material = dict(report)
    expected = str(material.pop("report_payload_sha256", ""))
    return expected == hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _count_jsonl_records(path: Path) -> int:
    """Validate and count a ledger without retaining its rows in memory."""
    count = 0
    with path.open("rt", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"AUTHORITATIVE_LEDGER_INVALID_JSON:{path.name}:{line_number}"
                ) from exc
            if not isinstance(row, Mapping):
                raise ValueError(
                    f"AUTHORITATIVE_LEDGER_ROW_NOT_OBJECT:{path.name}:{line_number}"
                )
            count += 1
    return count


def ledger_source_counts(v3_root: str | Path) -> dict[str, int | str]:
    """Count authoritative ledger records without interpreting their content."""
    root = Path(v3_root).resolve()
    if root.name != "v3":
        raise ValueError("SOURCE_COUNT_ROOT_MUST_BE_V3")
    singular = {
        "opportunities": "opportunity", "decisions": "decision",
        "order_intents": "order_intent", "executions": "execution",
        "lifecycles": "lifecycle", "market_segments": "market_segment",
    }
    counts: dict[str, int | str] = {}
    for plural, name in singular.items():
        path = root / "ledgers" / f"{name}.jsonl"
        counts[plural] = _count_jsonl_records(path) if path.is_file() else "UNKNOWN"
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build checksum-verifiable evidence coverage triage")
    parser.add_argument("binding_index", type=Path, help="Binding index JSON or JSONL.gz")
    parser.add_argument("--library-results", type=Path, help="Evaluated library rows as JSONL")
    parser.add_argument("--archive-root", type=Path, help="Retained research_archive root")
    parser.add_argument("--v3-root", type=Path, help="Canonical v3 root for authoritative ledger counts")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.binding_index.suffix == ".gz":
        binding_report = {"bindings": _read_jsonl(args.binding_index)}
    else:
        binding_report = json.loads(args.binding_index.read_text(encoding="utf-8-sig"))
    library = _read_jsonl(args.library_results) if args.library_results else []
    inputs = [{"path": str(args.binding_index), "sha256": _sha256(args.binding_index)}]
    if args.library_results:
        inputs.append({"path": str(args.library_results), "sha256": _sha256(args.library_results)})
    report = build_evidence_coverage_triage_report(
        binding_report, library,
        archive_summary=verify_archive_receipts(args.archive_root),
        source_counts=ledger_source_counts(args.v3_root) if args.v3_root else None,
        input_artifacts=inputs,
    )
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
