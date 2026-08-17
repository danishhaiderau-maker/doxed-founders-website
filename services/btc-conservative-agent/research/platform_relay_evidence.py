"""Pure relay-evidence validation, indexing, normalization, and bounded JSONL reads.

Importing this module performs no I/O and creates no runtime owners.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path

def _validate_platform_relay_evidence_payload(payload: dict) -> tuple[bool, str]:
    """Validate the immutable platform export before making it visible to research."""
    if not isinstance(payload, dict) or payload.get("schema") != "relay_lifecycle_evidence_v1":
        return False, "SCHEMA_INVALID"
    if not all(payload.get(key) for key in ("generatedAt", "generatingRevision", "runIdentity")):
        return False, "PROVENANCE_INCOMPLETE"
    if payload.get("agentSlug") != "conservative-btc" or not payload.get("userId"):
        return False, "SCOPE_INVALID"
    records = payload.get("records")
    if not isinstance(records, list):
        return False, "RECORDS_INVALID"
    event_ids = set()
    for record in records:
        if not isinstance(record, dict) or not all(
            record.get(key) for key in ("canonicalTradeId", "lifecycleId", "participantId")
        ) or not isinstance(record.get("events"), list):
            return False, "RECORD_INVALID"
        for event in record["events"]:
            if not isinstance(event, dict) or not all(
                event.get(key) for key in ("id", "eventType", "createdAt")
            ):
                return False, "EVENT_INVALID"
            event_id = str(event["id"])
            if event_id in event_ids:
                return False, "DUPLICATE_EVENT"
            event_ids.add(event_id)
    return True, "OK"


def _platform_relay_evidence_index(path="relay_lifecycle_evidence_v1.json") -> dict:
    """Load a qualified read-only platform export. Invalid/stale-shaped input
    returns no evidence, so cohort classification remains fail-closed."""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            export = json.load(handle)
        if export.get("schema") != "relay_lifecycle_evidence_v1":
            return {}
        if not all(export.get(key) for key in ("generatedAt", "generatingRevision", "runIdentity")):
            return {}
        records = export.get("records")
        if not isinstance(records, list):
            return {}
        grouped = {}
        for record in records:
            trade_id = record.get("canonicalTradeId") if isinstance(record, dict) else None
            if trade_id:
                grouped.setdefault(str(trade_id), []).append(copy.deepcopy(record))
        return {
            trade_id: {
                "schema": export["schema"],
                "generated_at": export["generatedAt"],
                "generating_revision": export["generatingRevision"],
                "run_identity": export["runIdentity"],
                "records": rows,
                "evidence_revision": hashlib.sha256(json.dumps(
                    rows, sort_keys=True, separators=(",", ":"), default=str
                ).encode("utf-8")).hexdigest(),
            }
            for trade_id, rows in grouped.items()
        }
    except (OSError, ValueError, TypeError):
        return {}


def _normalize_platform_bitfinex_evidence(records: list, canonical_trade_id: str) -> dict:
    """Project explicit immutable relay fields into one conservative record.

    This is a view over the raw events, never a replacement for them. Values
    are copied only from named payload fields; event timestamps are not
    re-labelled as exchange ACK/fill timestamps, missing fill ids remain
    missing, and no completeness flag is promoted merely because rows exist.
    """
    qualified_records = [row for row in records if isinstance(row, dict)] if isinstance(records, list) else []
    participant_keys = sorted({
        str(row.get("participantId") or row.get("participant_id"))
        for row in qualified_records
        if row.get("participantId") or row.get("participant_id")
    })
    if len(participant_keys) > 1:
        participants = {
            participant_id: _normalize_platform_bitfinex_evidence(
                [row for row in qualified_records
                 if str(row.get("participantId") or row.get("participant_id")) == participant_id],
                canonical_trade_id,
            )
            for participant_id in participant_keys
        }
        participant_fill_overlays = {
            participant_id: copy.deepcopy(row.get("copy_fill_observed"))
            for participant_id, row in participants.items()
            if row.get("copy_fill_observed")
        }
        return {
            "schema": "bitfinex_evidence_v1",
            "canonical_trade_id": str(canonical_trade_id),
            "participant_id": None,
            "source_lifecycle_id": None,
            "participants": participants,
            "bitfinex_order_ids": list(dict.fromkeys(
                order_id for row in participants.values()
                for order_id in row.get("bitfinex_order_ids") or []
            )),
            "fill_ids": list(dict.fromkeys(
                fill_id for row in participants.values()
                for fill_id in row.get("fill_ids") or []
            )),
            "negative_events": [copy.deepcopy(event) for row in participants.values()
                                for event in row.get("negative_events") or []],
            "execution_timing": [copy.deepcopy(event) for row in participants.values()
                                 for event in row.get("execution_timing") or []],
            "source_event_history": [copy.deepcopy(event) for row in participants.values()
                                     for event in row.get("source_event_history") or []],
            "analysis_exclusion_reasons": sorted({
                reason for row in participants.values()
                for reason in row.get("analysis_exclusion_reasons") or []
            }),
            "quantity_evidence_complete": False,
            "order_ack_history_complete": False,
            "stop_evidence_complete": False,
            "source_snapshot_evidence_complete": False,
            "reconciliation_complete": False,
            "cost_evidence_complete": False,
            "copy_fill_observed": ({
                "schema": "copy_fill_observed_v1",
                "immutable": True,
                "canonical_trade_id": str(canonical_trade_id),
                "participants": participant_fill_overlays,
                "source_strategy_state_unchanged": True,
            } if participant_fill_overlays else {}),
            "exchange_confirmed_shadow_overlay": ({
                "schema": "exchange_confirmed_shadow_overlay_v1",
                "canonical_trade_id": str(canonical_trade_id),
                "copy_state": "FILLED_PARTICIPANTS",
                "source_state": "UNCHANGED",
                "provenance": "COPY_FILL_OBSERVED",
                "participants": sorted(participant_fill_overlays),
                "source_strategy_state_unchanged": True,
            } if participant_fill_overlays else {}),
        }

    evidence = {
        "schema": "bitfinex_evidence_v1",
        "canonical_trade_id": str(canonical_trade_id),
        "participant_id": None,
        "source_lifecycle_id": None,
        "source_identity": {},
        "source_event_history": [],
        "client_order_id": None,
        "client_order_ids": [],
        "bitfinex_order_ids": [],
        "fill_ids": [],
        "source_quantity": None,
        "normalized_quantity": None,
        "filled_quantity": None,
        "protected_quantity": None,
        "remaining_quantity": None,
        "cancelled_quantity": None,
        "fills": [],
        "copy_fill_observed": {},
        "exchange_confirmed_shadow_overlay": {},
        "reprices": [],
        "chase_history": [],
        "cluster_evidence": {},
        "ack_history": [],
        "stop_chain": [],
        "exit_evidence": {},
        "terminal_authority": {},
        "bbo_evidence": {},
        "cost_evidence": {},
        "cost_evidence_complete": False,
        "fee_model": None,
        "execution_profile": None,
        "reconciliation": {},
        "source_snapshot_evidence": {},
        "negative_events": [],
        "execution_timing": [],
        "analysis_exclusion_reasons": [],
        "quantity_evidence_complete": False,
        "order_ack_history_complete": False,
        "stop_evidence_complete": False,
        "source_snapshot_evidence_complete": False,
        "reconciliation_complete": False,
    }

    def explicit(source, *keys):
        for key in keys:
            if key in source and source.get(key) not in (None, "", [], {}):
                return copy.deepcopy(source.get(key))
        return None

    def finite(value):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    def timestamp_ms(value):
        number = finite(value)
        if number is not None:
            return number
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000.0
        except ValueError:
            return None

    def append_unique(key, value):
        if value is None or value == "":
            return
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item is not None and item != "" and item not in evidence[key]:
                evidence[key].append(copy.deepcopy(item))

    def append_row(key, row):
        fingerprint = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
        if all(json.dumps(old, sort_keys=True, separators=(",", ":"), default=str) != fingerprint
               for old in evidence[key]):
            evidence[key].append(row)

    negative_types = {
        "MIRROR_DIFF", "STALE_NO_EXPOSURE", "NEGATIVE_EVIDENCE",
        "RECONCILE_CANCEL_FAILED", "EXPIRED", "BLOCKED",
        "CORRELATED_CLUSTER_BLOCKED",
    }
    unsupported_exit_markers = {
        "SOURCE_ABSENCE_FALLBACK", "SHOWCASE_POSITION_ABSENT", "SHOWCASE_VANISHED",
        "MANUAL_CLOSE", "ADMIN_MANUAL_CLOSE", "EMERGENCY_ACTION",
        "PROTECTION_FAILURE_EMERGENCY_CLOSE", "LATE_FILL_CLEANUP",
        "EXIT_ONLY_PENDING_CANCEL_PARTIAL_FILL",
    }
    allowed_types = {
        "ORDER_PLACED", "FILLED", "STOP_LOSS_ARMED", "UPDATE_STOPS",
        "EXIT", "EXECUTION_TIMING", *negative_types,
    }
    producer_assertions = set()
    for record in qualified_records:
        if not isinstance(record, dict):
            continue
        participant_id = explicit(record, "participantId", "participant_id")
        lifecycle_id = explicit(record, "lifecycleId", "lifecycle_id", "cycleId", "cycle_id")
        if evidence["participant_id"] is None and participant_id is not None:
            evidence["participant_id"] = participant_id
            if evidence["source_lifecycle_id"] is None and lifecycle_id is not None:
                evidence["source_lifecycle_id"] = lifecycle_id
        for event in record.get("events") or []:
            if not isinstance(event, dict):
                continue
            event_type = str(explicit(event, "event_type", "eventType", "type") or "").upper()
            if event_type not in allowed_types:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
            event_id = explicit(event, "id", "event_id", "eventId")
            event_created_at = explicit(event, "createdAt", "created_at")
            event_name = str(
                explicit(payload, "event", "diff_type", "reason", "exit_reason") or event_type
            ).upper()
            if evidence["fee_model"] is None:
                evidence["fee_model"] = explicit(payload, "fee_model")
            if evidence["execution_profile"] is None:
                evidence["execution_profile"] = explicit(payload, "execution_profile")

            source_event_id = explicit(payload, "source_event_id", "sourceEventId", "showcase_event_id")
            source_event_seq = explicit(payload, "source_event_seq", "sourceEventSeq", "showcase_event_seq")
            if source_event_id is not None or source_event_seq is not None:
                evidence["source_identity"] = {
                    key: value for key, value in {
                        "canonical_trade_id": str(canonical_trade_id),
                        "source_lifecycle_id": evidence.get("source_lifecycle_id"),
                        "source_event_id": source_event_id,
                        "source_event_seq": source_event_seq,
                        "source_event_at": explicit(payload, "source_event_at", "sourceEventAt"),
                        "platform_received_at": explicit(payload, "platform_received_at", "platformReceivedAt"),
                    }.items() if value is not None
                }
                append_row("source_event_history", {
                    key: value for key, value in {
                        "event_id": event_id,
                        "event_type": event_type,
                        "source_event_id": source_event_id,
                        "source_event_seq": source_event_seq,
                        "source_event_at": explicit(payload, "source_event_at", "sourceEventAt"),
                        "platform_received_at": explicit(
                            payload, "platform_received_at", "platformReceivedAt"
                        ),
                    }.items() if value is not None
                })
            cluster_evidence = explicit(payload, "correlated_cluster_evidence")
            if isinstance(cluster_evidence, dict):
                evidence["cluster_evidence"] = cluster_evidence
            entry_bbo = explicit(payload, "entry_bbo", "entryBbo")
            if isinstance(entry_bbo, dict):
                evidence["bbo_evidence"]["entry"] = entry_bbo
            close_bbo = explicit(payload, "close_bbo", "closeBbo")
            if isinstance(close_bbo, dict):
                evidence["bbo_evidence"]["exit"] = close_bbo

            cid = explicit(payload, "client_order_id", "clientOrderId", "cid")
            if evidence["client_order_id"] is None and cid is not None:
                evidence["client_order_id"] = cid
            append_unique("client_order_ids", cid)
            stop_cid = explicit(payload, "stop_client_order_id", "stopClientOrderId")
            append_unique("client_order_ids", stop_cid)
            order_id = explicit(
                payload, "bitfinex_order_id", "bitfinexOrderId",
                "exchange_order_id", "close_exchange_order_id", "exit_order_id",
                "order_id", "orderId",
            )
            stop_id = explicit(
                payload, "stop_order_id", "stopOrderId", "partialFillStopOrderId",
                "partial_fill_stop_order_id",
            )
            append_unique("bitfinex_order_ids", order_id)
            append_unique("bitfinex_order_ids", stop_id)
            append_unique("bitfinex_order_ids", explicit(payload, "bitfinex_order_ids", "exchange_order_ids"))
            predecessor_stop_id = explicit(
                payload, "superseded_stop_order_id", "supersededStopOrderId",
                "supersededPartialStopOrderId", "protectiveStopPredecessorId",
            )
            append_unique("bitfinex_order_ids", predecessor_stop_id)
            fill_id = explicit(payload, "fill_id", "fillId", "bitfinex_fill_id", "exchange_fill_id")
            fill_id_list = explicit(
                payload, "fill_ids", "bitfinex_fill_ids", "exchange_fill_ids",
                "entry_fill_ids", "exit_fill_ids", "exchange_exit_fill_ids",
            )
            # Authenticated Bitfinex fills often publish only exchange_fill_ids[].
            # Promote the first id so COPY_FILL_OBSERVED can form without inventing
            # a Showcase fill.
            if fill_id is None and isinstance(fill_id_list, list) and fill_id_list:
                fill_id = fill_id_list[0]
            append_unique("fill_ids", fill_id)
            append_unique("fill_ids", fill_id_list)

            quantity_fields = {
                "source_quantity": explicit(payload, "source_quantity", "source_qty", "source_exact_qty_btc"),
                "normalized_quantity": explicit(payload, "normalized_quantity", "normalized_qty", "venue_qty_btc")
                    or (explicit(payload, "qty") if event_type == "ORDER_PLACED" else None),
                "filled_quantity": explicit(payload, "filled_quantity", "filled_qty", "filledQty", "partial_fill_qty", "partialFillQty")
                    or (explicit(payload, "qty") if event_type == "FILLED" else None),
                "protected_quantity": explicit(payload, "protected_quantity", "protected_qty", "protected_exchange_qty")
                    or (explicit(payload, "qty") if event_type in {"STOP_LOSS_ARMED", "UPDATE_STOPS"} and stop_id is not None else None),
                "remaining_quantity": explicit(payload, "remaining_quantity", "remaining_qty", "remainingQty"),
            }
            for key, raw in quantity_fields.items():
                number = finite(raw)
                if number is None:
                    continue
                if key in {"filled_quantity", "protected_quantity"}:
                    evidence[key] = max(number, finite(evidence.get(key)) or 0.0)
                else:
                    evidence[key] = number
            cancelled = finite(explicit(
                payload, "cancelled_quantity", "cancelled_qty", "unfilled_qty_cancelled"
            ))
            if cancelled is not None:
                evidence["cancelled_quantity"] = cancelled
            if payload.get("remaining_entry_order_live") is False:
                evidence["remaining_quantity"] = 0.0

            if event_type == "FILLED" and (fill_id is not None or quantity_fields["filled_quantity"] is not None):
                fill_row = {
                    "event_id": event_id,
                    "fill_id": fill_id,
                    "order_id": order_id,
                    "quantity": finite(quantity_fields["filled_quantity"]),
                    "price": finite(explicit(payload, "fill_price", "fillPrice", "exchange_fill_price")),
                    "filled_at": explicit(payload, "exchange_fill_received_at", "fill_at", "filled_at"),
                    "fill_ids": explicit(payload, "fill_ids", "bitfinex_fill_ids", "exchange_fill_ids"),
                    "detection_path": explicit(payload, "fill_detection_path", "fill_source"),
                    "detection_context": explicit(payload, "fill_detection_context", "cancel_context"),
                    "entry_completion": explicit(payload, "entry_completion"),
                    "unfilled_quantity_cancelled": finite(explicit(payload, "unfilled_qty_cancelled")),
                }
                append_row("fills", {key: value for key, value in fill_row.items() if value is not None})

            ack_at = explicit(
                payload, "exchange_ack_at", "exchangeAckAt", "entryExchangeAckAtMs",
                "replacementExchangeAckAtMs", "stop_exchange_ack_at", "ack_at", "acknowledged_at",
            )
            if event_type == "EXECUTION_TIMING":
                stages = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
                stage_names = (
                    "queueEnteredAtMs", "executorStartedAtMs",
                    "databasePreflightStartedAtMs", "databasePreflightCompletedAtMs",
                    "bitfinexRequestStartedAtMs", "exchangeAckAtMs",
                    "persistenceStartedAtMs", "persistenceCompletedAtMs",
                )
                projected = {name: finite(stages.get(name)) for name in stage_names}
                present = {name: value for name, value in projected.items() if value is not None}
                missing = [name for name in stage_names if name not in present]
                queue_at = present.get("queueEnteredAtMs")
                exchange_ack_at = present.get("exchangeAckAtMs")
                if queue_at is None or exchange_ack_at is None:
                    sla_verdict = "UNKNOWN"
                    queue_to_ack_ms = None
                else:
                    queue_to_ack_ms = max(0.0, exchange_ack_at - queue_at)
                    sla_verdict = "PASS" if queue_to_ack_ms <= 3000 else "MISS"
                append_row("execution_timing", {
                    "event_id": event_id,
                    "timing_correlation_id": explicit(payload, "timing_correlation_id"),
                    "schema": explicit(payload, "schema") or "UNKNOWN",
                    "operation": explicit(payload, "operation") or "UNKNOWN",
                    "order_id": order_id,
                    "stages": present,
                    "missing_stages": missing,
                    "queue_to_exchange_ack_ms": queue_to_ack_ms,
                    "sla_3s_verdict": sla_verdict,
                    "complete": not missing,
                })
            elif event_type == "FILLED":
                fill_stages = {
                    "sourceEventAtMs": timestamp_ms(explicit(payload, "source_event_at", "source_fill_at")),
                    "exchangeFillOccurredAtMs": timestamp_ms(explicit(
                        payload, "exchange_fill_last_at", "exchange_fill_mts", "exchange_fill_first_at"
                    )),
                    "exchangeFillReceivedAtMs": timestamp_ms(explicit(payload, "exchange_fill_received_at")),
                    "fillDetectedAtMs": timestamp_ms(explicit(payload, "exchange_fill_detected_at", "fill_detected_at")),
                    "stopRequestStartedAtMs": timestamp_ms(explicit(payload, "stop_submit_started_at")),
                    "stopExchangeAckAtMs": timestamp_ms(explicit(payload, "stop_exchange_ack_at")),
                    "fillPersistenceCompletedAtMs": timestamp_ms(explicit(payload, "fill_persistence_completed_at")),
                }
                present = {key: value for key, value in fill_stages.items() if value is not None}
                append_row("execution_timing", {
                    "event_id": event_id, "schema": "relay_fill_protection_timing_v1",
                    "timing_correlation_id": explicit(payload, "timing_correlation_id"),
                    "operation": "FILL_PROTECTED", "order_id": order_id, "stages": present,
                    "missing_stages": [key for key in fill_stages if key not in present],
                    "complete": all(key in present for key in fill_stages),
                })
            elif event_type in {"STOP_LOSS_ARMED", "UPDATE_STOPS"}:
                stop_stages = {
                    "fillDetectedAtMs": timestamp_ms(explicit(payload, "partial_fill_detected_at", "fill_detected_at")),
                    "stopRequestStartedAtMs": timestamp_ms(explicit(payload, "stop_submit_started_at")),
                    "stopExchangeAckAtMs": timestamp_ms(explicit(payload, "stop_exchange_ack_at")),
                    "stopPersistenceCompletedAtMs": timestamp_ms(explicit(payload, "stop_persistence_completed_at")),
                }
                present = {key: value for key, value in stop_stages.items() if value is not None}
                append_row("execution_timing", {
                    "event_id": event_id, "schema": "relay_stop_timing_v1",
                    "timing_correlation_id": explicit(payload, "timing_correlation_id"),
                    "operation": "STOP_PROTECTION", "order_id": stop_id or order_id, "stages": present,
                    "missing_stages": [key for key in stop_stages if key not in present],
                    "complete": all(key in present for key in stop_stages),
                })
            elif event_type == "EXIT":
                close_stages = {
                    "sourceEventAtMs": timestamp_ms(explicit(payload, "source_event_at")),
                    "platformReceivedAtMs": timestamp_ms(explicit(payload, "platform_received_at")),
                    "closePreflightStartedAtMs": timestamp_ms(explicit(payload, "close_preflight_started_at")),
                    "closeRequestStartedAtMs": timestamp_ms(explicit(payload, "close_submit_started_at")),
                    "closeExchangeAckAtMs": timestamp_ms(explicit(payload, "close_exchange_ack_at")),
                    "closeConfirmedAtMs": timestamp_ms(explicit(payload, "close_confirmed_at")),
                    "closePersistenceCompletedAtMs": timestamp_ms(explicit(payload, "close_persistence_completed_at")),
                }
                present = {key: value for key, value in close_stages.items() if value is not None}
                append_row("execution_timing", {
                    "event_id": event_id, "schema": "relay_close_timing_v1",
                    "timing_correlation_id": explicit(payload, "timing_correlation_id"),
                    "operation": "TERMINAL_CLOSE", "order_id": order_id, "stages": present,
                    "missing_stages": [key for key in close_stages if key not in present],
                    "complete": all(key in present for key in close_stages),
                })
            ack_order_id = stop_id if stop_id is not None and explicit(
                payload, "stop_exchange_ack_at"
            ) is not None else order_id
            if ack_order_id is not None and ack_at is not None and event_type in {"ORDER_PLACED", "UPDATE_STOPS", "EXECUTION_TIMING"}:
                append_row("ack_history", {
                    "event_id": event_id, "order_id": ack_order_id, "ack_at": ack_at,
                    "operation": explicit(payload, "operation", "event") or event_type,
                })

            if event_type == "UPDATE_STOPS" and any(
                key in payload for key in ("new_limit", "newLimit", "limit_price", "limitPrice")
            ):
                reprice_row = {
                    "event_id": event_id,
                    "order_id": order_id,
                    "prior_price": finite(explicit(payload, "prior_limit", "priorLimit")),
                    "price": finite(explicit(payload, "new_limit", "newLimit", "limit_price", "limitPrice")),
                    "ack_at": ack_at,
                    "replacement_mode": explicit(payload, "replacementMode", "replacement_mode"),
                    "mark": finite(explicit(payload, "local_mark", "localMark")),
                    "chase_count": finite(explicit(payload, "limitChaseCount", "limit_chase_count")),
                    "source_event_id": source_event_id,
                    "source_event_seq": source_event_seq,
                }
                append_row("reprices", reprice_row)
                append_row("chase_history", {
                    key: value for key, value in reprice_row.items() if value is not None
                })

            if (
                event_type in {"STOP_LOSS_ARMED", "UPDATE_STOPS"}
                and stop_id is not None
                and (event_type == "STOP_LOSS_ARMED" or ack_at is not None)
            ):
                protected = finite(explicit(
                    payload, "protected_quantity", "protected_qty", "protected_exchange_qty",
                    "partial_fill_qty", "partialFillQty", "qty", "quantity",
                ))
                predecessor = predecessor_stop_id
                append_row("stop_chain", {
                    key: value for key, value in {
                        "event_id": event_id,
                        "order_id": stop_id,
                        "protected_quantity": protected,
                        "stop_price": finite(explicit(payload, "stop_price", "stopPrice")),
                        "ack_at": ack_at,
                        "predecessor_order_id": predecessor,
                        "client_order_id": stop_cid,
                        "event": explicit(payload, "event") or event_type,
                    }.items() if value is not None
                })
                if protected is not None:
                    evidence["protected_quantity"] = max(
                        protected, finite(evidence.get("protected_quantity")) or 0.0
                    )

            if event_type == "EXIT":
                exit_reason = explicit(payload, "exit_reason", "exitReason", "reason", "event")
                exit_row = {
                    "event_id": event_id,
                    "order_id": order_id,
                    "fill_id": fill_id,
                    "fill_ids": explicit(payload, "exit_fill_ids", "exchange_exit_fill_ids"),
                    "filled_quantity": finite(explicit(
                        payload, "exchange_exit_filled_qty", "exit_filled_qty", "qty_closed"
                    )),
                    "exit_price": finite(explicit(payload, "exit_price", "exitPrice")),
                    "exit_reason": exit_reason,
                    "exited_at": explicit(payload, "exchange_exit_at", "exit_at", "exited_at"),
                }
                evidence["exit_evidence"] = {
                    key: value for key, value in exit_row.items() if value is not None
                }
                actual_pnl = finite(explicit(
                    payload, "actual_bitfinex_realized_pnl_usd",
                    "exchange_realized_pnl_usd", "actual_realized_pnl_usd",
                ))
                if actual_pnl is not None:
                    evidence["actual_bitfinex_realized_pnl_usd"] = actual_pnl
                authority_kind = str(explicit(payload, "terminal_authority_kind") or "").upper()
                authority = explicit(payload, "terminal_authority_evidence")
                if isinstance(authority, dict):
                    evidence["terminal_authority"] = {
                        "kind": authority_kind,
                        "evidence": authority,
                    }
                    source_proof = {"authority_kind": authority_kind, **authority}
                    evidence["source_snapshot_evidence"] = source_proof
                    if authority_kind == "SIGNED_POSITION_CLOSED":
                        evidence["source_snapshot_evidence_complete"] = bool(
                            authority.get("trade_id") and authority.get("event_id")
                            and authority.get("event_seq") is not None
                            and authority.get("source_event_at_ms") is not None
                            and authority.get("platform_received_at_ms") is not None
                            and authority.get("exit_price") is not None
                            and authority.get("exit_reason")
                        )
                    elif authority_kind == "CANONICAL_TERMINAL_RECORD":
                        snapshot = authority.get("source_snapshot_evidence")
                        evidence["source_snapshot_evidence_complete"] = bool(
                            isinstance(snapshot, dict)
                            and snapshot.get("source_git_rev")
                            and snapshot.get("sequence") is not None
                            and snapshot.get("captured_at")
                            and snapshot.get("snapshot_age_sec") is not None
                            and snapshot.get("positions_synced") is True
                            and snapshot.get("orders_synced") is True
                            and snapshot.get("trades_synced") is True
                        )
                for key in ("trading_fee_usd", "funding_fee_usd", "spread_cost_usd", "slippage_usd"):
                    value = finite(payload.get(key)) if key in payload else None
                    if value is not None:
                        evidence["cost_evidence"][key] = value
                copy_slippage = finite(payload.get("copy_exit_slippage_usd"))
                if copy_slippage is not None:
                    evidence["cost_evidence"]["copy_exit_slippage_usd"] = copy_slippage
                marker = str(exit_reason or "").upper()
                if marker in unsupported_exit_markers:
                    evidence["analysis_exclusion_reasons"].append("TERMINAL_PROVENANCE_EXCLUDED")

            reconciliation = explicit(payload, "reconciliation", "final_reconciliation")
            if isinstance(reconciliation, dict):
                evidence["reconciliation"] = reconciliation
                if reconciliation.get("complete") is True:
                    evidence["reconciliation_complete"] = True

            if event_type in negative_types or event_name in unsupported_exit_markers:
                append_row("negative_events", {
                    key: value for key, value in {
                        "event_id": event_id,
                        "event_type": event_type,
                        "event": event_name,
                        "created_at": event_created_at,
                        "payload": copy.deepcopy(payload),
                    }.items() if value is not None
                })
                nested_exclusion_reasons = payload.get("analysis_exclusion_reasons")
                if isinstance(nested_exclusion_reasons, (list, tuple, set)):
                    for reason in nested_exclusion_reasons:
                        normalized_reason = str(reason or "").strip().upper()
                        if normalized_reason:
                            evidence["analysis_exclusion_reasons"].append(normalized_reason)
                if event_name in unsupported_exit_markers:
                    evidence["analysis_exclusion_reasons"].append(event_name)
                if event_name == "COPY_ORDER_NO_SHOWCASE":
                    evidence["analysis_exclusion_reasons"].append(event_name)
                if event_name == "CORRELATED_CLUSTER_BLOCKED":
                    evidence["analysis_exclusion_reasons"].append(event_name)

            # Producer assertions are necessary but never sufficient. They
            # are validated against the explicit projected fields below.
            for key in (
                "quantity_evidence_complete", "order_ack_history_complete",
                "stop_evidence_complete", "source_snapshot_evidence_complete",
                "reconciliation_complete",
            ):
                if payload.get(key) is True:
                    producer_assertions.add(key)

    receipt_targets = {
        "FILLED_PERSISTED": ("FILL_PROTECTED", "fillPersistenceCompletedAtMs"),
        "STOP_LOSS_ARMED_PERSISTED": ("STOP_PROTECTION", "stopPersistenceCompletedAtMs"),
        "PARTIAL_STOP_PERSISTED": ("STOP_PROTECTION", "stopPersistenceCompletedAtMs"),
        "STOP_REPLACEMENT_PERSISTED": ("STOP_PROTECTION", "stopPersistenceCompletedAtMs"),
        "EXIT_PERSISTED": ("TERMINAL_CLOSE", "closePersistenceCompletedAtMs"),
    }
    for receipt in evidence["execution_timing"]:
        target = receipt_targets.get(receipt.get("operation"))
        persisted_at = (receipt.get("stages") or {}).get("persistenceCompletedAtMs")
        if not target or persisted_at is None:
            continue
        target_operation, target_stage = target
        candidates = [row for row in evidence["execution_timing"]
                      if row.get("operation") == target_operation]
        correlation_id = receipt.get("timing_correlation_id")
        if correlation_id is not None:
            correlated = [row for row in candidates
                          if row.get("timing_correlation_id") == correlation_id]
            if correlated:
                candidates = correlated
        if receipt.get("order_id") is not None:
            same_order = [row for row in candidates if row.get("order_id") == receipt.get("order_id")]
            if same_order:
                candidates = same_order
        for row in candidates[-1:]:
            row["stages"][target_stage] = persisted_at
            row["missing_stages"] = [name for name in row.get("missing_stages") or []
                                     if name != target_stage]
            row["complete"] = not row["missing_stages"]

    evidence["analysis_exclusion_reasons"] = sorted(set(evidence["analysis_exclusion_reasons"]))
    authenticated_fills = [
        copy.deepcopy(row) for row in evidence["fills"]
        if row.get("fill_id") is not None
        and finite(row.get("quantity")) is not None
        and finite(row.get("price")) is not None
    ]
    if authenticated_fills:
        source_model = None
        recon_state = None
        for row in qualified_records:
            for event in row.get("events") or []:
                payload = event.get("payload") if isinstance(event, dict) else None
                if not isinstance(payload, dict):
                    continue
                if payload.get("source_model_fill_state"):
                    source_model = payload.get("source_model_fill_state")
                if payload.get("copy_reconciliation_state"):
                    recon_state = payload.get("copy_reconciliation_state")
        evidence["copy_fill_observed"] = {
            "schema": "copy_fill_observed_v1",
            "immutable": True,
            "canonical_trade_id": str(canonical_trade_id),
            "participant_id": evidence.get("participant_id"),
            "lifecycle_id": evidence.get("source_lifecycle_id"),
            "venue": "BITFINEX",
            "authority": "AUTHENTICATED_EXCHANGE_FILL",
            "fills": authenticated_fills,
            "fill_ids": [row["fill_id"] for row in authenticated_fills],
            "bitfinex_order_ids": list(evidence.get("bitfinex_order_ids") or []),
            "client_order_ids": list(evidence.get("client_order_ids") or []),
            "classification": recon_state or "COPY_ONLY_FILL_AUTHENTICATED_SOURCE_UNCONFIRMED",
            "source_model_fill_state": source_model or "SOURCE_UNCONFIRMED",
            "divergence_reason": "COPY_FILLED_SOURCE_UNFILLED_OR_UNKNOWN",
            "source_strategy_state_unchanged": True,
            "showcase_simulated_status": "UNCHANGED",
        }
        evidence["exchange_confirmed_shadow_overlay"] = {
            "schema": "exchange_confirmed_shadow_overlay_v1",
            "canonical_trade_id": str(canonical_trade_id),
            "participant_id": evidence.get("participant_id"),
            "copy_state": "FILLED",
            "source_state": "UNCHANGED",
            "provenance": "COPY_FILL_OBSERVED",
            "label": "EXCHANGE_CONFIRMED_SHADOW_POSITION",
            "excluded_from_showcase_strategy_stats": True,
            "source_strategy_state_unchanged": True,
        }
    evidence["cost_evidence_complete"] = all(
        key in evidence["cost_evidence"] for key in (
            "trading_fee_usd", "funding_fee_usd", "spread_cost_usd", "slippage_usd"
        )
    )
    evidence["actual_costs"] = copy.deepcopy(evidence["cost_evidence"])
    quantities_complete = all(
        finite(evidence.get(key)) is not None for key in (
            "source_quantity", "normalized_quantity", "filled_quantity", "protected_quantity"
        )
    ) and float(evidence.get("protected_quantity") or 0) + 1e-12 >= float(evidence.get("filled_quantity") or 0)
    evidence["quantity_evidence_complete"] = bool(quantities_complete)
    evidence["order_ack_history_complete"] = bool(
        evidence["ack_history"]
        and all(row.get("order_id") is not None and row.get("ack_at") is not None
                for row in evidence["ack_history"])
        and all(row.get("order_id") is not None and row.get("ack_at") is not None
                for row in evidence["reprices"])
    )
    evidence["stop_evidence_complete"] = bool(
        evidence["stop_chain"]
        and all(row.get("order_id") is not None
                and finite(row.get("protected_quantity")) is not None
                and row.get("ack_at") is not None for row in evidence["stop_chain"])
    )
    reconciliation = evidence.get("reconciliation") or {}
    current_reconciliation_complete = bool(
        reconciliation.get("schema") == "relay_final_reconciliation_v1"
        and reconciliation.get("complete") is True
        and reconciliation.get("position_reconciled") is True
        and finite(reconciliation.get("exchange_vs_ledger_delta_sats")) == 0.0
        and finite(reconciliation.get("order_delta")) == 0.0
        and finite(reconciliation.get("orphan_order_count")) == 0.0
        and finite(reconciliation.get("foreign_order_count")) == 0.0
    )
    legacy_asserted_reconciliation_complete = bool(
        "reconciliation_complete" in producer_assertions
        and reconciliation.get("complete") is True
        and finite(reconciliation.get("position_delta")) == 0.0
        and finite(reconciliation.get("order_delta")) == 0.0
        and finite(reconciliation.get("orphan_order_count")) == 0.0
        and finite(reconciliation.get("foreign_order_count")) == 0.0
    )
    evidence["reconciliation_complete"] = bool(
        current_reconciliation_complete or legacy_asserted_reconciliation_complete
    )
    return evidence


def _snapshot_with_platform_relay_evidence(snapshot: dict, trade_id: str, evidence_index=None) -> dict:
    joined = (evidence_index if evidence_index is not None else _platform_relay_evidence_index()).get(str(trade_id))
    if not joined:
        return snapshot
    enriched = copy.deepcopy(snapshot)
    records = joined["records"]
    events = []
    for record in records:
        participant_id = record.get("participantId")
        for event in record.get("events") or []:
            if isinstance(event, dict):
                normalized = {**copy.deepcopy(event), "participantId": participant_id}
                normalized["event_type"] = normalized.get("event_type") or normalized.get("eventType")
                events.append(normalized)
    enriched["platform_relay_evidence"] = joined
    enriched["platform_evidence_revision"] = joined["evidence_revision"]
    enriched["lifecycle_events"] = events
    # Raw immutable events are authoritative evidence, but unknown producer
    # payload shapes are never guessed into a completeness=true assertion.
    normalized_evidence = _normalize_platform_bitfinex_evidence(records, str(trade_id))
    evidence = copy.deepcopy(enriched.get("bitfinex_evidence") or {})
    for key, value in normalized_evidence.items():
        if key not in evidence or evidence.get(key) in (None, "", [], {}):
            evidence[key] = copy.deepcopy(value)
    evidence["platform_records"] = records
    if not evidence.get("participants"):
        evidence["participant_id"] = evidence.get("participant_id") or next(
            (r.get("participantId") for r in records if r.get("participantId")), None
        )
        evidence["source_lifecycle_id"] = evidence.get("source_lifecycle_id") or next(
            (r.get("lifecycleId") for r in records if r.get("lifecycleId")), None
        )
    enriched["bitfinex_evidence"] = evidence
    if evidence.get("copy_fill_observed"):
        # This overlay is copy-side research evidence only.  In particular it
        # must never set ``executed``, source fill price, or source status.
        enriched["copy_fill_observed"] = copy.deepcopy(evidence["copy_fill_observed"])
        overlay = copy.deepcopy(evidence.get("exchange_confirmed_shadow_overlay") or {})
        source_filled = bool(
            enriched.get("executed") is True
            or str(enriched.get("status") or "").upper() in {"FILLED", "OPEN", "CLOSED"}
        )
        overlay["source_state"] = "FILLED" if source_filled else "UNFILLED_OR_UNKNOWN"
        overlay["divergence_classification"] = (
            "BOTH_FILLED" if source_filled else "COPY_FILLED_SOURCE_UNFILLED_OR_UNKNOWN"
        )
        enriched["exchange_confirmed_shadow_overlay"] = overlay
    if enriched.get("actual_bitfinex_realized_pnl_usd") is None:
        explicit_pnl = evidence.get("actual_bitfinex_realized_pnl_usd")
        if explicit_pnl is not None:
            enriched["actual_bitfinex_realized_pnl_usd"] = explicit_pnl
    if not enriched.get("terminal_provenance"):
        exit_reason = (evidence.get("exit_evidence") or {}).get("exit_reason")
        if exit_reason:
            enriched["terminal_provenance"] = exit_reason
    return enriched


def _offline_sim_jsonl_paths(active_path, max_rotations=128):
    """Return bounded immutable generations oldest-to-newest, then active.

    Rotations are monotonically numbered by rotate_log (``.1``, ``.2``, ...).
    Temporary/non-numeric siblings are never research evidence. If retention
    leaves more generations than the bounded reader permits, retain the newest
    window so the active evidence join cannot be starved by ancient shards.
    """
    active = Path(active_path)
    rotations = []
    try:
        for path in active.parent.glob(active.name + ".*"):
            suffix = path.name[len(active.name) + 1:]
            if path.is_file() and suffix.isdigit():
                rotations.append((int(suffix), path))
    except OSError:
        rotations = []
    keep = max(0, int(max_rotations))
    ordered = sorted(rotations)
    if keep:
        ordered = ordered[-keep:]
    else:
        ordered = []
    return [str(path) for _, path in ordered] + [str(active)]


def _load_offline_sim_jsonl_revisions(
    active_path,
    max_rotations=128,
    target_trade_ids=None,
    on_read_error=None,
):
    """Load newest valid immutable row per canonical trade ID.

    Files and lines are consumed in append chronology, so a later immutable
    revision deterministically replaces an older revision for the same ID.
    The active file preserves ordinary behavior; rotations retain only the
    requested evidence-derived IDs. Malformed, non-object, oversized, and
    identity-less rows are skipped.
    """
    rows = {}
    active_resolved = os.path.abspath(str(active_path))
    targets = {
        str(trade_id).strip()
        for trade_id in (target_trade_ids or ())
        if str(trade_id).strip()
    }
    paths = _offline_sim_jsonl_paths(active_path, max_rotations=max_rotations)
    # Preserve the prior active-file behavior for ordinary source research.
    # Historical shards are scanned only when a bounded evidence-derived target
    # set exists, preventing 400MB+ replay archives from accumulating in RAM.
    if not targets:
        paths = [str(active_path)]
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if len(line.encode("utf-8", errors="ignore")) > 8 * 1024 * 1024:
                        continue
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        row = json.loads(stripped)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    trade_id = row.get("trade_id")
                    if not isinstance(trade_id, str):
                        continue
                    trade_id = trade_id.strip()
                    if not trade_id or len(trade_id) > 255:
                        continue
                    if os.path.abspath(path) != active_resolved and trade_id not in targets:
                        continue
                    rows[trade_id] = row
        except OSError as exc:
            if on_read_error is not None:
                on_read_error(path, exc)
    return rows
