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
RESEARCH_LANE_FAMILY_CONTINUOUS_AUG_ORIGINAL = "FAMILY_CONTINUOUS_AUG_ORIGINAL"
RESEARCH_LANE_FAMILY_COMMITTED_FADE_MAKER_90 = "FAMILY_COMMITTED_FADE_MAKER_90"
COMMITTED_FADE_MAKER_ADMISSION_POLICY_ID = "INVERTED_COMMITTED_SCORE_LED_SIDE_MAKER_V1"
RESEARCH_LANE_FAMILY_NOTRADE_FOLLOW_MAKER_60 = "FAMILY_NOTRADE_FOLLOW_MAKER_60"
RESEARCH_LANE_FAMILY_XVENUE_SESSION_FOLLOW_60M = "FAMILY_XVENUE_SESSION_FOLLOW_60M"
RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90 = "FAMILY_COMMITTED_FADE_TAKER_90"
RESEARCH_LANE_FAMILY_DANISH_CF = "FAMILY_DANISH_CF"
RESEARCH_LANE_FAMILY_DANISH_CF_NOES = "FAMILY_DANISH_CF_NOES"
RESEARCH_LANE_FAMILY_DANISH_CF_ALL_SESSIONS = "FAMILY_DANISH_CF_ALL_SESSIONS"
DANISH_CF_ADMISSION_POLICY_ID = "INVERTED_COMMITTED_SCORE_LED_SIDE_CONFIRM_MARKET_V1"
NOTRADE_FOLLOW_MAKER_ADMISSION_POLICY_ID = "SCORE_LED_SIDE_ON_RAW_AI_NO_TRADE_MAKER_CHASE_V1"
XVENUE_SESSION_FOLLOW_ADMISSION_POLICY_ID = "CROSS_VENUE_LEAD_OR_PREMIUM_SESSION_MAP_NO_AI_V1"
COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID = "INVERTED_COMMITTED_SCORE_LED_SIDE_TAKER_V1"
CONTINUOUS_AUG_ADMISSION_POLICY_ID = "OWN_AI_CALL_AUG_V3_HIGHER_SCORE_GAP5_V1"
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

# Display order; every user-facing "Tile N" number is derived from it
# (tile_number), never hard-coded.
COMBO_EXECUTION_LANES = (
    RESEARCH_LANE_FAMILY_DANISH_CF,
    RESEARCH_LANE_FAMILY_DANISH_CF_NOES,
    RESEARCH_LANE_FAMILY_DANISH_CF_ALL_SESSIONS,
    RESEARCH_LANE_FAMILY_CONTINUOUS_AUG_ORIGINAL,
    RESEARCH_LANE_FAMILY_COMMITTED_FADE_MAKER_90,
    RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90,
    RESEARCH_LANE_FAMILY_NOTRADE_FOLLOW_MAKER_60,
    RESEARCH_LANE_FAMILY_XVENUE_SESSION_FOLLOW_60M,
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
          signal_clock: str | None = None,
          signal_summary: str = "",
          live_exit_order: tuple[str, ...] = (),
          shadow_exits: tuple[str, ...] = (),
          early_cut_shadow_reason: str | None = None) -> dict:
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
        # Card metadata (outside the policy signature): plain-English signal,
        # live exit rules in first-trigger-wins order, and shadow-only exits.
        "signal_summary": signal_summary,
        "live_exit_order": tuple(live_exit_order),
        "shadow_exits": tuple(shadow_exits),
        "early_cut_shadow_reason": early_cut_shadow_reason,
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


RESEARCH_STACK_VERSION = "v31-danish-tiles-late-protection-v11"

# Shadow-only exits: evaluated on every filled trade's path by the shadow-exit
# recorder and never executed. Units follow research/genome_grid_study.py
# (bp of price == margin % at 100x); each spec runs with the tile's own time
# backstop and its 40 bp hard stop unless it overrides the hard stop.
SHADOW_EXIT_SCHEMA = "tile_shadow_exit_set_v1"
SHADOW_EXIT_SET = {
    "LATE_BE_20_5": {"family": "LATE_BREAKEVEN", "be_arm_bp": 20.0, "be_floor_bp": 5.0,
                     "label": "Break-even armed at +20 bp, stop to +5 bp"},
    "LATE_BE_25_3": {"family": "LATE_BREAKEVEN", "be_arm_bp": 25.0, "be_floor_bp": 3.0,
                     "label": "Break-even armed at +25 bp, stop to +3 bp"},
    "LATE_BE_30_5": {"family": "LATE_BREAKEVEN", "be_arm_bp": 30.0, "be_floor_bp": 5.0,
                     "label": "Break-even armed at +30 bp, stop to +5 bp"},
    "LATE_TRAIL_1.5ATR_ARM2ATR": {"family": "LATE_ATR_TRAIL", "trail_k": 1.5, "trail_arm_atr": 2.0,
                                  "label": "ATR trail 1.5 ATR behind the peak, armed after +2.0 ATR"},
    "LATE_TRAIL_2ATR_ARM2ATR": {"family": "LATE_ATR_TRAIL", "trail_k": 2.0, "trail_arm_atr": 2.0,
                                "label": "ATR trail 2.0 ATR behind the peak, armed after +2.0 ATR"},
    "LATE_GIVEBACK_KEEP50_ARM20": {"family": "LATE_GIVEBACK", "gb_keep": 0.5, "gb_arm_bp": 20.0,
                                   "label": "Give-back stop keeping 50% of the peak, armed after +20 bp"},
    "LATE_LADDER_20_5_30_15_45_30": {"family": "LATE_LADDER", "ladder": ((20.0, 5.0), (30.0, 15.0), (45.0, 30.0)),
                                     "label": "Profit-lock ladder +20→+5, +30→+15, +45→+30 bp"},
    "COND_CUT_12_5M_MFE2": {"family": "CONDITIONAL_EARLY_CUT", "thesis_bp": -12.0, "thesis_sec": 300,
                            "thesis_max_mfe": 2.0,
                            "label": "Early cut at −12 bp within 5 min, only if the trade never ran past +2 bp"},
    "COND_CUT_10_3M_MFE2": {"family": "CONDITIONAL_EARLY_CUT", "thesis_bp": -10.0, "thesis_sec": 180,
                            "thesis_max_mfe": 2.0,
                            "label": "Early cut at −10 bp within 3 min, only if the trade never ran past +2 bp"},
    "ATR_STOP_1.5": {"family": "ATR_STOP", "atr_stop_k": 1.5, "label": "Protective stop 1.5 ATR below entry"},
    "ATR_HARD_STOP_3ATR_25_60": {"family": "ATR_HARD_STOP", "hard_atr_k": 3.0, "hard_clamp_bp": (25.0, 60.0),
                                 "label": "Hard stop 3 ATR, clamped to 25-60 bp (replaces the 40 bp stop in shadow)"},
    "COMPOSITE_LATE_BE20_5_TRAIL1.5_ARM2": {
        "family": "LATE_COMPOSITE", "be_arm_bp": 20.0, "be_floor_bp": 5.0, "trail_k": 1.5, "trail_arm_atr": 2.0,
        "label": "Composite: break-even +20→+5 bp and ATR trail 1.5 armed at +2.0 ATR"},
}
ALL_SHADOW_EXITS = tuple(SHADOW_EXIT_SET)


def _shadow_exits_except(*live: str) -> tuple[str, ...]:
    return tuple(x for x in ALL_SHADOW_EXITS if x not in live)


# Exit rule ids for the card and the first-trigger-wins order the runtime's
# family_policy_common.exit_action evaluates.
LIVE_EXIT_RULES = frozenset({
    "HARD_STOP", "BREAKEVEN_LOCK", "ATR_TRAIL", "EARLY_CUT", "TIME_EXIT",
    # Continuous (Aug-2026 replica) rules, evaluated by its own policy module.
    "EARLY_FAIL", "STOP_LOSS", "PROFIT_LOCK_LADDER", "THESIS_FAST_CUT", "THESIS_INVALIDATED", "PEAK_FLOOR",
})
RISK_EXIT_RULES = frozenset({"HARD_STOP", "EARLY_CUT", "EARLY_FAIL", "STOP_LOSS", "THESIS_FAST_CUT"})


def registry_live_exit_order(exit_policy: dict) -> tuple[str, ...]:
    """First-trigger-wins order of family_policy_common.exit_action for a registry exit policy."""
    order = ["HARD_STOP"]
    if exit_policy.get("breakeven"):
        order.append("BREAKEVEN_LOCK")
    if exit_policy.get("trail"):
        order.append("ATR_TRAIL")
    if exit_policy.get("early_cut"):
        order.append("EARLY_CUT")
    order.append("TIME_EXIT")
    return tuple(order)


# Shared-call commit telemetry (decision snapshots): explicit side matching the
# score-led side with at least this score gap.
AI_COMMIT_MIN_SCORE_GAP = 30.0
# The shared AI prompt inputs are not part of any tile's execution signature,
# but tiles that read the shared call's side see them. Every change to the live
# prompt's inputs bumps this revision; the analyzer splits AI-driven tile cohorts
# on it (diagnostics/AI-INPUT-REVISION-RECEIPT-*.md records each boundary).
AI_PROMPT_INPUT_REVISION = "shared_direction_inputs_r2_20261002"
AI_PROMPT_INPUT_REVISION_HISTORY = (
    ("shared_direction_inputs_r1", "prompt v4_1 inputs before 2026-10-02 (ret_1m context 0, lower_high always False, volume_ratio vs forming bar)"),
    (AI_PROMPT_INPUT_REVISION, "ret_1m/ret_5m from the 1 s tape, two-sided swing-structure detector, volume_ratio on closed bars"),
)


# Continuous = exact replica of the early-August 2026 Continuous (d018ef31, before
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
# Registered in v7; pinned so the v8 retirement, the v9 committed-fade maker
# addition and the v10 hypothesis tiles do not split its cohort.
CONTINUOUS_AUG_POLICY_EPOCH = "v31-continuous-aug-original-v7"
# Registered in v9; pinned so later additions do not split its cohort.
COMMITTED_FADE_MAKER_POLICY_EPOCH = "v31-committed-fade-maker-v9"
# H9-H11 and the Danish tiles were never deployed before v11; they register
# together in this release.
HYPOTHESIS_TILES_POLICY_EPOCH = RESEARCH_STACK_VERSION
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
        signal_summary=("own DeepSeek call with the verbatim August v3 prompt after every shared three-minute call; "
                        "trades the higher score's side"),
        live_exit_order=tuple(_CONTINUOUS_AUG_EXIT["exit_order"]),
        shadow_exits=(),
        early_cut_shadow_reason=None,
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


def _committed_fade_maker_pre_registration(hypothesis_id: str) -> dict:
    """PROFIT-TILE-DESIGN-20261003; verdicts use post-registration REALISTIC_V1 paper trades only."""
    pre = {
        "schema": "tile_pre_registration_committed_fade_maker_v1",
        "hypothesis_id": hypothesis_id,
        "registered_utc": "2026-10-03T00:00:00Z",
        "registered_cohort": COMMITTED_FADE_MAKER_POLICY_EPOCH,
        "control_lane": None,
        "control_meaning": ("No live control lane: the analyzer's REALISTIC_V1 counterfactual of the same committed "
                            "calls (taker 60-min fade and the unfaded side) is the comparison; Continuous is the baseline"),
        "evidence_world": "REALISTIC_V1",
        "ci_method": "1H_CLUSTER_BOOTSTRAP_95",
        "variants_tried": 3416,
        "honest_label": ("HINT — fails the pre-set bar: 3 days, in-sample +9.5 bp/trade (1 h-cluster CI [-13,+37]), "
                         "day-fold walk-forward +5.0 bp [-15,+30], deflated Sharpe ~0 after 3,416 variants, "
                         "edge shrank daily, 4 h unseen window negative"),
        "promotion": {
            "meaning": "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY",
            "min_fills": 150, "min_utc_days": 7,
            "min_sessions_each": 3, "sessions": ("ASIA", "EU", "US"),
            "per_fill_ev_lower_ci95_gt_bp": 0.0,
            "both_sides_mean_ge_bp": 0.0,
            "both_halves_positive": True,
            "shadow_5s_delay_mean_gt_bp": 0.0,
            "max_single_day_profit_share": 0.30,
            "max_replay_parity_gap_bp": 1.0,
        },
        "kill": {
            "k1_after_fills": 80, "k1_mean_bp_at_or_below": 0.0,
            "k2_after_fills": 150, "k2_upper_ci95_lt_bp": 2.0,
            "k3_worst_trade_bp_below": -60.0, "k3_max_stale_feed_fill_share": 0.01,
            "k4_max_drawdown_usd": 1.0,
            "k5_max_days_without_promotion": 21,
            "k6_defect_action": "PAUSE_AND_QUARANTINE_NOT_A_STRATEGY_VERDICT",
        },
    }
    promote, kill = pre["promotion"], pre["kill"]
    pre["promotion_summary"] = (
        f"Pre-registered {hypothesis_id}: >={promote['min_fills']} trades over >={promote['min_utc_days']} UTC days "
        f"incl. >={promote['min_sessions_each']} each of Asia/EU/US sessions; per-trade EV 1 h-cluster lower 95% CI >0 "
        f"(REALISTIC_V1); both sides and both halves >=0; 5 s-delay shadow mean >0; no day "
        f">{promote['max_single_day_profit_share']:.0%} of profit; replay parity <={promote['max_replay_parity_gap_bp']:g} bp; "
        "promotion = owner review, never relay"
    )
    pre["kill_summary"] = (
        f"K1 mean <={kill['k1_mean_bp_at_or_below']:g} bp after {kill['k1_after_fills']} trades; "
        f"K2 1 h-cluster upper 95% CI <+{kill['k2_upper_ci95_lt_bp']:g} bp after {kill['k2_after_fills']} trades; "
        f"K3 any trade worse than {kill['k3_worst_trade_bp_below']:g} bp (stop failure) or "
        f">{kill['k3_max_stale_feed_fill_share']:.0%} of trades on a stale feed; K4 drawdown >${kill['k4_max_drawdown_usd']:.2f}; "
        f"K5 day {kill['k5_max_days_without_promotion']} without promotion = INCONCLUSIVE; "
        "K6 lifecycle/identity/analyzer/feed/mirror defect = pause and quarantine"
    )
    return pre


# Committed fade (maker) = fade only committed AI calls (explicit LONG/SHORT matching the
# scores), entered with a passive limit 0.10% beyond the decision-time price
# that rests 30 minutes without chase, then a 90-minute time exit and a 40 bp
# catastrophic stop. PROFIT-TILE-DESIGN-20261003 under REALISTIC_V1 fills.
_COMMITTED_FADE_MAKER_ENTRY = {
    "mode": "MAKER_LIMIT_OFFSET",
    "offset_pct": 0.10, "offset_reference": "LAST_PRICE_AT_DECISION",
    "chase_windows": (), "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
    "maker_ttl_sec": 1800, "never_cross_touch": True, "marketable_fallback": False,
    "direction_source": "INVERTED_SCORE_LED_SIDE",
    "refuse_on": ("SCORE_TIE", "INVALID_SCORES", "AI_ERROR", "RAW_AI_NO_TRADE",
                  "SCORE_DIRECTION_MISMATCH", "BBO_STALE"),
    "trades_raw_ai_no_trade": False, "min_score_gap": None,
    "commit_rule": "EXPLICIT_RAW_SIDE_EQUALS_SCORE_LED",
    "max_bbo_age_sec": 5.0,
    "ai_decision_role": "FEATURE_ONLY",
    "fill_model": "REALISTIC_V1",
}
_COMMITTED_FADE_MAKER_EXIT = {
    "family": "TIME_EXIT_WITH_CATASTROPHIC_STOP",
    "max_duration_sec": 5400,
    "hard_stop_bps": 40.0, "hard_stop_margin_pct": 40.0,
    "ladder": None, "breakeven": None, "trail": None, "take_profit": None,
    "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_CROSSED_THE_STOP",
    "max_open_positions": 3,
}

# HYPOTHESIS-TILES-20261004 sessions: the runtime's own UTC session labels.
HYPOTHESIS_SESSION_HOURS_UTC = {"ASIA": (0, 8), "EU": (8, 16), "US": (16, 24)}


def _hypothesis_pre_registration(hypothesis_id: str, *, honest_label: str, control_meaning: str,
                                 variants_tried: int, min_fills: int, k1_after_fills: int,
                                 k2_after_fills: int | None, k4_max_drawdown_usd: float,
                                 sessions: tuple[str, ...] = ("ASIA", "EU", "US"),
                                 k5_max_days_without_promotion: int | None = 21,
                                 decisions: tuple[str, ...] = ()) -> dict:
    """HYPOTHESIS-TILES-20261004; verdicts use post-registration REALISTIC_V1 paper trades only.

    ``k2_after_fills`` / ``k5_max_days_without_promotion`` of None omit that
    kill rule (owner-specified tiles carry exactly the owner's kill rules plus
    the K3 stop/feed and K6 defect safety checks).
    """
    pre = {
        "schema": "tile_pre_registration_hypothesis_v1",
        "hypothesis_id": hypothesis_id,
        "registered_utc": "2026-10-04T00:00:00Z",
        "registered_cohort": HYPOTHESIS_TILES_POLICY_EPOCH,
        "control_lane": None,
        "control_meaning": control_meaning,
        "evidence_world": "REALISTIC_V1",
        "ci_method": "1H_CLUSTER_BOOTSTRAP_95",
        "variants_tried": variants_tried,
        "honest_label": honest_label,
        "pre_registered_decisions": tuple(decisions),
        "promotion": {
            "meaning": "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY",
            "min_fills": min_fills, "min_utc_days": 7,
            "min_sessions_each": 3, "sessions": tuple(sessions),
            "session_hours_utc": {s: HYPOTHESIS_SESSION_HOURS_UTC[s] for s in sessions},
            "per_fill_ev_lower_ci95_gt_bp": 0.0,
            "both_halves_positive": True,
            "max_single_day_profit_share": 0.30,
            "max_replay_parity_gap_bp": 1.0,
        },
        "kill": {
            "k1_after_fills": k1_after_fills, "k1_mean_bp_at_or_below": 0.0,
            "k2_after_fills": k2_after_fills, "k2_upper_ci95_lt_bp": 2.0,
            "k3_worst_trade_bp_below": -60.0, "k3_max_stale_feed_fill_share": 0.01,
            "k4_max_drawdown_usd": k4_max_drawdown_usd,
            "k5_max_days_without_promotion": k5_max_days_without_promotion,
            "k6_defect_action": "PAUSE_AND_QUARANTINE_NOT_A_STRATEGY_VERDICT",
        },
    }
    if k2_after_fills is None:
        pre["kill"]["k2_upper_ci95_lt_bp"] = None
    promote, kill = pre["promotion"], pre["kill"]
    session_names = "/".join(s.title() if s != "EU" and s != "US" else s for s in sessions)
    session_hours = "/".join(f"{lo}-{hi}" for lo, hi in promote["session_hours_utc"].values())
    pre["promotion_summary"] = (
        f"Pre-registered {hypothesis_id}: >={promote['min_fills']} trades over >={promote['min_utc_days']} UTC days "
        f"incl. >={promote['min_sessions_each']} each of {session_names} sessions (UTC {session_hours}); per-trade EV "
        "1 h-cluster lower 95% CI >0 (REALISTIC_V1); both halves positive; no day "
        f">{promote['max_single_day_profit_share']:.0%} of profit; replay parity <={promote['max_replay_parity_gap_bp']:g} bp; "
        "promotion = owner review, never relay"
    )
    parts = [f"K1 mean <={kill['k1_mean_bp_at_or_below']:g} bp after {kill['k1_after_fills']} trades"]
    if kill["k2_after_fills"] is not None:
        parts.append(f"K2 1 h-cluster upper 95% CI <+{kill['k2_upper_ci95_lt_bp']:g} bp after {kill['k2_after_fills']} trades")
    parts.append(f"K3 any trade worse than {kill['k3_worst_trade_bp_below']:g} bp (stop failure) or "
                 f">{kill['k3_max_stale_feed_fill_share']:.0%} of trades on a stale feed")
    parts.append(f"K4 drawdown >${kill['k4_max_drawdown_usd']:.2f}")
    if kill["k5_max_days_without_promotion"] is not None:
        parts.append(f"K5 day {kill['k5_max_days_without_promotion']} without promotion = INCONCLUSIVE")
    parts.append("K6 lifecycle/identity/analyzer/feed/mirror defect = pause and quarantine")
    pre["kill_summary"] = "; ".join(parts)
    return pre


# Late-armed protections adopted by the HYPOTHESIS-TILES-20261004 re-test
# (nested walk-forward by UTC day, REALISTIC_V1, capped per signal): none was
# significantly worse than the time exit, so each tile runs them as one
# composite, first trigger wins (family_policy_common.exit_action order).
LATE_BREAKEVEN_20_5 = {"trigger_margin_pct": 20.0, "lock_margin_pct": 5.0}
LATE_ATR_TRAIL_1_5_ARM_2 = {"atr_k": 1.5, "arm_atr_k": 2.0, "atr_source": "FILL_TIME_3M_ATR14"}
CONDITIONAL_EARLY_CUT_12_5M = {"cut_margin_pct": -12.0, "window_sec": 300, "max_peak_margin_pct": 2.0}


def _composite_exit(*, max_duration_sec: int, max_open_positions: int, breakeven: dict | None = None,
                    trail: dict | None = None, early_cut: dict | None = None,
                    volatility_scaling: str) -> dict:
    policy = {
        "family": "COMPOSITE_FIRST_TRIGGER_WINS",
        "max_duration_sec": int(max_duration_sec),
        "hard_stop_bps": 40.0, "hard_stop_margin_pct": 40.0,
        "ladder": None, "take_profit": None,
        "breakeven": dict(breakeven) if breakeven else None,
        "trail": dict(trail) if trail else None,
        "early_cut": dict(early_cut) if early_cut else None,
        "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_CROSSED_THE_STOP",
        "max_open_positions": int(max_open_positions),
        "volatility_scaling": volatility_scaling,
    }
    policy["exit_order"] = registry_live_exit_order(policy)
    return policy


# H9 = the score-led side of shared calls where the raw AI abstained
# (NO_TRADE), entered with a 0.15% passive limit chased 25% of the remaining
# gap every 3 minutes in minutes 10-25 (1 h TTL), then the late-armed
# composite exit, a 60-minute backstop and a 40 bp catastrophic stop.
_NOTRADE_FOLLOW_MAKER_ENTRY = {
    "mode": "MAKER_LIMIT_OFFSET_CHASE",
    "offset_pct": 0.15, "offset_reference": "LAST_PRICE_AT_DECISION",
    "chase_windows": (2, 3, 4), "remaining_gap_step_pct": 25.0, "reprice_sec": 180,
    "maker_ttl_sec": 3600, "never_cross_touch": True, "marketable_fallback": False,
    "direction_source": "SCORE_LED_SIDE",
    "refuse_on": ("RAW_AI_COMMITTED", "SCORE_TIE", "INVALID_SCORES", "AI_ERROR", "BBO_STALE"),
    "trades_raw_ai_no_trade": True, "trades_only_raw_ai_no_trade": True, "min_score_gap": None,
    "max_bbo_age_sec": 5.0,
    "ai_decision_role": "ABSTENTION_GATE_SIDE_FROM_SCORES",
    "volatility_scaling": "NONE - ATR/vol/ADX-scaled offsets and regime chase tested and rejected nested-OOS",
    "fill_model": "REALISTIC_V1",
}
_NOTRADE_FOLLOW_MAKER_EXIT = _composite_exit(
    max_duration_sec=3600, max_open_positions=10,
    breakeven=LATE_BREAKEVEN_20_5, trail=LATE_ATR_TRAIL_1_5_ARM_2,
    volatility_scaling="ATR trail armed after +2 ATR; the ATR-scaled hard stop stays shadow-only",
)
# H10 = this tile's own copy of the generic cross-venue lead and premium rules;
# either trigger takes the Bitfinex taker in its direction inside the frozen
# session map; late break-even, 60-minute backstop, 40 bp stop, three open.
XVENUE_SESSION_MAP_SOURCE = (
    "research API META_SESSION XVENUE cell at generation 4a3290296f55@2026-10-03T15:06:11Z: "
    "ASIA/EU/US all FOLLOW|TAKER_AT_SIGNAL|XV_TIME_3600_HARD40BP (train EV +0.20/+0.63/+3.75 bp)"
)
_XVENUE_SESSION_FOLLOW_ENTRY = {
    "mode": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_windows": (),
    "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
    "direction_source": "CROSS_VENUE_LEAD_OR_PREMIUM",
    "signal_clock": CROSS_VENUE_SIGNAL_CLOCK,
    "leader_venues": ("binance", "bybit"),
    "lookback_sec": 10, "lead_threshold_bps": 8.0,
    "premium_mean_window_sec": 3600, "premium_min_mean_samples": 1200,
    "premium_long_threshold_bps": 1.75, "premium_short_threshold_bps": -1.88,
    "max_fill_forward_sec": 5,
    "opposite_triggers": "CONFLICT_NO_TRADE",
    "allowed_sessions": ("ASIA", "EU", "US"),
    "session_hours_utc": HYPOTHESIS_SESSION_HOURS_UTC,
    "session_map_source": XVENUE_SESSION_MAP_SOURCE,
    "max_venue_age_sec": 2.0, "max_bbo_age_sec": 2.0, "max_spread_bps": 3.0,
    "taker_protection_bps": 5.0, "taker_ttl_sec": 3,
    "shadow_entry_delay_sec": 1,
    "min_submit_interval_sec": 5, "max_submissions_per_hour": 60,
    "ai_decision_role": "NONE",
    "volatility_scaling": "NONE - taker at the trigger",
}
_XVENUE_SESSION_FOLLOW_EXIT = _composite_exit(
    max_duration_sec=3600, max_open_positions=3, breakeven=LATE_BREAKEVEN_20_5,
    volatility_scaling="NONE - the late ATR trail was significantly worse capped per signal",
)
# H11 = the committed-fade side of the maker tile entered as a taker at the
# signal inside Asia+EU (pre-registered session rule), late-armed composite,
# conditional early cut, 90-minute backstop, 40 bp stop.
_COMMITTED_FADE_TAKER_ENTRY = {
    "mode": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_windows": (),
    "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
    "direction_source": "INVERTED_SCORE_LED_SIDE",
    "refuse_on": ("SCORE_TIE", "INVALID_SCORES", "AI_ERROR", "RAW_AI_NO_TRADE",
                  "SCORE_DIRECTION_MISMATCH", "BBO_STALE", "SPREAD_ABOVE_MAX", "SESSION_GATED"),
    "trades_raw_ai_no_trade": False, "min_score_gap": None,
    "commit_rule": "EXPLICIT_RAW_SIDE_EQUALS_SCORE_LED",
    "allowed_sessions": ("ASIA", "EU"),
    "session_hours_utc": HYPOTHESIS_SESSION_HOURS_UTC,
    "max_bbo_age_sec": 5.0, "max_spread_bps": 3.0,
    "taker_protection_bps": 5.0, "taker_ttl_sec": 3,
    "ai_decision_role": "FEATURE_ONLY",
    "volatility_scaling": "NONE - ATR-scaled maker offsets tied with taker; taker is the simpler rule",
    "fill_model": "REALISTIC_V1",
}
_COMMITTED_FADE_TAKER_EXIT = _composite_exit(
    max_duration_sec=5400, max_open_positions=3,
    breakeven=LATE_BREAKEVEN_20_5, trail=LATE_ATR_TRAIL_1_5_ARM_2, early_cut=CONDITIONAL_EARLY_CUT_12_5M,
    volatility_scaling="ATR trail armed after +2 ATR; the ATR-scaled hard stop stays shadow-only",
)
# Danish "Confirmed Fade" (owner spec 2026-10-04): fade committed AI calls with
# a resting limit 0.10% better than the signal; if price moves 3 bp our way
# before it fills, cancel and take the market within a 5 bp cap (skip when the
# cap is exceeded); unfilled after 30 minutes the signal is dropped.
DANISH_SESSIONS_ASIA_EU = ("ASIA", "EU")
DANISH_SESSIONS_ALL = ("ASIA", "EU", "US")


def _danish_entry(sessions: tuple[str, ...]) -> dict:
    return {
        "mode": "MAKER_LIMIT_OFFSET_CONFIRM_MARKET",
        "offset_pct": 0.10, "offset_reference": "LAST_PRICE_AT_DECISION",
        "chase_windows": (), "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
        "maker_ttl_sec": 1800, "never_cross_touch": True, "marketable_fallback": False,
        "confirm_move_bps": 3.0, "confirm_market_cap_bps": 5.0, "confirm_taker_ttl_sec": 3,
        "direction_source": "INVERTED_SCORE_LED_SIDE",
        "refuse_on": ("SCORE_TIE", "INVALID_SCORES", "AI_ERROR", "RAW_AI_NO_TRADE",
                      "SCORE_DIRECTION_MISMATCH", "BBO_STALE", "SPREAD_ABOVE_MAX", "SESSION_GATED",
                      "CONFIRM_CAP_EXCEEDED"),
        "trades_raw_ai_no_trade": False, "min_score_gap": None,
        "commit_rule": "EXPLICIT_RAW_SIDE_EQUALS_SCORE_LED",
        "allowed_sessions": tuple(sessions),
        "session_hours_utc": HYPOTHESIS_SESSION_HOURS_UTC,
        "max_bbo_age_sec": 5.0, "max_spread_bps": 3.0,
        "ai_decision_role": "FEATURE_ONLY",
        "volatility_scaling": "NONE",
        "fill_model": "REALISTIC_V1",
    }


def _danish_exit(*, early_cut: bool) -> dict:
    return _composite_exit(
        max_duration_sec=5400, max_open_positions=3, breakeven=LATE_BREAKEVEN_20_5,
        early_cut=CONDITIONAL_EARLY_CUT_12_5M if early_cut else None,
        volatility_scaling="NONE - owner spec; fixed bp protections",
    )


def _danish_pre_registration(hypothesis_id: str, *, sessions: tuple[str, ...], honest_label: str) -> dict:
    return _hypothesis_pre_registration(
        hypothesis_id,
        honest_label=honest_label,
        control_meaning=("Committed fade (maker) and (taker) trade the same committed calls and side; paired by "
                         "shared call. The Danish tiles differ from each other only in the early cut and sessions"),
        variants_tried=3, min_fills=150, k1_after_fills=80, k2_after_fills=None,
        k4_max_drawdown_usd=1.0, sessions=sessions, k5_max_days_without_promotion=None,
        decisions=("kill = mean <= 0 after 80 closes, or drawdown $1.00 (owner rule)",),
    )


COMBO_LANE_SPECS = {
    # Owner spec 2026-10-04 (HYPOTHESIS-TILES-20261004 "Danish"): three
    # variants of one confirmed-fade design differing only in the early cut
    # and the session gate.
    RESEARCH_LANE_FAMILY_DANISH_CF: _tile(
        lane=RESEARCH_LANE_FAMILY_DANISH_CF,
        label="Danish · confirmed fade of committed AI calls, Asia+EU, 0.10% limit or 3 bp confirm-to-market",
        raw_policy_id=("DANISH_CONFIRMED_FADE_COMMITTED_ASIA_EU_SPREADLE3BP_MAKER_0.10_CONFIRM3BP_TAKER_CAP5BPS_TTL1800"
                       "|TIME_5400_BE20TO5_CUT12BP5M_MFE2_HARD40BP_CAP3"),
        id_prefix="dcf",
        module="paper_policy_family_danish_cf.py",
        test_module="test_paper_policy_family_danish_cf.py",
        entry=_danish_entry(DANISH_SESSIONS_ASIA_EU),
        exit_policy=_danish_exit(early_cut=True),
        hypothesis_result={
            "status": "HINT_4D_REPLAY_CI_LOWER_NEAR_0",
            "hypothesis_id": "DANISH_CF_A_20261004",
            "in_sample": ("REALISTIC_V1 2026-09-30..10-03, Asia+EU, 3 slots: 99 fills +11.30 bp/fill, 1 h-cluster CI "
                          "[+0.5,+23.4], n_eff 38, max DD -$0.76; 27 limit fills, 72 confirm-to-market, 4 cap skips"),
            "corrected": ("fixed rule on test days +10.76 bp [-1.1,+25.4]; the early cut alone cost -183 bp vs the time "
                          "exit (31 cut, 17 would have recovered); nested joint selection preferred a taker entry"),
            "expected_live": "-4 to +8 bp/trade after decay (central ~+2); ~25 trades/day",
        },
        pre_registration=_danish_pre_registration(
            "DANISH_CF_A_20261004", sessions=DANISH_SESSIONS_ASIA_EU,
            honest_label=("HINT - 4 days, +11.3 bp/trade, 1 h-cluster CI [+0.5,+23.4] in-sample; test days "
                          "+10.8 bp [-1.1,+25.4]; the early cut was net negative in the replay"),
        ),
        admission_treatment=DANISH_CF_ADMISSION_POLICY_ID,
        max_active_signals=3,
        entry_ttl_sec=1800,
        subtitle="HINT — owner design, 4-day replay — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=HYPOTHESIS_TILES_POLICY_EPOCH,
        signal_summary="fade the shared AI's committed LONG/SHORT call (explicit side matching the score-led side)",
        live_exit_order=registry_live_exit_order(_danish_exit(early_cut=True)),
        shadow_exits=_shadow_exits_except("LATE_BE_20_5", "COND_CUT_12_5M_MFE2"),
    ),
    RESEARCH_LANE_FAMILY_DANISH_CF_NOES: _tile(
        lane=RESEARCH_LANE_FAMILY_DANISH_CF_NOES,
        label="Danish — no early stop · confirmed fade of committed AI calls, Asia+EU, no early cut",
        raw_policy_id=("DANISH_CONFIRMED_FADE_COMMITTED_ASIA_EU_SPREADLE3BP_MAKER_0.10_CONFIRM3BP_TAKER_CAP5BPS_TTL1800"
                       "|TIME_5400_BE20TO5_HARD40BP_CAP3"),
        id_prefix="dcn",
        module="paper_policy_family_danish_cf_noes.py",
        test_module="test_paper_policy_family_danish_cf_noes.py",
        entry=_danish_entry(DANISH_SESSIONS_ASIA_EU),
        exit_policy=_danish_exit(early_cut=False),
        hypothesis_result={
            "status": "HINT_4D_REPLAY_CI_SPANS_0",
            "hypothesis_id": "DANISH_CF_NOES_20261004",
            "in_sample": ("REALISTIC_V1 2026-09-30..10-03, Asia+EU, 3 slots: 93 fills +11.36 bp/fill, 1 h-cluster CI "
                          "[-0.6,+24.6]"),
            "corrected": "fixed rule on test days +11.61 bp [-1.2,+27.9]",
            "expected_live": "-4 to +8 bp/trade after decay (central ~+2); ~23 trades/day",
        },
        pre_registration=_danish_pre_registration(
            "DANISH_CF_NOES_20261004", sessions=DANISH_SESSIONS_ASIA_EU,
            honest_label="HINT - 4 days, +11.4 bp/trade, 1 h-cluster CI [-0.6,+24.6]; test days +11.6 bp [-1.2,+27.9]",
        ),
        admission_treatment=DANISH_CF_ADMISSION_POLICY_ID,
        max_active_signals=3,
        entry_ttl_sec=1800,
        subtitle="HINT — owner design, 4-day replay — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=HYPOTHESIS_TILES_POLICY_EPOCH,
        signal_summary="fade the shared AI's committed LONG/SHORT call (explicit side matching the score-led side)",
        live_exit_order=registry_live_exit_order(_danish_exit(early_cut=False)),
        shadow_exits=_shadow_exits_except("LATE_BE_20_5"),
        early_cut_shadow_reason="owner variant without the early stop; the conditional cut is recorded in shadow",
    ),
    RESEARCH_LANE_FAMILY_DANISH_CF_ALL_SESSIONS: _tile(
        lane=RESEARCH_LANE_FAMILY_DANISH_CF_ALL_SESSIONS,
        label="Danish — all sessions · confirmed fade of committed AI calls, every session, early cut",
        raw_policy_id=("DANISH_CONFIRMED_FADE_COMMITTED_ALL_SESSIONS_SPREADLE3BP_MAKER_0.10_CONFIRM3BP_TAKER_CAP5BPS_TTL1800"
                       "|TIME_5400_BE20TO5_CUT12BP5M_MFE2_HARD40BP_CAP3"),
        id_prefix="dca",
        module="paper_policy_family_danish_cf_all_sessions.py",
        test_module="test_paper_policy_family_danish_cf_all_sessions.py",
        entry=_danish_entry(DANISH_SESSIONS_ALL),
        exit_policy=_danish_exit(early_cut=True),
        hypothesis_result={
            "status": "HINT_4D_REPLAY_CI_SPANS_0",
            "hypothesis_id": "DANISH_CF_ALL_SESSIONS_20261004",
            "in_sample": ("REALISTIC_V1 2026-09-30..10-03, all sessions, 3 slots: 139 fills +5.6 bp/fill, 1 h-cluster "
                          "CI [-3.5,+16.1]; US session -7.2 bp/fill"),
            "corrected": "fixed rule on test days +3.06 bp/fill; Asia+EU minus all +1.27 bp/signal [-0.17,+3.21]",
            "expected_live": "-6 to +5 bp/trade after decay (central ~0); ~35 trades/day",
        },
        pre_registration=_danish_pre_registration(
            "DANISH_CF_ALL_SESSIONS_20261004", sessions=DANISH_SESSIONS_ALL,
            honest_label=("HINT - 4 days, +5.6 bp/trade, 1 h-cluster CI [-3.5,+16.1]; the US session lost "
                          "in-sample (control for the Asia+EU session gate)"),
        ),
        admission_treatment=DANISH_CF_ADMISSION_POLICY_ID,
        max_active_signals=3,
        entry_ttl_sec=1800,
        subtitle="HINT — owner design without the session gate — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=HYPOTHESIS_TILES_POLICY_EPOCH,
        signal_summary="fade the shared AI's committed LONG/SHORT call (explicit side matching the score-led side)",
        live_exit_order=registry_live_exit_order(_danish_exit(early_cut=True)),
        shadow_exits=_shadow_exits_except("LATE_BE_20_5", "COND_CUT_12_5M_MFE2"),
    ),
    # Owner-requested 2026-10-03: the August Continuous tile (demoted to a
    # label in #136, retired in #233) restored as a permanent paper-only baseline.
    RESEARCH_LANE_FAMILY_CONTINUOUS_AUG_ORIGINAL: _continuous_aug_original_tile(),
    # PROFIT-TILE-DESIGN-20261003: the genome FADE/time-exit winner reduced to
    # its clean source (committed calls only; fading NO_TRADE calls loses),
    # maker entry and a 90-minute hold. Three slots so evidence accrues ~3x
    # faster than one; 1 h-cluster CIs absorb the overlap.
    RESEARCH_LANE_FAMILY_COMMITTED_FADE_MAKER_90: _tile(
        lane=RESEARCH_LANE_FAMILY_COMMITTED_FADE_MAKER_90,
        label="Committed fade (maker) · inverted committed AI side, 0.10% maker limit 30 min, 90-min hold, 40 bp stop",
        raw_policy_id="INVERT_COMMITTED_SCORE_LED_SIDE_MAKER_OFFSET_0.10_NOCHASE_TTL1800|TIME_5400_HARD40BP",
        id_prefix="cfm",
        module="paper_policy_family_committed_fade_maker_90.py",
        test_module="test_paper_policy_family_committed_fade_maker_90.py",
        entry=dict(_COMMITTED_FADE_MAKER_ENTRY),
        exit_policy=dict(_COMMITTED_FADE_MAKER_EXIT),
        hypothesis_result={
            "status": "HINT_3D_WALK_FORWARD_CI_SPANS_0",
            "hypothesis_id": "H8_COMMITTED_FADE_MAKER_90_20261003",
            "in_sample": ("REALISTIC_V1, 2026-09-30..10-02, 3 slots: 67 trades +8.9 bp/trade, 1 h-cluster CI [-7,+28]; "
                          "1 slot: 24 trades +9.5 bp, every day positive; all fills +13.5 bp [-3,+32], n_eff 28"),
            "corrected": ("day-fold walk-forward 19 trades +5.0 bp [-15,+30]; deflated Sharpe ~0 after 3,416 variants; "
                          "unseen 17:12-21:30Z window -20 bp (n_eff ~4)"),
            "expected_live": "-5 to +6 bp/trade after decay (central ~+2); 15-25 trades/day",
        },
        pre_registration=_committed_fade_maker_pre_registration("H8_COMMITTED_FADE_MAKER_90_20261003"),
        admission_treatment=COMMITTED_FADE_MAKER_ADMISSION_POLICY_ID,
        max_active_signals=3,
        entry_ttl_sec=1800,
        subtitle="HINT — 3-day REALISTIC_V1 walk-forward, CI spans 0 — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=COMMITTED_FADE_MAKER_POLICY_EPOCH,
        signal_summary="fade the shared AI's committed LONG/SHORT call (explicit side matching the score-led side)",
        live_exit_order=("HARD_STOP", "TIME_EXIT"),
        shadow_exits=ALL_SHADOW_EXITS,
        early_cut_shadow_reason=("policy frozen in the deploying stack; the re-test rule would adopt the composite "
                                 "and the conditional cut - owner-approved follow-up, shadow until then"),
    ),
    # HYPOTHESIS-TILES-20261004 H9: follow the score-led side only when the
    # AI abstains; ten slots because the edge needs concurrency (cap 3 lost).
    RESEARCH_LANE_FAMILY_NOTRADE_FOLLOW_MAKER_60: _tile(
        lane=RESEARCH_LANE_FAMILY_NOTRADE_FOLLOW_MAKER_60,
        label="No-trade follow (maker) · score-led side on AI NO_TRADE, 0.15% maker + 25% chase, late BE + ATR trail, 60-min backstop",
        raw_policy_id=("SCORE_LED_SIDE_ON_RAW_AI_NO_TRADE_MAKER_OFFSET_0.15_CHASE_w234_s25_i180_TTL3600"
                       "|TIME_3600_BE20TO5_TRAIL1.5ATR_ARM2ATR_HARD40BP_CAP10"),
        id_prefix="ntf",
        module="paper_policy_family_notrade_follow_maker_60.py",
        test_module="test_paper_policy_family_notrade_follow_maker_60.py",
        entry=dict(_NOTRADE_FOLLOW_MAKER_ENTRY),
        exit_policy=dict(_NOTRADE_FOLLOW_MAKER_EXIT),
        hypothesis_result={
            "status": "HINT_4D_NESTED_WF_CI_SPANS_0",
            "hypothesis_id": "H9_NOTRADE_FOLLOW_MAKER_60_20261004",
            "in_sample": ("REALISTIC_V1 2026-09-30..10-03, 10 slots: 337 fills +3.05 bp/fill, 1 h-cluster CI [-4.7,+11.2], "
                          "n_eff 61, max DD -$3.00; uncapped +6.19 bp; 3 slots -2.47 bp"),
            "corrected": ("nested day-fold walk-forward base rule +4.54 bp/fill [-3.8,+13.3] (232 fills, 3 folds); "
                          "deflated Sharpe 0.16; late-armed composite vs time exit capped per signal +0.21 bp "
                          "(not worse) - adopted; conditional cut -0.87 bp per fill - shadow only"),
            "expected_live": "-2 to +5 bp/trade after decay (central ~+1.5); ~100 trades/day",
        },
        pre_registration=_hypothesis_pre_registration(
            "H9_NOTRADE_FOLLOW_MAKER_60_20261004",
            honest_label=("HINT - 4 days, nested walk-forward +4.5 bp/trade, 1 h-cluster CI [-3.8,+13.3], edge needs "
                          "10 concurrent slots (3 slots lost), one trend day carries most of the profit"),
            control_meaning=("No live control lane: the analyzer's REALISTIC_V1 counterfactual of the same NO_TRADE calls "
                             "(taker entry, inverted side) is the comparison; Continuous is the baseline"),
            variants_tried=1079, min_fills=500, k1_after_fills=300, k2_after_fills=600,
            k4_max_drawdown_usd=3.0,
            decisions=("composite late break-even +20->+5 bp and ATR trail 1.5 armed at +2 ATR adopted (not "
                       "significantly worse nested-OOS)", "conditional early cut shadow-only (worse)"),
        ),
        admission_treatment=NOTRADE_FOLLOW_MAKER_ADMISSION_POLICY_ID,
        max_active_signals=10,
        entry_ttl_sec=3600,
        subtitle="HINT — 4-day nested walk-forward, CI spans 0 — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=HYPOTHESIS_TILES_POLICY_EPOCH,
        signal_summary="follow the score-led side only when the shared AI returned NO_TRADE",
        live_exit_order=registry_live_exit_order(_NOTRADE_FOLLOW_MAKER_EXIT),
        shadow_exits=_shadow_exits_except("COMPOSITE_LATE_BE20_5_TRAIL1.5_ARM2"),
        early_cut_shadow_reason="conditional cut on top of the composite was -0.87 bp/fill nested-OOS (worse)",
    ),
    # HYPOTHESIS-TILES-20261004 H10: one tile for both cross-venue triggers
    # with a 60-minute hold, gated by the session map frozen at registration.
    RESEARCH_LANE_FAMILY_XVENUE_SESSION_FOLLOW_60M: _tile(
        lane=RESEARCH_LANE_FAMILY_XVENUE_SESSION_FOLLOW_60M,
        label="Cross-venue session follow · Binance/Bybit lead or premium, frozen session map, taker, late BE, 60-min backstop",
        raw_policy_id=("XVENUE_LEAD8BP_OR_PREMIUM_L1.75_S1.88BP_SESSIONMAP_ASIA_EU_US_SPREADLE3BP_TAKER_CAP5BPS"
                       "|TIME_3600_BE20TO5_HARD40BP_CAP3"),
        id_prefix="xvs",
        module="paper_policy_family_xvenue_session_follow_60m.py",
        test_module="test_paper_policy_family_xvenue_session_follow_60m.py",
        entry=dict(_XVENUE_SESSION_FOLLOW_ENTRY),
        exit_policy=dict(_XVENUE_SESSION_FOLLOW_EXIT),
        hypothesis_result={
            "status": "HINT_SHORT_RECHECK_CI_SPANS_0",
            "hypothesis_id": "H10_XVENUE_SESSION_FOLLOW_60M_20261004",
            "in_sample": ("research nested OOS +1.09 bp/trade [-1.5,+4.2], 655 fills, n_eff 222 (10-min clusters); "
                          "REALISTIC_V1 re-check on shadow triggers 2026-10-02..03, 3 slots, 8.9 s latency: "
                          "93 fills +2.96 bp, 1 h-cluster CI [-7.3,+13.5], max DD -$1.10"),
            "corrected": ("one nested fold only; session re-selection unstable; 3 s latency replay -2.05 bp at 3 slots; "
                          "uncapped every-trigger mean +0.75 bp [-3.6,+5.5]"),
            "expected_live": "-2 to +3 bp/trade after decay (central ~+0.5); ~70 trades/day",
        },
        pre_registration=_hypothesis_pre_registration(
            "H10_XVENUE_SESSION_FOLLOW_60M_20261004",
            honest_label=("HINT - 1.3-day REALISTIC_V1 re-check +3.0 bp/trade, 1 h-cluster CI [-7.3,+13.5]; "
                          "the frozen session map admits all three sessions"),
            control_meaning=("No live control lane: the tile's own shadow outcome of every qualifying trigger (logged "
                             "whether or not the tile is ON) is the comparison"),
            variants_tried=83, min_fills=500, k1_after_fills=300, k2_after_fills=500,
            k4_max_drawdown_usd=1.0,
            decisions=("late break-even +20->+5 bp adopted (not worse)",
                       "late ATR trail rejected (significantly worse capped per signal)",
                       "conditional early cut shadow-only (one fold)"),
        ),
        admission_treatment=XVENUE_SESSION_FOLLOW_ADMISSION_POLICY_ID,
        max_active_signals=3,
        entry_ttl_sec=3,
        subtitle="HINT — 1.3-day re-check, CI spans 0 — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=HYPOTHESIS_TILES_POLICY_EPOCH,
        signal_clock=CROSS_VENUE_SIGNAL_CLOCK,
        signal_summary=("Binance/Bybit lead Bitfinex by >=8 bp over 10 s, or their premium over Bitfinex leaves its "
                        "60-minute mean (+1.75 / -1.88 bp); trade in the leaders' direction"),
        live_exit_order=registry_live_exit_order(_XVENUE_SESSION_FOLLOW_EXIT),
        shadow_exits=_shadow_exits_except("LATE_BE_20_5"),
        early_cut_shadow_reason="only one nested fold; not adopted live",
    ),
    # HYPOTHESIS-TILES-20261004 H11: the maker tile's committed-fade side
    # with a taker entry; it beat the maker rule nested-OOS per signal.
    RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90: _tile(
        lane=RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90,
        label="Committed fade (taker) · inverted committed AI side, Asia+EU, taker, late BE + ATR trail, 90-min backstop",
        raw_policy_id=("INVERT_COMMITTED_SCORE_LED_SIDE_ASIA_EU_SPREADLE3BP_TAKER_CAP5BPS"
                       "|TIME_5400_BE20TO5_TRAIL1.5ATR_ARM2ATR_CUT12BP5M_MFE2_HARD40BP_CAP3"),
        id_prefix="cft",
        module="paper_policy_family_committed_fade_taker_90.py",
        test_module="test_paper_policy_family_committed_fade_taker_90.py",
        entry=dict(_COMMITTED_FADE_TAKER_ENTRY),
        exit_policy=dict(_COMMITTED_FADE_TAKER_EXIT),
        hypothesis_result={
            "status": "HINT_4D_NESTED_WF_CI_SPANS_0",
            "hypothesis_id": "H11_COMMITTED_FADE_TAKER_90_20261004",
            "in_sample": ("REALISTIC_V1 2026-09-30..10-03, 3 slots: 119 fills +8.36 bp/fill, 1 h-cluster CI [-4.2,+22.8], "
                          "max DD -$0.95; the maker rule on the same calls 71 fills +9.30 bp (1.88 vs 1.25 bp per signal)"),
            "corrected": ("nested day-fold entry selection chose taker in 2/3 folds: +6.79 bp/fill, 1.51 bp/signal vs "
                          "maker 4.38 bp/fill, 0.55 bp/signal on the same days; deflated Sharpe 0.66 (13 entries)"),
            "expected_live": "-4 to +6 bp/trade after decay (central ~+2); ~35 trades/day",
        },
        pre_registration=_hypothesis_pre_registration(
            "H11_COMMITTED_FADE_TAKER_90_20261004",
            honest_label=("HINT - 4 days, nested walk-forward +6.8 bp/trade, 1 h-cluster CI [-6.0,+21.6]; "
                          "US session negative in-sample and on test days (-11.0 bp, n 39)"),
            control_meaning="Committed fade (maker) trades the same calls and side; paired by shared call",
            variants_tried=13, min_fills=150, k1_after_fills=80, k2_after_fills=150,
            k4_max_drawdown_usd=1.0, sessions=("ASIA", "EU"),
            decisions=("Asia+EU session gate adopted by the pre-registered rule (nested gate chose it 2/2 folds)",
                       "composite late break-even +20->+5 bp and ATR trail 1.5 armed at +2 ATR adopted "
                       "(capped per signal -1.66 bp [-4.45,+1.03], not significantly worse)",
                       "conditional early cut -12 bp in 5 min if MFE <=+2 bp live (+0.56 bp on top of the composite)"),
        ),
        admission_treatment=COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID,
        max_active_signals=3,
        entry_ttl_sec=3,
        subtitle="HINT — 4-day nested walk-forward, CI spans 0 — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=HYPOTHESIS_TILES_POLICY_EPOCH,
        signal_summary="fade the shared AI's committed LONG/SHORT call (explicit side matching the score-led side)",
        live_exit_order=registry_live_exit_order(_COMMITTED_FADE_TAKER_EXIT),
        shadow_exits=_shadow_exits_except("COMPOSITE_LATE_BE20_5_TRAIL1.5_ARM2", "COND_CUT_12_5M_MFE2"),
    ),
}
COMPARISON_BENCHMARK_LANE = None
PRIMARY_PRODUCTION_LANE = COMBO_EXECUTION_LANES[0]
BENCHMARK_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_PROFILE_ID = "CONTINUOUS_BENCHMARK_v1"
BENCHMARK_ROLE = "BENCHMARK"
PRIMARY_PRODUCTION_ROLE = "BENCHMARK"
RESEARCH_CANDIDATE_LANE = COMBO_EXECUTION_LANES[0]
RESEARCH_CANDIDATE_ROLE = "RESEARCH_CANDIDATE"

RESEARCH_STACK_FEATURES = (
    "Every tile number is derived from the registry display order (tile_number). Danish, Danish - no early stop and Danish - all sessions (HINT, owner design) fade the shared AI's committed LONG/SHORT calls: a resting limit 0.10% better than the decision-time price; if price moves 3 bp our way first the limit is cancelled and a taker taken within a 5 bp cap (skipped above it); unfilled after 30 minutes the signal is dropped. Spread <=3 bp and BBO <=5 s old. Exit first trigger wins: 40 bp catastrophic stop, break-even armed at +20 bp moving the stop to +5 bp, a -12 bp early cut in the first 5 minutes only if the trade never ran past +2 bp (not on the no-early-stop variant), 90-minute backstop. Danish and Danish - no early stop trade Asia+EU (UTC 00-16); Danish - all sessions trades every session. Up to three each; kill at mean <=0 after 80 trades or a $1.00 drawdown. Continuous (Aug-2026 original, BASELINE BENCHMARK) is an exact replica of the early-August Continuous tile: its own DeepSeek call with the verbatim v3 prompt after every shared three-minute call, a 0.1% maker limit chased 25% every 60 s for 10 minutes, Scenario C ladder, -12% thesis cut, 30% stop, -32% early fail, 40/10 peak floor, 2 h cap; default-ON, paper-only, relay-ineligible, never promoted or retired for performance. Committed fade (maker, HINT) rests a 0.10% maker limit for 30 minutes on committed calls, 90-minute hold and 40 bp stop (policy frozen). Committed fade (taker, H11) takes the same committed calls as a taker inside Asia+EU with the late-armed composite (break-even +20->+5 bp, ATR trail 1.5 armed at +2 ATR), the conditional early cut, a 90-minute backstop and a 40 bp stop. No-trade follow (maker, H9) follows the score-led side on AI NO_TRADE with a 0.15% chased maker limit, the late-armed composite, a 60-minute backstop, ten slots. Cross-venue session follow (H10) takes a Bitfinex taker on either generic cross-venue trigger inside its frozen session map with a late break-even, 60-minute backstop, 40 bp stop, three slots. All research tiles are default-OFF, paper-only and relay-ineligible with pre-registered promotion and kill rules; each records the shared shadow-exit set (SHADOW_EXIT_SET). v11 retires the cross-venue lead and premium tiles (owner: losing), adds the three Danish tiles and the late-armed protections; Continuous keeps v7 and Committed fade (maker) keeps v9. Earlier cohorts remain quarantined"
)
EXECUTION_FIX_VERSION = RESEARCH_STACK_VERSION
ANALYZER_SYNC_ID = RESEARCH_STACK_VERSION
RESEARCH_DASHBOARD_VERSION = RESEARCH_STACK_VERSION
EXPECTED_EXCHANGE = "bitfinex"
EXPECTED_BOT_VERSION = EXECUTION_FIX_VERSION

ACTIVE_TILE_REGISTRY = {lane: dict(COMBO_LANE_SPECS[lane]) for lane in COMBO_EXECUTION_LANES}
ACTIVE_TILE_ORDER = COMBO_EXECUTION_LANES


def tile_number(lane: str) -> int | None:
    """1-based user-facing tile number derived from the registry display order."""
    try:
        return ACTIVE_TILE_ORDER.index(str(lane or "").upper()) + 1
    except ValueError:
        return None


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
    # Retired 2026-10-03 (owner): both Trend Fade 60 tiles
    # (diagnostics/XVENUE-INVERT-STUDY-20261003.md).
    "FAMILY_TREND_FADE_60", "FAMILY_TREND_FADE_60_COMMITTED",
    # Retired 2026-10-04 (owner): both cross-venue tiles were losing
    # (diagnostics/HYPOTHESIS-TILES-20261004.md); the generic cross-venue
    # evaluator, feeds and shadow collection stay.
    "FAMILY_XVENUE_LEAD_60S", "FAMILY_XVENUE_PREMIUM_60S",
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
    "INVERT_SCORE_LED_SIDE_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP",
    "INVERT_COMMITTED_SCORE_LED_SIDE_GAP30_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP",
    "XVENUE_LEAD_W10S_TH8BP_BOTHFRESH_SPREADLE3BP_TAKER_CAP5BPS|TIME_60_HARD40BP",
    "XVENUE_PREMIUM_DEV60M_L1.75_S1.88BP_BOTHFRESH_SPREADLE3BP_TAKER_CAP5BPS|TIME_60_HARD40BP",
})


PARTIAL_EXIT_RELAY_CAPABILITY = "BLOCKED_PARTIAL_REDUCTION_UNPROVEN"


def tile_has_partial_exits(spec: dict) -> bool:
    """True when the tile's exit policy closes a position in more than one part."""
    exit_policy = spec.get("exit_policy") or {}
    return bool(exit_policy.get("partial_take_profits")) or exit_policy.get("family") == "HYBRID_RUNNER"


def _tile_card_metadata_defects(spec: dict) -> list[str]:
    """Every tile must carry complete ENTRY / EXIT / RISK MANAGEMENT card metadata."""
    out = []
    order = tuple(spec.get("live_exit_order") or ())
    if not order:
        out.append("CARD_MISSING_LIVE_EXIT_ORDER")
    unknown = sorted(set(order).difference(LIVE_EXIT_RULES))
    if unknown:
        out.append("CARD_UNKNOWN_EXIT_RULE:" + ",".join(unknown))
    exit_policy = spec.get("exit_policy") if isinstance(spec.get("exit_policy"), dict) else {}
    if exit_policy.get("family") == "COMPOSITE_FIRST_TRIGGER_WINS":
        if order != registry_live_exit_order(exit_policy) or tuple(exit_policy.get("exit_order") or ()) != order:
            out.append("CARD_EXIT_ORDER_DIFFERS_FROM_RUNTIME")
    unknown_shadow = sorted(set(spec.get("shadow_exits") or ()).difference(SHADOW_EXIT_SET))
    if unknown_shadow:
        out.append("UNKNOWN_SHADOW_EXIT:" + ",".join(unknown_shadow))
    if not any(r in RISK_EXIT_RULES and r not in ("HARD_STOP", "STOP_LOSS") for r in order) \
            and not spec.get("early_cut_shadow_reason"):
        out.append("EARLY_CUT_NEITHER_LIVE_NOR_SHADOW_WITH_REASON")
    entry = spec.get("entry_policy") if isinstance(spec.get("entry_policy"), dict) else {}
    if entry.get("confirm_move_bps") is not None and (
        spec.get("platform_relay_eligible") or spec.get("live_copy_eligible")
    ):
        out.append("CONFIRM_MARKET_ENTRY_MUST_BE_RELAY_INELIGIBLE")
    try:
        from tile_card_sections import card_section_defects
    except ImportError:
        out.append("CARD_SECTIONS_GENERATOR_MISSING")
    else:
        out.extend(card_section_defects(spec, SHADOW_EXIT_SET))
    return out


def tile_card_sections(lane: str) -> dict | None:
    """Plain-English ENTRY / EXIT / RISK MANAGEMENT sections for one active tile."""
    spec = ACTIVE_TILE_REGISTRY.get(str(lane or "").upper())
    if not spec:
        return None
    from tile_card_sections import card_sections
    return card_sections(spec, SHADOW_EXIT_SET)


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
        defects.extend(f"{lane}:{d}" for d in _tile_card_metadata_defects(spec))
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


def tile_chase_windows(lane) -> tuple[int, ...]:
    """Registry-owned chase windows; empty for taker tiles and lanes outside the registry."""
    spec = ACTIVE_TILE_REGISTRY.get(str(lane or "").upper()) or {}
    entry = spec.get("entry_policy") if isinstance(spec.get("entry_policy"), dict) else {}
    return tuple(int(w) for w in (entry.get("chase_windows") or spec.get("chase_windows") or ()))


def chasing_tile_lanes() -> tuple[str, ...]:
    return tuple(lane for lane in ACTIVE_TILE_REGISTRY if tile_chase_windows(lane))


def active_tile_lifecycle_manifest() -> tuple[dict, ...]:
    """Stable cross-layer roster used by audits, APIs, dashboards and analyzers."""
    return tuple(
        {
            "lane": lane,
            "display_order": index,
            "tile_number": index,
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
            "signal_summary": ACTIVE_TILE_REGISTRY[lane]["signal_summary"],
            "live_exit_order": tuple(ACTIVE_TILE_REGISTRY[lane]["live_exit_order"]),
            "shadow_exits": tuple(ACTIVE_TILE_REGISTRY[lane]["shadow_exits"]),
            "early_cut_shadow_reason": ACTIVE_TILE_REGISTRY[lane]["early_cut_shadow_reason"],
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
