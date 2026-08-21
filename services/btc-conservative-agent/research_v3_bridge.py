"""Dual-write bridge from immutable v2.2 events into normalized V3 ledgers."""
from __future__ import annotations

from typing import Any, Mapping

from research_v3_contract import COLLECTOR_VERSION
from research_v3_store import V3EvidenceStore


def _first(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), None)


def dual_write_v22_record(record: Mapping[str, Any], *, data_dir: str) -> dict[str, Any]:
    """Normalize one durable v2.2 event without copying its market path per row."""
    envelope = record.get("envelope") if isinstance(record.get("envelope"), Mapping) else {}
    epoch_id = str(_first(record.get("epoch_id"), envelope.get("epoch_id")) or "")
    event_id = str(_first(record.get("event_id"), record.get("trade_id")) or "")
    episode_id = str(_first(record.get("event_episode_id"), envelope.get("event_episode_id")) or "")
    if not epoch_id or not event_id or not episode_id:
        raise ValueError("V3_IDENTITY_INCOMPLETE")
    store = V3EvidenceStore(data_dir, epoch_id=epoch_id)
    tape = record.get("canonical_tape") if isinstance(record.get("canonical_tape"), Mapping) else {}
    path_1m = tape.get("path_1m") if isinstance(tape.get("path_1m"), list) else []
    ticks_1s = tape.get("ticks_1s_optional") if isinstance(tape.get("ticks_1s_optional"), list) else []
    signal_ts = float(_first(envelope.get("signal_ts"), record.get("signal_ts"), 0) or 0)
    symbol = str(_first((record.get("feature_snapshot_at_signal") or {}).get("symbol"), "BTCUSD"))
    segment_refs = []
    if path_1m:
        segment_refs.append(store.put_market_segment(
            source="CANONICAL_1M", symbol=symbol, timeframe="1m",
            start_ts=float(_first(tape.get("canonical_tape_start"), signal_ts) or signal_ts),
            end_ts=float(_first(tape.get("canonical_tape_end"), signal_ts) or signal_ts),
            rows=path_1m,
        ))
    if ticks_1s:
        times = [float(_first(row.get("t"), row.get("ts"), 0) or 0) for row in ticks_1s]
        segment_refs.append(store.put_market_segment(
            source="CANONICAL_1S", symbol=symbol, timeframe="1s",
            start_ts=min(times), end_ts=max(times), rows=ticks_1s,
        ))

    writes = []
    writes.append(store.append("opportunity", {
        "record_id": f"opportunity:{episode_id}",
        "episode_id": episode_id,
        "shared_ai_call_id": _first((record.get("event_episode") or {}).get("shared_ai_call_id"), record.get("shared_ai_call_id")),
        "signal_ts": signal_ts,
        "symbol": symbol,
        "raw_direction": _first(envelope.get("raw_direction"), record.get("raw_direction")),
        "feature_snapshot_at_signal": record.get("feature_snapshot_at_signal") or {},
        "pre_signal_context": record.get("pre_signal_context") or {},
        "collector_version": COLLECTOR_VERSION,
    }))
    writes.append(store.append("decision", {
        "record_id": f"decision:{event_id}",
        "episode_id": episode_id,
        "event_id": event_id,
        "executed_direction": _first(envelope.get("executed_direction"), record.get("direction")),
        "primary_outcome": _first(record.get("primary_outcome"), envelope.get("primary_outcome")),
        "decision_tree_snapshot": record.get("decision_tree_snapshot") or {},
        "exact_reason": record.get("exact_reason"),
        "would_block": record.get("would_block"),
        "would_block_reason": record.get("would_block_reason"),
        "policy_signature": _first(record.get("policy_signature"), envelope.get("policy_signature")),
        "policy_epoch_id": _first(record.get("policy_epoch_id"), envelope.get("policy_epoch_id")),
    }))
    writes.append(store.append("order_intent", {
        "record_id": f"order-intent:{event_id}",
        "episode_id": episode_id,
        "event_id": event_id,
        "execution_basis": record.get("research_execution_basis") or envelope.get("research_execution_basis") or {},
        "chase_schedule": record.get("research_chase_schedule") or envelope.get("research_chase_schedule") or {},
        "entry_children_count": len(record.get("entry_children") or []),
        "search_receipt": envelope.get("policy_search") or {},
    }))
    if record.get("live_fill_ts") is not None or record.get("live_fill_price") is not None:
        writes.append(store.append("execution", {
            "record_id": f"execution:{event_id}:primary-fill",
            "episode_id": episode_id,
            "event_id": event_id,
            "execution_world": "PAPER_OR_SOURCE_RECORDED",
            "fill_ts": record.get("live_fill_ts"),
            "fill_price": record.get("live_fill_price"),
            "quantity_basis": record.get("research_execution_basis") or {},
            "authenticated_exchange_actual": False,
        }))
    for ref in segment_refs:
        writes.append(store.append("market_segment", {
            "record_id": f"market-segment:{event_id}:{ref['sha256']}",
            "episode_id": episode_id,
            "event_id": event_id,
            "segment_ref": ref,
            "coverage": tape.get("coverage") or {},
        }))
    writes.append(store.append("lifecycle", {
        "record_id": f"lifecycle:{event_id}:terminal",
        "episode_id": episode_id,
        "event_id": event_id,
        "observation_status": record.get("observation_status"),
        "outcome_state": (
            "DATA_ERROR" if record.get("observation_status") == "DATA_ERROR"
            else "CENSORED" if record.get("negative_evidence") is True
            else "REJECTED" if record.get("primary_outcome") == "REJECTED"
            else "FULL_FILL" if record.get("primary_outcome") == "ACCEPTED_FILLED"
            else "NO_FILL" if record.get("primary_outcome") == "ACCEPTED_UNFILLED"
            else "UNSUPPORTED"
        ),
        "ranking_eligible": bool(record.get("ranking_eligible")),
        "replay_eligibility": record.get("replay_eligibility") or {},
        "market_segment_refs": segment_refs,
    }))
    return {
        "schema": "v22_to_v3_dual_write_receipt_v1",
        "epoch_id": epoch_id,
        "event_id": event_id,
        "episode_id": episode_id,
        "writes": writes,
        "store_verification": store.verify(),
    }

