"""Versioned, bounded search design for static and dynamic BTC policies.

The collector stores one immutable market path and a compact manifest receipt.
It does not materialize the full Cartesian product. The analyzer screens axes,
zooms stable neighborhoods, and validates frozen candidates chronologically.
"""
from __future__ import annotations

import hashlib
import json
from math import prod

from chase_offset_touch_grid import CHASE_POLICIES, OFFSET_PCT_GRID
from research_entry_baselines import (
    CHASE_WINDOW_BUCKETS,
    CHASE_WINDOW_SECONDS,
    ENTRY_BASELINE_REGISTRY,
)

SEARCH_MANIFEST_SCHEMA = "policy_search_manifest_v1"
SEARCH_MANIFEST_VERSION = "complete_static_dynamic_v1_20260820"

ENTRY_TTL_MIN = (3, 5, 8, 10, 15, 20, 30, 45, 60)
THESIS_CUT_MARGIN_PCT = (-4, -8, -10, -12, -15, -18, -20, -25, -30, -40, -50, -75, -100)
THESIS_WINDOW_SEC = (60, 180, 300, 600)
HARD_STOP_MARGIN_PCT = (10, 12, 13, 15, 18, 20, 25, 30, 40, 50, 75, 100)
ATR_MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)
TIME_STOP_MIN = (5, 10, 15, 20, 30, 45, 60, 90, 120)
FILL_MODELS = ("IDEAL_TOUCH", "CONSERVATIVE_BBO_DEPTH_TAPE", "AUTHENTICATED_ACTUAL")
PARTIAL_FILL_POLICIES = ("FULL_ONLY", "KEEP_PARTIAL_PROTECTED", "CANCEL_REMAINDER")
LADDERS = {
    "none": (),
    "legacy_4_2": ((4, 2),),
    "scenario_c_8": ((8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120)),
    "early_tight": ((2, 0.5), (3, 1), (4, 2), (5, 3), (8, 6), (12, 10)),
    "early_loose": ((4, 1), (6, 2), (10, 5), (15, 9), (25, 17)),
    "high_capture": ((4, 3), (6, 5), (10, 8), (15, 12), (25, 20), (40, 32)),
    "runner_friendly": ((5, 1), (8, 3), (12, 6), (20, 12), (40, 25), (80, 55)),
}

CAUSAL_REGIME_FEATURES = (
    "direction", "session", "adx_bucket", "adx_slope_bucket", "atr_percentile_bucket",
    "volume_percentile_bucket", "spread_bucket", "liquidity_bucket", "funding_bucket",
    "open_interest_change_bucket", "ema_alignment", "multi_tf_agreement", "structure_bias",
    "support_resistance_location", "rsi_bucket", "stoch_rsi_bucket", "delta_bucket",
    "imbalance_bucket", "reversal_risk_bucket",
)
INDICATOR_FAMILIES = (
    "ADX", "ADX_SLOPE", "ATR", "RSI", "STOCH_RSI", "VOLUME", "DELTA", "IMBALANCE",
    "EMA_ALIGNMENT", "MULTI_TF", "STRUCTURE", "SUPPORT_RESISTANCE", "FUNDING",
    "OPEN_INTEREST", "SPREAD", "LIQUIDITY", "REVERSAL_RISK", "ENTRY_AGE",
)


def build_policy_search_manifest() -> dict:
    dimensions = {
        "entry_offset_pct": list(OFFSET_PCT_GRID),
        "entry_ttl_min": list(ENTRY_TTL_MIN),
        "chase_policy_id": [str(row["id"]) for row in CHASE_POLICIES],
        "thesis_cut_margin_pct": list(THESIS_CUT_MARGIN_PCT),
        "thesis_window_sec": list(THESIS_WINDOW_SEC),
        "hard_stop_margin_pct": list(HARD_STOP_MARGIN_PCT),
        "atr_multiplier": list(ATR_MULTIPLIERS),
        "time_stop_min": list(TIME_STOP_MIN),
        "ladder_id": list(LADDERS),
        "fill_model": list(FILL_MODELS),
        "partial_fill_policy": list(PARTIAL_FILL_POLICIES),
    }
    counts = {name: len(values) for name, values in dimensions.items()}
    entry_count = counts["entry_offset_pct"] * counts["entry_ttl_min"] * counts["chase_policy_id"]
    exit_count = prod(counts[name] for name in (
        "thesis_cut_margin_pct", "thesis_window_sec", "hard_stop_margin_pct",
        "atr_multiplier", "time_stop_min", "ladder_id",
    ))
    execution_count = counts["fill_model"] * counts["partial_fill_policy"]
    material = {
        "schema": SEARCH_MANIFEST_SCHEMA,
        "version": SEARCH_MANIFEST_VERSION,
        "dimensions": dimensions,
        "ladders": {name: [list(rung) for rung in rungs] for name, rungs in LADDERS.items()},
        "indicator_families": list(INDICATOR_FAMILIES),
        "causal_regime_features": list(CAUSAL_REGIME_FEATURES),
        "entry_baseline_registry": ENTRY_BASELINE_REGISTRY,
        "entry_treatment_axes": {
            "execution_mode": ["MARKET_AT_SIGNAL", "LIMIT"],
            "chase_mode": ["NO_CHASE", "CHASE_13_MIN_COMPRESSED", "CHASE_30_MIN_LEGACY"],
            "chase_window_bucket": list(CHASE_WINDOW_BUCKETS),
            "chase_window_bucket_seconds": CHASE_WINDOW_SECONDS,
            "expiry_action": ["EXPIRE_UNFILLED", "FINAL_MARKET_AFTER_EXPIRY"],
            "execution_class": "RESEARCH_ONLY",
            "missing_evidence_outcome": "UNKNOWN",
        },
        "search_protocol": {
            "stage_1": "independent-axis screening on chronological training episodes",
            "stage_2": "Cartesian zoom only inside stable positive neighborhoods",
            "stage_3": "freeze static and dynamic candidates before untouched OOS",
            "stage_4": "episode-block bootstrap, drawdown, costs, execution calibration, multiple-testing control",
            "dynamic_policy": "causal regime classifier plus frozen regime-policy map and CONTROL/NO_TRADE fallback",
            "activation": "shadow challenger only; never auto-activate from in-sample replay",
        },
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    material["signature"] = "search-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    material["counts"] = {
        **counts,
        "entry_policy_cartesian": entry_count,
        "exit_policy_cartesian": exit_count,
        "execution_model_cartesian": execution_count,
        "naive_full_cartesian": entry_count * exit_count * execution_count,
        "materialization_note": "Do not persist this Cartesian product; replay stored paths hierarchically.",
    }
    return material


POLICY_SEARCH_MANIFEST = build_policy_search_manifest()


def compact_search_receipt() -> dict:
    return {
        "schema": SEARCH_MANIFEST_SCHEMA,
        "version": SEARCH_MANIFEST_VERSION,
        "signature": POLICY_SEARCH_MANIFEST["signature"],
        "counts": dict(POLICY_SEARCH_MANIFEST["counts"]),
        "indicator_families": list(INDICATOR_FAMILIES),
        "causal_regime_features": list(CAUSAL_REGIME_FEATURES),
        "entry_baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "entry_baseline_count": len(ENTRY_BASELINE_REGISTRY["baselines"]),
        "chase_window_buckets": list(CHASE_WINDOW_BUCKETS),
        "chase_window_bucket_seconds": CHASE_WINDOW_SECONDS,
    }
