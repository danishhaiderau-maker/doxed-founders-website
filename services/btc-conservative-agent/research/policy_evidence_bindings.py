"""Build exact, read-only bindings from the canonical V3 evidence ledgers.

The binding index is coverage evidence, not an execution simulator.  It joins
only persisted causal identities, verifies content-addressed segment objects,
and fails closed when a schedule or a required market horizon is absent.
"""
from __future__ import annotations

import hashlib
import gzip
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.policy_evidence_schema import canonical_json, generation_identity


LEDGERS = ("opportunity", "decision", "order_intent", "execution", "lifecycle", "market_segment")
ALL_OPPORTUNITY_FUTURE_ROLE = "SIGNAL_TO_120M_FUTURE_PATH"
REQUIRED_FUTURE_HORIZONS_SEC = frozenset({60, 300, 900, 1800, 3600, 7200})


def segment_role(row: Mapping[str, Any]) -> str:
    coverage = row.get("coverage") if isinstance(row.get("coverage"), Mapping) else {}
    return str(
        row.get("context_role") or row.get("segment_role")
        or coverage.get("context_role") or coverage.get("segment_role") or ""
    ).upper()


def complete_conservative_future_path(row: Mapping[str, Any]) -> bool:
    """Accept the all-opportunity tape only when its full declared contract is proven."""
    coverage = row.get("coverage") if isinstance(row.get("coverage"), Mapping) else {}
    raw_horizons = coverage.get("required_horizons_sec")
    if not isinstance(raw_horizons, (list, tuple, set)):
        return False
    try:
        horizons = {int(value) for value in raw_horizons}
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(
        segment_role(row) == ALL_OPPORTUNITY_FUTURE_ROLE
        and str(row.get("future_path_status") or coverage.get("future_path_status") or "").upper() == "COMPLETE"
        and coverage.get("conservative_bbo_depth_eligible") is True
        and REQUIRED_FUTURE_HORIZONS_SEC.issubset(horizons)
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"V3_LEDGER_ROW_NOT_OBJECT:{path.name}:{number}")
            rows.append(value)
    return rows


def _key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(str(row.get(field) or "") for field in ("epoch_id", "opportunity_id", "episode_id"))


def _policy_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (*_key(row), str(row.get("policy_signature") or ""))


def _contained(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _atomic_write_bytes(path: Path, data: bytes, *, containment_root: Path) -> None:
    """Atomically publish bytes without following a target outside its authority."""
    authority = containment_root.resolve()
    parent = path.parent.resolve()
    target = parent / path.name
    if not _contained(authority, target):
        raise ValueError("BINDING_PUBLICATION_PATH_OUTSIDE_AUTHORITY")
    if path.is_symlink():
        raise ValueError("BINDING_PUBLICATION_TARGET_SYMLINK_FORBIDDEN")
    parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_segment(v3_root: Path, row: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    ref = row.get("segment_ref") if isinstance(row.get("segment_ref"), Mapping) else {}
    digest = str(ref.get("sha256") or "").lower()
    relative = str(ref.get("relative_path") or "")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None, ["UNKNOWN_TAPE_SHA256_INVALID"]
    candidate = (v3_root.parent / relative).resolve()
    if not _contained(v3_root.parent, candidate):
        return None, ["UNKNOWN_TAPE_PATH_OUTSIDE_V3"]
    expected = (v3_root / "market_segments" / digest[:2] / f"{digest}.json").resolve()
    if candidate != expected:
        return None, ["UNKNOWN_TAPE_PATH_NOT_CANONICAL"]
    if not candidate.is_file():
        return None, ["UNKNOWN_TAPE_OBJECT_MISSING"]
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual != digest:
        return None, ["UNKNOWN_TAPE_CHECKSUM_MISMATCH"]
    errors: list[str] = []
    try:
        envelope = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, ["UNKNOWN_TAPE_ENVELOPE_INVALID"]
    if not isinstance(envelope, Mapping) or envelope.get("schema") != "market_segment_v3":
        errors.append("UNKNOWN_TAPE_ENVELOPE_SCHEMA_INVALID")
    for field in ("source", "symbol", "timeframe", "start_ts", "end_ts"):
        if ref.get(field) is not None and envelope.get(field) != ref.get(field):
            errors.append(f"UNKNOWN_TAPE_ENVELOPE_{field.upper()}_MISMATCH")
    if ref.get("row_count") is not None:
        rows = envelope.get("rows") if isinstance(envelope.get("rows"), list) else []
        if len(rows) != int(ref["row_count"]):
            errors.append("UNKNOWN_TAPE_ENVELOPE_ROW_COUNT_MISMATCH")
    return (digest if not errors else None), errors


def _recognized_status_only_segment(row: Mapping[str, Any]) -> bool:
    """True only for declared future-path state rows that own no object."""
    if row.get("segment_ref") is not None:
        return False
    role = segment_role(row)
    status = str(row.get("future_path_status") or "").upper()
    return bool(
        role == f"{ALL_OPPORTUNITY_FUTURE_ROLE}_REQUEST" and status == "PENDING"
        or role == ALL_OPPORTUNITY_FUTURE_ROLE and status in {"PENDING", "UNKNOWN"}
    )


def authoritative_future_path_segments(
    v3_root: Path, rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select the latest valid future-path state per owner; retain history counts.

    Ledgers remain append-only.  A recovery record can supersede an earlier
    UNKNOWN without making that history disappear from the audit metadata.
    """
    material = list(rows)
    ordinary: list[dict[str, Any]] = []
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in material:
        if segment_role(row) != ALL_OPPORTUNITY_FUTURE_ROLE:
            ordinary.append(row)
            continue
        owner = str(row.get("future_path_owner_key") or "")
        if not owner:
            ordinary.append(row)
            continue
        histories[owner].append(row)
    selected: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    invalid_newer_ids: list[str] = []
    for owner in sorted(histories):
        history = histories[owner]
        chosen = None
        for candidate in reversed(history):
            if _recognized_status_only_segment(candidate):
                chosen = candidate
                break
            _digest, errors = _verify_segment(v3_root, candidate)
            if not errors:
                chosen = candidate
                break
            invalid_newer_ids.append(str(candidate.get("record_id") or ""))
        if chosen is not None:
            selected.append(chosen)
            selected_ids.append(str(chosen.get("record_id") or ""))
    return ordinary + selected, {
        "future_path_history_count": sum(len(value) for value in histories.values()),
        "future_path_owner_count": len(histories),
        "selected_future_path_record_ids": sorted(selected_ids),
        "invalid_newer_future_path_record_ids": sorted(filter(None, invalid_newer_ids)),
    }


def _schedule_hash(row: Mapping[str, Any]) -> str | None:
    schedule = row.get("chase_schedule")
    if not isinstance(schedule, Mapping) or not schedule:
        return None
    computed = hashlib.sha256(canonical_json(schedule).encode("utf-8")).hexdigest()
    persisted = str(row.get("schedule_sha256") or "")
    if persisted and persisted != computed:
        return None
    return computed


def authoritative_schedule_intents(
    rows: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Select only final persisted schedule versions.

    The submit receipt truthfully freezes the schedule as it existed when the
    order was registered.  Reprices and the terminal boundary happen later, so
    the collector appends a terminal evidence snapshot instead of mutating the
    submit receipt. Submit-time/open versions are never sufficient for replay;
    conflicting terminal snapshots still fail closed downstream.
    """
    authoritative = [
        row for row in rows
        if isinstance(row.get("chase_schedule"), Mapping)
        and row["chase_schedule"].get("authoritative") is True
        and row.get("schedule_id")
    ]
    return [
        row for row in authoritative
        if str(row.get("intent_kind") or "").upper()
        == "AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL"
    ]


def build_v3_binding_index(v3_root: str | Path) -> dict[str, Any]:
    """Return deterministic binding coverage for every persisted policy decision."""
    root = Path(v3_root).resolve()
    if root.name != "v3":
        raise ValueError("V3_BINDING_ROOT_MUST_BE_V3")
    ledgers = {name: _read_jsonl(root / "ledgers" / f"{name}.jsonl") for name in LEDGERS}
    # Fly is the raw-data authority and atomically refreshes ``ledgers``.  The
    # local analyzer may additionally bind checksum-verified archived paths
    # from this separate append-only derived overlay.  Keeping the authorities
    # distinct prevents a mirror refresh from erasing or impersonating locally
    # recovered evidence.
    recovery_segments = _read_jsonl(
        root / "recovery_ledgers" / "market_segment.jsonl"
    )
    ledgers["market_segment"].extend(recovery_segments)
    opportunities = {_key(row): row for row in ledgers["opportunity"]}
    segments: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    intents: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in ledgers["market_segment"]:
        segments[_key(row)].append(row)
    for row in ledgers["order_intent"]:
        intents[_policy_key(row)].append(row)

    bindings: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for decision in ledgers["decision"]:
        key = _key(decision)
        policy_key = _policy_key(decision)
        reasons: set[str] = set()
        if not all(key):
            reasons.add("UNKNOWN_CAUSAL_IDENTITY_INCOMPLETE")
        if key not in opportunities:
            reasons.add("UNKNOWN_OPPORTUNITY_ROW_MISSING")
        policy_signature = policy_key[-1]
        if not policy_signature:
            reasons.add("UNKNOWN_POLICY_SIGNATURE_MISSING")

        tape_ids: set[str] = set()
        roles: set[str] = set()
        conservative_roles: set[str] = set()
        selected_segments, future_history = authoritative_future_path_segments(
            root, segments.get(key, []),
        )
        for segment in selected_segments:
            # Request/PENDING/UNKNOWN rows declare the denominator and the
            # missing-evidence state. They intentionally have no object ref
            # and must not be misclassified as a malformed SHA. A row that
            # actually claims a ref is always verified and fails closed.
            claimed_ref = segment.get("segment_ref")
            if not _recognized_status_only_segment(segment):
                digest, errors = _verify_segment(root, segment)
                if errors:
                    reasons.update(errors)
                elif digest:
                    tape_ids.add(digest)
            coverage = segment.get("coverage") if isinstance(segment.get("coverage"), Mapping) else {}
            role = segment_role(segment)
            if role:
                roles.add(role)
                if (
                    coverage.get("conservative_bbo_depth_eligible") is True
                    and role != ALL_OPPORTUNITY_FUTURE_ROLE
                ) or complete_conservative_future_path(segment):
                    conservative_roles.add(role)
        if not tape_ids:
            reasons.add("UNKNOWN_TAPE_IDS_MISSING")

        no_order = (
            decision.get("order_intent_expected") is False
            and str(decision.get("outcome_state") or "").upper() == "REJECTED"
        )
        authoritative_intents = authoritative_schedule_intents(
            intents.get(policy_key, [])
        )
        schedule_hashes = sorted({value for row in authoritative_intents if (value := _schedule_hash(row))})
        schedule_ids = sorted({str(row["schedule_id"]) for row in authoritative_intents})
        if not no_order:
            if len(schedule_hashes) == 0:
                reasons.add("UNKNOWN_AUTHORITATIVE_SCHEDULE_MISSING")
            elif len(schedule_hashes) > 1 or len(schedule_ids) > 1:
                reasons.add("UNKNOWN_SCHEDULE_VERSION_CONFLICT")

        entry_complete = bool(conservative_roles & {
            "ENTRY_PATH", "ENTRY_AND_EXIT_PATH", "FULL_LIFECYCLE",
            ALL_OPPORTUNITY_FUTURE_ROLE,
        })
        post_exit_complete = bool(conservative_roles & {
            "POST_EXIT_PATH", "ENTRY_AND_EXIT_PATH", "FULL_LIFECYCLE",
            ALL_OPPORTUNITY_FUTURE_ROLE,
        })
        if not entry_complete:
            reasons.add("UNKNOWN_REQUIRED_ENTRY_HORIZONS_INCOMPLETE")
        if not post_exit_complete:
            reasons.add("UNKNOWN_REQUIRED_POST_EXIT_HORIZONS_INCOMPLETE")

        reason_counts.update(reasons)
        bindings.append({
            "schema": "v3_policy_evidence_binding_v1",
            "epoch_id": key[0] or None,
            "opportunity_id": key[1] or None,
            "episode_id": key[2] or None,
            "event_id": decision.get("event_id"),
            "shared_ai_call_id": decision.get("shared_ai_call_id"),
            "policy_id": decision.get("policy_id"),
            "policy_signature": policy_signature or None,
            "research_lane": decision.get("research_lane"),
            "tape_ids": sorted(tape_ids),
            "segment_roles": sorted(roles),
            "conservative_segment_roles": sorted(conservative_roles),
            "schedule_sha256": schedule_hashes[0] if len(schedule_hashes) == 1 else None,
            "schedule_id": schedule_ids[0] if len(schedule_ids) == 1 else None,
            "schedule_status": "NOT_APPLICABLE_NO_ORDER" if no_order else (
                "EXACT" if len(schedule_hashes) == 1 and len(schedule_ids) == 1 else "UNKNOWN"
            ),
            "required_entry_horizons_complete": entry_complete,
            "required_post_exit_horizons_complete": post_exit_complete,
            "exact_binding_complete": not reasons,
            "coverage_status": "EXACTLY_BOUND" if not reasons else "UNKNOWN_UNVERIFIABLE",
            "unknown_reason_codes": sorted(reasons),
            "conservative_outcome": None,
            **future_history,
        })
    bindings.sort(key=lambda row: tuple(str(row.get(field) or "") for field in (
        "epoch_id", "opportunity_id", "episode_id", "policy_signature", "event_id"
    )))
    encoded = canonical_json(bindings)
    complete = sum(bool(row["exact_binding_complete"]) for row in bindings)
    return {
        "schema": "v3_policy_evidence_binding_index_v1",
        "classification": "DERIVED_READ_ONLY_COVERAGE",
        "outcome_evaluation_performed": False,
        "timestamp_join_performed": False,
        "decision_binding_count": len(bindings),
        "exactly_bound_count": complete,
        "unknown_unverifiable_count": len(bindings) - complete,
        "unknown_reason_counts": dict(sorted(reason_counts.items())),
        "raw_market_segment_row_count": len(ledgers["market_segment"]) - len(recovery_segments),
        "recovery_market_segment_row_count": len(recovery_segments),
        "bindings_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "bindings": bindings,
    }


def persist_v3_binding_index(
    canonical_root: str | Path, *, analyzer_revision: str,
    summary_destination: str | Path | None = None,
) -> dict[str, Any]:
    """Atomically persist a generation-bound, rebuildable binding index.

    The exhaustive rows remain beneath the canonical store's ``derived`` tree.
    Only the compact summary is intended for analyzer/dashboard publication.
    """
    root = Path(canonical_root).resolve()
    if root.name != "canonical-research-data":
        raise ValueError("BINDING_INDEX_ROOT_NOT_CANONICAL_RESEARCH_DATA")
    current_path = root / "canonical_dataset_current.json"
    if not current_path.is_file():
        raise ValueError("CANONICAL_DATASET_MANIFEST_MISSING")
    current = json.loads(current_path.read_text(encoding="utf-8-sig"))
    generation = generation_identity(current, analyzer_revision=analyzer_revision)
    report = build_v3_binding_index(root / "v3")
    observed_epochs = sorted({str(row.get("epoch_id") or "") for row in report["bindings"] if row.get("epoch_id")})
    if observed_epochs and observed_epochs != [generation["epoch_id"]]:
        raise ValueError("BINDING_INDEX_EPOCH_MISMATCH")

    directory = root / "derived" / "policy-evidence" / generation["generation_key"]
    directory.mkdir(parents=True, exist_ok=True)
    exhaustive = directory / "binding-index.jsonl.gz"
    fd, temporary_name = tempfile.mkstemp(prefix=".binding-index.", suffix=".tmp", dir=directory)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="binding-index.jsonl", mode="wb", fileobj=raw, mtime=0) as zipped:
                for row in report["bindings"]:
                    zipped.write((canonical_json(row) + "\n").encode("utf-8"))
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, exhaustive)
    finally:
        temporary.unlink(missing_ok=True)

    summary = {key: value for key, value in report.items() if key != "bindings"}
    summary.update({
        "generation": generation,
        "exhaustive_relative_path": exhaustive.relative_to(root).as_posix(),
        "exhaustive_sha256": hashlib.sha256(exhaustive.read_bytes()).hexdigest(),
        "exhaustive_size_bytes": exhaustive.stat().st_size,
        "qualification_allowed": False,
        "note": "Coverage identities only; no fill or PnL outcome was evaluated.",
    })
    summary_path = directory / "binding-index-summary.json"
    encoded = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    _atomic_write_bytes(summary_path, encoded.encode("utf-8"), containment_root=root)
    if summary_destination is not None:
        destination = Path(summary_destination)
        # The caller owns the publication directory. Confine the write to that
        # exact directory and forbid a pre-existing symlink target.
        _atomic_write_bytes(
            destination, encoded.encode("utf-8"),
            containment_root=destination.parent,
        )
    return summary
