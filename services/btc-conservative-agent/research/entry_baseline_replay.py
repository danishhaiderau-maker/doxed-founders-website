"""Identical-opportunity conservative replay for signed entry baselines.

This module is research-only.  It accepts already-bound episode evidence and
never derives a fill from a candle, signal price, or ideal touch.  Every
baseline is evaluated by the same quantity-aware public-tape evaluator.  A
missing or mismatched identity, schedule, BBO/depth row, quantity constraint,
latency, or fee input produces UNKNOWN rather than NO_FILL.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

try:
    from .conservative_limit_fill import evaluate_limit_fill
except ImportError:  # direct script/test execution
    from conservative_limit_fill import evaluate_limit_fill

from research_entry_baselines import (
    ENTRY_BASELINE_REGISTRY,
    missing_baseline_evidence,
)
from research_v3_contract import canonical_hash


REPLAY_SCHEMA = "entry_baseline_same_opportunity_replay_v1"
EPISODE_RECEIPT_SCHEMA = "entry_baseline_episode_receipt_v1"


def _baseline_rows() -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in ENTRY_BASELINE_REGISTRY["baselines"])


def _unknown(
    baseline: Mapping[str, Any], episode: Mapping[str, Any], *codes: str,
) -> dict[str, Any]:
    return {
        "baseline_id": baseline["baseline_id"],
        "policy_signature": baseline["policy_signature"],
        "episode_id": episode.get("episode_id"),
        "opportunity_id": episode.get("opportunity_id"),
        "outcome_state": "UNKNOWN",
        "supported": False,
        "rejection_codes": list(dict.fromkeys(str(code) for code in codes if code)),
        "conservative_receipt": None,
    }


def _schedule_envelope(
    baseline: Mapping[str, Any], episode: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, list[str]]:
    schedules = episode.get("baseline_schedules")
    if not isinstance(schedules, Mapping):
        return None, ["BASELINE_SCHEDULES_MISSING"]
    envelope = schedules.get(baseline["baseline_id"])
    if not isinstance(envelope, Mapping):
        return None, ["BASELINE_SCHEDULE_MISSING"]
    failures = []
    for key in ("episode_id", "opportunity_id", "policy_signature"):
        expected = baseline["policy_signature"] if key == "policy_signature" else episode.get(key)
        if not expected or str(envelope.get(key) or "") != str(expected):
            failures.append(f"BASELINE_SCHEDULE_{key.upper()}_MISMATCH")
    schedule = envelope.get("schedule")
    if not isinstance(schedule, list) or not schedule:
        failures.append("BASELINE_SCHEDULE_MISSING_OR_EMPTY")
    return envelope, failures


def _rows_cover_timestamp(rows: list[Mapping[str, Any]], timestamp: Any) -> bool:
    try:
        expected = int(float(timestamp))
    except (TypeError, ValueError):
        return False
    return any(
        row.get("schema") == "market_microstructure_1s_v1"
        and row.get("fresh") is True
        and row.get("valid_bbo") is True
        and int(float(row.get("bucket_ts"))) == expected
        for row in rows
        if row.get("bucket_ts") is not None
    )


def _market_schedule_failures(
    baseline: Mapping[str, Any], episode: Mapping[str, Any],
    envelope: Mapping[str, Any], rows: list[Mapping[str, Any]],
) -> list[str]:
    baseline_id = baseline["baseline_id"]
    if baseline_id not in {"MARKET_ENTRY_AT_SIGNAL", "FINAL_MARKET_AFTER_EXPIRY"}:
        return []
    timestamp = (
        episode.get("signal_ts") if baseline_id == "MARKET_ENTRY_AT_SIGNAL"
        else episode.get("expiry_ts")
    )
    try:
        expected_ts = int(float(timestamp))
    except (TypeError, ValueError):
        return ["MARKET_ENTRY_TIMESTAMP_MISSING"]
    schedule = envelope.get("schedule") or []
    if len(schedule) != 1:
        return ["MARKET_ENTRY_REQUIRES_ONE_SECOND_SCHEDULE"]
    interval = schedule[0]
    try:
        if int(interval.get("start_ts")) != expected_ts or int(interval.get("end_ts")) != expected_ts + 1:
            return ["MARKET_ENTRY_SCHEDULE_TIMESTAMP_MISMATCH"]
        limit = float(interval.get("limit_price"))
    except (TypeError, ValueError):
        return ["MARKET_ENTRY_SCHEDULE_INVALID"]
    row = None
    for candidate in rows:
        try:
            candidate_ts = int(float(candidate.get("bucket_ts")))
        except (TypeError, ValueError):
            continue
        if candidate_ts == expected_ts:
            row = candidate
            break
    if row is None:
        return []  # the evidence gate emits the more precise missing-BBO code
    quote_key = "ask" if str(episode.get("direction")).upper() == "LONG" else "bid"
    try:
        quote = float(row.get(quote_key))
    except (TypeError, ValueError):
        return ["MARKET_ENTRY_SIDE_QUOTE_MISSING"]
    if limit != quote:
        return ["MARKET_ENTRY_LIMIT_NOT_BOUND_TO_SIDE_QUOTE"]
    return []


def _evidence_projection(
    baseline: Mapping[str, Any], episode: Mapping[str, Any],
    envelope: Mapping[str, Any], rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    constraints = episode.get("signed_quantity_constraints")
    qty = episode.get("requested_qty")
    remaining = episode.get("requested_remaining_qty")
    schedule = envelope.get("schedule") or []
    expiry_ts = episode.get("expiry_ts")
    return {
        "signal_time_bbo": _rows_cover_timestamp(rows, episode.get("signal_ts")),
        "expiry_time_bbo": _rows_cover_timestamp(rows, expiry_ts),
        "executable_depth": bool(rows) and all(
            row.get("ask_qty") is not None and row.get("bid_qty") is not None for row in rows
        ),
        "bbo_depth_trade_tape": rows,
        "requested_quantity": qty,
        "requested_remaining_quantity": remaining,
        "venue_quantity_constraints": constraints,
        "latency": episode.get("latency_sec"),
        "fees": episode.get("fees_usd"),
        "slippage": episode.get("slippage_model"),
        "signed_stage_receipts": envelope if schedule else None,
        "authoritative_final_schedule": envelope if schedule else None,
        "authoritative_parent_expiry": episode.get("authoritative_parent_expiry"),
    }


def replay_episode(episode: Mapping[str, Any]) -> dict[str, Any]:
    """Replay all registered baselines against one causal opportunity."""
    episode_id = str(episode.get("episode_id") or "")
    opportunity_id = str(episode.get("opportunity_id") or "")
    if not episode_id or not opportunity_id:
        raise ValueError("EPISODE_AND_OPPORTUNITY_ID_REQUIRED")
    rows = [dict(row) for row in (episode.get("market_microstructure_rows") or []) if isinstance(row, Mapping)]
    canonical_identity_failures = [
        f"MISSING_{name.upper()}"
        for name in (
            "dataset_epoch", "source_revision", "tile_config_signature",
            "direction", "symbol",
        )
        if not str(episode.get(name) or "").strip()
    ]
    results = []
    for baseline in _baseline_rows():
        if canonical_identity_failures:
            results.append(_unknown(baseline, episode, *canonical_identity_failures))
            continue
        envelope, schedule_failures = _schedule_envelope(baseline, episode)
        if envelope is None or schedule_failures:
            results.append(_unknown(baseline, episode, *schedule_failures))
            continue
        market_failures = _market_schedule_failures(baseline, episode, envelope, rows)
        if market_failures:
            results.append(_unknown(baseline, episode, *market_failures))
            continue
        evidence = _evidence_projection(baseline, episode, envelope, rows)
        gate = missing_baseline_evidence(baseline["baseline_id"], evidence)
        if not gate["complete"]:
            results.append(_unknown(baseline, episode, *gate["rejection_codes"]))
            continue
        requested_qty = (
            episode.get("requested_remaining_qty")
            if baseline["baseline_id"] == "FINAL_MARKET_AFTER_EXPIRY"
            else episode.get("requested_qty")
        )
        receipt = evaluate_limit_fill(
            rows,
            direction=str(episode.get("direction") or "UNKNOWN"),
            requested_qty=requested_qty,
            chase_schedule=envelope["schedule"],
            # Each one-second bucket is independently executable from its
            # fresh opposite BBO.  A wider aggressor context would cross a
            # reprice boundary for one-second market-entry schedules and turn
            # exact BBO evidence into artificial interval ambiguity.
            aggressor_window_sec=1,
            symbol=str(episode.get("symbol") or ""),
            quantity_constraints=episode.get("signed_quantity_constraints"),
        )
        receipt["declared_fees_usd"] = episode.get("fees_usd")
        receipt["measured_input_latency_sec"] = episode.get("latency_sec")
        receipt["declared_slippage_model"] = episode.get("slippage_model")
        terminal = str(receipt.get("final_classification") or "").upper()
        if receipt.get("supported") is not True or terminal not in {
            "FULL_FILL", "PARTIAL_FILL", "NO_FILL",
        }:
            results.append(_unknown(
                baseline, episode,
                *(receipt.get("negative_reasons") or ["CONSERVATIVE_EVALUATOR_UNSUPPORTED"]),
            ))
            continue
        results.append({
            "baseline_id": baseline["baseline_id"],
            "policy_signature": baseline["policy_signature"],
            "episode_id": episode_id,
            "opportunity_id": opportunity_id,
            "outcome_state": terminal,
            "supported": True,
            "rejection_codes": [],
            "conservative_receipt": receipt,
        })
    material = {
        "schema": EPISODE_RECEIPT_SCHEMA,
        "episode_id": episode_id,
        "opportunity_id": opportunity_id,
        "dataset_epoch": episode.get("dataset_epoch"),
        "source_revision": episode.get("source_revision"),
        "tile_config_signature": episode.get("tile_config_signature"),
        "baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "results": results,
    }
    material["receipt_id"] = canonical_hash("entry-baseline-episode", material)
    return material


def materialize_same_opportunity_replay(
    episodes: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return deterministic episode receipts plus comparable outcome counts."""
    receipts = [replay_episode(episode) for episode in episodes]
    receipts.sort(key=lambda row: (str(row["opportunity_id"]), str(row["episode_id"])))
    expected = [row["baseline_id"] for row in _baseline_rows()]
    summaries = {}
    for baseline_id in expected:
        states = Counter(
            result["outcome_state"]
            for receipt in receipts for result in receipt["results"]
            if result["baseline_id"] == baseline_id
        )
        summaries[baseline_id] = {
            "opportunities": len(receipts),
            "full_fills": states["FULL_FILL"],
            "partial_fills": states["PARTIAL_FILL"],
            "no_fills": states["NO_FILL"],
            "unknown": states["UNKNOWN"],
        }
    material = {
        "schema": REPLAY_SCHEMA,
        "baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "same_opportunity_count": len(receipts),
        "baseline_ids": expected,
        "summaries": summaries,
        "episode_receipts": receipts,
    }
    material["report_id"] = canonical_hash("entry-baseline-replay", material)
    return material
