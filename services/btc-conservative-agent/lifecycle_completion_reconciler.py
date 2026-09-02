"""Fail-closed reconciliation of Research V3 lifecycle completion evidence.

The scanner joins only the immutable composite lifecycle identity used by
``lifecycle_bundles``.  It does not translate an old ``terminal`` label into a
completion receipt: terminal schedule, entry outcome, flat position, gap-free
post-observation horizon, and (for fills) cost-complete exit evidence must all
already exist in the V3 ledgers.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from lifecycle_bundles import LifecycleKey, collect_lifecycle_rows
from lifecycle_completion_receipts import (
    EVIDENCE_COLLECTED_SCHEMA,
    build_evidence_collected_receipt,
    build_lifecycle_completion_receipt,
    build_lifecycle_transfer_ready_receipt,
)
from research_v3_contract import canonical_json
from research_v3_store import V3EvidenceStore, _collection_provenance


RECONCILER_SCHEMA = "lifecycle_completion_reconciliation_v1"
_PROVENANCE_FIELDS = ("source_revision", "deployed_revision", "tile_config_signature")
_PROVENANCE_SENTINELS = frozenset({"", "UNKNOWN", "NOT_DEPLOYED_LOCAL"})


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _unique(rows: Iterable[Mapping[str, Any]], predicate) -> list[Mapping[str, Any]]:
    return [row for row in rows if predicate(row)]


def _provenance(rows: list[Mapping[str, Any]]) -> tuple[dict[str, str], list[str]]:
    proven: dict[str, str] = {}
    blockers: list[str] = []
    for field in _PROVENANCE_FIELDS:
        values = {str(row.get(field) or "").strip() for row in rows}
        sentinels = {value for value in values if value.upper() in _PROVENANCE_SENTINELS}
        if sentinels:
            blockers.append(f"{field.upper()}_MISSING_OR_SENTINEL")
            values.difference_update(sentinels)
        if len(values) != 1:
            blockers.append(f"{field.upper()}_AMBIGUOUS")
        elif values:
            proven[field] = next(iter(values))
    return proven, blockers


def _post_observation(rows: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return one explicit gap-free observation proof, never a time inference."""
    candidates: list[Mapping[str, Any]] = []
    for row in rows:
        direct = row.get("post_observation")
        if isinstance(direct, Mapping):
            candidates.append(direct)
        coverage = row.get("coverage")
        if (
            str(row.get("ledger") or "") == "market_segment"
            and str(row.get("context_role") or "").upper() in {"POST_EXIT_PATH", "FULL_LIFECYCLE"}
            and isinstance(coverage, Mapping)
        ):
            candidates.append(coverage)
    valid = [
        item for item in candidates
        if item.get("complete") is True
        and item.get("gaps_absent") is True
        and _finite(item.get("complete_through_ts")) is not None
    ]
    if not valid:
        return None
    valid.sort(key=lambda item: float(item["complete_through_ts"]))
    return valid[-1]


def evaluate_lifecycle_completion(
    key: LifecycleKey, rows: Iterable[Mapping[str, Any]], *, now: float,
    lifecycle_horizon_sec: float = 7200.0,
    reconciliation_allowance_sec: float = 180.0,
) -> dict[str, Any]:
    """Build a receipt only when one exact lifecycle is completely proven."""
    material = [dict(row) for row in rows]
    blockers: list[str] = []
    provenance, provenance_blockers = _provenance(material)
    blockers.extend(provenance_blockers)

    schedules = _unique(material, lambda row: (
        row.get("ledger") == "order_intent"
        and row.get("intent_kind") == "AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL"
        and row.get("schedule_lifecycle_final") is True
        and row.get("chase_schedule_authoritative") is True
    ))
    schedule_hashes = {str(row.get("schedule_sha256") or "") for row in schedules}
    if len(schedules) != 1 or len(schedule_hashes) != 1 or "" in schedule_hashes:
        blockers.append("UNIQUE_TERMINAL_SCHEDULE_NOT_PROVEN")
        schedule_row: Mapping[str, Any] = {}
    else:
        schedule_row = schedules[0]
    chase_schedule = schedule_row.get("chase_schedule")
    chase_schedule = chase_schedule if isinstance(chase_schedule, Mapping) else {}
    terminal_ts = _finite(chase_schedule.get("terminal_ts_exact")) or _finite(chase_schedule.get("terminal_ts"))

    fill_rows = _unique(material, lambda row: (
        row.get("ledger") == "execution" and str(row.get("record_id") or "").endswith(":primary-fill")
    ))
    close_rows = _unique(material, lambda row: (
        row.get("ledger") == "execution" and str(row.get("record_id") or "").endswith(":paper-close")
    ))
    closed_lifecycle = _unique(material, lambda row: (
        row.get("ledger") == "lifecycle"
        and row.get("terminal") is True
        and row.get("observation_status") == "PAPER_POSITION_CLOSED"
    ))
    no_fill_rows = _unique(material, lambda row: (
        row.get("ledger") == "lifecycle"
        and row.get("terminal") is True
        and row.get("terminal_no_fill") is True
    ))
    explicit_unknown = _unique(material, lambda row: (
        row.get("ledger") == "lifecycle"
        and row.get("terminal") is True
        and str(row.get("entry_outcome") or "").upper() == "UNKNOWN"
        and bool(str(row.get("unknown_reason") or "").strip())
    ))
    fill_lifecycle = _unique(material, lambda row: (
        row.get("ledger") == "lifecycle"
        and row.get("observation_status") == "PAPER_POSITION_OPEN"
        and str(row.get("outcome_state") or "").upper() in {"FULL_FILL", "PARTIAL_FILL"}
    ))

    # Decision and lane-entry rows intentionally use lane-scoped audit IDs
    # (``lane-decision:*`` / ``lane-entry:*``).  They share the composite
    # lifecycle identity and AI call, but are not execution event IDs.  Only
    # rows which can prove the terminal execution lifecycle select the one
    # canonical event ID used by the completion receipt.
    canonical_event_rows = [
        *schedules, *fill_rows, *close_rows, *fill_lifecycle,
        *closed_lifecycle, *no_fill_rows, *explicit_unknown,
    ]
    event_ids = {
        str(row.get("event_id") or "").strip()
        for row in canonical_event_rows
        if str(row.get("event_id") or "").strip()
    }
    if len(event_ids) != 1 or any(
        not str(row.get("event_id") or "").strip()
        for row in canonical_event_rows
    ):
        blockers.append("EVENT_ID_MISSING_OR_AMBIGUOUS")

    audit_rows = _unique(material, lambda row: (
        str(row.get("event_id") or "").startswith(("lane-decision:", "lane-entry:"))
        or "LANE_POLICY_VERDICT" in str(row.get("record_id") or "")
        or ":lane-entry:" in str(row.get("record_id") or "")
    ))
    if audit_rows:
        audit_call_ids = {
            str(row.get("shared_ai_call_id") or "").strip() for row in audit_rows
        }
        canonical_call_ids = {
            str(row.get("shared_ai_call_id") or "").strip()
            for row in canonical_event_rows
        }
        if "" in audit_call_ids or "" in canonical_call_ids or (
            len(audit_call_ids | canonical_call_ids) != 1
        ):
            blockers.append("SHARED_AI_CALL_ID_MISSING_OR_AMBIGUOUS")
        for row in audit_rows:
            supplied = (
                row.get("collection_epoch_id") or row.get("epoch_id"),
                row.get("episode_id"),
                row.get("policy_signature"), row.get("research_lane"),
            )
            expected = (
                key.collection_epoch_id, key.episode_id,
                key.policy_signature, key.research_lane,
            )
            if supplied != expected:
                blockers.append("LANE_AUDIT_COMPOSITE_BINDING_MISMATCH")
                break

    proof: dict[str, Any] = {
        "terminal_schedule": {
            "authoritative": schedule_row.get("chase_schedule_authoritative") is True,
            "schedule_lifecycle_final": schedule_row.get("schedule_lifecycle_final") is True,
            "terminal_ts": terminal_ts,
            "terminal_reason": chase_schedule.get("terminal_reason"),
            "schedule_sha256": schedule_row.get("schedule_sha256"),
        },
        "open_quantity": None,
        "post_observation": _post_observation(material),
    }
    if fill_rows or close_rows or closed_lifecycle:
        if len(fill_rows) != 1 or len(close_rows) != 1 or len(closed_lifecycle) != 1:
            blockers.append("UNIQUE_FILLED_LIFECYCLE_NOT_PROVEN")
        if len(fill_lifecycle) != 1:
            blockers.append("UNIQUE_ENTRY_OUTCOME_NOT_PROVEN")
            outcome = "UNKNOWN"
        else:
            outcome = str(fill_lifecycle[0]["outcome_state"]).upper()
        close = close_rows[0] if len(close_rows) == 1 else {}
        canonical_economics = close.get("canonical_economics")
        economics_source = canonical_economics if isinstance(canonical_economics, Mapping) else close
        proof.update({
            "entry_outcome": outcome,
            "position_state": "CLOSED",
            "open_quantity": 0.0,
            "filled_quantity": close.get("filled_qty"),
            "requested_quantity": schedule_row.get("requested_qty"),
            "exit_evidence": {
                "terminal": len(closed_lifecycle) == 1,
                "close_ts": close.get("close_ts"),
                "receipt_sha256": hashlib.sha256(canonical_json(close).encode("utf-8")).hexdigest() if close else None,
            },
            "economics": {
                name: economics_source.get(name) for name in (
                    "gross_pnl_usd", "trading_fees_usd", "funding_fees_usd",
                    "slippage_cost_usd", "latency_cost_usd", "net_pnl_usd",
                )
            } | {
                name: economics_source.get(name) for name in (
                    "gross_pnl_basis", "net_pnl_reconciliation_basis",
                    "separately_subtracted_from_gross", "attribution_only_not_subtracted",
                )
            },
            "path_extrema": {
                "mfe_usd": (close.get("path_extrema") or {}).get("mfe_usd") if isinstance(close.get("path_extrema"), Mapping) else None,
                "mae_usd": (close.get("path_extrema") or {}).get("mae_usd") if isinstance(close.get("path_extrema"), Mapping) else None,
            },
        })
    elif len(no_fill_rows) == 1 and not explicit_unknown:
        proof.update({
            "entry_outcome": "NO_FILL",
            "position_state": "NEVER_OPENED",
            "open_quantity": 0.0,
            "filled_quantity": 0.0,
            "requested_quantity": schedule_row.get("requested_qty"),
        })
    elif len(explicit_unknown) == 1 and not no_fill_rows:
        proof.update({
            "entry_outcome": "UNKNOWN",
            "position_state": explicit_unknown[0].get("position_state"),
            "open_quantity": explicit_unknown[0].get("open_quantity"),
            "unknown_reason": explicit_unknown[0].get("unknown_reason"),
        })
    else:
        proof.update({"entry_outcome": "UNKNOWN", "position_state": "UNKNOWN"})
        blockers.append("UNIQUE_ENTRY_OUTCOME_NOT_PROVEN")

    structural_blockers = sorted(set(blockers))
    built = build_lifecycle_completion_receipt(
        proof, now=now, lifecycle_horizon_sec=lifecycle_horizon_sec,
        reconciliation_allowance_sec=reconciliation_allowance_sec,
    )
    blockers = sorted(set(blockers + built["blockers"]))
    return {
        "schema": RECONCILER_SCHEMA,
        "identity": key.as_dict(),
        "ready": not blockers and built["receipt"] is not None,
        "classification": built["classification"],
        "blockers": blockers,
        "structural_blockers": structural_blockers,
        "provenance": provenance,
        "event_id": next(iter(event_ids)) if len(event_ids) == 1 else None,
        "terminal_proof": proof,
        "receipt": built["receipt"] if not blockers else None,
    }


def evaluate_lifecycle_transfer_ready(
    key: LifecycleKey, rows: Iterable[Mapping[str, Any]], *, now: float,
    lifecycle_horizon_sec: float = 7200.0,
    reconciliation_allowance_sec: float = 180.0,
) -> dict[str, Any]:
    """Evaluate early immutable transfer without relaxing qualification."""
    qualification = evaluate_lifecycle_completion(
        key, rows, now=now,
        lifecycle_horizon_sec=lifecycle_horizon_sec,
        reconciliation_allowance_sec=reconciliation_allowance_sec,
    )
    built = build_lifecycle_transfer_ready_receipt(
        qualification["terminal_proof"], now=now,
        lifecycle_horizon_sec=lifecycle_horizon_sec,
        reconciliation_allowance_sec=reconciliation_allowance_sec,
    )
    blockers = sorted(set(
        list(qualification["structural_blockers"]) + list(built["blockers"])
    ))
    return {
        "schema": "lifecycle_transfer_ready_reconciliation_v1",
        "identity": key.as_dict(),
        "ready": not blockers and built["receipt"] is not None,
        "classification": built["classification"],
        "blockers": blockers,
        "qualification_ready": qualification["ready"],
        "qualification_blockers": qualification["blockers"],
        "provenance": qualification["provenance"],
        "event_id": qualification["event_id"],
        "receipt": built["receipt"] if not blockers else None,
    }


def reconcile_lifecycle_completions(
    root: str | Path, *, epoch_id: str, now: float, append: bool = True,
    lifecycle_horizon_sec: float = 7200.0,
    reconciliation_allowance_sec: float = 180.0,
) -> dict[str, Any]:
    """Scan all exact identities and idempotently append proven completions."""
    root = Path(root).resolve()
    grouped = collect_lifecycle_rows(root)
    store = V3EvidenceStore(root, epoch_id=str(epoch_id))
    runtime_provenance = _collection_provenance()
    assessments = []
    writes = []
    collection_writes = []
    for key, rows in sorted(grouped.items()):
        if key.collection_epoch_id != str(epoch_id):
            continue
        assessment = evaluate_lifecycle_completion(
            key, rows, now=now, lifecycle_horizon_sec=lifecycle_horizon_sec,
            reconciliation_allowance_sec=reconciliation_allowance_sec,
        )
        assessments.append(assessment)
        if not append or not assessment["ready"]:
            continue
        provenance_mismatch = [
            field for field in _PROVENANCE_FIELDS
            if assessment["provenance"].get(field) != runtime_provenance.get(field)
        ]
        if provenance_mismatch:
            assessment["ready"] = False
            assessment["receipt"] = None
            assessment["blockers"] = sorted(set(
                assessment["blockers"]
                + [f"RUNTIME_{field.upper()}_MISMATCH" for field in provenance_mismatch]
            ))
            continue
        receipt = assessment["receipt"]
        event_id = assessment["event_id"]
        existing_collection_rows = [
            row for row in rows
            if row.get("ledger") == "lifecycle"
            and row.get("observation_status") == "EVIDENCE_COLLECTION_COMPLETE"
            and str(row.get("record_id") or "") == f"lifecycle:{event_id}:evidence-collected"
        ]
        if len(existing_collection_rows) > 1:
            assessment["ready"] = False
            assessment["receipt"] = None
            assessment["blockers"] = sorted(set(
                assessment["blockers"] + ["EVIDENCE_COLLECTION_RECEIPT_AMBIGUOUS"]
            ))
            continue
        collected = build_evidence_collected_receipt(
            receipt, identity=key.as_dict(), event_id=event_id,
            provenance=assessment["provenance"], collected_at=now,
            lifecycle_horizon_sec=lifecycle_horizon_sec,
            reconciliation_allowance_sec=reconciliation_allowance_sec,
        )
        if not collected["ready"]:
            assessment["ready"] = False
            assessment["receipt"] = None
            assessment["blockers"] = sorted(set(
                assessment["blockers"] + collected["blockers"]
            ))
            continue
        if existing_collection_rows:
            existing = existing_collection_rows[0].get("evidence_collection_receipt")
            if not isinstance(existing, Mapping) or existing.get("schema") != EVIDENCE_COLLECTED_SCHEMA:
                assessment["ready"] = False
                assessment["receipt"] = None
                assessment["blockers"] = sorted(set(
                    assessment["blockers"] + ["EVIDENCE_COLLECTION_RECEIPT_COLLISION"]
                ))
                continue
            # The first durable collection timestamp is immutable. Rebuild
            # against it and require byte-for-byte canonical equality.
            expected = build_evidence_collected_receipt(
                receipt, identity=key.as_dict(), event_id=event_id,
                provenance=assessment["provenance"],
                collected_at=existing.get("evidence_collected_at"),
                lifecycle_horizon_sec=lifecycle_horizon_sec,
                reconciliation_allowance_sec=reconciliation_allowance_sec,
            )
            if not expected["ready"] or canonical_json(existing) != canonical_json(expected["receipt"]):
                assessment["ready"] = False
                assessment["receipt"] = None
                assessment["blockers"] = sorted(set(
                    assessment["blockers"] + ["EVIDENCE_COLLECTION_RECEIPT_COLLISION"]
                ))
                continue
            collected = expected
        row = {
            "record_id": f"lifecycle:{event_id}:bundle-completion:{receipt['completion_receipt_sha256'][:16]}",
            "event_id": event_id,
            "episode_id": key.episode_id,
            "policy_signature": key.policy_signature,
            "research_lane": key.research_lane,
            "terminal": True,
            "observation_status": "LIFECYCLE_BUNDLE_COMPLETE",
            "outcome_state": receipt["entry_outcome"],
            "bundle_completion": receipt,
            **assessment["provenance"],
        }
        writes.append(store.append("lifecycle", row))
        collection_row = {
            "record_id": f"lifecycle:{event_id}:evidence-collected",
            "event_id": event_id,
            "episode_id": key.episode_id,
            "policy_signature": key.policy_signature,
            "research_lane": key.research_lane,
            "terminal": True,
            "observation_status": "EVIDENCE_COLLECTION_COMPLETE",
            "outcome_state": receipt["entry_outcome"],
            "evidence_collected_at": collected["receipt"]["evidence_collected_at"],
            "evidence_collection_receipt": collected["receipt"],
            **assessment["provenance"],
        }
        collection_writes.append(store.append("lifecycle", collection_row))
    return {
        "schema": RECONCILER_SCHEMA,
        "epoch_id": str(epoch_id),
        "identity_count": len(assessments),
        "ready_count": sum(item["ready"] for item in assessments),
        "written_count": sum(item.get("written") is True for item in writes),
        "duplicate_count": sum(item.get("duplicate") is True for item in writes),
        "evidence_collected_written_count": sum(
            item.get("written") is True for item in collection_writes
        ),
        "evidence_collected_duplicate_count": sum(
            item.get("duplicate") is True for item in collection_writes
        ),
        "assessments": assessments,
        "writes": writes,
        "evidence_collection_writes": collection_writes,
    }
