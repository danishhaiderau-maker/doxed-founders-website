"""Dual-write bridge from immutable v2.2 events into normalized V3 ledgers."""
from __future__ import annotations

from typing import Any, Mapping
import hashlib

from research_v3_contract import COLLECTOR_VERSION
from research_v3_store import V3EvidenceStore
from research_v3_contract import canonical_json


def _first(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), None)


def _causal_identity(event_id: str, *sources: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the same causal episode for provisional and live paper rows."""
    shared = str(_first(*(source.get("shared_ai_call_id") for source in sources)) or "").strip()
    stable_episode_id = str(_first(*(source.get("event_episode_id") for source in sources)) or "").strip()
    symbol = str(_first(*(source.get("symbol") or source.get("pair") for source in sources), "BTCUSD")).upper()
    raw_direction = str(_first(*(source.get("raw_direction") for source in sources)) or "").upper()
    executed_direction = str(_first(*(source.get("executed_direction") or source.get("final_direction") or source.get("signal_dir") or source.get("dir") for source in sources)) or "UNKNOWN").upper()
    raw_direction = raw_direction or executed_direction
    if shared:
        causal_key = f"shared:{symbol}:{raw_direction}:{shared}"
        episode_id = "episode-" + hashlib.sha256(causal_key.encode("utf-8")).hexdigest()[:20]
        grouping_basis = "SHARED_AI_CALL"
    else:
        episode_id, grouping_basis = stable_episode_id, "STABLE_EVENT_EPISODE"
    if not event_id or not episode_id:
        raise ValueError("V3_CAUSAL_IDENTITY_INCOMPLETE")
    return {"event_id": str(event_id), "episode_id": episode_id, "shared_ai_call_id": shared or None,
            "symbol": symbol, "raw_direction": raw_direction, "executed_direction": executed_direction,
            "grouping_basis": grouping_basis}


def _paper_policy_identity(epoch_id: str, *sources: Mapping[str, Any]) -> dict[str, str]:
    policy_id = str(_first(*(source.get("policy_id") or source.get("raw_policy_id") for source in sources)) or "UNSPECIFIED_PAPER_POLICY")
    signature = str(_first(*(source.get("policy_signature") for source in sources)) or "")
    if not signature:
        spec = {
            "policy_id": policy_id,
            "research_lane": _first(*(source.get("research_lane") for source in sources)),
            "entry_limit_policy": _first(*(source.get("entry_limit_policy") for source in sources)),
            "entry_offset_pct": _first(*(source.get("entry_offset_pct") for source in sources)),
            "exit_config": _first(*(source.get("exit_config") for source in sources)),
            "paper_only": True,
        }
        signature = "paper-policy-" + hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()[:20]
    policy_epoch_id = str(_first(*(source.get("policy_epoch_id") for source in sources)) or "")
    if not policy_epoch_id:
        policy_epoch_id = "paper-policy-epoch-" + hashlib.sha256(
            f"{epoch_id}|{signature}".encode("utf-8")
        ).hexdigest()[:20]
    return {"policy_id": policy_id, "policy_signature": signature, "policy_epoch_id": policy_epoch_id}


def dual_write_paper_order_intent(order: Mapping[str, Any], signal: Mapping[str, Any], *, epoch_id: str, data_dir: str) -> dict[str, Any]:
    """Write an actual paper order intent immediately, before path maturity."""
    event_id = str(_first(order.get("trade_id"), signal.get("trade_id")) or "")
    identity = _causal_identity(event_id, signal, order)
    policy = _paper_policy_identity(str(epoch_id), signal, order)
    store = V3EvidenceStore(data_dir, epoch_id=str(epoch_id))
    signal_ts = float(_first((signal.get("timing") or {}).get("signal_ts"), signal.get("created_ts_ts"), order.get("signal_created_ts"), order.get("created_ts"), 0) or 0)
    opportunity = store.append("opportunity", {
        "record_id": f"opportunity:{identity['episode_id']}", "episode_id": identity["episode_id"],
        "shared_ai_call_id": identity["shared_ai_call_id"], "signal_ts": signal_ts,
        "symbol": identity["symbol"], "raw_direction": identity["raw_direction"],
        "feature_snapshot_at_signal": signal.get("research_feature_snapshot") or {},
        "grouping_basis": identity["grouping_basis"], "collector_version": COLLECTOR_VERSION,
    })
    intent = store.append("order_intent", {
        "record_id": f"order-intent:{event_id}:paper-submit", "episode_id": identity["episode_id"], "event_id": event_id,
        "intent_kind": "ACTUAL_PAPER_LIMIT_SUBMIT", "submitted_ts": _first(order.get("created_ts"), order.get("order_created_ts")),
        "signal_price": _first(order.get("signal_price"), signal.get("signal_price")),
        "limit_price": _first(order.get("limit_price"), order.get("price")), "requested_qty": order.get("qty"),
        "executed_direction": identity["executed_direction"], "research_lane": _first(order.get("research_lane"), signal.get("research_lane")),
        "paper_only": bool(order.get("paper_only") or signal.get("paper_only")),
        "relay_eligible": bool(order.get("relay_eligible", signal.get("relay_eligible", False))),
        "chase_schedule": order.get("research_chase_schedule") or signal.get("research_chase_schedule") or {},
        "chase_schedule_authoritative": bool(order.get("chase_schedule_authoritative") or signal.get("chase_schedule_authoritative")),
        **policy,
    })
    lifecycle = store.append("lifecycle", {
        "record_id": f"lifecycle:{event_id}:paper-order-submitted", "episode_id": identity["episode_id"], "event_id": event_id,
        "observation_status": "PAPER_ORDER_SUBMITTED", "outcome_state": "PENDING_FILL", "terminal": False,
        "ranking_eligible": False, "ranking_blocker": "PATH_NOT_MATURED",
    })
    return {"schema": "v3_paper_order_intent_receipt_v1", "epoch_id": str(epoch_id), **identity,
            "writes": [opportunity, intent, lifecycle], "store_verification": store.verify()}


def dual_write_paper_fill(order: Mapping[str, Any], signal: Mapping[str, Any], position: Mapping[str, Any], *, epoch_id: str, data_dir: str) -> dict[str, Any]:
    """Write an observed paper fill once, without claiming exchange execution."""
    event_id = str(_first(position.get("trade_id"), order.get("trade_id"), signal.get("trade_id")) or "")
    identity = _causal_identity(event_id, signal, order, position)
    policy = _paper_policy_identity(str(epoch_id), signal, order, position)
    store = V3EvidenceStore(data_dir, epoch_id=str(epoch_id))
    execution = store.append("execution", {
        "record_id": f"execution:{event_id}:primary-fill", "episode_id": identity["episode_id"], "event_id": event_id,
        "execution_world": "SHOWCASE_PAPER_OBSERVED", "fill_ts": _first(position.get("entry_ts"), order.get("fill_ts")),
        "fill_price": _first(position.get("entry"), order.get("fill_price")), "filled_qty": _first(position.get("qty"), order.get("qty")),
        "requested_qty": order.get("qty"), "partial_fill": bool(order.get("partial_fill")),
        "fill_model": _first(position.get("fill_model"), order.get("fill_model")),
        "authenticated_exchange_actual": False, "paper_observation": True,
        "source_market_evidence_required_for_conservative_claim": True,
        **policy,
    })
    lifecycle = store.append("lifecycle", {
        "record_id": f"lifecycle:{event_id}:paper-filled", "episode_id": identity["episode_id"], "event_id": event_id,
        "observation_status": "PAPER_POSITION_OPEN", "outcome_state": "PARTIAL_FILL" if order.get("partial_fill") else "FULL_FILL",
        "terminal": False, "ranking_eligible": False, "ranking_blocker": "EXIT_PATH_NOT_MATURED",
    })
    return {"schema": "v3_paper_fill_receipt_v1", "epoch_id": str(epoch_id), **identity,
            "writes": [execution, lifecycle], "store_verification": store.verify()}


def dual_write_provisional_source(event_id: str, source: Mapping[str, Any], *, epoch_id: str, data_dir: str) -> dict[str, Any]:
    """Record the causal opportunity immediately, before its long path matures."""
    signal_ts = float(_first(source.get("created_ts_ts"), source.get("signal_ts"), 0) or 0)
    direction = str(_first(source.get("raw_direction"), source.get("final_direction"), "UNKNOWN")).upper()
    symbol = str(_first(source.get("symbol"), source.get("pair"), "BTCUSD")).upper()
    shared = str(source.get("shared_ai_call_id") or "").strip()
    stable_episode_id = str(source.get("event_episode_id") or "").strip()
    if not shared and not stable_episode_id:
        # Do not mint a fallback episode while the signal is still being
        # enriched.  A later upsert commonly supplies the shared AI identity;
        # writing now would leave an immutable orphan and inflate N.
        return {
            "schema": "v3_provisional_dual_write_receipt_v1",
            "event_id": str(event_id),
            "written": False,
            "deferred": True,
            "reason": "CAUSAL_IDENTITY_PENDING",
            "writes": [],
        }
    if shared:
        causal_key = f"shared:{symbol}:{direction}:{shared}"
        grouping_basis = "SHARED_AI_CALL"
        episode_id = "episode-" + hashlib.sha256(causal_key.encode("utf-8")).hexdigest()[:20]
    else:
        episode_id = stable_episode_id
        grouping_basis = "STABLE_EVENT_EPISODE"
    store = V3EvidenceStore(data_dir, epoch_id=str(epoch_id))
    opportunity = store.append("opportunity", {
        "record_id": f"opportunity:{episode_id}",
        "episode_id": episode_id,
        "shared_ai_call_id": shared or None,
        "signal_ts": signal_ts,
        "symbol": symbol,
        "raw_direction": direction,
        "feature_snapshot_at_signal": source.get("research_feature_snapshot") or {},
        "first_observed_as_provisional": True,
        "grouping_basis": grouping_basis,
        "collector_version": COLLECTOR_VERSION,
    })
    lifecycle = store.append("lifecycle", {
        "record_id": f"lifecycle:{event_id}:opened",
        "episode_id": episode_id,
        "event_id": str(event_id),
        "observation_status": str(source.get("observation_status") or "PENDING"),
        "outcome_state": "CENSORED",
        "terminal": False,
        "ranking_eligible": False,
        "ranking_blocker": "PATH_NOT_MATURED",
    })
    return {
        "schema": "v3_provisional_dual_write_receipt_v1",
        "event_id": str(event_id),
        "episode_id": episode_id,
        "writes": [opportunity, lifecycle],
        "store_verification": store.verify(),
    }


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
        # These are immutable policy-intent results, not duplicated market
        # paths. Keeping the exact fill/chase receipts lets the V3 analyzer
        # evaluate protection variants without rereading mutable v2 rows.
        "entry_children": record.get("entry_children") or [],
        "signal_price": _first(envelope.get("signal_price"), record.get("signal_price")),
        "executed_direction": _first(envelope.get("executed_direction"), record.get("direction")),
        "atr14_pct": _first(record.get("atr14_pct"), envelope.get("atr14_pct")),
        "leverage": _first((record.get("research_execution_basis") or {}).get("leverage"), (envelope.get("control_cell") or {}).get("leverage"), 100.0),
        "margin_usd": _first((record.get("research_execution_basis") or {}).get("margin_usd"), (envelope.get("control_cell") or {}).get("margin_usd"), 20.0),
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
        "terminal": True,
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
