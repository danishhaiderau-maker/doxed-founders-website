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
)
from research_v3_contract import canonical_hash


ENTRY_BASELINE_SCHEMA = "research_entry_baseline_registry_v1"


_BASELINES = (
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


def build_entry_baseline_registry() -> dict[str, Any]:
    rows = []
    for source in _BASELINES:
        row = deepcopy(source)
        row["execution_class"] = "RESEARCH_ONLY"
        row["relay_eligible"] = False
        row["missing_evidence_outcome"] = "UNKNOWN"
        row["policy_signature"] = canonical_hash("entry-baseline", row)
        rows.append(row)
    material = {"schema": ENTRY_BASELINE_SCHEMA, "baselines": rows}
    material["registry_signature"] = canonical_hash("entry-baselines", material)
    return material


ENTRY_BASELINE_REGISTRY = build_entry_baseline_registry()


def classify_baseline_evidence(
    baseline_id: str, evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fail closed when required evidence for a baseline is absent.

    This intentionally does not calculate fills.  The conservative replay
    engine may supply a terminal classification only after it has consumed the
    signed evidence named by the baseline.
    """
    baseline = next(
        (row for row in ENTRY_BASELINE_REGISTRY["baselines"]
         if row["baseline_id"] == str(baseline_id)),
        None,
    )
    if baseline is None:
        raise KeyError(f"UNKNOWN_ENTRY_BASELINE:{baseline_id}")
    supplied = evidence if isinstance(evidence, Mapping) else {}
    missing = [name for name in baseline["required_evidence"] if not supplied.get(name)]
    if missing:
        return {
            "baseline_id": baseline["baseline_id"],
            "policy_signature": baseline["policy_signature"],
            "outcome_state": "UNKNOWN",
            "supported": False,
            "rejection_codes": [f"MISSING_{name.upper()}" for name in missing],
        }
    terminal = str(supplied.get("terminal_outcome") or "").upper()
    if terminal not in {"FULL_FILL", "PARTIAL_FILL", "NO_FILL"}:
        return {
            "baseline_id": baseline["baseline_id"],
            "policy_signature": baseline["policy_signature"],
            "outcome_state": "UNKNOWN",
            "supported": False,
            "rejection_codes": ["CONSERVATIVE_TERMINAL_OUTCOME_MISSING"],
        }
    return {
        "baseline_id": baseline["baseline_id"],
        "policy_signature": baseline["policy_signature"],
        "outcome_state": terminal,
        "supported": True,
        "rejection_codes": [],
    }
