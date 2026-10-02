"""Canonical tile registry for the active paper-research architecture.

Adding or retiring a tile starts here. Runtime, API, dashboards, analyzer and
monitoring consume this registry (or the roster derived from it); they must not
maintain an independent list of active tiles. Policy-specific implementation
code may still live in its own module, but its lifecycle metadata and ownership
surfaces are declared here so retirement can be audited instead of merely
hiding a card.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any
# Startup-only, explicit research treatment. A restart changes policy identity;
# this is not a mutable per-request switch or permission to relay live orders.
SCORE_LED_PAPER_RESEARCH_ENABLED = os.getenv("SCORE_LED_PAPER_RESEARCH_ENABLED", "") == "1"
SCORE_LED_ADMISSION_POLICY_ID = "SCORE_LED_NON_TIE_PAPER_V2"
SCORE_LED_ADMISSION_SCHEMA = "score_led_paper_admission_v2"


def _bounded_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Bound arbitrary-size Python integers before converting them to float;
    # float(10**400), for example, raises OverflowError instead of returning
    # an out-of-range value that the finite check can reject.
    if value < 0 or value > 100:
        return None
    try:
        score = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(score) or score < 0.0 or score > 100.0:
        return None
    return score


def resolve_score_led_paper_admission(
    ai: dict | None,
    *,
    score_led_enabled: bool,
    research_mode: bool,
    forced_paper: bool,
    live_armed: bool,
    bitfinex_live_enabled: bool,
) -> dict:
    """Choose the stronger valid score only for the disarmed paper cohort.

    applied=False means callers must preserve the pre-existing admission
    behavior. An applied rejection is fail-closed for invalid or tied scores.
    The input mapping is never mutated.
    """
    result = {
        "schema": SCORE_LED_ADMISSION_SCHEMA,
        "policy_id": SCORE_LED_ADMISSION_POLICY_ID,
        "applied": False,
        "accepted": False,
        "effective_direction": None,
        "reason": "SCORE_LED_TREATMENT_INACTIVE",
        "long_score": None,
        "short_score": None,
        "score_gap": None,
    }
    if not (
        score_led_enabled
        and research_mode
        and forced_paper
        and not live_armed
        and not bitfinex_live_enabled
    ):
        return result

    result["applied"] = True
    source = ai if isinstance(ai, dict) else {}
    factors = source.get("factors") if isinstance(source.get("factors"), dict) else {}
    if source.get("ai_error") or factors.get("score_parse_error"):
        result["reason"] = "SCORE_LED_AI_OR_SCORE_PARSE_ERROR"
        return result
    long_raw = source.get("long_score")
    short_raw = source.get("short_score")
    if long_raw is None:
        long_raw = factors.get("long_score")
    if short_raw is None:
        short_raw = factors.get("short_score")
    long_score = _bounded_score(long_raw)
    short_score = _bounded_score(short_raw)
    if long_score is None or short_score is None:
        result["reason"] = "SCORE_LED_INVALID_OR_MISSING_SCORES"
        return result

    result["long_score"] = long_score
    result["short_score"] = short_score
    result["score_gap"] = abs(long_score - short_score)
    if long_score == short_score:
        result["reason"] = "SCORE_LED_TRUE_TIE"
        return result

    result["accepted"] = True
    result["effective_direction"] = "LONG" if long_score > short_score else "SHORT"
    result["reason"] = "SCORE_LED_VALID_NON_TIE"
    return result

RESEARCH_LANE_AI_SCAN = "AI_SCAN"
RESEARCH_LANE_FAMILY_TREND_FADE_60 = "FAMILY_TREND_FADE_60"
RESEARCH_LANE_FAMILY_TREND_FADE_60_COMMITTED = "FAMILY_TREND_FADE_60_COMMITTED"
RESEARCH_LANE_FAMILY_XVENUE_LEAD_60S = "FAMILY_XVENUE_LEAD_60S"
RESEARCH_LANE_FAMILY_XVENUE_PREMIUM_60S = "FAMILY_XVENUE_PREMIUM_60S"
RESEARCH_LANE_FAMILY_CONTINUOUS_AUG_ORIGINAL = "FAMILY_CONTINUOUS_AUG_ORIGINAL"
INVERTED_SCORE_LED_ADMISSION_POLICY_ID = "INVERTED_SCORE_LED_SIDE_V1"
CONTINUOUS_AUG_ADMISSION_POLICY_ID = "OWN_AI_CALL_AUG_V3_HIGHER_SCORE_GAP5_V1"
INVERTED_COMMITTED_ADMISSION_POLICY_ID = "INVERTED_COMMITTED_SCORE_LED_SIDE_GAP30_V1"
CROSS_VENUE_LEAD_ADMISSION_POLICY_ID = "CROSS_VENUE_LEAD_NO_AI_V1"
CROSS_VENUE_PREMIUM_ADMISSION_POLICY_ID = "CROSS_VENUE_PREMIUM_NO_AI_V1"
# Tiles on this clock are triggered by the per-second cross-venue evaluator,
# never by the shared three-minute AI call.
CROSS_VENUE_SIGNAL_CLOCK = "PER_SECOND_CROSS_VENUE_EVALUATOR"
TILE_REGISTRY_SCHEMA = "research_tile_registry_v1"
TILE_ARCHITECTURE_VERSION = 3
# Complete atomic add/retire contract from the V3.1 objective.  Every active
# tile declares this same surface roster, so a registry consumer can prove it
# has handled the whole lifecycle rather than treating a dashboard card as the
# tile boundary.
TILE_COMPONENT_SURFACES = (
    "runtime_evaluation",
    "paper_routing",
    "relay_allowlist",
    "policy_identity_signatures",
    "api_payloads",
    "production_dashboard",
    "mirror_manifests",
    "analyzer_loaders",
    "analyzer_reports",
    "analyzer_api",
    "analyzer_dashboard",
    "monitoring",
    "regression_tests",
    "documentation",
)
TILE_LIFECYCLE_STATES = frozenset({"PAPER_ONLY"})

COMBO_EXECUTION_LANES = (
    RESEARCH_LANE_FAMILY_TREND_FADE_60,
    RESEARCH_LANE_FAMILY_TREND_FADE_60_COMMITTED,
    RESEARCH_LANE_FAMILY_XVENUE_LEAD_60S,
    RESEARCH_LANE_FAMILY_XVENUE_PREMIUM_60S,
    RESEARCH_LANE_FAMILY_CONTINUOUS_AUG_ORIGINAL,
)
COMBO_TILE_DISPLAY_ORDER = COMBO_EXECUTION_LANES


def _policy_signature(*, raw_policy_id: str, entry: dict, exit_policy: dict,
                      ladder: tuple[tuple[float, float], ...] = (),
                      entry_ttl_sec: int = 1800) -> str:
    """Bind causal identity to every execution-defining policy parameter."""
    material = {
        "raw_policy_id": raw_policy_id,
        "entry_policy": entry,
        "exit_policy": exit_policy,
        "ladder": tuple(tuple(row) for row in ladder),
        "entry_ttl_sec": entry_ttl_sec,
        "path_end_sec": int(exit_policy.get("max_duration_sec", 7200)),
        "requested_margin_usd": 0.25,
        "account_risk_pct": 0.5,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# Each tile owns its own concurrent pending+open+awaiting capacity. One shared
# three-minute AI call places at most one order per tile, and an order can rest
# for entry_ttl_sec (1800s), so a tile needs ~10 slots to never refuse a cycle.
TILE_MAX_ACTIVE_SIGNALS = 10


def _tile(*, lane: str, label: str, raw_policy_id: str, id_prefix: str,
          module: str, test_module: str, entry: dict, exit_policy: dict,
          relay_capability: str = "BLOCKED_UNQUALIFIED",
          ladder: tuple[tuple[float, float], ...] = (),
          ladder_label: str = "", ladder_profile_id: str = "",
          hypothesis_result: dict | None = None,
          entry_ttl_sec: int = 1800,
          subtitle: str | None = None, policy_epoch: str | None = None,
          max_active_signals: int = TILE_MAX_ACTIVE_SIGNALS,
          pre_registration: dict | None = None,
          admission_treatment: str | None = None,
          default_enabled: bool = False,
          signal_clock: str | None = None) -> dict:
    # Score-led admission only describes tiles triggered by the shared AI call.
    if SCORE_LED_PAPER_RESEARCH_ENABLED and signal_clock is None:
        raw_policy_id = SCORE_LED_ADMISSION_POLICY_ID + "::" + raw_policy_id
    chase = tuple(entry["chase_windows"])
    tile = {
        "tile_id": lane,
        "label": label,
        "subtitle": "ANALYZER-PROFITABLE HYPOTHESIS — PAPER ONLY — exact execution test",
        "combo_key": raw_policy_id,
        "raw_policy_id": raw_policy_id,
        "policy_signature": _policy_signature(
            raw_policy_id=raw_policy_id, entry=entry,
            exit_policy=exit_policy, ladder=ladder,
            entry_ttl_sec=entry_ttl_sec,
        ),
        "policy_epoch": policy_epoch or ("v31-score-led-non-tie-paper-v2" if SCORE_LED_PAPER_RESEARCH_ENABLED else "v31-analyzer-hypothesis-paper-v1"),
        "admission_treatment": admission_treatment or (SCORE_LED_ADMISSION_POLICY_ID if SCORE_LED_PAPER_RESEARCH_ENABLED else "AI_FILTERED_V1"),
        "research_lane": lane,
        "execution_scope": "PAPER_ONLY",
        "paper_eligible": True,
        "live_copy_eligible": False,
        "relay_capability": relay_capability,
        "requested_margin_usd": 0.25,
        "risk_limits": {"account_risk_pct": 0.5, "hard_stop_margin_pct": float(exit_policy.get("hard_stop_margin_pct", 30.0))},
        "max_active_signals": int(max_active_signals),
        "analyzer_cohort": raw_policy_id,
        "presentation": {
            "family": exit_policy["family"],
            "evidence": "CONSERVATIVE_BBO_DEPTH_REQUIRED",
            "hypothesis_result": dict(hypothesis_result or {}),
        },
        "retirement_status": "ACTIVE_RESEARCH",
        "component_surfaces": TILE_COMPONENT_SURFACES,
        "entry_policy": entry,
        "exit_policy": exit_policy,
        "ai_min": 0, "ai_max": 101, "spread_min": -99, "spread_max": 99,
        "entry_mode": "IMMEDIATE", "is_benchmark": False,
        "is_research_candidate": True, "is_legacy": False,
        "is_independent_ai": False, "uses_shared_ai_direction": True,
        "paper_only": True, "platform_relay_eligible": False,
        "default_enabled": bool(default_enabled), "id_prefix": id_prefix,
        "toggle_key": "research_lane_enabled", "lifecycle_state": "PAPER_ONLY",
        "implementation_modules": (module,), "dedicated_test_modules": (test_module,),
        "entry_offset_pct": entry["offset_pct"],
        "initial_rest_sec": min(chase) * 300 if chase else 0,
        "chase_windows": chase,
        "chase_age_sec": (min(chase) * 300, (max(chase) + 1) * 300) if chase else (),
        "chase_interval_sec": entry["reprice_sec"],
        "chase_remaining_gap_step_pct": entry["remaining_gap_step_pct"],
        "entry_ttl_sec": entry_ttl_sec, "margin_usd": 0.25,
        "account_risk_pct": 0.5, "path_end_sec": int(exit_policy.get("max_duration_sec", 7200)),
        "exit_profile_id": raw_policy_id.split("|", 1)[1],
        "promotion_criteria": "Conservative chronological OOS, bounded drawdown, cross-world parity and every live gate GREEN",
        "kill_criteria": "Stop new entries on identity, fill, lifecycle, protection, mirror, analyzer or dashboard contradiction",
        "research_question": f"Does {raw_policy_id} retain positive conservative OOS EV with bounded drawdown?",
    }
    if pre_registration:
        tile["pre_registration"] = pre_registration
        tile["promotion_criteria"] = pre_registration["promotion_summary"]
        tile["kill_criteria"] = pre_registration["kill_summary"]
    if ladder:
        tile.update({
            "ladder": tuple(tuple(row) for row in ladder),
            "ladder_label": ladder_label,
            "ladder_profile_id": ladder_profile_id,
        })
    if signal_clock is not None:
        tile.update({"signal_clock": signal_clock, "uses_shared_ai_direction": False})
    if SCORE_LED_PAPER_RESEARCH_ENABLED and subtitle is None:
        tile["label"] = label + " · score-led paper"
        tile["subtitle"] = "HIGHER SCORE ADMISSION EXPERIMENT — PAPER ONLY — NOT AI APPROVAL"
        tile["presentation"]["hypothesis_result"] = {"status": "UNTESTED_NEW_ADMISSION_TREATMENT"}
    if subtitle is not None:
        tile["subtitle"] = subtitle
    return tile


RESEARCH_STACK_VERSION = "v31-continuous-aug-original-v7"
# Trend Fade 60 was registered in the v4 cohort and continues unchanged through
# the v5, v6 and v7 roster changes; its policy epoch and pre-registration cohort
# stay pinned so its identity, ledger and verdict window are not split by them.
TREND_FADE_60_POLICY_EPOCH = "v31-dynamic-adaptive-ladder-paper-v4"
# Cross-venue lead was registered in the v5 cohort and continues unchanged.
XVENUE_LEAD_POLICY_EPOCH = "v31-trend-fade-single-tile-v5"
# Committed fade and cross-venue premium were registered in v6 and continue
# unchanged through the v7 Continuous (Aug original) addition.
COMMITTED_FADE_PREMIUM_POLICY_EPOCH = "v31-committed-fade-premium-v6"
# The shared AI prompt inputs are not part of any tile's execution signature,
# but tiles that read the shared call's side see them. Every change to the live
# prompt's inputs bumps this revision; the analyzer splits AI-driven tile cohorts
# on it (diagnostics/AI-INPUT-REVISION-RECEIPT-*.md records each boundary).
AI_PROMPT_INPUT_REVISION = "shared_direction_inputs_r2_20261002"
AI_PROMPT_INPUT_REVISION_HISTORY = (
    ("shared_direction_inputs_r1", "prompt v4_1 inputs before 2026-10-02 (ret_1m context 0, lower_high always False, volume_ratio vs forming bar)"),
    (AI_PROMPT_INPUT_REVISION, "ret_1m/ret_5m from the 1 s tape, two-sided swing-structure detector, volume_ratio on closed bars"),
)


def _trend_fade_pre_registration(
    hypothesis_id: str, *,
    registered_utc: str = "2026-10-01T08:30:00Z",
    registered_cohort: str = TREND_FADE_60_POLICY_EPOCH,
    control_lane: str | None = None,
    control_meaning: str = "AI's own score-led side on the same shared call",
    control_status: str | None = "RETIRED_20261002_NO_PAIRED_CONTROL",
    honest_label: str = "in-sample +$1.30 / 47 trades; expected heavy decay; beta test",
) -> dict:
    """Owner-approved beta test; verdicts use post-registration trades only."""
    pre = {
        "schema": "tile_pre_registration_trade_count_v1",
        "hypothesis_id": hypothesis_id,
        "registered_utc": registered_utc,
        "registered_cohort": registered_cohort,
        "control_lane": control_lane,
        "control_meaning": control_meaning,
        **({"control_status": control_status} if control_status else {}),
        "evidence_world": "CONSERVATIVE_BBO",
        "ci_method": "6H_CLUSTER_BOOTSTRAP_95",
        "honest_label": honest_label,
        "promotion": {
            "meaning": "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY",
            "min_fills": 150,
            "per_fill_ev_lower_ci95_gt_bp": 0.0,
            "both_halves_positive": True,
            "max_2h_window_profit_share": 0.30,
        },
        "kill": {
            "k1_after_fills": 40, "k1_net_usd_at_or_below": -0.40,
            "k2_after_fills": 80, "k2_net_usd_at_or_below": 0.0,
            "k3_worst_trade_bp_below": -60.0,
            "k4_max_drawdown_usd": 1.0,
            "k5_max_days_without_promotion": 14,
        },
    }
    promote, kill = pre["promotion"], pre["kill"]
    pre["promotion_summary"] = (
        f"Pre-registered {hypothesis_id}: >={promote['min_fills']} trades, per-trade EV lower 95% CI >0, "
        f"both halves positive, no single 2 h window >{promote['max_2h_window_profit_share']:.0%} of profit; "
        "promotion = owner review, never relay"
    )
    pre["kill_summary"] = (
        f"K1 net <=${kill['k1_net_usd_at_or_below']:.2f} after {kill['k1_after_fills']} trades; "
        f"K2 net <=${kill['k2_net_usd_at_or_below']:.2f} after {kill['k2_after_fills']} trades; "
        f"K3 any trade worse than {kill['k3_worst_trade_bp_below']:g} bp (stop failure); "
        f"K4 drawdown >${kill['k4_max_drawdown_usd']:.2f}; "
        f"K5 day {kill['k5_max_days_without_promotion']} without promotion"
    )
    return pre


# One entry shared by Trend Fade 60 and its profit-lock variant so the two
# tiles pair on identical signals; only the exit and capacity differ.
_TREND_FADE_60_ENTRY = {
    "mode": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_windows": (),
    "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
    "direction_source": "INVERTED_SCORE_LED_SIDE",
    "refuse_on": ("SCORE_TIE", "INVALID_SCORES", "AI_ERROR"),
    "trades_raw_ai_no_trade": True, "min_score_gap": None,
    "max_spread_bps": 1.68, "max_bbo_age_sec": 5.0,
    "taker_protection_bps": 5.0, "taker_ttl_sec": 15,
    "ai_decision_role": "FEATURE_ONLY",
}
COMMITTED_FADE_MIN_SCORE_GAP = 30.0
# Tile 2 = Tile 1 restricted to calls where the AI committed to a side: raw
# direction LONG/SHORT (never NO_TRADE, never a score/direction mismatch) and a
# score gap of at least 30. Same side rule, entry, exit and capacity.
_TREND_FADE_60_COMMITTED_ENTRY = {
    **_TREND_FADE_60_ENTRY,
    "refuse_on": ("SCORE_TIE", "INVALID_SCORES", "AI_ERROR", "RAW_AI_NO_TRADE",
                  "SCORE_DIRECTION_MISMATCH", "SCORE_GAP_BELOW_MIN"),
    "trades_raw_ai_no_trade": False,
    "min_score_gap": COMMITTED_FADE_MIN_SCORE_GAP,
    "commit_rule": "EXPLICIT_RAW_SIDE_EQUALS_SCORE_LED_AND_GAP_GE_30",
}
# Tile 5 = exact replica of the early-August 2026 Continuous (d018ef31, before
# the NO_TRADE prompt 636fa9ca4/f0620b33b, #136 demotion and #233 retirement).
# It makes its own DeepSeek call with the verbatim v3 prompt after every
# shared three-minute call; see paper_policy_family_continuous_aug_original.py.
_CONTINUOUS_AUG_ENTRY = {
    "mode": "MAKER_LIMIT_OFFSET_CHASE",
    "offset_pct": 0.1,
    # August chased from creation every 60 s while the order was under 10 min.
    "chase_windows": (0, 1), "chase_start_sec": 0, "chase_max_age_sec": 600,
    "remaining_gap_step_pct": 25.0, "reprice_sec": 60,
    "near_fill_usd": 10.0, "near_fill_pct": 0.1, "min_original_gap_usd": 10.0,
    "max_gap_close_pct": 90.0, "marketable_fallback": False,
    "direction_source": "OWN_AI_CALL_HIGHER_SCORE",
    "ai_prompt_id": "shared_direction_adx_evidence_v3_20260721",
    "ai_model_requested_aug": "deepseek-v4-flash",
    "ai_model_served": "deepseek-flash",
    "ai_temperature": 0.0,
    "ai_cadence": "AFTER_EVERY_SHARED_3MIN_CALL_WHILE_TILE_ON",
    "ai_decision_role": "DIRECTION_AND_ADMISSION",
    "min_score_gap": 5, "min_score_sum": 50, "r2_spread_floor": 4,
    "refuse_on": ("AI_ERROR", "AI_PARSE_FAILED", "AI_RETURNED_ZERO_SCORES",
                  "SCORE_GAP_BELOW_5", "TREND_HIERARCHY_COUNTER_TREND",
                  "STRUCTURE_AGREEMENT", "R2_SPREAD_FLOOR", "DUPLICATE_LIMIT_PRICE"),
    "duplicate_tolerance_usd": 15.0, "duplicate_tolerance_pct": 0.25,
    "fill_model": "PLATFORM_REALISTIC_BBO_DEPTH",
    "shadow_fill_model": "AUG_OPTIMISTIC_TOUCH",
}
# Scenario C (scenario_c_config.TRAIL_LADDER_SCENARIO_C), margin % at 100x.
CONTINUOUS_AUG_LADDER = (
    (8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120),
)
_CONTINUOUS_AUG_EXIT = {
    "family": "AUG_CONTINUOUS_SCENARIO_C",
    "max_duration_sec": 7200,
    "hard_stop_margin_pct": 30.0,
    "early_fail_margin_pct": -32.0, "post_fill_grace_sec": 90,
    "thesis_cut_margin_pct": -12.0, "thesis_mfe_protect_pct": 5.0,
    "thesis_min_age_sec": 300, "thesis_exit_if_above_pct": 8.0,
    "thesis_flip_margin": 1, "thesis_decay_delta": 2,
    "peak_never_loser_min_peak": 40.0, "peak_never_loser_floor": 10.0,
    "spread_penalty_threshold": 5, "spread_penalty_lock_tighten_pct": 1.0,
    "exit_order": ("EARLY_FAIL", "STOP_LOSS", "PROFIT_LOCK_LADDER",
                   "THESIS_FAST_CUT", "THESIS_INVALIDATED", "TIME_EXIT"),
}
CONTINUOUS_AUG_POLICY_EPOCH = RESEARCH_STACK_VERSION
CONTINUOUS_AUG_CARD_TEXT = "Exact replica of Aug-2026 Continuous (realistic fills; August touch-fill shadow alongside)"


def _continuous_aug_original_tile() -> dict:
    """Owner-requested permanent baseline; never promoted, never relay-capable."""
    tile = _tile(
        lane=RESEARCH_LANE_FAMILY_CONTINUOUS_AUG_ORIGINAL,
        label="Continuous (Aug-2026 original) · own v3 AI call, 0.1% maker + 25% chase, Scenario C",
        raw_policy_id="AUG_V3_OWN_AI_GAP5_OFFSET_0.10_CHASE_s25_i60_10M|SCENARIO_C_THESIS12_SL30_EF32_PNL40_10_120M",
        id_prefix="caug",
        module="paper_policy_family_continuous_aug_original.py",
        test_module="test_paper_policy_family_continuous_aug_original.py",
        entry=dict(_CONTINUOUS_AUG_ENTRY),
        exit_policy=dict(_CONTINUOUS_AUG_EXIT),
        ladder=CONTINUOUS_AUG_LADDER,
        ladder_label="8→5, 12→10, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120",
        ladder_profile_id="SCENARIO_C_RUNNER_8_v8_20260820",
        hypothesis_result={
            "status": "BASELINE_BENCHMARK",
            "hypothesis_id": "BASELINE_CONTINUOUS_AUG_ORIGINAL_20261003",
            "in_sample": "Aug 6-11 2026: 78 closes, 67.9% win, +$28.34 at $20x100 touch fills (77/78 SHORT)",
            "expected_live": "Yardstick, not a hypothesis; realistic fills expected to cost ~2 bp/trade versus August",
        },
        admission_treatment=CONTINUOUS_AUG_ADMISSION_POLICY_ID,
        entry_ttl_sec=1800,
        subtitle="BASELINE BENCHMARK — " + CONTINUOUS_AUG_CARD_TEXT + " — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=CONTINUOUS_AUG_POLICY_EPOCH,
        default_enabled=True,
    )
    tile.update({
        "baseline_role": "BASELINE_BENCHMARK",
        "card_text": CONTINUOUS_AUG_CARD_TEXT,
        "uses_shared_ai_direction": False,
        "own_ai_call": True,
        "presentation": {**tile["presentation"], "evidence": "CONSERVATIVE_BBO_DEPTH_REQUIRED_PLUS_AUG_TOUCH_SHADOW"},
        "promotion_criteria": "N/A — permanent baseline benchmark; never promoted, never relay",
        "kill_criteria": "Never retired for performance; pause only on identity, fill, lifecycle, protection, mirror, analyzer or dashboard contradiction",
        "research_question": "What does the August Continuous earn today under realistic fills? Every other tile is judged against it.",
    })
    return tile


def _committed_fade_pre_registration(hypothesis_id: str) -> dict:
    """INDICATOR-SEARCH-MODEL-A S2 + AI-DIRECTION-AUDIT-MODEL-A item 2; post-registration trades only."""
    pre = {
        "schema": "tile_pre_registration_committed_fade_v1",
        "hypothesis_id": hypothesis_id,
        "registered_utc": "2026-10-02T07:00:00Z",
        "registered_cohort": COMMITTED_FADE_PREMIUM_POLICY_EPOCH,
        "control_lane": RESEARCH_LANE_FAMILY_TREND_FADE_60,
        "control_meaning": "Trend Fade 60 fades every non-tie call (incl. NO_TRADE) on the same shared calls",
        "evidence_world": "CONSERVATIVE_BBO",
        "ci_method": "2H_CLUSTER_BOOTSTRAP_95",
        "honest_label": "HINT — dev +12.6 bp/call [+3.3,+25.2], holdout +11.6 [-4.5,+27.9]; contaminated by the audit that proposed it",
        "promotion": {
            "meaning": "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY",
            "min_fills": 150, "min_distinct_hours": 40, "min_regime_days": 3,
            "regime_day_rule": "UTC day |r24h| >= 1.5% = trend day (up/down), else range",
            "per_fill_ev_lower_ci95_gt_bp": 0.0,
            "beats_control_by_bp": 10.0,
            "both_halves_positive": True,
        },
        "kill": {
            "k1_after_fills": 150, "k1_hit_rate_below": 0.52,
            "k2_trend_day_mean_bp_below": -15.0,
            "k3_worst_trade_bp_below": -60.0,
            "k4_max_drawdown_usd": 1.0,
            "k5_max_days_without_promotion": 21,
        },
    }
    promote, kill = pre["promotion"], pre["kill"]
    pre["promotion_summary"] = (
        f"Pre-registered {hypothesis_id}: >={promote['min_fills']} trades across >={promote['min_distinct_hours']} "
        f"hours and >={promote['min_regime_days']} regime-days (up, down, range); per-trade EV 2 h-cluster lower 95% CI >0; "
        f"beats Trend Fade 60 on the same calls by >={promote['beats_control_by_bp']:g} bp/trade; both halves positive; "
        "promotion = owner review, never relay"
    )
    pre["kill_summary"] = (
        f"K1 hit rate <{kill['k1_hit_rate_below']:.0%} after {kill['k1_after_fills']} trades; "
        f"K2 any up/down-trend day averaging <{kill['k2_trend_day_mean_bp_below']:g} bp/trade; "
        f"K3 any trade worse than {kill['k3_worst_trade_bp_below']:g} bp (stop failure); "
        f"K4 drawdown >${kill['k4_max_drawdown_usd']:.2f}; "
        f"K5 day {kill['k5_max_days_without_promotion']} without promotion"
    )
    return pre


def _xvenue_premium_pre_registration(hypothesis_id: str) -> dict:
    """INDICATOR-SEARCH-MODEL-A S1 (premium arm, 60 s only); post-registration trades only."""
    pre = {
        "schema": "tile_pre_registration_xvp_v1",
        "hypothesis_id": hypothesis_id,
        "registered_utc": "2026-10-02T07:00:00Z",
        "registered_cohort": COMMITTED_FADE_PREMIUM_POLICY_EPOCH,
        "control_lane": None,
        "control_meaning": "No AI control: the trigger is the cross-venue premium deviation; Tile 3 is the sibling cross-venue rule",
        "evidence_world": "CONSERVATIVE_BBO",
        "ci_method": "1H_CLUSTER_BOOTSTRAP_95",
        "honest_label": "HINT — 8h holdout: 1 m 78% hit, +2.0 bp/trade [+0.7,+3.2], 115 independent; thresholds fixed on dev",
        "promotion": {
            "meaning": "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY",
            "min_fills": 500, "min_utc_days": 7,
            "min_sessions_each": 3, "sessions": ("ASIA", "EU", "US"),
            "per_fill_ev_lower_ci95_gt_bp": 0.0,
            "shadow_5s_delay_mean_gt_bp": 0.0,
            "max_single_day_profit_share": 0.30,
            "both_sides_mean_ge_bp": 0.0,
            "max_replay_parity_gap_bp": 1.0,
            "max_median_signal_to_fill_sec": 2.0,
        },
        "kill": {
            "k1_after_fills": 300, "k1_mean_bp_at_or_below": 0.0,
            "k2_after_fills": 300, "k2_shadow_5s_delay_mean_below_bp": -0.5,
            "k3_worst_trade_bp_below": -45.0, "k3_max_stale_feed_fill_share": 0.01,
            "k4_max_drawdown_usd": 0.50,
            "k5_max_days_without_promotion": 14,
            "k6_defect_action": "PAUSE_AND_QUARANTINE_NOT_A_STRATEGY_VERDICT",
        },
    }
    promote, kill = pre["promotion"], pre["kill"]
    pre["promotion_summary"] = (
        f"Pre-registered {hypothesis_id}: >={promote['min_fills']} trades over >={promote['min_utc_days']} UTC days "
        f"incl. >={promote['min_sessions_each']} each of Asia/EU/US sessions; per-trade EV 1 h-cluster lower 95% CI >0; "
        f"5 s-delay shadow mean >0; no day >{promote['max_single_day_profit_share']:.0%} of profit; both sides >=0; "
        f"replay parity <={promote['max_replay_parity_gap_bp']:g} bp, median signal->fill "
        f"<={promote['max_median_signal_to_fill_sec']:g} s; promotion = owner review, never relay"
    )
    pre["kill_summary"] = (
        f"K1 mean <={kill['k1_mean_bp_at_or_below']:g} bp after {kill['k1_after_fills']} trades; "
        f"K2 5 s-delay shadow mean <{kill['k2_shadow_5s_delay_mean_below_bp']:g} bp after {kill['k2_after_fills']}; "
        f"K3 any trade worse than {kill['k3_worst_trade_bp_below']:g} bp or >{kill['k3_max_stale_feed_fill_share']:.0%} "
        f"of trades on a stale feed; K4 drawdown >${kill['k4_max_drawdown_usd']:.2f}; "
        f"K5 day {kill['k5_max_days_without_promotion']} without promotion = INCONCLUSIVE; "
        "K6 lifecycle/identity/analyzer/feed/mirror defect = pause and quarantine"
    )
    return pre


def _xvenue_lead_pre_registration(hypothesis_id: str) -> dict:
    """NEXT-TILE-RESEARCH-20261002 rules; verdicts use post-registration trades only."""
    pre = {
        "schema": "tile_pre_registration_xvl_v1",
        "hypothesis_id": hypothesis_id,
        "registered_utc": "2026-10-01T21:30:00Z",
        "registered_cohort": XVENUE_LEAD_POLICY_EPOCH,
        "control_lane": None,
        "control_meaning": "No AI control: the trigger is the cross-venue lead, not a shared AI call",
        "evidence_world": "CONSERVATIVE_BBO",
        "ci_method": "1H_CLUSTER_BOOTSTRAP_95",
        "honest_label": "HINT — 12h evidence: 160 trades, 65% wins, +2.43 bp after spread, OOS half +2.53 bp",
        "promotion": {
            "meaning": "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY",
            "min_fills": 1000, "min_utc_days": 5,
            "min_asia_sessions": 3, "min_asia_session_fills": 50,
            "asia_session_utc_hours": (0, 8),
            "per_fill_ev_lower_ci95_gt_bp": 0.0,
            "min_positive_days_of_first_5": 4,
            "both_halves_positive": True,
            "max_single_hour_profit_share": 0.15,
            "max_replay_parity_gap_bp": 1.0,
            "max_median_signal_to_fill_sec": 2.0,
            "max_stop_overshoot_bp": 10.0,
        },
        "kill": {
            "k1_after_fills": 150, "k1_mean_bp_at_or_below": 0.0,
            "k2_after_fills": 400, "k2_upper_ci95_lt_bp": 0.5,
            "k3_worst_trade_bp_below": -45.0, "k3_max_stale_feed_fill_share": 0.01,
            "k4_max_drawdown_usd": 0.50,
            "k5_max_days_without_promotion": 10,
            "k6_defect_action": "PAUSE_AND_QUARANTINE_NOT_A_STRATEGY_VERDICT",
        },
    }
    promote, kill = pre["promotion"], pre["kill"]
    pre["promotion_summary"] = (
        f"Pre-registered {hypothesis_id}: >={promote['min_fills']} trades over >={promote['min_utc_days']} UTC days "
        f"incl. >={promote['min_asia_sessions']} Asia sessions with >={promote['min_asia_session_fills']} trades; "
        f"per-trade EV 1 h-cluster lower 95% CI >0; net positive on >={promote['min_positive_days_of_first_5']} of the "
        f"first 5 days and both halves; no hour >{promote['max_single_hour_profit_share']:.0%} of profit; replay parity "
        f"<={promote['max_replay_parity_gap_bp']:g} bp, median signal->fill <={promote['max_median_signal_to_fill_sec']:g} s, "
        f"stops within {promote['max_stop_overshoot_bp']:g} bp; promotion = owner review, never relay"
    )
    pre["kill_summary"] = (
        f"K1 mean <={kill['k1_mean_bp_at_or_below']:g} bp after {kill['k1_after_fills']} trades; "
        f"K2 1 h-cluster upper 95% CI <+{kill['k2_upper_ci95_lt_bp']:g} bp after {kill['k2_after_fills']} trades; "
        f"K3 any trade worse than {kill['k3_worst_trade_bp_below']:g} bp or >{kill['k3_max_stale_feed_fill_share']:.0%} "
        f"of trades on a stale feed; K4 drawdown >${kill['k4_max_drawdown_usd']:.2f}; "
        f"K5 day {kill['k5_max_days_without_promotion']} without promotion = INCONCLUSIVE; "
        "K6 lifecycle/identity/analyzer/feed/mirror defect = pause and quarantine"
    )
    return pre



COMBO_LANE_SPECS = {
    # Owner-approved beta test of the strongest in-sample research idea
    # (TILE2-DESIGN-20261001 "Tile 4"): fade the score-led side of the same
    # shared call. Only ties, invalid scores and AI errors refuse; raw AI
    # NO_TRADE and small gaps trade, because the research tested every
    # score-led side. 40 bp at 100x = 40% margin, below liquidation.
    RESEARCH_LANE_FAMILY_TREND_FADE_60: _tile(
        lane=RESEARCH_LANE_FAMILY_TREND_FADE_60,
        label="Trend-label fade · inverted AI side, 60-min hold, 40 bp catastrophic stop",
        raw_policy_id="INVERT_SCORE_LED_SIDE_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP",
        id_prefix="ftf",
        module="paper_policy_family_trend_fade_60.py",
        test_module="test_paper_policy_family_trend_fade_60.py",
        entry=dict(_TREND_FADE_60_ENTRY),
        exit_policy={
            "family": "TIME_EXIT_WITH_CATASTROPHIC_STOP",
            "max_duration_sec": 3600,
            "hard_stop_bps": 40.0, "hard_stop_margin_pct": 40.0,
            "ladder": None, "breakeven": None, "trail": None, "take_profit": None,
            "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_CROSSED_THE_STOP",
            "max_open_positions": 1,
        },
        hypothesis_result={
            "status": "IN_SAMPLE_ONLY_BETA_TEST",
            "hypothesis_id": "H4_TREND_FADE_60_20261001",
            "in_sample": "+$1.30 / 47 trades (27W/20L), +11.0 bp/trade, PF 2.0, max DD $0.17; holdout 9 trades $0.00",
            "corrected": "family-wise circular-shift p 0.13; DSR ~0.1; per-trade CI includes 0",
            "expected_live": "0 to +5 bp/trade after 50-100% decay",
        },
        pre_registration=_trend_fade_pre_registration("H4_TREND_FADE_60_20261001"),
        admission_treatment=INVERTED_SCORE_LED_ADMISSION_POLICY_ID,
        max_active_signals=1,
        entry_ttl_sec=15,
        subtitle="BETA TEST — in-sample +$1.30 / 47 trades; expected heavy decay — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=TREND_FADE_60_POLICY_EPOCH,
    ),
    # Owner-approved 2026-10-02 (replaces the retired profit-lock ladder Tile 2):
    # Trend Fade 60 restricted to calls where the AI committed to a side. It
    # never fades NO_TRADE, a score/direction mismatch or a gap below 30; side,
    # entry, exit, stop and capacity are identical to Tile 1 so the two pair on
    # the same shared calls.
    RESEARCH_LANE_FAMILY_TREND_FADE_60_COMMITTED: _tile(
        lane=RESEARCH_LANE_FAMILY_TREND_FADE_60_COMMITTED,
        label="Trend Fade 60 — committed calls only · inverted AI side when the AI commits (gap ≥30), 60-min hold, 40 bp stop",
        raw_policy_id="INVERT_COMMITTED_SCORE_LED_SIDE_GAP30_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP",
        id_prefix="ftc",
        module="paper_policy_family_trend_fade_60_committed.py",
        test_module="test_paper_policy_family_trend_fade_60_committed.py",
        entry=dict(_TREND_FADE_60_COMMITTED_ENTRY),
        exit_policy={
            "family": "TIME_EXIT_WITH_CATASTROPHIC_STOP",
            "max_duration_sec": 3600,
            "hard_stop_bps": 40.0, "hard_stop_margin_pct": 40.0,
            "ladder": None, "breakeven": None, "trail": None, "take_profit": None,
            "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_CROSSED_THE_STOP",
            "max_open_positions": 1,
        },
        hypothesis_result={
            "status": "HINT_DEV_AND_HOLDOUT_SAME_SIGN",
            "hypothesis_id": "H6_TREND_FADE_60_COMMITTED_20261002",
            "in_sample": "dev 63% / +12.6 bp per call [+3.3,+25.2] (38 h); holdout 64% / +11.6 bp [-4.5,+27.9] (12 h); skipped all 5 Trend Fade hard stops",
            "expected_live": "0 to +6 bp/trade; trades ~45% of calls; gives up ~half the range-period profit",
        },
        pre_registration=_committed_fade_pre_registration("H6_TREND_FADE_60_COMMITTED_20261002"),
        admission_treatment=INVERTED_COMMITTED_ADMISSION_POLICY_ID,
        max_active_signals=1,
        entry_ttl_sec=15,
        subtitle="HINT — committed-call fade, holdout CI spans 0 — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=COMMITTED_FADE_PREMIUM_POLICY_EPOCH,
    ),
    # NEXT-TILE-RESEARCH-20261002: when the mean Binance/Bybit 10 s return
    # leads Bitfinex by >=8 bp, take the Bitfinex taker in their direction
    # and exit after 60 s. Other venues are price data only; fees are
    # Bitfinex-only. Triggered by the per-second evaluator, never by the AI.
    RESEARCH_LANE_FAMILY_XVENUE_LEAD_60S: _tile(
        lane=RESEARCH_LANE_FAMILY_XVENUE_LEAD_60S,
        label="Cross-venue lead · follow Binance/Bybit ≥8 bp lead over 10 s, 60-s hold, 40 bp catastrophic stop",
        raw_policy_id="XVENUE_LEAD_W10S_TH8BP_BOTHFRESH_SPREADLE3BP_TAKER_CAP5BPS|TIME_60_HARD40BP",
        id_prefix="xvl",
        module="paper_policy_family_xvenue_lead.py",
        test_module="test_paper_policy_family_xvenue_lead.py",
        entry={
            "mode": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_windows": (),
            "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
            "direction_source": "CROSS_VENUE_LEAD",
            "signal_clock": CROSS_VENUE_SIGNAL_CLOCK,
            "leader_venues": ("binance", "bybit"),
            "lookback_sec": 10, "lead_threshold_bps": 8.0,
            "max_venue_age_sec": 2.0, "max_bbo_age_sec": 2.0, "max_spread_bps": 3.0,
            "taker_protection_bps": 5.0, "taker_ttl_sec": 3,
            "shadow_entry_delay_sec": 1,
            "min_submit_interval_sec": 5, "max_submissions_per_hour": 60,
            "ai_decision_role": "NONE",
        },
        exit_policy={
            "family": "TIME_EXIT_WITH_CATASTROPHIC_STOP",
            "max_duration_sec": 60,
            "hard_stop_bps": 40.0, "hard_stop_margin_pct": 40.0,
            "ladder": None, "breakeven": None, "trail": None, "take_profit": None,
            "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_CROSSED_THE_STOP",
            "max_open_positions": 1,
        },
        hypothesis_result={
            "status": "HINT_12H_EVIDENCE",
            "hypothesis_id": "H5_XVENUE_LEAD_60S_20261002",
            "in_sample": "160 trades / 12 h, 65% wins, +2.43 bp after spread; OOS half +2.53 bp; corr -0.14 vs Trend Fade",
            "expected_live": "+0.6 to +1.3 bp/trade after decay; 150-280 trades/day",
        },
        pre_registration=_xvenue_lead_pre_registration("H5_XVENUE_LEAD_60S_20261002"),
        admission_treatment=CROSS_VENUE_LEAD_ADMISSION_POLICY_ID,
        max_active_signals=1,
        entry_ttl_sec=3,
        subtitle="HINT — 12h evidence, not validated across days — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=XVENUE_LEAD_POLICY_EPOCH,
        signal_clock=CROSS_VENUE_SIGNAL_CLOCK,
    ),
    # INDICATOR-SEARCH-MODEL-A signal 1 (owner-approved 2026-10-02): when the
    # Binance/Bybit premium over Bitfinex sits in its dev-fixed tails versus its
    # own 60-minute mean, take the Bitfinex taker in the leaders' direction and
    # exit after 60 s (the 300 s arm was not materially better: +1.4 vs +1.8 bp
    # non-overlapping, half the independent N). Per-second evaluator, no AI.
    RESEARCH_LANE_FAMILY_XVENUE_PREMIUM_60S: _tile(
        lane=RESEARCH_LANE_FAMILY_XVENUE_PREMIUM_60S,
        label="Cross-venue premium · Binance/Bybit premium over Bitfinex ≥+1.75 / ≤−1.88 bp vs its 60-min mean, 60-s hold, 40 bp stop",
        raw_policy_id="XVENUE_PREMIUM_DEV60M_L1.75_S1.88BP_BOTHFRESH_SPREADLE3BP_TAKER_CAP5BPS|TIME_60_HARD40BP",
        id_prefix="xvp",
        module="paper_policy_family_xvenue_premium.py",
        test_module="test_paper_policy_family_xvenue_premium.py",
        entry={
            "mode": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_windows": (),
            "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
            "direction_source": "CROSS_VENUE_PREMIUM",
            "signal_clock": CROSS_VENUE_SIGNAL_CLOCK,
            "leader_venues": ("binance", "bybit"),
            "premium_mean_window_sec": 3600, "premium_min_mean_samples": 1200,
            "premium_long_threshold_bps": 1.75, "premium_short_threshold_bps": -1.88,
            "max_fill_forward_sec": 5,
            "max_venue_age_sec": 2.0, "max_bbo_age_sec": 2.0, "max_spread_bps": 3.0,
            "taker_protection_bps": 5.0, "taker_ttl_sec": 3,
            "shadow_entry_delay_sec": 1,
            "min_submit_interval_sec": 5, "max_submissions_per_hour": 60,
            "ai_decision_role": "NONE",
        },
        exit_policy={
            "family": "TIME_EXIT_WITH_CATASTROPHIC_STOP",
            "max_duration_sec": 60,
            "hard_stop_bps": 40.0, "hard_stop_margin_pct": 40.0,
            "ladder": None, "breakeven": None, "trail": None, "take_profit": None,
            "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_CROSSED_THE_STOP",
            "max_open_positions": 1,
        },
        hypothesis_result={
            "status": "HINT_8H_HOLDOUT_EVIDENCE",
            "hypothesis_id": "H7_XVENUE_PREMIUM_60S_20261002",
            "in_sample": "8 h cross-venue holdout: 1 m 78% hit, +2.0 bp/trade after spread [+0.7,+3.2], 148 trades (115 independent), BH q 0.013; 5 m arm +2.3 bp",
            "expected_live": "+0.5 to +1.5 bp/trade after decay; overlaps Tile 3 triggers",
        },
        pre_registration=_xvenue_premium_pre_registration("H7_XVENUE_PREMIUM_60S_20261002"),
        admission_treatment=CROSS_VENUE_PREMIUM_ADMISSION_POLICY_ID,
        max_active_signals=1,
        entry_ttl_sec=3,
        subtitle="HINT — 8h holdout evidence, not validated across days — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=COMMITTED_FADE_PREMIUM_POLICY_EPOCH,
        signal_clock=CROSS_VENUE_SIGNAL_CLOCK,
    ),
    # Owner-requested 2026-10-03: the August Continuous tile (demoted to a
    # label in #136, retired in #233) restored as a permanent paper-only baseline.
    RESEARCH_LANE_FAMILY_CONTINUOUS_AUG_ORIGINAL: _continuous_aug_original_tile(),
}
COMPARISON_BENCHMARK_LANE = None
PRIMARY_PRODUCTION_LANE = RESEARCH_LANE_FAMILY_TREND_FADE_60
BENCHMARK_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_PROFILE_ID = "CONTINUOUS_BENCHMARK_v1"
BENCHMARK_ROLE = "BENCHMARK"
PRIMARY_PRODUCTION_ROLE = "BENCHMARK"
RESEARCH_CANDIDATE_LANE = RESEARCH_LANE_FAMILY_TREND_FADE_60
RESEARCH_CANDIDATE_ROLE = "RESEARCH_CANDIDATE"

RESEARCH_STACK_FEATURES = (
    "Tiles 1 and 2 consume the shared three-minute call, each with its own lock, order, position, ledger and analyzer cohort, and pair on identical signals. Tile 1 (Trend Fade 60, beta test) trades the opposite of the score-led side with a taker at the signal (5 bp cap, 15 s; stand aside when spread >1.68 bp), a 60-minute time exit and a 40 bp catastrophic stop; only ties, invalid scores and AI errors refuse. It holds one position at a time, is default-OFF and keeps its v4 identity and cohort unchanged. Tile 2 (Trend Fade 60 - committed calls only, HINT) is identical except that it fades only calls where the AI committed to an explicit side matching the score-led side with a score gap of at least 30; it never fades NO_TRADE. Tile 3 (Cross-venue lead, HINT, 12 h evidence) and Tile 4 (Cross-venue premium, HINT, 8 h holdout evidence) use no AI and do not consume the shared call: a bounded per-second cross-venue evaluator takes a Bitfinex taker (5 bp cap, 3 s) in the leaders' direction when every feed is <=2 s old and spread <=3 bp - Tile 3 on a >=8 bp 10 s Binance/Bybit lead over Bitfinex, Tile 4 when the Binance/Bybit premium over Bitfinex is >=+1.75 bp (long) or <=-1.88 bp (short) versus its own 60-minute mean - exits after 60 s with a 40 bp catastrophic stop, holds one position, and logs every qualifying trigger as a shadow signal whether or not the tile is ON. Stops fill at the side-correct quote that crossed them. All four carry pre-registered promotion and kill rules and are paper-only and relay-ineligible. Tile 5 (Continuous, Aug-2026 original, BASELINE BENCHMARK) is an exact replica of the early-August Continuous tile: after every shared three-minute call it makes its own DeepSeek call with the verbatim v3 prompt (never NO_TRADE, temperature 0), takes the higher score's side when the gap is >=5, the scores sum to >=50 and the side does not fight confirmed structure, rests a 0.1% maker limit chased 25% of the remaining gap every 60 s for 10 minutes, and exits on the Scenario C ladder, a -12% thesis cut (MFE protect 5%), a 30% stop, a -32% early fail, a 40/10 peak floor or the 2 h cap. Its primary ledger uses realistic BBO/depth fills with the August touch fill recorded as a shadow; it is default-ON, paper-only, relay-ineligible and never promoted or retired for performance. v7 adds Tile 5; Tiles 1-4 keep their cohorts. v6 retires the Trend Fade 60 profit-lock ladder tile and starts the Tile 2 (committed) and Tile 4 (premium) cohorts; Tile 1 keeps v4 and Tile 3 keeps v5. v1/v2 remain quarantined plumbing-defect cohorts, v3 the prior single-tile cohort, v4 the four-tile cohort and v5 the ladder/lead cohort"
)
EXECUTION_FIX_VERSION = RESEARCH_STACK_VERSION
ANALYZER_SYNC_ID = RESEARCH_STACK_VERSION
RESEARCH_DASHBOARD_VERSION = RESEARCH_STACK_VERSION
EXPECTED_EXCHANGE = "bitfinex"
EXPECTED_BOT_VERSION = EXECUTION_FIX_VERSION

ACTIVE_TILE_REGISTRY = {lane: dict(COMBO_LANE_SPECS[lane]) for lane in COMBO_EXECUTION_LANES}
ACTIVE_TILE_ORDER = COMBO_EXECUTION_LANES


def active_tile_policy_epochs() -> tuple[str, ...]:
    """Distinct tile policy epochs in display order; tiles may pin older cohorts."""
    epochs: list[str] = []
    for lane in ACTIVE_TILE_ORDER:
        epoch = str(ACTIVE_TILE_REGISTRY[lane].get("policy_epoch") or "")
        if epoch and epoch not in epochs:
            epochs.append(epoch)
    return tuple(epochs)

# Retiring a tile means removing it from ACTIVE_TILE_REGISTRY and recording its
# lane token here for one release. The registry audit then fails while that
# token remains on any active execution/UI/analyzer surface. Historical data is
# quarantined separately and never keeps runtime code alive.
RETIRED_TILE_LANES = frozenset({
    "OFFSET_029_ATR_TP_25", "OFFSET_029_ATR_PROTECTED",
    "OFFSET_029_ATR_REGIME", "PROTECTED_W234_SCENARIO_C",
    # Retired 2026-10-01: all lost in conservative paper evidence.
    "FAMILY_CHANDELIER_3", "FAMILY_ATR_TARGET_2_5", "FAMILY_ATR_TRAIL",
    "FAMILY_HYBRID_RUNNER", "FAMILY_MFE_GIVEBACK", "CONTINUOUS",
    # Retired 2026-10-02: all three Dynamic Adaptive tiles lost in paper.
    "FAMILY_ADAPTIVE_REGIME", "FAMILY_ADAPTIVE_REGIME_LADDER",
    "FAMILY_ADAPTIVE_REGIME_LADDER_BE",
    # Retired 2026-10-02 (owner): profit-lock ladder variant of Trend Fade 60,
    # replaced by the committed-calls-only variant.
    "FAMILY_TREND_FADE_60_LADDER",
})
RETIRED_POLICY_IDENTITIES = frozenset({
    "OFFSET_0.03_CHASE_w234_s25_i180|CHANDELIER_3",
    "OFFSET_0.02_CHASE_w234_s25_i180|ATR_TP_2.5_ATR_SL_1.5",
    "OFFSET_0.04_CHASE_all_on_s50_i60|ATR_TRAIL_SL_2_ARM_1.25_TRAIL_1",
    "OFFSET_0.03_CHASE_w234_s25_i180|HYBRID_secure_33_runner_TRAIL_1",
    "OFFSET_0.03_CHASE_w234_s25_i180|ATR_TP_2.5_GIVEBACK_20PCT",
    "OFFSET_0.30_CHASE_w234_s50_i180|CHANDELIER_1.5",
    "OFFSET_0.27_CHASE_w234_s50_i180|ATR_TP_2.5_SCENARIO_C",
    "OFFSET_0.30_CHASE_w234_s50_i180|ATR_TRAIL_SL_1.5_ARM_0.75_TRAIL_1",
    "OFFSET_0.30_CHASE_w234_s50_i180|HYBRID_secure_25_25_runner_TRAIL_1",
    "OFFSET_0.30_CHASE_w234_s50_i180|ATR_TP_2.5_GIVEBACK_20PCT",
    "ADAPTIVE_RV15_P40_P90_FZ1.5_T5BPS_M1TICK_G40|ATR_TRAIL_SL_1.5_ARM_0.75_TRAIL_1",
    "ADAPTIVE_RV15_P40_P90_FZ1.5_T5BPS_M1TICK_G40|ATR_TRAIL_SL_1.5_ARM_0.75_TRAIL_1_SCENARIO_C_CAP1",
    "ADAPTIVE_RV15_P40_P90_FZ1.5_T5BPS_M1TICK_G40|ATR_TRAIL_SL_1.5_ARM_0.75_TRAIL_1_SCENARIO_C_BE4_LOCK1_CAP1",
    "INVERT_SCORE_LED_SIDE_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP_SCENARIO_C_CAP5",
})


PARTIAL_EXIT_RELAY_CAPABILITY = "BLOCKED_PARTIAL_REDUCTION_UNPROVEN"


def tile_has_partial_exits(spec: dict) -> bool:
    """True when the tile's exit policy closes a position in more than one part."""
    exit_policy = spec.get("exit_policy") or {}
    return bool(exit_policy.get("partial_take_profits")) or exit_policy.get("family") == "HYBRID_RUNNER"


def validate_tile_registry() -> tuple[str, ...]:
    """Return registry defects; an empty tuple is the only deployable state."""
    defects = []
    lanes = tuple(ACTIVE_TILE_REGISTRY)
    if tuple(ACTIVE_TILE_ORDER) != tuple(dict.fromkeys(ACTIVE_TILE_ORDER)):
        defects.append("DUPLICATE_TILE_IN_DISPLAY_ORDER")
    if set(ACTIVE_TILE_ORDER) != set(lanes):
        defects.append("DISPLAY_ORDER_REGISTRY_MISMATCH")
    required = {
        "tile_id", "label", "raw_policy_id", "policy_signature", "policy_epoch",
        "research_lane", "execution_scope", "paper_eligible", "live_copy_eligible",
        "relay_capability", "requested_margin_usd", "risk_limits", "analyzer_cohort",
        "presentation", "retirement_status", "entry_policy", "exit_policy",
        "id_prefix", "toggle_key", "lifecycle_state", "implementation_modules",
        "dedicated_test_modules",
        "component_surfaces", "max_active_signals",
    }
    prefixes = {}
    for lane, spec in ACTIVE_TILE_REGISTRY.items():
        missing = sorted(required.difference(spec))
        if missing:
            defects.append(f"{lane}:MISSING:{','.join(missing)}")
        prefix = str(spec.get("id_prefix") or "")
        if prefix in prefixes:
            defects.append(f"DUPLICATE_ID_PREFIX:{prefix}:{prefixes[prefix]}:{lane}")
        prefixes[prefix] = lane
        cap = spec.get("max_active_signals")
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            defects.append(f"{lane}:INVALID_MAX_ACTIVE_SIGNALS")
        if spec.get("paper_only") and spec.get("platform_relay_eligible"):
            defects.append(f"{lane}:PAPER_ONLY_RELAY_CONTRADICTION")
        if not spec.get("paper_only") or spec.get("execution_scope") != "PAPER_ONLY":
            defects.append(f"{lane}:NOT_STRICTLY_PAPER_ONLY")
        if spec.get("live_copy_eligible") or spec.get("platform_relay_eligible"):
            defects.append(f"{lane}:LIVE_COPY_MUST_FAIL_CLOSED")
        # Default ON is an owner choice for paper collection only; it can never
        # stand in for relay qualification.
        if spec.get("default_enabled") and (
            not spec.get("paper_only")
            or spec.get("platform_relay_eligible")
            or spec.get("live_copy_eligible")
            or spec.get("relay_capability") != "BLOCKED_UNQUALIFIED"
        ):
            defects.append(f"{lane}:DEFAULT_ON_REQUIRES_PAPER_ONLY_RELAY_BLOCKED")
        # Exchange-side partial reductions are not wired; a partial-exit tile
        # can never be relay-capable until they are.
        if tile_has_partial_exits(spec):
            if spec.get("platform_relay_eligible") or spec.get("live_copy_eligible"):
                defects.append(f"{lane}:PARTIAL_EXIT_RELAY_REQUIRES_EXCHANGE_REDUCTIONS")
            if spec.get("relay_capability") != PARTIAL_EXIT_RELAY_CAPABILITY:
                defects.append(f"{lane}:PARTIAL_EXIT_RELAY_CAPABILITY_NOT_BLOCKED")
        if spec.get("tile_id") != lane or spec.get("research_lane") != lane:
            defects.append(f"{lane}:TILE_IDENTITY_MISMATCH")
        state = str(spec.get("lifecycle_state") or "")
        if state not in TILE_LIFECYCLE_STATES:
            defects.append(f"{lane}:INVALID_LIFECYCLE_STATE:{state}")
        if state == "PAPER_ONLY" and not spec.get("paper_only"):
            defects.append(f"{lane}:PAPER_ONLY_STATE_WITHOUT_GATE")
        if state == "BENCHMARK" and not spec.get("is_benchmark"):
            defects.append(f"{lane}:BENCHMARK_STATE_WITHOUT_ROLE")
        surfaces = tuple(spec.get("component_surfaces") or ())
        if surfaces != TILE_COMPONENT_SURFACES:
            missing_surfaces = sorted(set(TILE_COMPONENT_SURFACES).difference(surfaces))
            extra_surfaces = sorted(set(surfaces).difference(TILE_COMPONENT_SURFACES))
            defects.append(
                f"{lane}:COMPONENT_SURFACE_CONTRACT_MISMATCH:"
                f"missing={','.join(missing_surfaces) or '-'}:"
                f"extra={','.join(extra_surfaces) or '-'}"
            )
    overlap = set(lanes).intersection(RETIRED_TILE_LANES)
    if overlap:
        defects.append("ACTIVE_RETIRED_OVERLAP:" + ",".join(sorted(overlap)))
    active_policy_ids = {str(spec.get("raw_policy_id") or "") for spec in ACTIVE_TILE_REGISTRY.values()}
    policy_overlap = active_policy_ids.intersection(RETIRED_POLICY_IDENTITIES)
    if policy_overlap:
        defects.append("ACTIVE_RETIRED_POLICY_OVERLAP:" + ",".join(sorted(policy_overlap)))
    return tuple(defects)


def tile_max_active_signals(lane) -> int | None:
    """Registry-owned per-tile capacity; None for lanes outside the registry."""
    spec = ACTIVE_TILE_REGISTRY.get(str(lane or "").upper())
    if not spec:
        return None
    return int(spec["max_active_signals"])


def active_tile_lifecycle_manifest() -> tuple[dict, ...]:
    """Stable cross-layer roster used by audits, APIs, dashboards and analyzers."""
    return tuple(
        {
            "lane": lane,
            "display_order": index,
            "label": ACTIVE_TILE_REGISTRY[lane]["label"],
            "raw_policy_id": ACTIVE_TILE_REGISTRY[lane]["raw_policy_id"],
            "policy_signature": ACTIVE_TILE_REGISTRY[lane]["policy_signature"],
            "policy_epoch": ACTIVE_TILE_REGISTRY[lane]["policy_epoch"],
            "admission_treatment": ACTIVE_TILE_REGISTRY[lane]["admission_treatment"],
            "id_prefix": ACTIVE_TILE_REGISTRY[lane]["id_prefix"],
            "toggle_key": ACTIVE_TILE_REGISTRY[lane]["toggle_key"],
            "lifecycle_state": ACTIVE_TILE_REGISTRY[lane]["lifecycle_state"],
            "paper_only": bool(ACTIVE_TILE_REGISTRY[lane].get("paper_only", False)),
            "relay_eligible": bool(ACTIVE_TILE_REGISTRY[lane].get("platform_relay_eligible", False)),
            "relay_capability": ACTIVE_TILE_REGISTRY[lane]["relay_capability"],
            "requested_margin_usd": ACTIVE_TILE_REGISTRY[lane]["requested_margin_usd"],
            "risk_limits": ACTIVE_TILE_REGISTRY[lane]["risk_limits"],
            "analyzer_cohort": ACTIVE_TILE_REGISTRY[lane]["analyzer_cohort"],
            "entry_policy": ACTIVE_TILE_REGISTRY[lane]["entry_policy"],
            "exit_policy": ACTIVE_TILE_REGISTRY[lane]["exit_policy"],
            "presentation": ACTIVE_TILE_REGISTRY[lane]["presentation"],
            "ladder": tuple(ACTIVE_TILE_REGISTRY[lane].get("ladder") or ()),
            "ladder_label": ACTIVE_TILE_REGISTRY[lane].get("ladder_label"),
            "ladder_profile_id": ACTIVE_TILE_REGISTRY[lane].get("ladder_profile_id"),
            "implementation_modules": tuple(ACTIVE_TILE_REGISTRY[lane]["implementation_modules"]),
            "dedicated_test_modules": tuple(ACTIVE_TILE_REGISTRY[lane]["dedicated_test_modules"]),
            "component_surfaces": tuple(ACTIVE_TILE_REGISTRY[lane]["component_surfaces"]),
        }
        for index, lane in enumerate(ACTIVE_TILE_ORDER, start=1)
    )


def active_tile_registry_signature() -> str:
    """Deterministic identity shared by runtime, mirror, analyzer and monitors."""
    payload = {
        "schema": TILE_REGISTRY_SCHEMA,
        "architecture_version": TILE_ARCHITECTURE_VERSION,
        "tiles": active_tile_lifecycle_manifest(),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

COMBO_CHASE_DELAY_LANES = ()
COMBO_CHASE_ISOLATION_PAIRS = ()
ACTIVE_CHASE_ISOLATION_PAIRS = ()
ACTIVE_CHASE_ISOLATION_LANES = ()
COMBO_CHASE_DIRECT_REFERENCE = None

COMBO_LANE_LABELS = {lane: spec["label"] for lane, spec in COMBO_LANE_SPECS.items()}
COMBO_LANE_LABELS[RESEARCH_LANE_AI_SCAN] = "AI Scan (no orders)"

_COMBO_TOGGLE_DEFAULTS = {
    lane: bool(COMBO_LANE_SPECS[lane].get("default_enabled", False))
    for lane in COMBO_EXECUTION_LANES
}


def is_deterministic_bracket_lane(lane: str) -> bool:
    """Bracket tiles — own tick loop, never AI_SCAN fan-out or independent AI."""
    lane_u = str(lane or "").upper()
    spec = COMBO_LANE_SPECS.get(lane_u) or {}
    return bool(spec.get("is_deterministic_bracket"))


def is_static_bracket_lane(lane: str) -> bool:
    """Resting-limit bracket variant — never chase/reprice after submission."""
    lane_u = str(lane or "").upper()
    spec = COMBO_LANE_SPECS.get(lane_u) or {}
    return str(spec.get("chase_mode") or "").upper() == "STATIC"


def is_independent_ai_lane(lane: str) -> bool:
    """Lanes with their own DeepSeek prompt — never inherit AI_SCAN / CONTINUOUS decisions."""
    lane_u = str(lane or "").upper()
    if is_deterministic_bracket_lane(lane_u):
        return False
    spec = COMBO_LANE_SPECS.get(lane_u) or {}
    return bool(spec.get("is_independent_ai"))


def is_shared_ai_direction_lane(lane: str) -> bool:
    """True for lanes that consume AI_SCAN direction without sharing policy state."""
    lane_u = str(lane or "").upper()
    spec = COMBO_LANE_SPECS.get(lane_u) or {}
    return bool(spec.get("uses_shared_ai_direction"))


def is_cross_venue_clock_lane(lane: str) -> bool:
    """Tiles triggered by the per-second cross-venue evaluator, never by an AI call."""
    spec = ACTIVE_TILE_REGISTRY.get(str(lane or "").upper()) or {}
    return spec.get("signal_clock") == CROSS_VENUE_SIGNAL_CLOCK


def cross_venue_clock_lanes() -> tuple[str, ...]:
    return tuple(lane for lane in ACTIVE_TILE_ORDER if is_cross_venue_clock_lane(lane))


def _session_from_features(features: dict) -> str:
    """Derive session bucket aligned with bot `_research_session_bucket` labels.

    Returns ASIA / LONDON / OVERLAP / NEW_YORK (or unknown).
    """
    if not features:
        return "unknown"
    # Prefer already-computed research bucket when present.
    sess = features.get("session_bucket")
    if not sess:
        rb = features.get("research_buckets") or {}
        sess = rb.get("session_bucket")
    if sess:
        return str(sess).upper()
    ts = (
        features.get("ts_utc")
        or features.get("ts")
        or features.get("signal_ts")
        or features.get("entry_ts")
    )
    if not ts:
        return "unknown"
    try:
        from datetime import datetime, timezone
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return "unknown"
        h = dt.astimezone(timezone.utc).hour
        if h < 8:
            return "ASIA"
        if h < 13:
            return "LONDON"
        if h < 16:
            return "OVERLAP"
        if h < 22:
            return "NEW_YORK"
        return "ASIA"
    except Exception:
        return "unknown"


def _bucket_adx(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "unknown"
    if v < 15:
        return "lt_15"
    if v < 20:
        return "15_20"
    if v < 25:
        return "20_25"
    if v < 30:
        return "25_30"
    if v < 40:
        return "30_40"
    return "gte_40"


def _bucket_spread(v):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return "unknown"
    if v <= 2:
        return "lte_2"
    if v <= 4:
        return "3_4"
    return "gte_5"


def _apply_extra_filters(lane: str, ai: dict, final_direction: str, spread: int,
                          features: dict = None, signal_age_sec: float = None) -> tuple:
    """Apply data-grounded extra_filters declared in the lane spec.

    Returns (passes: bool, block_reason: str).
    """
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper()) or {}
    xf = spec.get("extra_filters") or {}
    if not xf:
        return True, ""

    features = features or {}

    adx_max = xf.get("adx_max")
    if adx_max is not None:
        adx = (
            features.get("adx_at_entry")
            or features.get("adx")
            or features.get("mom_adx")
        )
        if adx is None:
            mc = features.get("market_context") or {}
            adx = (mc.get("trend_strength") or {}).get("adx")
        if adx is not None:
            try:
                if float(adx) > float(adx_max):
                    return False, f"ADX_OVER_CAP ({float(adx):.1f} > {adx_max})"
            except (TypeError, ValueError):
                pass

    struct_bl = xf.get("structure_blacklist") or []
    if struct_bl:
        struct = (
            features.get("structure_bias_at_entry")
            or features.get("structure_bias")
            or features.get("mtf_structure")
        )
        if struct and str(struct).upper() in [s.upper() for s in struct_bl]:
            return False, f"STRUCTURE_BLACKLISTED ({struct})"

    sess_bl = xf.get("session_blacklist") or []
    if sess_bl:
        sess = _session_from_features(features)
        if sess in [s.upper() for s in sess_bl]:
            return False, f"SESSION_BLACKLISTED ({sess})"

    age_min = xf.get("signal_age_min_sec")
    if age_min is not None and signal_age_sec is not None:
        try:
            if float(signal_age_sec) < float(age_min):
                return False, f"SIGNAL_TOO_YOUNG ({float(signal_age_sec):.0f}s < {age_min}s)"
        except (TypeError, ValueError):
            pass

    fp_path = xf.get("sl_fingerprint_report_path")
    fp_max = xf.get("sl_fingerprint_match_max")
    if fp_path and fp_max is not None:
        try:
            import json
            import os
            resolved = fp_path
            if not os.path.isabs(resolved):
                # Resolve relative to this module / agent cwd so LAB filters work
                # regardless of process working directory.
                candidates = [
                    resolved,
                    os.path.join(os.path.dirname(__file__), resolved),
                    os.path.join(os.getcwd(), resolved),
                ]
                resolved = next((p for p in candidates if os.path.exists(p)), resolved)
            if os.path.exists(resolved):
                with open(resolved, "r", encoding="utf-8") as f:
                    fp_report = json.load(f)
                rules = ((fp_report.get("fingerprint_spec") or {}).get("rules")) or []
                if rules:
                    feature_lookup = {
                        "session": _session_from_features(features),
                        "adx_bucket": _bucket_adx(
                            features.get("adx_at_entry") or features.get("adx")
                        ),
                        "spread_bucket": _bucket_spread(spread),
                        "struct": (
                            features.get("structure_bias_at_entry")
                            or features.get("structure_bias")
                            or "UNKNOWN"
                        ),
                        "direction": final_direction,
                    }
                    matches = 0
                    for rule in rules:
                        feat = rule.get("feature")
                        val = str(rule.get("value") or "").upper()
                        actual = str(feature_lookup.get(feat, "") or "").upper()
                        if actual == val:
                            matches += 1
                    if matches > int(fp_max):
                        return False, f"SL_FINGERPRINT_MATCH ({matches} > {fp_max})"
        except Exception:
            pass

    return True, ""


def _normalized_directional_spread(ai: dict, final_direction: str) -> int:
    """Return the legacy 0-10 spread from either shared or legacy scores.

    The direction-only shared prompt emits LONG/SHORT scores on 0-100. The
    older combo matcher only inspected bull/bear, so a research candidate could
    pass its authoritative >=2 policy gate and then be contradicted here as
    SPREAD_UNDER_MIN (0 < 2). Keep one normalization contract at this boundary.
    """
    ai = ai or {}
    factors = ai.get("factors") if isinstance(ai.get("factors"), dict) else {}
    long_score = int(ai.get("long_score") or factors.get("long_score") or 0)
    short_score = int(ai.get("short_score") or factors.get("short_score") or 0)
    direction = str(final_direction or "").upper()
    if long_score > 0 or short_score > 0:
        raw_gap = (
            long_score - short_score
            if direction == "LONG"
            else short_score - long_score
        )
        sign = -1 if raw_gap < 0 else 1
        return sign * (abs(raw_gap) // 10)
    bull = int(ai.get("bull_score") or factors.get("bull_score") or 0)
    bear = int(ai.get("bear_score") or factors.get("bear_score") or 0)
    return bull - bear if direction == "LONG" else bear - bull


def combo_lane_matches(lane: str, ai: dict, final_direction: str, spread: int = None,
                       features: dict = None, signal_age_sec: float = None) -> bool:
    """Match AI_SCAN-inherited combo tiles. Independent-AI lanes always return False here.

    Optional `features` / `signal_age_sec` enable data-grounded `extra_filters`
    (SL_AVOIDANCE_V1). Backward compatible when those kwargs are omitted.
    """
    lane_u = str(lane or "").upper()
    if is_independent_ai_lane(lane_u) or is_deterministic_bracket_lane(lane_u):
        return False
    spec = COMBO_LANE_SPECS.get(lane_u)
    if not spec or not ai or spec.get("is_legacy") or spec.get("is_shadow_only"):
        return False
    try:
        prob = int(ai.get("win_prob") or 0)
    except (TypeError, ValueError):
        prob = 0
    if prob < spec["ai_min"] or prob >= spec["ai_max"]:
        return False
    if spread is None:
        spread = _normalized_directional_spread(ai, final_direction)
    spread = int(spread or 0)
    if not (spec["spread_min"] <= spread <= spec["spread_max"]):
        return False
    passes, _ = _apply_extra_filters(
        lane_u, ai, final_direction, spread, features, signal_age_sec
    )
    return passes


def combo_lane_match_detail(lane: str, ai: dict, final_direction: str, spread: int = None,
                            features: dict = None, signal_age_sec: float = None) -> dict:
    """Like combo_lane_matches but returns {passes, block_reason} for telemetry."""
    lane_u = str(lane or "").upper()
    if is_independent_ai_lane(lane_u):
        return {"passes": False, "block_reason": "INDEPENDENT_AI_LANE"}
    if is_deterministic_bracket_lane(lane_u):
        return {"passes": False, "block_reason": "DETERMINISTIC_BRACKET_LANE"}
    spec = COMBO_LANE_SPECS.get(lane_u)
    if not spec:
        return {"passes": False, "block_reason": "LANE_NOT_FOUND"}
    if spec.get("is_legacy"):
        return {"passes": False, "block_reason": "LANE_LEGACY"}
    if spec.get("is_shadow_only"):
        return {"passes": False, "block_reason": "LANE_SHADOW_ONLY"}
    if not ai:
        return {"passes": False, "block_reason": "NO_AI"}
    try:
        prob = int(ai.get("win_prob") or 0)
    except (TypeError, ValueError):
        prob = 0
    if prob < spec["ai_min"]:
        return {"passes": False, "block_reason": f"AI_UNDER_MIN ({prob} < {spec['ai_min']})"}
    if prob >= spec["ai_max"]:
        return {"passes": False, "block_reason": f"AI_OVER_MAX ({prob} >= {spec['ai_max']})"}
    if spread is None:
        spread = _normalized_directional_spread(ai, final_direction)
    spread = int(spread or 0)
    if spread < spec["spread_min"]:
        return {
            "passes": False,
            "block_reason": f"SPREAD_UNDER_MIN ({spread} < {spec['spread_min']})",
            "directional_spread": spread,
        }
    if spread > spec["spread_max"]:
        return {
            "passes": False,
            "block_reason": f"SPREAD_OVER_MAX ({spread} > {spec['spread_max']})",
            "directional_spread": spread,
        }
    passes, block_reason = _apply_extra_filters(
        lane_u, ai, final_direction, spread, features, signal_age_sec
    )
    return {
        "passes": passes,
        "block_reason": block_reason,
        "directional_spread": spread,
    }


def is_shadow_only_lane(lane: str) -> bool:
    """Shadow/research telemetry lanes -- never order-capable by construction."""
    lane_u = str(lane or "").upper()
    spec = COMBO_LANE_SPECS.get(lane_u) or {}
    return bool(spec.get("is_shadow_only"))


def is_combo_execution_lane(lane: str) -> bool:
    lane_u = str(lane or "").upper()
    if lane_u not in COMBO_LANE_SPECS:
        return False
    if is_shadow_only_lane(lane_u):
        return False
    return lane_u in COMBO_EXECUTION_LANES


def is_ai_scan_lane(lane: str) -> bool:
    return str(lane or "").upper() == RESEARCH_LANE_AI_SCAN


def combo_entry_mode(lane: str) -> str:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    return str(spec.get("entry_mode") or "IMMEDIATE")


def is_chase_3plus_entry_lane(lane: str) -> bool:
    return combo_entry_mode(lane) == "CHASE_3PLUS"


def is_virtual_chase_entry_lane(lane: str) -> bool:
    return combo_entry_mode(lane) == "VIRTUAL_CHASE"


def is_immediate_entry_lane(lane: str) -> bool:
    mode = combo_entry_mode(lane)
    return mode in ("IMMEDIATE", "VIRTUAL_CHASE")


def is_benchmark_lane(lane: str) -> bool:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    return bool(spec.get("is_benchmark")) or str(lane or "").upper() == BENCHMARK_LANE


def is_research_candidate_lane(lane: str) -> bool:
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    return bool(spec.get("is_research_candidate"))


def get_lane_ladder_override(lane: str):
    """Per-lane Scenario C ladder override, or None to fall back to the global ladder.

    Returns a tuple (ladder, ladder_label, ladder_profile_id) when the lane spec declares
    a `ladder` override; otherwise None. Kept optional — lanes without an override use the
    shared global TRAIL_LADDER_SCENARIO_C.
    """
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper(), {})
    ladder = spec.get("ladder")
    if not ladder:
        return None
    return (
        list(ladder),
        str(spec.get("ladder_label") or ""),
        str(spec.get("ladder_profile_id") or ""),
    )


# ============================================================================
# [ADD_2026-07-08] Per-lane position sizing (Phase 2)
# ============================================================================
SIZE_MULT_MIN = 0.1
SIZE_MULT_MAX = 2.0


def resolve_lane_size_multiplier(lane: str, features: dict = None) -> float:
    """Compute the position-size multiplier for a lane given signal features.

    Returns a float in [SIZE_MULT_MIN, SIZE_MULT_MAX]. Lanes without a
    `size_multipliers` spec return 1.0 (no change).
    """
    spec = COMBO_LANE_SPECS.get(str(lane or "").upper()) or {}
    multipliers_cfg = spec.get("size_multipliers")
    if not multipliers_cfg:
        return 1.0

    features = features or {}
    combined = 1.0
    for feat_name, value_map in multipliers_cfg.items():
        actual = features.get(feat_name)
        if actual is None:
            rb = features.get("research_buckets") or {}
            actual = rb.get(feat_name) or rb.get(feat_name.replace("_bucket", ""))
        if actual is None and feat_name == "session_bucket":
            actual = _session_from_features(features)
        if actual is None:
            continue
        actual_str = str(actual).upper()
        mult = None
        for k, v in value_map.items():
            if str(k).upper() == actual_str:
                mult = v
                break
        if mult is None:
            mult = value_map.get("default", 1.0)
        try:
            combined *= float(mult)
        except (TypeError, ValueError):
            pass

    return max(SIZE_MULT_MIN, min(SIZE_MULT_MAX, combined))


def combo_toggle_defaults() -> dict:
    return dict(_COMBO_TOGGLE_DEFAULTS)


def any_combo_execution_enabled(enabled_map: dict = None, continuous_enabled: bool = False) -> bool:
    merged = combo_toggle_defaults()
    if enabled_map:
        for lane, val in enabled_map.items():
            if lane in merged:
                merged[lane] = bool(val)
    return any(merged.values())
