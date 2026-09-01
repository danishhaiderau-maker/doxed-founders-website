"""Signed research-only entry baselines and their evidence gates.

These specifications are comparison identities, not executable order settings.
They deliberately do not synthesize outcomes from a signal price or candle
touch.  A baseline remains UNKNOWN until its required exchange evidence is
bound to the same causal episode.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from chase_offset_touch_grid import (
    COMPRESSED_SHADOW_EXPIRY_SEC,
    COMPRESSED_SHADOW_INITIAL_OFFSET_PCT,
    COMPRESSED_SHADOW_POLICY_ID,
    COMPRESSED_SHADOW_STAGE_SECONDS,
    COMPRESSED_SHADOW_STEP_PCT,
    orig_limit_price,
    _chase_target,
)
from research_v3_contract import canonical_hash


ENTRY_BASELINE_SCHEMA = "research_entry_baseline_registry_v1"
CHASE_WINDOW_BUCKETS = tuple(range(6))
CHASE_WINDOW_SECONDS = 300


_CORE_BASELINES = (
    {
        "baseline_id": "MARKET_ENTRY_AT_SIGNAL",
        "entry_type": "MARKET_ENTRY",
        "timing": "SIGNAL_TIME",
        "places_order": False,
        "required_evidence": (
            "signal_time_bbo", "executable_depth", "requested_quantity",
            "venue_quantity_constraints", "latency", "fees", "slippage",
        ),
    },
    {
        "baseline_id": "NO_CHASE_LIMIT",
        "entry_type": "LIMIT",
        "timing": "SIGNAL_TIME_TO_EXPIRY",
        "offset_axis_pct": "0.01_TO_0.30",
        "initial_offset_pct": 0.10,
        "chase_policy_id": "no_chase",
        "terminal_expiry_sec": 1800,
        "places_order": False,
        "required_evidence": (
            "signal_time_bbo", "bbo_depth_trade_tape", "requested_quantity",
            "venue_quantity_constraints", "latency", "fees",
        ),
    },
    {
        "baseline_id": "CHASE_13_MIN_COMPRESSED",
        "entry_type": "LIMIT_CHASE",
        "timing": "SIGNAL_TIME_TO_EXPIRY",
        "initial_offset_pct": COMPRESSED_SHADOW_INITIAL_OFFSET_PCT,
        "stage_seconds": COMPRESSED_SHADOW_STAGE_SECONDS,
        "remaining_gap_step_fraction": COMPRESSED_SHADOW_STEP_PCT,
        "terminal_expiry_sec": COMPRESSED_SHADOW_EXPIRY_SEC,
        "source_policy_id": COMPRESSED_SHADOW_POLICY_ID,
        "places_order": False,
        "required_evidence": (
            "signed_stage_receipts", "bbo_depth_trade_tape", "requested_quantity",
            "venue_quantity_constraints", "latency", "fees",
        ),
    },
    {
        "baseline_id": "CHASE_30_MIN_LEGACY",
        "entry_type": "LIMIT_CHASE",
        "timing": "SIGNAL_TIME_TO_EXPIRY",
        "initial_offset_pct": 0.10,
        "chase_window_buckets": (2, 3, 4),
        "bucket_seconds": 300,
        "reprice_interval_sec": 180,
        "remaining_gap_step_fraction": 0.50,
        "terminal_expiry_sec": 1800,
        "source_entry_policy_id": "OFFSET_0.10_CHASE_w234_s50_i180",
        "places_order": False,
        "required_evidence": (
            "authoritative_final_schedule", "bbo_depth_trade_tape", "requested_quantity",
            "venue_quantity_constraints", "latency", "fees",
        ),
    },
    {
        "baseline_id": "FINAL_MARKET_AFTER_EXPIRY",
        "entry_type": "FINAL_MARKET_AFTER_EXPIRY",
        "timing": "LIMIT_EXPIRY",
        "parent_entry_required": True,
        "places_order": False,
        "required_evidence": (
            "authoritative_parent_expiry", "expiry_time_bbo", "executable_depth",
            "requested_remaining_quantity", "venue_quantity_constraints", "latency",
            "fees", "slippage",
        ),
    },
)


_CHASE_WINDOW_BASELINES = tuple(
    {
        "baseline_id": f"CHASE_WINDOW_{bucket}",
        "entry_type": "LIMIT_CHASE_WINDOW",
        "timing": f"SIGNAL_PLUS_{bucket * CHASE_WINDOW_SECONDS}_TO_{(bucket + 1) * CHASE_WINDOW_SECONDS}_SEC",
        "chase_window_bucket": bucket,
        "window_start_sec": bucket * CHASE_WINDOW_SECONDS,
        "window_end_sec": (bucket + 1) * CHASE_WINDOW_SECONDS,
        "terminal_expiry_sec": 1800,
        "places_order": False,
        "required_evidence": (
            "signed_stage_receipts", "bbo_depth_trade_tape", "requested_quantity",
            "venue_quantity_constraints", "latency", "fees",
        ),
    }
    for bucket in CHASE_WINDOW_BUCKETS
)


_BASELINES = _CORE_BASELINES + _CHASE_WINDOW_BASELINES


def build_entry_baseline_registry() -> dict[str, Any]:
    rows = []
    for source in _BASELINES:
        row = deepcopy(source)
        row["execution_class"] = "RESEARCH_ONLY"
        row["relay_eligible"] = False
        row["missing_evidence_outcome"] = "UNKNOWN"
        row["policy_signature"] = canonical_hash("entry-baseline", row)
        rows.append(row)
    material = {
        "schema": ENTRY_BASELINE_SCHEMA,
        "chase_window_axis": {
            "bucket_seconds": CHASE_WINDOW_SECONDS,
            "buckets": list(CHASE_WINDOW_BUCKETS),
            "coverage": "EXPLICIT_RESEARCH_TREATMENTS",
        },
        "baselines": rows,
    }
    material["registry_signature"] = canonical_hash("entry-baselines", material)
    return material


ENTRY_BASELINE_REGISTRY = build_entry_baseline_registry()


def materialize_signal_time_baseline_schedules(
    opportunity: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the baseline schedule evidence available at signal time.

    This is deliberately not a schedule synthesizer.  Producers may supply an
    already-observed/signed schedule envelope; absent schedule evidence is
    persisted as UNKNOWN so a later analyzer cannot reconstruct a favourable
    treatment from post-signal prices.
    """
    episode_id = str(opportunity.get("episode_id") or "").strip()
    opportunity_id = str(
        opportunity.get("opportunity_id") or opportunity.get("record_id") or ""
    ).strip()
    supplied = opportunity.get("baseline_schedules")
    supplied = supplied if isinstance(supplied, Mapping) else {}

    def first(*values: Any) -> Any:
        return next((value for value in values if value not in (None, "")), None)

    features = opportunity.get("feature_snapshot_at_signal")
    features = features if isinstance(features, Mapping) else {}
    source_features = features.get("source_features")
    source_features = source_features if isinstance(source_features, Mapping) else {}
    market_context = features.get("market_context")
    market_context = market_context if isinstance(market_context, Mapping) else {}
    explicit_bbo = opportunity.get("signal_time_bbo")
    explicit_bbo = explicit_bbo if isinstance(explicit_bbo, Mapping) else {}
    bbo = first(
        explicit_bbo,
        features.get("signal_time_bbo"), features.get("bbo"),
        source_features.get("signal_time_bbo"), source_features.get("bbo"),
        market_context.get("signal_time_bbo"), market_context.get("bbo"),
    )
    if not isinstance(bbo, Mapping) or not bbo:
        bbo = features
    bbo = bbo if isinstance(bbo, Mapping) else {}
    try:
        signal_ts = int(float(opportunity.get("signal_ts")))
        direction = str(opportunity.get("direction") or opportunity.get("raw_direction") or "").upper()
        bid = float(first(bbo.get("bid"), bbo.get("best_bid")))
        ask = float(first(bbo.get("ask"), bbo.get("best_ask")))
        bid_qty = float(first(bbo.get("bid_qty"), bbo.get("best_bid_qty")))
        ask_qty = float(first(bbo.get("ask_qty"), bbo.get("best_ask_qty")))
        side_quote = ask if direction == "LONG" else bid
        reference = float(first(
            opportunity.get("signal_price"), features.get("signal_price"),
            source_features.get("signal_price"), market_context.get("signal_price"),
            side_quote,
        ))
        inputs_complete = (
            signal_ts > 0 and direction in {"LONG", "SHORT"}
            and bid > 0 and ask > 0 and bid <= ask
            and bid_qty > 0 and ask_qty > 0 and reference > 0
        )
    except (TypeError, ValueError):
        inputs_complete = False
        signal_ts = 0
        direction = "UNKNOWN"
        side_quote = reference = 0.0

    def offset_limit(offset_pct: float) -> float:
        return orig_limit_price(reference, direction, float(offset_pct))

    def interval(start: int, end: int, limit: float, index: int) -> dict[str, Any]:
        return {
            "bucket_id": f"signal-schedule:{index}", "start_ts": start,
            "end_ts": end, "limit_price": limit, "generation": index,
            "reference_basis": "PRE_SIGNAL_REFERENCE_AND_BBO_ONLY",
        }

    def signed_schedule(baseline: Mapping[str, Any]) -> list[dict[str, Any]]:
        baseline_id = baseline["baseline_id"]
        if baseline_id == "FINAL_MARKET_AFTER_EXPIRY":
            return []  # expiry BBO is unknowable at signal time
        if baseline_id == "MARKET_ENTRY_AT_SIGNAL":
            return [interval(signal_ts, signal_ts + 1, side_quote, 0)]
        if baseline.get("entry_type") == "LIMIT_CHASE_WINDOW":
            start = signal_ts + int(baseline["window_start_sec"])
            end = signal_ts + int(baseline["window_end_sec"])
            return [interval(start, end, offset_limit(0.10), 0)]
        expiry = signal_ts + int(baseline.get("terminal_expiry_sec") or 1800)
        limit = offset_limit(float(baseline.get("initial_offset_pct") or 0.10))
        if baseline_id == "NO_CHASE_LIMIT":
            return [interval(signal_ts, expiry, limit, 0)]
        stage_times = baseline.get("stage_seconds")
        if stage_times:
            starts = [signal_ts + int(value) for value in stage_times]
            step = float(baseline.get("remaining_gap_step_fraction") or 0.25)
        else:
            every = int(baseline.get("reprice_interval_sec") or 180)
            windows = set(int(value) for value in (baseline.get("chase_window_buckets") or ()))
            starts = [signal_ts] + [
                ts for ts in range(signal_ts + every, expiry, every)
                if ((ts - signal_ts) // CHASE_WINDOW_SECONDS) in windows
            ]
            step = float(baseline.get("remaining_gap_step_fraction") or 0.50)
        rows = []
        for index, start in enumerate(starts):
            if index:
                limit = _chase_target(direction, limit, reference, step)
            end = starts[index + 1] if index + 1 < len(starts) else expiry
            rows.append(interval(start, end, limit, index))
        return rows
    captured = {}
    for baseline in ENTRY_BASELINE_REGISTRY["baselines"]:
        baseline_id = baseline["baseline_id"]
        candidate = supplied.get(baseline_id)
        if isinstance(candidate, Mapping):
            envelope = deepcopy(dict(candidate))
            envelope.setdefault("episode_id", episode_id)
            envelope.setdefault("opportunity_id", opportunity_id)
            envelope.setdefault("policy_signature", baseline["policy_signature"])
            envelope.setdefault("capture_status", "CAPTURED_AT_SIGNAL")
        elif inputs_complete:
            schedule = signed_schedule(baseline)
            if schedule:
                envelope = {
                    "episode_id": episode_id,
                    "opportunity_id": opportunity_id,
                    "policy_signature": baseline["policy_signature"],
                    "schedule": schedule,
                    "capture_status": "CAPTURED_AT_SIGNAL",
                    "capture_basis": "PRE_SIGNAL_REFERENCE_AND_BBO_ONLY",
                }
            else:
                envelope = {
                    "episode_id": episode_id, "opportunity_id": opportunity_id,
                    "policy_signature": baseline["policy_signature"], "schedule": [],
                    "capture_status": "UNKNOWN_FUTURE_BBO_REQUIRED",
                    "rejection_codes": ["EXPIRY_BBO_UNAVAILABLE_AT_SIGNAL_TIME"],
                }
        else:
            envelope = {
                "episode_id": episode_id,
                "opportunity_id": opportunity_id,
                "policy_signature": baseline["policy_signature"],
                "schedule": [],
                "capture_status": "UNKNOWN_NOT_CAPTURED_AT_SIGNAL",
                "rejection_codes": ["BASELINE_SCHEDULE_NOT_CAPTURED_AT_SIGNAL"],
            }
        captured[baseline_id] = envelope
    return {
        "schema": "entry_baseline_signal_schedule_snapshot_v1",
        "signal_ts": opportunity.get("signal_ts"),
        "baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "schedules": captured,
    }


def _evidence_present(value: Any) -> bool:
    """Treat explicit zero-valued cost/latency evidence as present."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list, dict, set)):
        return bool(value)
    return True


def missing_baseline_evidence(
    baseline_id: str, evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return only the signed-input gate, before terminal replay exists."""
    baseline = next(
        (row for row in ENTRY_BASELINE_REGISTRY["baselines"]
         if row["baseline_id"] == str(baseline_id)),
        None,
    )
    if baseline is None:
        raise KeyError(f"UNKNOWN_ENTRY_BASELINE:{baseline_id}")
    supplied = evidence if isinstance(evidence, Mapping) else {}
    missing = [
        name for name in baseline["required_evidence"]
        if not _evidence_present(supplied.get(name))
    ]
    return {
        "baseline_id": baseline["baseline_id"],
        "policy_signature": baseline["policy_signature"],
        "complete": not missing,
        "missing_evidence": missing,
        "rejection_codes": [f"MISSING_{name.upper()}" for name in missing],
    }


def classify_baseline_evidence(
    baseline_id: str, evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fail closed when required evidence for a baseline is absent.

    This intentionally does not calculate fills.  The conservative replay
    engine may supply a terminal classification only after it has consumed the
    signed evidence named by the baseline.
    """
    supplied = evidence if isinstance(evidence, Mapping) else {}
    gate = missing_baseline_evidence(baseline_id, supplied)
    if gate["missing_evidence"]:
        return {
            "baseline_id": gate["baseline_id"],
            "policy_signature": gate["policy_signature"],
            "outcome_state": "UNKNOWN",
            "supported": False,
            "rejection_codes": gate["rejection_codes"],
        }
    terminal = str(supplied.get("terminal_outcome") or "").upper()
    if terminal not in {"FULL_FILL", "PARTIAL_FILL", "NO_FILL"}:
        return {
            "baseline_id": gate["baseline_id"],
            "policy_signature": gate["policy_signature"],
            "outcome_state": "UNKNOWN",
            "supported": False,
            "rejection_codes": ["CONSERVATIVE_TERMINAL_OUTCOME_MISSING"],
        }
    return {
        "baseline_id": gate["baseline_id"],
        "policy_signature": gate["policy_signature"],
        "outcome_state": terminal,
        "supported": True,
        "rejection_codes": [],
    }
