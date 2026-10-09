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
RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90 = "FAMILY_COMMITTED_FADE_TAKER_90"
RESEARCH_LANE_FAMILY_PREMIUM_REVERSION_60M = "FAMILY_PREMIUM_REVERSION_60M"
RESEARCH_LANE_FAMILY_RANDOM_CONTROL_TAKER_90 = "FAMILY_RANDOM_CONTROL_TAKER_90"
COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID = "INVERTED_COMMITTED_SCORE_LED_SIDE_TAKER_V1"
PREMIUM_REVERSION_ADMISSION_POLICY_ID = "CROSS_VENUE_PREMIUM_DEV60M_REVERSION_NO_AI_V1"
RANDOM_CONTROL_ADMISSION_POLICY_ID = "RANDOM_COIN_SIDE_ON_COMMITTED_CALL_TAKER_V1"
# FREEZE21B additions (owner, 2026-10-04 17:53/17:54 AEDT): Grok Strategist's
# pre-registered rules PREREG-GS-20261004 (GS-01..04) and -B (B1..B3) as
# visible paper tiles (regime_adaptive_binding.py).
RESEARCH_LANE_FAMILY_GS01_XV_PREMIUM_ATR_TP = "FAMILY_GS01_XV_PREMIUM_ATR_TP"
RESEARCH_LANE_FAMILY_GSB1_CVD_DIV_REGIME = "FAMILY_GSB1_CVD_DIV_REGIME"
RESEARCH_LANE_FAMILY_GSB2_REGIME_SWITCHER = "FAMILY_GSB2_REGIME_SWITCHER"
RESEARCH_LANE_FAMILY_GSB3_COMMITTED_FADE_REGIME = "FAMILY_GSB3_COMMITTED_FADE_REGIME"
GS_PREMIUM_FOLLOW_ADMISSION_POLICY_ID = "GS01_CROSS_VENUE_PREMIUM_FOLLOW_NO_AI_V1"
GS_CVD_DIVERGENCE_ADMISSION_POLICY_ID = "GS_CVD_DIVERGENCE_3M_EVENT_NO_AI_V1"
GSB2_REGIME_SWITCH_ADMISSION_POLICY_ID = "GSB2_CVD_DIVERGENCE_OR_COMMITTED_FADE_BY_REGIME_V1"
GSB3_COMMITTED_FADE_ADMISSION_POLICY_ID = "GSB3_INVERTED_COMMITTED_SCORE_LED_SIDE_REGIME_V1"
# FREEZE21B mid-epoch additions (owner, 2026-10-05 ~20:15 AEDT; Health Monitor +
# Grok Strategist spec GS05-GS06-TILE-SPECS-20261005): appended as tiles 12/13
# after the eleven frozen tiles, which stay byte-identical (research_freeze.
# MID_EPOCH_ADDITIONS; frozen_roster_registry_signature proves it in CI).
RESEARCH_LANE_FAMILY_GS06_COMMITTED_FADE_ATR_TP = "FAMILY_GS06_COMMITTED_FADE_ATR_TP"
# PHASE03 mid-epoch additions (owner, 2026-10-07): three research tiles, all
# default OFF, paper-only, relay-ineligible (research_freeze.MID_EPOCH_ADDITIONS).
# GS-07 = fast cross-venue premium fade (15-60s mean); the Danish regime router
# routes QUIET/TREND -> committed fade and VIOLENT -> cross-venue premium
# reversion; the fade pool pools the committed fade and premium reversion signals.
RESEARCH_LANE_FAMILY_GS07_FAST_PREMIUM_FADE = "FAMILY_GS07_FAST_PREMIUM_FADE"
RESEARCH_LANE_FAMILY_DANISH_REGIME_ROUTER = "FAMILY_DANISH_REGIME_ROUTER"
RESEARCH_LANE_FAMILY_FADE_POOL = "FAMILY_FADE_POOL"
GS07_FAST_PREMIUM_FADE_ADMISSION_POLICY_ID = "GS07_FAST_CROSS_VENUE_PREMIUM_FADE_NO_AI_V1"
DANISH_REGIME_ROUTER_ADMISSION_POLICY_ID = "DANISH_REGIME_ROUTER_DYNAMIC_V1"
FADE_POOL_ADMISSION_POLICY_ID = "FADE_POOL_COMMITTED_AND_PREMIUM_V1"
# Tiles on this clock are triggered by the per-second cross-venue evaluator,
# never by the shared three-minute AI call.
CROSS_VENUE_SIGNAL_CLOCK = "PER_SECOND_CROSS_VENUE_EVALUATOR"
# Tiles on this clock are triggered at a closed 3-minute Bitfinex bar by the
# CVD-divergence evaluator (regime_bars_3m.py), never by the shared AI call.
BAR_CLOSE_SIGNAL_CLOCK = "BAR_CLOSE_3M_CVD_EVALUATOR"
EVALUATOR_SIGNAL_CLOCKS = frozenset({CROSS_VENUE_SIGNAL_CLOCK, BAR_CLOSE_SIGNAL_CLOCK})
TILE_REGISTRY_SCHEMA = "research_tile_registry_v1"
PARTIAL_EXIT_RELAY_CAPABILITY = "BLOCKED_PARTIAL_REDUCTION_UNPROVEN"
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
# PHASE02 roster (owner, 2026-10-07): five losing tiles retired in one atomic
# registry transaction. The eight survivors keep their original relative order:
# H-A, H-C, the control, GS-01, B1, B2, B3, GS-06, GS-07, the Danish regime
# router, and the fade pool.
COMBO_EXECUTION_LANES = (
    RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90,
    RESEARCH_LANE_FAMILY_PREMIUM_REVERSION_60M,
    RESEARCH_LANE_FAMILY_RANDOM_CONTROL_TAKER_90,
    RESEARCH_LANE_FAMILY_GS01_XV_PREMIUM_ATR_TP,
    RESEARCH_LANE_FAMILY_GSB1_CVD_DIV_REGIME,
    RESEARCH_LANE_FAMILY_GSB2_REGIME_SWITCHER,
    RESEARCH_LANE_FAMILY_GSB3_COMMITTED_FADE_REGIME,
    RESEARCH_LANE_FAMILY_GS06_COMMITTED_FADE_ATR_TP,
    RESEARCH_LANE_FAMILY_GS07_FAST_PREMIUM_FADE,
    RESEARCH_LANE_FAMILY_DANISH_REGIME_ROUTER,
    RESEARCH_LANE_FAMILY_FADE_POOL,
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


def tile_lane_for_trade_id(trade_id) -> str | None:
    """Active tile whose registry ``id_prefix`` starts this paper trade id, else None."""
    text = str(trade_id or "")
    for lane, spec in ACTIVE_TILE_REGISTRY.items():
        prefix = str(spec.get("id_prefix") or "")
        if prefix and (text.startswith(prefix + "-") or text.startswith(prefix + "_")):
            return lane
    return None


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


RESEARCH_STACK_VERSION = "v31-freeze21b-8t-v14"

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


# Shadow-exit recorder view of SHADOW_EXIT_SET (shadow_exit_paths.py kinds).
# Every tile replays the whole catalog under its own time backstop and hard
# stop; ids the tile lists in ``shadow_exits`` are its card's shadow-only exits
# (role CARD_SHADOW), the rest are CATALOG comparisons. Observation only: none
# of this reaches execution or the policy signature.
SHADOW_EXIT_FAMILY_KINDS = {
    "LATE_BREAKEVEN": "LATE_BREAKEVEN", "LATE_ATR_TRAIL": "LATE_ATR_TRAIL",
    "LATE_GIVEBACK": "GIVEBACK", "LATE_LADDER": "LADDER",
    "CONDITIONAL_EARLY_CUT": "CONDITIONAL_EARLY_CUT", "ATR_STOP": "ATR_HARD_STOP",
    "ATR_HARD_STOP": "ATR_HARD_STOP", "LATE_COMPOSITE": "COMPOSITE",
}
SHADOW_EXIT_KINDS = frozenset({*SHADOW_EXIT_FAMILY_KINDS.values(), "HOLD"})
SHADOW_EXIT_DEFAULT_HARD_STOP_BP = 40.0
SHADOW_EXIT_DEFAULT_BACKSTOP_SEC = 7200


def _shadow_exit_guard(lane=None) -> dict:
    spec = ACTIVE_TILE_REGISTRY.get(str(lane or "").upper()) or {}
    exit_policy = spec.get("exit_policy") or {}
    stop = exit_policy.get("hard_stop_bps", exit_policy.get("hard_stop_margin_pct", SHADOW_EXIT_DEFAULT_HARD_STOP_BP))
    backstop = exit_policy.get("max_duration_sec", SHADOW_EXIT_DEFAULT_BACKSTOP_SEC)
    return {"hard_stop_bp": float(stop), "backstop_sec": int(backstop)}


def _shadow_exit_recorder_specs(key: str, item: dict, guard: dict, role: str) -> list[dict]:
    family = item.get("family")
    base = {"label": item.get("label") or key, "role": role}
    if family == "LATE_BREAKEVEN":
        body = {"kind": "LATE_BREAKEVEN", "arm_bp": item["be_arm_bp"], "floor_bp": item["be_floor_bp"]}
    elif family == "LATE_ATR_TRAIL":
        body = {"kind": "LATE_ATR_TRAIL", "arm_atr": item["trail_arm_atr"], "trail_atr": item["trail_k"]}
    elif family == "LATE_GIVEBACK":
        body = {"kind": "GIVEBACK", "arm_bp": item["gb_arm_bp"], "giveback_frac": round(1.0 - item["gb_keep"], 6)}
    elif family == "LATE_LADDER":
        body = {"kind": "LADDER", "rungs_bp": tuple(tuple(r) for r in item["ladder"])}
    elif family == "CONDITIONAL_EARLY_CUT":
        body = {"kind": "CONDITIONAL_EARLY_CUT", "cut_bp": item["thesis_bp"], "within_sec": item["thesis_sec"],
                "max_mfe_bp": item["thesis_max_mfe"]}
    elif family == "ATR_STOP":
        body = {"kind": "ATR_HARD_STOP", "stop_atr": item["atr_stop_k"]}
    elif family == "ATR_HARD_STOP":
        return [{"id": key, **base, "kind": "ATR_HARD_STOP", "stop_atr": item["hard_atr_k"],
                 "clamp_bp": tuple(item["hard_clamp_bp"]), "backstop_sec": guard["backstop_sec"]}]
    elif family == "LATE_COMPOSITE":
        be, trail = f"{key}:be", f"{key}:trail"
        return [
            {"id": be, **base, "role": "COMPOSITE_MEMBER", "kind": "LATE_BREAKEVEN",
             "arm_bp": item["be_arm_bp"], "floor_bp": item["be_floor_bp"], **guard},
            {"id": trail, **base, "role": "COMPOSITE_MEMBER", "kind": "LATE_ATR_TRAIL",
             "arm_atr": item["trail_arm_atr"], "trail_atr": item["trail_k"], **guard},
            {"id": key, **base, "kind": "COMPOSITE", "members": (be, trail)},
        ]
    else:
        raise ValueError(f"UNSUPPORTED_SHADOW_EXIT_FAMILY:{key}:{family}")
    return [{"id": key, **base, **body, **guard}]


def tile_shadow_exit_set(lane=None) -> tuple:
    """Recorder specs for a tile (or the defaults for any non-tile signal lane), from SHADOW_EXIT_SET."""
    spec = ACTIVE_TILE_REGISTRY.get(str(lane or "").upper()) or {}
    card = set(spec.get("shadow_exits") or ())
    guard = _shadow_exit_guard(lane)
    out = []
    for key, item in SHADOW_EXIT_SET.items():
        out.extend(_shadow_exit_recorder_specs(key, item, guard, "CARD_SHADOW" if key in card else "CATALOG"))
    out.append({"id": "tile_stop_backstop_only", "label": "Only the hard stop and time backstop",
                "role": "REFERENCE", "kind": "HOLD", **guard})
    out.append({"id": "hold_to_horizon", "label": "Hold to the time backstop (no stop)", "role": "REFERENCE",
                "kind": "HOLD", "backstop_sec": guard["backstop_sec"]})
    return tuple(out)




# Exit rule ids for the card and the first-trigger-wins order the runtime's
# family_policy_common.exit_action evaluates.
LIVE_EXIT_RULES = frozenset({
    "HARD_STOP", "BREAKEVEN_LOCK", "ATR_TRAIL", "EARLY_CUT", "TIME_EXIT",
    # Continuous (Aug-2026 replica) rules, evaluated by its own policy module.
    "EARLY_FAIL", "STOP_LOSS", "PROFIT_LOCK_LADDER", "THESIS_FAST_CUT", "THESIS_INVALIDATED", "PEAK_FLOOR",
    # GS-20261004 regime stacks (gs_regime_exit_stack.py).
    "THESIS_CUT", "VOL_SHOCK", "MFE_GIVEBACK", "INDICATOR_FLIP", "ATR_TAKE_PROFIT", "LADDER_TP1",
    "LADDER_LOCK", "TIME_BACKSTOP",
})
RISK_EXIT_RULES = frozenset({"HARD_STOP", "EARLY_CUT", "EARLY_FAIL", "STOP_LOSS", "THESIS_FAST_CUT", "THESIS_CUT"})


def registry_live_exit_order(exit_policy: dict) -> tuple[str, ...]:
    """First-trigger-wins order of family_policy_common.exit_action for a registry exit policy.

    GS/B regime exit stacks (gs_regime_exit_stack) declare their own order."""
    if exit_policy.get("family") == "REGIME_ADAPTIVE_FIRST_TRIGGER_WINS":
        return tuple(exit_policy["exit_order"])
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


# ---------------------------------------------------------------------------
# POST-FREEZE PR-A (owner approval 2026-10-04 15:02 AEDT,
# SYSTEM-REVIEW-20261004 actions 1-2): three distinct hypotheses plus one
# execution-cost control, frozen for 21 days on one declared data epoch
# (research_freeze.py). Every tile carries the freeze pre-registration below:
# a target sample in distinct UTC hours (n_eff), a kill rule and a day-21
# decision. Exit and entry variants are tested offline on the research table,
# never as extra live tiles.
# ---------------------------------------------------------------------------
FREEZE21_POLICY_EPOCH = RESEARCH_STACK_VERSION
FREEZE21_ID = "FREEZE21B-20261004"  # judged in the freeze21b epoch (re-declared 17:53 AEDT)
FREEZE21_REGISTERED_UTC = "2026-10-04T04:02:00Z"
FREEZE21_DECISION_DAY = 21
# Bonferroni across the three hypotheses (the control is not a trial).
FREEZE21_FAMILY_ALPHA = 0.05
FREEZE21_HYPOTHESES = 3
FREEZE21_PER_TEST_ALPHA = FREEZE21_FAMILY_ALPHA / FREEZE21_HYPOTHESES
HYPOTHESIS_SESSION_HOURS_UTC = {"ASIA": (0, 8), "EU": (8, 16), "US": (16, 24)}
FREEZE21_SESSIONS_ASIA_EU = ("ASIA", "EU")
FREEZE21_SESSIONS_ALL = ("ASIA", "EU", "US")
# Seed of the control's direction coin; part of its entry policy (signature).
RANDOM_CONTROL_COIN_SALT = "FREEZE21-RANDOM-CONTROL-v1"


def _freeze21_pre_registration(hypothesis_id: str, *, role: str, honest_label: str, control_lane: str | None,
                               control_meaning: str, sessions: tuple[str, ...], min_distinct_hours: int,
                               min_fills: int, k1_after_distinct_hours: int | None,
                               k4_max_drawdown_usd: float | None, variants_tried: int,
                               decisions: tuple[str, ...] = ()) -> dict:
    """Freeze pre-registration (schema ``tile_pre_registration_freeze21_v1``).

    Verdicts use REALISTIC_V1 paper fills of the declared freeze epoch only.
    The sample is counted in distinct UTC hours with a closed fill (n_eff), not
    in fills: overlapping trades in one hour are one observation. ``role`` is
    HYPOTHESIS (one of the three Bonferroni trials) or CONTROL (measures pure
    execution cost; never a trial, never promoted).
    """
    if role not in ("HYPOTHESIS", "CONTROL"):
        raise ValueError(f"unknown freeze role {role!r}")
    hypothesis = role == "HYPOTHESIS"
    pre = {
        "schema": "tile_pre_registration_freeze21_v1",
        "hypothesis_id": hypothesis_id,
        "role": role,
        "freeze_id": FREEZE21_ID,
        "registered_utc": FREEZE21_REGISTERED_UTC,
        "registered_cohort": FREEZE21_POLICY_EPOCH,
        "control_lane": control_lane,
        "control_meaning": control_meaning,
        "evidence_world": "REALISTIC_V1",
        "ci_method": "1H_CLUSTER_BOOTSTRAP",
        "variants_tried": variants_tried,
        "honest_label": honest_label,
        "pre_registered_decisions": tuple(decisions),
        "target": {
            "min_distinct_hours": int(min_distinct_hours),
            "min_fills": int(min_fills),
            "min_utc_days": 14,
            "meaning": "n_eff = distinct UTC hours with at least one closed fill in the freeze epoch",
        },
        # Kept for the generic pre-registration readers (session map and the
        # owner-review promotion bar). Promotion never arms the relay.
        "promotion": {
            "meaning": "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY" if hypothesis else "NEVER_PROMOTED_CONTROL",
            "min_fills": int(min_fills), "min_utc_days": 14,
            "min_distinct_hours": int(min_distinct_hours),
            "min_sessions_each": 3, "sessions": tuple(sessions),
            "session_hours_utc": {s: HYPOTHESIS_SESSION_HOURS_UTC[s] for s in sessions},
            "per_fill_ev_lower_ci_gt_bp": 0.0,
            "ci_alpha": FREEZE21_PER_TEST_ALPHA,
            "max_single_day_profit_share": 0.30,
        },
        "kill": {
            "k1_after_distinct_hours": k1_after_distinct_hours,
            "k1_mean_bp_at_or_below": 0.0 if k1_after_distinct_hours is not None else None,
            "k3_worst_trade_bp_below": -60.0, "k3_max_stale_feed_fill_share": 0.01,
            "k4_max_drawdown_usd": k4_max_drawdown_usd,
            "k6_defect_action": "PAUSE_AND_QUARANTINE_NOT_A_STRATEGY_VERDICT",
            "action": ("owner decision: toggle OFF with the documented freeze override "
                       "(reason KILL_RULE:<lane>:<rule>), then retire after the freeze"),
        },
        "day21": {
            "decision_day": FREEZE21_DECISION_DAY,
            "anchor": "FREEZE_EPOCH_START (data_epoch.json started_at of the declared freeze epoch)",
            "family_alpha": FREEZE21_FAMILY_ALPHA,
            "bonferroni_hypotheses": FREEZE21_HYPOTHESES,
            "per_test_alpha": FREEZE21_PER_TEST_ALPHA,
            "pass_lower_ci_gt_bp": 0.0,
            "fail_mean_at_or_below_bp": 0.0,
            "fail_upper_ci_lt_bp": 2.0,
        },
    }
    if hypothesis:
        pre["day21"].update({
            "pass": (f"n_eff >= {min_distinct_hours} distinct hours AND the 1 h-cluster "
                     f"{100 * (1 - FREEZE21_PER_TEST_ALPHA):.2f}% CI lower bound > 0 bp"
                     + (" AND the paired difference versus the control is > 0" if control_lane else "")
                     + " -> CONTINUE: owner review (never relay); variants only via the research table"),
            "fail": "mean <= 0 bp, or the CI upper bound < +2 bp -> RETIRE the tile",
            "inconclusive": ("anything else (including n_eff short of target) -> RETIRE the live tile; it may "
                             "return only with a new pre-registration, never by extending this one"),
        })
    else:
        pre["day21"].update({
            "pass": "REPORT the control mean and CI as the execution cost every hypothesis is read against",
            "fail": ("control 1 h-cluster CI lower bound > 0 bp (a random side made money) -> FILL_MODEL_SUSPECT: "
                     "pause verdicts and audit the REALISTIC_V1 fill model"),
            "inconclusive": "n/a - the control is a measurement, not a trial",
        })
    target, kill = pre["target"], pre["kill"]
    pre["promotion_summary"] = (
        f"Pre-registered {hypothesis_id} ({role.lower()}, {FREEZE21_ID}): target n_eff >= "
        f"{target['min_distinct_hours']} distinct UTC hours (>= {target['min_fills']} fills, >= 14 UTC days); "
        f"day-{FREEZE21_DECISION_DAY} decision: {pre['day21']['pass']}"
    )
    parts = []
    if kill["k1_after_distinct_hours"] is not None:
        parts.append(f"K1 mean <=0 bp after {kill['k1_after_distinct_hours']} distinct hours")
    parts.append("K3 any trade worse than -60 bp (stop failure) or >1% of trades on a stale feed")
    if kill["k4_max_drawdown_usd"] is not None:
        parts.append(f"K4 drawdown >${kill['k4_max_drawdown_usd']:.2f}")
    parts.append("K6 lifecycle/identity/analyzer/feed/mirror defect = pause and quarantine")
    parts.append(f"day {FREEZE21_DECISION_DAY}: {pre['day21']['fail']}")
    pre["kill_summary"] = "; ".join(parts)
    return pre


# Late-armed protections adopted by HYPOTHESIS-TILES-20261004 (nested
# walk-forward by UTC day, REALISTIC_V1, capped per signal), first trigger wins
# in family_policy_common.exit_action order.
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


# H-A (unchanged H11 rule) = the canonical committed-AI fade: taker at the
# signal inside Asia+EU, late-armed composite, conditional early cut,
# 90-minute backstop, 40 bp stop. Entry and exit are byte-identical to v11, so
# its policy signature is unchanged; only the pre-registration is new.
_COMMITTED_FADE_TAKER_ENTRY = {
    "mode": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_windows": (),
    "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
    "direction_source": "INVERTED_SCORE_LED_SIDE",
    "refuse_on": ("SCORE_TIE", "INVALID_SCORES", "AI_ERROR", "RAW_AI_NO_TRADE",
                  "SCORE_DIRECTION_MISMATCH", "BBO_STALE", "SPREAD_ABOVE_MAX", "SESSION_GATED"),
    "trades_raw_ai_no_trade": False, "min_score_gap": None,
    "commit_rule": "EXPLICIT_RAW_SIDE_EQUALS_SCORE_LED",
    "allowed_sessions": FREEZE21_SESSIONS_ASIA_EU,
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
# H-C (no AI) = Bitfinex-vs-leaders premium mean reversion: when the
# Binance/Bybit premium over Bitfinex leaves its own 60-minute mean by the
# fixed MODEL-A tails (+1.75 / -1.88 bp, never re-fitted), take the Bitfinex
# taker toward convergence and hold 60 minutes (time exit + 40 bp stop only).
# Generic per-second premium evaluator (cross_venue_premium.py).
_PREMIUM_REVERSION_ENTRY = {
    "mode": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_windows": (),
    "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
    "direction_source": "CROSS_VENUE_PREMIUM",
    "signal_clock": CROSS_VENUE_SIGNAL_CLOCK,
    "leader_venues": ("binance", "bybit"),
    "premium_mean_window_sec": 3600, "premium_min_mean_samples": 1200,
    "premium_long_threshold_bps": 1.75, "premium_short_threshold_bps": -1.88,
    "max_fill_forward_sec": 5,
    "allowed_sessions": FREEZE21_SESSIONS_ALL,
    "session_hours_utc": HYPOTHESIS_SESSION_HOURS_UTC,
    "max_venue_age_sec": 2.0, "max_bbo_age_sec": 2.0, "max_spread_bps": 3.0,
    "taker_protection_bps": 5.0, "taker_ttl_sec": 3,
    "shadow_entry_delay_sec": 1,
    # One entry per 15 minutes at most: a 60-minute hold on a persistent
    # deviation would otherwise stack correlated entries.
    "min_submit_interval_sec": 900, "max_submissions_per_hour": 4,
    "ai_decision_role": "NONE",
    "volatility_scaling": "NONE - taker at the trigger",
    "fill_model": "REALISTIC_V1",
}
_PREMIUM_REVERSION_EXIT = _composite_exit(
    max_duration_sec=3600, max_open_positions=3,
    volatility_scaling="NONE - pure signal test: time exit and catastrophic stop only",
)
# Control = the same committed calls, session gate, taker entry and exits as
# H-A, with the side from a deterministic coin (sha256 of the shared call id
# and RANDOM_CONTROL_COIN_SALT). Its mean is pure execution cost.
_RANDOM_CONTROL_ENTRY = {
    **_COMMITTED_FADE_TAKER_ENTRY,
    "direction_source": "RANDOM_COIN_ON_COMMITTED_CALL",
    "coin_salt": RANDOM_CONTROL_COIN_SALT,
    "coin_rule": "sha256(salt|shared_ai_call_id)[0] even -> LONG, odd -> SHORT",
    "refuse_on": ("SCORE_TIE", "INVALID_SCORES", "AI_ERROR", "RAW_AI_NO_TRADE",
                  "SCORE_DIRECTION_MISMATCH", "NO_CALL_ID_FOR_COIN", "BBO_STALE", "SPREAD_ABOVE_MAX",
                  "SESSION_GATED"),
    "ai_decision_role": "TRIGGER_ONLY_SIDE_IS_RANDOM",
}
_RANDOM_CONTROL_EXIT = dict(_COMMITTED_FADE_TAKER_EXIT)


COMBO_LANE_SPECS = {
    # Tile 1 - H-A: the one canonical committed-AI fade.
    RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90: _tile(
        lane=RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90,
        label="H-A Committed fade (taker) · inverted committed AI side, Asia+EU, taker, late BE + ATR trail, 90-min backstop",
        raw_policy_id=("INVERT_COMMITTED_SCORE_LED_SIDE_ASIA_EU_SPREADLE3BP_TAKER_CAP5BPS"
                       "|TIME_5400_BE20TO5_TRAIL1.5ATR_ARM2ATR_CUT12BP5M_MFE2_HARD40BP_CAP3"),
        id_prefix="cft",
        module="paper_policy_family_committed_fade_taker_90.py",
        test_module="test_paper_policy_family_committed_fade_taker_90.py",
        entry=dict(_COMMITTED_FADE_TAKER_ENTRY),
        exit_policy=dict(_COMMITTED_FADE_TAKER_EXIT),
        hypothesis_result={
            "status": "FREEZE21_HYPOTHESIS_HINT_CI_SPANS_0",
            "hypothesis_id": "FREEZE21_HA_COMMITTED_FADE_TAKER_90",
            "in_sample": ("committed calls 1-4 Oct: follow-mean at 60/90 min -10.9/-16.5 bp (fade +), but 1 Oct -12.0, "
                          "2 Oct -14.1, 3 Oct -2.1 bp; ~60 independent hours. H11 replay 119 fills +8.4 bp/fill, "
                          "1 h-cluster CI [-4.2,+22.8]"),
            "corrected": "nested day-fold +6.8 bp/fill [-6.0,+21.6]; earlier walk-forward FADE lost 3/3 folds (-11.5 bp)",
            "expected_live": "-4 to +6 bp/trade (central ~+1); ~35 trades/day; ~10 distinct hours/day (Asia+EU)",
        },
        pre_registration=_freeze21_pre_registration(
            "FREEZE21_HA_COMMITTED_FADE_TAKER_90", role="HYPOTHESIS",
            honest_label=("HINT - the contrarian effect comes from 2 trending days (1-2 Oct) and vanished on 3 Oct; "
                          "this is the one clean forward test of it"),
            control_lane=RESEARCH_LANE_FAMILY_RANDOM_CONTROL_TAKER_90,
            control_meaning=("Random-direction control trades the same committed calls with identical entry and "
                             "exits; the paired difference is the value of the fade side over execution cost"),
            sessions=FREEZE21_SESSIONS_ASIA_EU, min_distinct_hours=150, min_fills=300,
            k1_after_distinct_hours=80, k4_max_drawdown_usd=3.0, variants_tried=13,
            decisions=("one canonical fade variant; the Danish, maker and no-early-stop variants retired",
                       "entry and exits unchanged from v11 H11 (policy signature unchanged)"),
        ),
        admission_treatment=COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID,
        max_active_signals=3,
        entry_ttl_sec=3,
        subtitle="FREEZE21 H-A — canonical committed-AI fade — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=FREEZE21_POLICY_EPOCH,
        default_enabled=True,
        signal_summary="fade the shared AI's committed LONG/SHORT call (explicit side matching the score-led side)",
        live_exit_order=registry_live_exit_order(_COMMITTED_FADE_TAKER_EXIT),
        shadow_exits=_shadow_exits_except("COMPOSITE_LATE_BE20_5_TRAIL1.5_ARM2", "COND_CUT_12_5M_MFE2"),
    ),
    # Tile 2 - H-C: the non-AI idea.
    RESEARCH_LANE_FAMILY_PREMIUM_REVERSION_60M: _tile(
        lane=RESEARCH_LANE_FAMILY_PREMIUM_REVERSION_60M,
        label="H-C Premium reversion (no AI) · Binance/Bybit premium vs its 60-min mean, Bitfinex taker toward convergence, 60-min hold",
        raw_policy_id=("XVENUE_PREMIUM_DEV60M_L1.75_S1.88BP_REVERSION_ALL_SESSIONS_SPREADLE3BP_TAKER_CAP5BPS_GAP900S"
                       "|TIME_3600_HARD40BP_CAP3"),
        id_prefix="pmr",
        module="paper_policy_family_premium_reversion_60m.py",
        test_module="test_paper_policy_family_premium_reversion_60m.py",
        entry=dict(_PREMIUM_REVERSION_ENTRY),
        exit_policy=dict(_PREMIUM_REVERSION_EXIT),
        hypothesis_result={
            "status": "FREEZE21_HYPOTHESIS_DESCRIPTIVE_ONLY",
            "hypothesis_id": "FREEZE21_HC_PREMIUM_REVERSION_60M",
            "in_sample": ("own cross-venue minute tape 1-4 Oct (3,745 minutes, mid-to-mid, no spread): premium "
                          "deviation in its 10%/90% tails, Bitfinex move toward the leaders over 60 min +6.5 bp "
                          "per signal minute, positive on all 4 days (+5.2/+8.0/+2.0/+6.0); overlapping minutes, "
                          "~70 independent hours"),
            "corrected": ("part of that move is the seconds-scale catch-up a 1 s-delayed taker cannot capture; "
                          "the 60 s premium tile lost -1.86 bp on the spread"),
            "expected_live": "-3 to +4 bp/trade after the spread (central ~0); up to ~4 trades/hour while deviated",
        },
        pre_registration=_freeze21_pre_registration(
            "FREEZE21_HC_PREMIUM_REVERSION_60M", role="HYPOTHESIS",
            honest_label=("DESCRIPTIVE - 2.8 days of minute tape, no execution replay; thresholds are the fixed "
                          "MODEL-A tails, not fitted here; the only non-AI idea in the freeze"),
            control_lane=None,
            control_meaning=("Clock tile, never paired by shared AI call; the evaluator's shadow outcome of every "
                             "qualifying second (xvp_shadow_signals.jsonl, logged whether or not the tile is ON) "
                             "is the comparison"),
            sessions=FREEZE21_SESSIONS_ALL, min_distinct_hours=150, min_fills=300,
            k1_after_distinct_hours=80, k4_max_drawdown_usd=3.0, variants_tried=4,
            decisions=("premium rule only (the lead rule had a CI below 0 at 60 s)",
                       "thresholds fixed at the MODEL-A 20/80 tails; 60-minute hold; no protections beyond the stop"),
        ),
        admission_treatment=PREMIUM_REVERSION_ADMISSION_POLICY_ID,
        max_active_signals=3,
        entry_ttl_sec=3,
        subtitle="FREEZE21 H-C — premium mean reversion, no AI — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=FREEZE21_POLICY_EPOCH,
        default_enabled=True,
        signal_clock=CROSS_VENUE_SIGNAL_CLOCK,
        signal_summary=("the Binance/Bybit premium over Bitfinex leaves its own 60-minute mean by >=+1.75 / "
                        "<=-1.88 bp; take Bitfinex toward the leaders (expected convergence)"),
        live_exit_order=registry_live_exit_order(_PREMIUM_REVERSION_EXIT),
        shadow_exits=ALL_SHADOW_EXITS,
        early_cut_shadow_reason="pure signal test: every protection, including the early cut, is shadow-only",
    ),
    # Tile 3 - control: H-A's calls and exits with a random side.
    RESEARCH_LANE_FAMILY_RANDOM_CONTROL_TAKER_90: _tile(
        lane=RESEARCH_LANE_FAMILY_RANDOM_CONTROL_TAKER_90,
        label="Control · random side on H-A's committed calls, Asia+EU, taker, H-A's exits (execution cost)",
        raw_policy_id=("RANDOM_COIN_ON_COMMITTED_CALL_ASIA_EU_SPREADLE3BP_TAKER_CAP5BPS"
                       "|TIME_5400_BE20TO5_TRAIL1.5ATR_ARM2ATR_CUT12BP5M_MFE2_HARD40BP_CAP3"),
        id_prefix="rnd",
        module="paper_policy_family_random_control_taker_90.py",
        test_module="test_paper_policy_family_random_control_taker_90.py",
        entry=dict(_RANDOM_CONTROL_ENTRY),
        exit_policy=dict(_RANDOM_CONTROL_EXIT),
        hypothesis_result={
            "status": "FREEZE21_CONTROL",
            "hypothesis_id": "FREEZE21_CONTROL_RANDOM_TAKER_90",
            "in_sample": "n/a - a coin has no edge by construction",
            "corrected": "expected mean = minus the round-trip execution cost (spread ~1.6-1.8 bp plus slippage)",
            "expected_live": "about -2 bp/trade; ~35 trades/day (same calls as H-A)",
        },
        pre_registration=_freeze21_pre_registration(
            "FREEZE21_CONTROL_RANDOM_TAKER_90", role="CONTROL",
            honest_label="CONTROL - measures pure execution cost; never a trial, never promoted",
            control_lane=None,
            control_meaning="Is the control for H-A (same calls, entry and exits; side from a deterministic coin)",
            sessions=FREEZE21_SESSIONS_ASIA_EU, min_distinct_hours=150, min_fills=300,
            k1_after_distinct_hours=None, k4_max_drawdown_usd=None, variants_tried=1,
            decisions=("no performance kill: it runs while H-A runs and stops with it or at day 21",),
        ),
        admission_treatment=RANDOM_CONTROL_ADMISSION_POLICY_ID,
        max_active_signals=3,
        entry_ttl_sec=3,
        subtitle="FREEZE21 CONTROL — random direction, H-A's exits — PAPER ONLY — RELAY INELIGIBLE",
        policy_epoch=FREEZE21_POLICY_EPOCH,
        default_enabled=True,
        signal_summary=("the shared AI's committed LONG/SHORT calls (same calls as H-A); the side is a "
                        "deterministic coin flip, not the AI"),
        live_exit_order=registry_live_exit_order(_RANDOM_CONTROL_EXIT),
        shadow_exits=_shadow_exits_except("COMPOSITE_LATE_BE20_5_TRAIL1.5_ARM2", "COND_CUT_12_5M_MFE2"),
    ),
}
# ---------------------------------------------------------------------------
# FREEZE21B additions (owner Danish, 2026-10-04 17:53 + 17:54 AEDT: "every
# strategy must be a visible paper tile"). Grok Strategist's pre-registered
# shadow rules become paper tiles with the specs frozen in
# /workspace/grok-strategist/preregistration/PREREG-GS-20261004{,-B}.{md,json}:
# GS-01..04 (06:20:20Z) and B1..B3 (06:34:58Z). $25 notional (0.25 margin at
# 100x), one open position per tile, paper only, relay fail-closed. Entry,
# regime and exit mechanics: regime_bars_3m.py, gs_regime_exit_stack.py,
# regime_adaptive_binding.py. Deviations from the offline specs are listed in
# diagnostics/FREEZE21-PROTOCOL-20261004.md (freeze21b section).
# ---------------------------------------------------------------------------
FREEZE21B_ID = "FREEZE21B-20261004"
FREEZE21B_REGISTERED_UTC = "2026-10-04T06:53:00Z"
GS_PREREG_PATH = "grok-strategist/preregistration/PREREG-GS-20261004.json"
GSB_PREREG_PATH = "grok-strategist/preregistration/PREREG-GS-20261004-B.json"
GS_REGIME_EXIT_FAMILY = "REGIME_ADAPTIVE_FIRST_TRIGGER_WINS"
GS_FREE_SPREAD_GUARD_BPS = 10.0  # execution sanity guard only; the GS specs have no spread filter


def _gs_pre_registration(rule_id: str, *, prereg_path: str, registered_utc: str, bonferroni_k: int,
                         honest_label: str, giveback_arm_bp: float, extra_kills: dict | None = None,
                         decisions: tuple[str, ...] = ()) -> dict:
    """GS pre-registration (schema ``tile_pre_registration_gs20261004_v1``); judged at the freeze21b epoch end."""
    kill = {
        "harm_after_fills": 30, "harm_mean_bp_at_or_below": -2.0, "harm_ci95_upper_below_bp": 0.0,
        "futility_min_n_eff": 30, "futility_mean_below_bp": 1.0, "bonferroni_k": int(bonferroni_k),
        "control_min_edge_bp": 2.0, "control_scoring": "OFFLINE_GROK_STRATEGIST_RANDOM_SIDE_SAME_TRIGGERS",
        "giveback_after_fills": 30, "giveback_mfe_arm_bp": float(giveback_arm_bp), "giveback_rate_above": 0.25,
        **(extra_kills or {}),
        "action": ("owner decision: toggle OFF with the documented freeze override "
                   "(reason KILL_RULE:<lane>:<rule>), then retire after the freeze"),
    }
    pre = {
        "schema": "tile_pre_registration_gs20261004_v1",
        "hypothesis_id": rule_id,
        "role": "HYPOTHESIS",
        "freeze_id": FREEZE21B_ID,
        "registered_utc": registered_utc,
        "source_prereg": prereg_path,
        "registered_cohort": RESEARCH_STACK_VERSION,
        "control_lane": None,
        "control_meaning": ("Random-direction control on the same triggers, scored offline by Grok Strategist "
                            "(no live control tile per rule)"),
        "evidence_world": "REALISTIC_V1",
        "ci_method": "1H_CLUSTER_BOOTSTRAP",
        "metric": "net bp per closed fill (live); Strategist's per-signal score (misses = 0) is the offline cross-check",
        "variants_tried": int(bonferroni_k),
        "honest_label": honest_label,
        "pre_registered_decisions": tuple(decisions),
        "target": {"min_fills": 30, "min_n_eff": 30,
                   "meaning": "n_eff = distinct UTC hours with at least one closed fill in the freeze21b epoch"},
        "promotion": {"meaning": "PASS_FORWARD_TO_OWNER_REVIEW_NEVER_RELAY", "min_fills": 30, "min_n_eff": 30,
                      "mean_bp_gt": 0.0, "bonferroni_k": int(bonferroni_k), "family_alpha": 0.05},
        "kill": kill,
        "decision": {
            "decision_day": FREEZE21_DECISION_DAY,
            "anchor": "FREEZE_EPOCH_START (freeze21b data_epoch.json started_at)",
            "pass": ("PASS_FORWARD: fills >= 30, n_eff >= 30, mean > 0 and the one-sided 1 h-cluster lower bound at "
                     f"alpha 0.05/{int(bonferroni_k)} > 0 (and beats the offline random control by >= 2 bp)"),
            "fail": "KILLED: any kill rule fires (harm any time; futility at the epoch end)",
            "inconclusive": "INSUFFICIENT: fewer than 30 fills or n_eff < 30 at the epoch end",
        },
    }
    parts = [f"harm: after 30 fills mean <= -2 bp with CI95 upper < 0",
             f"futility at epoch end: n_eff >= 30 and mean < +1 bp or Bonferroni(k={int(bonferroni_k)}) lower bound <= 0",
             "control: must beat the random side on the same triggers by >= 2 bp (offline)",
             f"give-back: MFE >= {giveback_arm_bp:g} bp then closed <= 0 in > 25% of trades after 30 fills"]
    for key, text in (("fill_rate_below", "fill rate < {v:.0%} after 40 signals"),
                      ("latency_p50_above_sec", "signal->submit p50 > {v:g} s"),
                      ("data_missing_share_above", "Bitfinex trade fields missing > {v:.0%} -> INSUFFICIENT_DATA"),
                      ("be_armed_negative_share_above", "> {v:.0%} of break-even-armed trades close negative"),
                      ("worst_trade_below_bp", "any trade < {v:g} bp")):
        if kill.get(key) is not None:
            parts.append(text.format(v=kill[key]))
    pre["promotion_summary"] = (f"Pre-registered {rule_id} ({FREEZE21B_ID}): target >= 30 fills and n_eff >= 30 "
                                f"distinct hours; epoch end: {pre['decision']['pass']}")
    pre["kill_summary"] = "; ".join(parts)
    return pre


# GS simple stack (gslib.simulate_exit): first trigger wins in the order
# HARD, BREAKEVEN_LOCK, ATR_TRAIL, MFE_GIVEBACK, EARLY_CUT, ATR_TAKE_PROFIT,
# then the time stop. ATR = 3 m ATR14 in bp at the signal (4 bp when missing).
def _gs_simple_exit(*, tp_atr: float | None, be_atr: float, trail_atr: float | None = None,
                    trail_arm_atr: float | None = None) -> dict:
    profile = {
        "stack": "GS_SIMPLE_V1", "hard_bp": 35.0, "cut_bp": 8.0, "cut_win_sec": 300, "cut_close_sec": 1,
        "time_sec": 3600, "be_atr": be_atr, "be_floor": 6.0, "lock_bp": 1.0,
        "trail_atr": trail_atr, "trail_arm_atr": trail_arm_atr, "trail_floor": 5.0, "trail_arm_floor": 8.0,
        "tp_atr": tp_atr, "tp_floor": 8.0, "gb_arm": None, "gb_frac": None,
        "order": ("HARD_STOP", "BREAKEVEN_LOCK", "ATR_TRAIL", "MFE_GIVEBACK", "THESIS_CUT", "ATR_TAKE_PROFIT"),
    }
    order = ["HARD_STOP", "BREAKEVEN_LOCK"] + (["ATR_TRAIL"] if trail_atr else []) + ["THESIS_CUT"] \
        + (["ATR_TAKE_PROFIT"] if tp_atr else []) + ["TIME_BACKSTOP"]
    return {
        "family": GS_REGIME_EXIT_FAMILY, "max_duration_sec": 3600,
        "hard_stop_bps": 35.0, "hard_stop_margin_pct": 35.0,
        "profiles": {"ALL": profile}, "regime_profiles": {"QUIET": "ALL", "TREND": "ALL", "VIOLENT": "ALL"},
        "take_profit_fill": "MAKER_LIMIT_AT_TARGET_FILLED_WHEN_SIDE_CORRECT_MARK_TRADES_THROUGH",
        "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_FIRED_THE_RULE",
        "atr_source": "REGIME_BARS_3M_ATR14_BP_AT_SIGNAL_DEFAULT_4BP",
        "max_open_positions": 1, "exit_order": tuple(order),
    }


def _b_profile(*, hard, cut, cut_close, time_sec, tp1_atr, tp1_floor, tp_atr, tp_floor, be_atr, be_floor=6.0,
               trail_atr=None, trail_arm_atr=None, gb_arm, gb_frac, shock_k=1.5, shock_profit_only=False,
               flip_profit_only=False) -> dict:
    return {
        "stack": "DYNLIB_REGIME_V1", "hard_bp": float(hard), "cut_bp": float(cut), "cut_win_sec": 300,
        "cut_close_sec": int(cut_close), "time_sec": int(time_sec),
        "tp1_atr": tp1_atr, "tp1_floor": float(tp1_floor), "tp1_frac": 0.5, "lock_bp": 2.0,
        "tp_atr": tp_atr, "tp_floor": float(tp_floor), "be_atr": be_atr, "be_floor": float(be_floor),
        "trail_atr": trail_atr, "trail_arm_atr": trail_arm_atr, "trail_floor": 5.0, "trail_arm_floor": 8.0,
        "gb_arm": gb_arm, "gb_frac": gb_frac, "shock_k": shock_k, "shock_profit_only": bool(shock_profit_only),
        "flip": True, "flip_profit_only": bool(flip_profit_only),
        "order": ("HARD_STOP", "THESIS_CUT", "VOL_SHOCK", "BREAKEVEN_LOCK", "ATR_TRAIL", "MFE_GIVEBACK",
                  "INDICATOR_FLIP", "ATR_TAKE_PROFIT"),
    }


# PREREG-GS-20261004-B profiles (dynlib PR / PR_REV), byte-for-byte numbers.
GSB_PROFILES = {
    "MOM_QUIET": _b_profile(hard=30, cut=8, cut_close=1, time_sec=7200, tp1_atr=1.0, tp1_floor=6, tp_atr=2.0,
                            tp_floor=10, be_atr=1.0, gb_arm=10, gb_frac=0.5),
    "MOM_TREND": _b_profile(hard=30, cut=8, cut_close=1, time_sec=10800, tp1_atr=1.5, tp1_floor=8, tp_atr=None,
                            tp_floor=10, be_atr=1.0, trail_atr=3.0, trail_arm_atr=1.5, gb_arm=15, gb_frac=0.5),
    "MOM_VIOLENT": _b_profile(hard=35, cut=9, cut_close=1, time_sec=5400, tp1_atr=1.0, tp1_floor=8, tp_atr=2.0,
                              tp_floor=12, be_atr=0.75, trail_atr=1.5, trail_arm_atr=1.0, gb_arm=12, gb_frac=0.4),
    "REV_QUIET": _b_profile(hard=30, cut=9, cut_close=60, time_sec=7200, tp1_atr=1.5, tp1_floor=6, tp_atr=2.5,
                            tp_floor=10, be_atr=1.5, gb_arm=15, gb_frac=0.5, shock_profit_only=True,
                            flip_profit_only=True),
    "REV_TREND": _b_profile(hard=30, cut=8, cut_close=60, time_sec=7200, tp1_atr=1.0, tp1_floor=6, tp_atr=2.0,
                            tp_floor=10, be_atr=1.0, gb_arm=10, gb_frac=0.5, shock_profit_only=False,
                            flip_profit_only=True),
    "REV_VIOLENT": _b_profile(hard=35, cut=9, cut_close=60, time_sec=5400, tp1_atr=1.0, tp1_floor=8, tp_atr=2.5,
                              tp_floor=12, be_atr=1.0, trail_atr=2.0, trail_arm_atr=1.5, gb_arm=12, gb_frac=0.4,
                              shock_profit_only=True, flip_profit_only=True),
}
GSB_LIVE_EXIT_ORDER = ("HARD_STOP", "THESIS_CUT", "VOL_SHOCK", "BREAKEVEN_LOCK", "ATR_TRAIL", "MFE_GIVEBACK",
                       "INDICATOR_FLIP", "ATR_TAKE_PROFIT", "LADDER_TP1", "LADDER_LOCK", "TIME_BACKSTOP")


def _gsb_exit(regime_profiles: dict) -> dict:
    used = {name: GSB_PROFILES[name] for name in sorted(set(v for v in regime_profiles.values() if v))}
    return {
        "family": GS_REGIME_EXIT_FAMILY,
        "max_duration_sec": max(p["time_sec"] for p in used.values()),
        "hard_stop_bps": max(p["hard_bp"] for p in used.values()),
        "hard_stop_margin_pct": max(p["hard_bp"] for p in used.values()),
        "profiles": used, "regime_profiles": dict(regime_profiles),
        "partial_take_profits": (("LADDER_TP1", 0.5),),
        "take_profit_fill": "MAKER_LIMIT_AT_TARGET_FILLED_WHEN_SIDE_CORRECT_MARK_TRADES_THROUGH",
        "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_FIRED_THE_RULE",
        "atr_source": "REGIME_BARS_3M_ATR14_BP_AT_SIGNAL_DEFAULT_4BP",
        "max_open_positions": 1, "exit_order": GSB_LIVE_EXIT_ORDER,
    }


# Regime classifiers (last closed 3 m bar at the signal; regime_bars_3m.classify_regime).
GSB_REGIME = {"violent_atr_pct_gte": 80.0, "violent_spread_bp_gte": 3.0, "trend_adx_gte": 25.0}
# Entry executions (dynlib.do_entry / gslib.limit_entry). Reprice times are
# seconds after the order rests: the first second of every reprice_sec step
# inside the declared 5-minute windows (window w covers [(w-1)*300, w*300)).
GS_EXEC = {
    "TAKER": {"kind": "TAKER"},
    "TOUCH": {"kind": "TOUCH", "offset_atr_k": 0.0, "chase_windows": (1, 2), "gap_step": 1.0, "reprice_sec": 60,
              "ttl_sec": 600, "fallback": "TAKER_IF_DRIFT_LE_0.5ATR_FAVOURABLE_AND_GE_-8BP",
              "fallback_favourable_atr_k": 0.5, "fallback_adverse_bp": 8.0, "order_ttl_sec": 660},
    "OFFSET": {"kind": "OFFSET", "offset_atr_k": 0.25, "chase_windows": (1, 2, 3), "gap_step": 0.5,
               "reprice_sec": 120, "ttl_sec": 900, "order_ttl_sec": 900},
    "DEEP_0.75": {"kind": "DEEP", "offset_atr_k": 0.75, "chase_windows": (), "gap_step": 0.0, "reprice_sec": 180,
                  "ttl_sec": 1200, "order_ttl_sec": 1200},
    "DEEP_1.5": {"kind": "DEEP", "offset_atr_k": 1.5, "chase_windows": (), "gap_step": 0.0, "reprice_sec": 180,
                 "ttl_sec": 1200, "order_ttl_sec": 1200},
}


def _gs_entry(*, mode: str, direction_source: str, ai_role: str, regime: dict | None,
              regime_exec: dict, **extra) -> dict:
    return {
        "mode": mode, "offset_pct": 0.0, "chase_windows": (), "remaining_gap_step_pct": 0.0, "reprice_sec": 0,
        "direction_source": direction_source,
        "allowed_sessions": FREEZE21_SESSIONS_ALL, "session_hours_utc": HYPOTHESIS_SESSION_HOURS_UTC,
        "max_bbo_age_sec": 5.0, "max_spread_bps": GS_FREE_SPREAD_GUARD_BPS,
        "taker_protection_bps": 5.0, "taker_ttl_sec": 3,
        "regime_classifier": dict(regime) if regime else None,
        "regime_exec": {k: (dict(GS_EXEC[v]) if v else None) for k, v in regime_exec.items()},
        "regime_source": "REGIME_BARS_3M_LAST_CLOSED_BAR_AT_SIGNAL",
        "ai_decision_role": ai_role, "fill_model": "REALISTIC_V1",
        **extra,
    }


_GS01_ENTRY = _gs_entry(
    mode="TAKER_AT_SIGNAL", direction_source="CROSS_VENUE_PREMIUM", ai_role="NONE", regime=None,
    regime_exec={"QUIET": "TAKER", "TREND": "TAKER", "VIOLENT": "TAKER"},
    signal_clock=CROSS_VENUE_SIGNAL_CLOCK,
    **{k: _PREMIUM_REVERSION_ENTRY[k] for k in (
        "leader_venues", "premium_mean_window_sec", "premium_min_mean_samples", "premium_long_threshold_bps",
        "premium_short_threshold_bps", "max_fill_forward_sec", "max_venue_age_sec", "shadow_entry_delay_sec")},
    trigger_stream="H-C premium rule (same thresholds and 3 bp spread gate), own evaluator instance",
    min_submit_interval_sec=5, max_submissions_per_hour=60,
)
_GS01_ENTRY.update({"max_bbo_age_sec": 2.0, "max_spread_bps": 3.0})
_CVD_TRIGGER = {"cvd_indicator": "CVD_DIVERGENCE_20", "cvd_lookback_bars": 20, "bar_sec": 180,
                "trigger_event": "TRANSITION_INTO_LONG_OR_SHORT_AT_BAR_CLOSE"}
_GSB1_ENTRY = _gs_entry(
    mode="REGIME_ADAPTIVE", direction_source="CVD_DIVERGENCE_3M", ai_role="NONE", regime=GSB_REGIME,
    regime_exec={"QUIET": "TOUCH", "TREND": None, "VIOLENT": "DEEP_0.75"},
    signal_clock=BAR_CLOSE_SIGNAL_CLOCK, min_submit_interval_sec=5, max_submissions_per_hour=60,
    flip_indicator={"QUIET": "CVD_DIVERGENCE_20", "VIOLENT": "CVD_DIVERGENCE_20"}, **_CVD_TRIGGER,
)
_COMMITTED_FADE_KEYS = {
    "refuse_on": ("SCORE_TIE", "INVALID_SCORES", "AI_ERROR", "RAW_AI_NO_TRADE", "SCORE_DIRECTION_MISMATCH",
                  "BBO_STALE", "FADE_SPREAD_ABOVE_MAX", "FADE_SESSION_GATED"),
    "trades_raw_ai_no_trade": False, "min_score_gap": None, "commit_rule": "EXPLICIT_RAW_SIDE_EQUALS_SCORE_LED",
    "fade_allowed_sessions": FREEZE21_SESSIONS_ASIA_EU, "fade_max_spread_bps": 3.0,
    "fade_definition": "H-A committed fade (frozen scorer: explicit side == score-led side, Asia+EU, spread <= 3 bp)",
}
_GSB2_ENTRY = _gs_entry(
    mode="REGIME_ADAPTIVE", direction_source="INVERTED_SCORE_LED_SIDE", ai_role="FEATURE_ONLY",
    regime=GSB_REGIME, regime_exec={"QUIET": "TOUCH", "TREND": "OFFSET", "VIOLENT": "DEEP_0.75"},
    bar_clock_trigger="CVD_DIVERGENCE_20", min_submit_interval_sec=5, max_submissions_per_hour=60,
    regime_trigger={"QUIET": "CVD_DIVERGENCE_3M", "TREND": "COMMITTED_FADE", "VIOLENT": "CVD_DIVERGENCE_3M"},
    flip_indicator={"QUIET": "CVD_DIVERGENCE_20", "TREND": "CVD_TREND_20", "VIOLENT": "CVD_DIVERGENCE_20"},
    **_CVD_TRIGGER, **_COMMITTED_FADE_KEYS,
)
_GSB3_ENTRY = _gs_entry(
    mode="REGIME_ADAPTIVE", direction_source="INVERTED_SCORE_LED_SIDE", ai_role="FEATURE_ONLY",
    # PHASE02 (owner, 2026-10-07): committed fade loses in VIOLENT (H-A -3.63
    # bp/trade, GS-06 -4.19). The locked meta-rule routes VIOLENT to premium
    # reversion, not fade, so B3 stands aside there (mirrors B1's TREND aside).
    regime=GSB_REGIME, regime_exec={"QUIET": "TOUCH", "TREND": "OFFSET"},
    flip_indicator={"QUIET": "CVD_TREND_20", "TREND": "CVD_TREND_20"},
    **_COMMITTED_FADE_KEYS,
)
_GS01_EXIT = _gs_simple_exit(tp_atr=2.5, be_atr=2.0)
_GSB1_EXIT = _gsb_exit({"QUIET": "MOM_QUIET", "TREND": None, "VIOLENT": "MOM_VIOLENT"})
_GSB2_EXIT = _gsb_exit({"QUIET": "REV_QUIET", "TREND": "MOM_TREND", "VIOLENT": "REV_VIOLENT"})
_GSB3_EXIT = _gsb_exit({"QUIET": "MOM_QUIET", "TREND": "MOM_TREND", "VIOLENT": None})
GS_SHADOW_REASON = "pre-registered stack: its own thesis cut is live; the shared catalog is shadow-only comparison"


def _gs_tile(*, lane, label, raw_policy_id, id_prefix, module, entry, exit_policy, pre, hypothesis, subtitle,
             signal_summary, admission, signal_clock=None, entry_ttl_sec=3, relay=None,
             max_active_signals=1) -> dict:
    return _tile(
        lane=lane, label=label, raw_policy_id=raw_policy_id, id_prefix=id_prefix,
        module=module, test_module="test_" + module, entry=dict(entry), exit_policy=dict(exit_policy),
        relay_capability=relay or "BLOCKED_UNQUALIFIED",
        hypothesis_result=hypothesis, pre_registration=pre, admission_treatment=admission,
        max_active_signals=int(max_active_signals), entry_ttl_sec=entry_ttl_sec, subtitle=subtitle, policy_epoch=FREEZE21_POLICY_EPOCH,
        default_enabled=True, signal_clock=signal_clock, signal_summary=signal_summary,
        live_exit_order=tuple(exit_policy["exit_order"]), shadow_exits=ALL_SHADOW_EXITS,
    )


_GS_HONEST = "SHADOW RULE PROMOTED TO A PAPER TILE BY OWNER ORDER - discovery-only evidence, CI spans 0"
COMBO_LANE_SPECS.update({
    RESEARCH_LANE_FAMILY_GS01_XV_PREMIUM_ATR_TP: _gs_tile(
        lane=RESEARCH_LANE_FAMILY_GS01_XV_PREMIUM_ATR_TP,
        label="GS-01 Premium follow + ATR TP · H-C premium trigger, taker, maker TP 2.5 ATR, BE 2 ATR, 60-min stop",
        raw_policy_id="GS01_XVENUE_PREMIUM_DEV60M_L1.75_S1.88BP_FOLLOW_TAKER_CAP5BPS|GS_TP2.5ATR_BE2ATR_LOCK1_CUT8BP5M_HARD35BP_T60M_CAP1",
        id_prefix="gs1", module="paper_policy_family_gs01_xv_premium_atr_tp.py",
        entry=_GS01_ENTRY, exit_policy=_GS01_EXIT, signal_clock=CROSS_VENUE_SIGNAL_CLOCK,
        admission=GS_PREMIUM_FOLLOW_ADMISSION_POLICY_ID,
        hypothesis={"status": "GS20261004_DISCOVERY_ONLY_CI_SPANS_0", "hypothesis_id": "GS-20261004-01",
                    "in_sample": "discovery only (pre-freeze21, in-sample selection); see PREREG-GS-20261004.md"},
        pre=_gs_pre_registration(
            "GS-20261004-01", prereg_path=GS_PREREG_PATH, registered_utc="2026-10-04T06:20:20Z", bonferroni_k=4,
            honest_label=_GS_HONEST, giveback_arm_bp=8.0, extra_kills={"latency_p50_above_sec": 5.0},
            decisions=("same trigger stream as H-C (own evaluator instance, no shadow file)",)),
        subtitle="GS-01 — premium follow, ATR take-profit — PAPER ONLY — RELAY INELIGIBLE",
        signal_summary=("H-C's cross-venue premium trigger (Binance/Bybit premium vs its 60-min mean "
                        ">=+1.75 / <=-1.88 bp); taker toward convergence; ATR-scaled maker take-profit"),
    ),
    RESEARCH_LANE_FAMILY_GSB1_CVD_DIV_REGIME: _gs_tile(
        lane=RESEARCH_LANE_FAMILY_GSB1_CVD_DIV_REGIME,
        label="B1 CVD divergence, regime-managed · touch limit when quiet, stand aside in trend, deep 0.75 ATR limit when violent, momentum exit set",
        raw_policy_id="GSB1_CVD_DIVERGENCE_3M_QUIET_TOUCH_TREND_ASIDE_VIOLENT_DEEP0.75ATR|DYN_MOM_QUIET_VIOLENT_LADDER_BE_TRAIL_GB_SHOCK_FLIP_CAP1",
        id_prefix="gb1", module="paper_policy_family_gsb1_cvd_div_regime.py",
        entry=_GSB1_ENTRY, exit_policy=_GSB1_EXIT, signal_clock=BAR_CLOSE_SIGNAL_CLOCK,
        admission=GS_CVD_DIVERGENCE_ADMISSION_POLICY_ID, entry_ttl_sec=1200,
        relay=PARTIAL_EXIT_RELAY_CAPABILITY,
        hypothesis={"status": "GS20261004_DISCOVERY_ONLY_CI_SPANS_0", "hypothesis_id": "GS-20261004-B1",
                    "in_sample": "discovery only (see PREREG-GS-20261004-B.md)"},
        pre=_gs_pre_registration(
            "GS-20261004-B1", prereg_path=GSB_PREREG_PATH, registered_utc="2026-10-04T06:34:58Z", bonferroni_k=3,
            honest_label=_GS_HONEST, giveback_arm_bp=10.0,
            extra_kills={"be_armed_negative_share_above": 0.10, "worst_trade_below_bp": -45.0,
                         "fill_rate_below": 0.60, "fill_rate_after_signals": 40, "latency_p50_above_sec": 5.0}),
        subtitle="B1 — CVD divergence with regime-specific entry and exits — PAPER ONLY — RELAY INELIGIBLE",
        signal_summary=("the GS-03 CVD divergence event; QUIET: passive touch limit with re-pegs and a guarded "
                        "taker fallback; TREND: stand aside; VIOLENT: deep 0.75 ATR limit, 20-min TTL"),
    ),
    RESEARCH_LANE_FAMILY_GSB2_REGIME_SWITCHER: _gs_tile(
        lane=RESEARCH_LANE_FAMILY_GSB2_REGIME_SWITCHER,
        label="B2 Regime switcher · CVD divergence reversion in quiet/violent, committed-AI fade in trend, regime exits",
        raw_policy_id="GSB2_QUIET_VIOLENT_CVD_DIVERGENCE_REV_TREND_COMMITTED_FADE_MOM_OFFSET0.25ATR|DYN_REV_QUIET_VIOLENT_MOM_TREND_LADDER_BE_TRAIL_GB_SHOCK_FLIP_CAP1",
        id_prefix="gb2", module="paper_policy_family_gsb2_regime_switcher.py",
        entry=_GSB2_ENTRY, exit_policy=_GSB2_EXIT, admission=GSB2_REGIME_SWITCH_ADMISSION_POLICY_ID,
        entry_ttl_sec=1200, relay=PARTIAL_EXIT_RELAY_CAPABILITY,
        hypothesis={"status": "GS20261004_DISCOVERY_ONLY_CI_SPANS_0", "hypothesis_id": "GS-20261004-B2",
                    "in_sample": "discovery only (see PREREG-GS-20261004-B.md)"},
        pre=_gs_pre_registration(
            "GS-20261004-B2", prereg_path=GSB_PREREG_PATH, registered_utc="2026-10-04T06:34:58Z", bonferroni_k=3,
            honest_label=_GS_HONEST, giveback_arm_bp=10.0,
            extra_kills={"be_armed_negative_share_above": 0.10, "worst_trade_below_bp": -45.0,
                         "fill_rate_below": 0.60, "fill_rate_after_signals": 40, "latency_p50_above_sec": 5.0}),
        subtitle="B2 — regime switcher (CVD reversion / committed fade) — PAPER ONLY — RELAY INELIGIBLE",
        signal_summary=("QUIET and VIOLENT: the CVD divergence event (reversion exit set); TREND (ADX >= 25): fade "
                        "the shared AI's committed call (H-A definition) with a 0.25 ATR offset limit"),
    ),
    RESEARCH_LANE_FAMILY_GSB3_COMMITTED_FADE_REGIME: _gs_tile(
        lane=RESEARCH_LANE_FAMILY_GSB3_COMMITTED_FADE_REGIME,
        label="B3 Committed fade, regime-managed · H-A's committed-AI fade with touch / 0.25 ATR offset entries in quiet / trend and a momentum exit set (stand aside in violent)",
        raw_policy_id="GSB3_INVERT_COMMITTED_SCORE_LED_SIDE_ASIA_EU_SPREADLE3BP_QUIET_TOUCH_TREND_OFFSET0.25ATR_VIOLENT_ASIDE|DYN_MOM_QUIET_TREND_LADDER_BE_TRAIL_GB_SHOCK_FLIP_CAP1",
        id_prefix="gb3", module="paper_policy_family_gsb3_committed_fade_regime.py",
        entry=_GSB3_ENTRY, exit_policy=_GSB3_EXIT, admission=GSB3_COMMITTED_FADE_ADMISSION_POLICY_ID,
        entry_ttl_sec=1200, relay=PARTIAL_EXIT_RELAY_CAPABILITY,
        hypothesis={"status": "GS20261004_DISCOVERY_ONLY_CI_SPANS_0", "hypothesis_id": "GS-20261004-B3",
                    "in_sample": "discovery only (see PREREG-GS-20261004-B.md)"},
        pre=_gs_pre_registration(
            "GS-20261004-B3", prereg_path=GSB_PREREG_PATH, registered_utc="2026-10-04T06:34:58Z", bonferroni_k=3,
            honest_label=_GS_HONEST, giveback_arm_bp=10.0,
            extra_kills={"be_armed_negative_share_above": 0.10, "worst_trade_below_bp": -45.0,
                         "fill_rate_below": 0.60, "fill_rate_after_signals": 40, "latency_p50_above_sec": 5.0},
            decisions=("committed fade follows H-A and the frozen scorer, not the prereg prose 'gap >= 30'",
                       "PHASE02 (2026-10-07): B3 stands aside in VIOLENT (fade loses there; VIOLENT routes to premium reversion)")),
        subtitle="B3 — committed-AI fade, quiet/trend regimes only — PAPER ONLY — RELAY INELIGIBLE",
        signal_summary=("fade the shared AI's committed LONG/SHORT call (H-A definition, Asia+EU, spread <= 3 bp); "
                        "entry and exits chosen by the regime of the last closed 3-minute bar; VIOLENT stands aside"),
    ),
})

# ---------------------------------------------------------------------------
# FREEZE21B mid-epoch addition GS-06 (owner ask 2026-10-05 ~20:15 AEDT;
# spec diagnostics/GS05-GS06-TILE-SPECS-20261005.md, Health Monitor + Grok
# Strategist). Paper only, relay ineligible, same freeze21b data epoch; the
# window starts at the deploy of the registering revision (research_freeze.
# MID_EPOCH_ADDITIONS). GS-05 was retired in the PHASE02 transaction
# (2026-10-07). GS-06 reuses the H-A building blocks above without touching
# any frozen tile's entry or exit dict.
# ---------------------------------------------------------------------------
GS0506_SPEC_PATH = "diagnostics/GS05-GS06-TILE-SPECS-CORRECTED-20261005.md"
GS0506_REGISTERED_UTC = "2026-10-05T09:15:00Z"
GS0506_DECISION_ANCHOR = ("MID_EPOCH_ADDITION_START (first boot of the registering revision); judged with the "
                          "freeze21b epoch end, fewer than 21 days of data by construction")


# CORRECTED 2026-10-05 20:37 AEDT (Danish-approved, Grok Strategist
# GS05-GS06-TILE-SPECS-CORRECTED-20261005 after STRATEGY-CROSSCHECK-20261005).
# GS-05 was retired in the PHASE02 atomic transaction (2026-10-07); GS-06
# remains: HM F1 "patient fade". The first-draft quick-TP / ladder exits were
# never shipped.
_GS06_ENTRY = _gs_entry(
    mode="REGIME_ADAPTIVE", direction_source="INVERTED_SCORE_LED_SIDE", ai_role="FEATURE_ONLY",
    regime=GSB_REGIME, regime_exec={"QUIET": "TAKER", "TREND": "TAKER", "VIOLENT": "TAKER"},
    **_COMMITTED_FADE_KEYS,
)
_GS06_ENTRY.update({
    "allowed_sessions": FREEZE21_SESSIONS_ASIA_EU, "max_spread_bps": 3.0, "max_bbo_age_sec": 5.0,
    "refuse_on": ("SCORE_TIE", "INVALID_SCORES", "AI_ERROR", "RAW_AI_NO_TRADE", "SCORE_DIRECTION_MISMATCH",
                  "BBO_STALE", "SPREAD_ABOVE_MAX", "SESSION_GATED"),
    "regime_audit": True,
    # VIOLENT is traded as its own pre-registered cell; every VIOLENT decision
    # also carries the old meta-rule's stand-aside as a shadow tag.
    "regime_cells": {"QUIET": "QUIET_TREND", "TREND": "QUIET_TREND", "VIOLENT": "VIOLENT"},
    "shadow_stand_aside_regimes": ("VIOLENT",),
})
# HM F1 "patient fade": -12 bp / 5 min cut, 40 bp stop, break-even armed at
# +25 bp locking +8 bp, ATR trail (2.5 ATR distance) armed at max(+25 bp,
# 3 ATR) i.e. after break-even, 120-minute backstop; no TP, no ladder.
_GS06_PATIENT_PROFILE = {
    "stack": "PATIENT_FADE_HM_F1", "hard_bp": 40.0, "cut_bp": 12.0, "cut_win_sec": 300, "cut_close_sec": 1,
    "time_sec": 7200, "be_atr": 0.0, "be_floor": 25.0, "lock_bp": 8.0,
    "trail_atr": 2.5, "trail_arm_atr": 3.0, "trail_floor": 0.0, "trail_arm_floor": 25.0,
    "tp_atr": None, "tp_floor": 8.0, "gb_arm": None, "gb_frac": None,
    "order": ("HARD_STOP", "THESIS_CUT", "BREAKEVEN_LOCK", "ATR_TRAIL"),
}
_GS06_EXIT = {
    "family": GS_REGIME_EXIT_FAMILY, "max_duration_sec": 7200,
    "hard_stop_bps": 40.0, "hard_stop_margin_pct": 40.0,
    "profiles": {"ALL": dict(_GS06_PATIENT_PROFILE)},
    "regime_profiles": {"QUIET": "ALL", "TREND": "ALL", "VIOLENT": "ALL"},
    "take_profit_fill": "NONE - no take-profit (patient fade)",
    "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_FIRED_THE_RULE",
    "atr_source": "REGIME_BARS_3M_ATR14_BP_AT_SIGNAL_DEFAULT_4BP",
    "max_open_positions": 2,
    "exit_order": ("HARD_STOP", "THESIS_CUT", "BREAKEVEN_LOCK", "ATR_TRAIL", "TIME_BACKSTOP"),
}


def _gs0506_pre(rule_id: str, *, benchmark_lane: str, extra_kills: dict, decisions: tuple) -> dict:
    pre = _gs_pre_registration(
        rule_id, prereg_path=GS0506_SPEC_PATH, registered_utc=GS0506_REGISTERED_UTC, bonferroni_k=2,
        honest_label=("MID-EPOCH PAPER TILE BY OWNER ORDER - built from live freeze21b profit patterns "
                      "(in-sample), no out-of-sample evidence yet"),
        giveback_arm_bp=8.0,
        extra_kills={"benchmark_lane": benchmark_lane, "benchmark_min_edge_bp": 1.0, "latency_p50_above_sec": 5.0,
                     **extra_kills},
        decisions=("mid-epoch addition: window starts at the deploy of the registering revision",) + tuple(decisions),
    )
    pre["mid_epoch_addition"] = True
    pre["decision"]["anchor"] = GS0506_DECISION_ANCHOR
    pre["kill_summary"] += f"; must beat {benchmark_lane} on the same triggers by >= 1 bp mean"
    return pre


COMBO_LANE_SPECS.update({
    RESEARCH_LANE_FAMILY_GS06_COMMITTED_FADE_ATR_TP: _gs_tile(
        lane=RESEARCH_LANE_FAMILY_GS06_COMMITTED_FADE_ATR_TP,
        label=("GS-06 Committed fade, patient exit · H-A invert entry, Asia+EU, taker (VIOLENT its own cell), "
               "BE +25 -> +8, 2.5 ATR trail, 120-min"),
        raw_policy_id=("GS06_INVERT_COMMITTED_SCORE_LED_SIDE_ASIA_EU_SPREADLE3BP_TAKER_ALL_REGIMES_VIOLENT_CELL|"
                       "PATIENT_CUT12BP5M_HARD40BP_BE25_LOCK8_TRAIL2.5ATR_ARM3ATR_AFTER_BE_T120M_CAP2"),
        id_prefix="gs6", module="paper_policy_family_gs06_committed_fade_atr_tp.py",
        entry=_GS06_ENTRY, exit_policy=_GS06_EXIT, admission=COMMITTED_FADE_TAKER_ADMISSION_POLICY_ID,
        max_active_signals=2,
        hypothesis={"status": "GS20261005_MID_EPOCH_IN_SAMPLE_PATTERN", "hypothesis_id": "GS-20261005-06",
                    "in_sample": "freeze21b live profit pattern (PROFIT-PATTERN-FREEZE21B-20261005); not OOS"},
        pre=_gs0506_pre(
            "GS-20261005-06", benchmark_lane=RESEARCH_LANE_FAMILY_COMMITTED_FADE_TAKER_90,
            extra_kills={"control_lane_live": RESEARCH_LANE_FAMILY_RANDOM_CONTROL_TAKER_90,
                         "control_live_min_edge_bp": 2.0, "two_sided_min_fills_before_pass": 10,
                         "report_cells": ("QUIET_TREND", "VIOLENT"),
                         "violent_cell_harm_after_fills": 30, "violent_cell_harm_mean_bp_at_or_below": -2.0},
            decisions=("committed fade follows H-A and the frozen scorer (explicit side == score-led side)",
                       "VIOLENT is traded as its own pre-registered cell (same exits); every VIOLENT decision also "
                       "carries the old meta-rule stand-aside as a shadow tag; QUIET/TREND and VIOLENT are reported "
                       "separately and the VIOLENT cell can be gated only by an explicit owner decision",
                       "patient-fade exits (HM F1) per GS05-GS06-TILE-SPECS-CORRECTED-20261005",
                       "GS-01-style research-candidate rules (Bonferroni over the two mid-epoch tiles)")),
        subtitle="GS-06 — committed-AI fade, patient exit — PAPER ONLY — RELAY INELIGIBLE",
        signal_summary=("fade the shared AI's committed LONG/SHORT call (H-A definition, Asia+EU, spread <= 3 bp) "
                        "as a taker in every regime (VIOLENT reported as its own cell); patient break-even/trail exits"),
    ),
})

# ---------------------------------------------------------------------------
# PHASE03 mid-epoch additions (owner, 2026-10-07): three research tiles, all
# default OFF, paper-only, relay-ineligible. Specs recovered from the operator
# transcript (Grok Strategist) + diagnostics/DANISH-REGIME-TILE-DESIGN-20261007.md.
#   GS-07   = fast cross-venue premium fade (60 s premium mean, 20-min scalp).
#   Danish  = regime router: QUIET/TREND -> committed fade, VIOLENT -> premium reversion.
#   FadePool= pooled committed fade + premium reversion (OR-gate), composite exits.
# All three share the freeze21b data epoch (research_freeze.MID_EPOCH_ADDITIONS).
# ---------------------------------------------------------------------------
PHASE03_REGISTERED_UTC = "2026-10-07T11:00:00Z"
PHASE03_DECISION_ANCHOR = ("MID_EPOCH_ADDITION_START (first boot of the registering revision); judged with the "
                           "freeze21b epoch end, fewer than 21 days of data by construction")


def _phase3_pre(rule_id: str, *, spec_source: str, decisions: tuple[str, ...]) -> dict:
    pre = _gs_pre_registration(
        rule_id, prereg_path=spec_source, registered_utc=PHASE03_REGISTERED_UTC, bonferroni_k=3,
        honest_label=("MID-EPOCH PAPER TILE BY OWNER ORDER - design-doc / in-sample hypothesis, "
                      "no out-of-sample evidence yet"),
        giveback_arm_bp=8.0, extra_kills={"latency_p50_above_sec": 5.0},
        decisions=("mid-epoch addition: window starts at the deploy of the registering revision",) + tuple(decisions),
    )
    pre["mid_epoch_addition"] = True
    pre["decision"]["anchor"] = PHASE03_DECISION_ANCHOR
    return pre


# GS-07 = H-C's cross-venue premium rule on a fast (60 s) mean instead of the
# 60-minute mean, held for a 20-minute scalp, up to two open (paper only).
_GS07_ENTRY = {
    **_PREMIUM_REVERSION_ENTRY,
    "premium_mean_window_sec": 60, "premium_min_mean_samples": 15,
    "evaluator_id_prefix": "gs7xvp",
    # Fast tile: one signal per minute is plenty; the 2-open-position cap is the real bound.
    "min_submit_interval_sec": 60, "max_submissions_per_hour": 60,
}
_GS07_EXIT = _composite_exit(
    max_duration_sec=1200, max_open_positions=2,
    breakeven=LATE_BREAKEVEN_20_5, trail=LATE_ATR_TRAIL_1_5_ARM_2,
    volatility_scaling="ATR trail armed after +2 ATR; the ATR-scaled hard stop stays shadow-only",
)

# Danish regime router exits: QUIET/TREND route to the committed fade (90-min
# backstop), VIOLENT routes to premium reversion (60-min backstop). Both cells
# share the design-doc protections: 40 bp hard stop, late break-even +20 -> +5,
# ATR trail 1.5 armed at 1.5 ATR, conditional early cut -12 bp within 5 min.
_DNR_FADE_PROFILE = {
    "stack": "DANISH_ROUTER_FADE", "hard_bp": 40.0, "cut_bp": 12.0, "cut_win_sec": 300, "cut_close_sec": 1,
    # Conditional early cut (CONDITIONAL_EARLY_CUT_12_5M): only while MFE <= +2 bp.
    "cut_max_peak_bp": CONDITIONAL_EARLY_CUT_12_5M["max_peak_margin_pct"],
    "time_sec": 5400, "be_atr": 0.0, "be_floor": 20.0, "lock_bp": 5.0,
    "trail_atr": 1.5, "trail_arm_atr": 1.5, "trail_floor": 0.0, "trail_arm_floor": 0.0,
    "tp_atr": None, "tp_floor": 8.0, "gb_arm": None, "gb_frac": None,
    "order": ("HARD_STOP", "THESIS_CUT", "BREAKEVEN_LOCK", "ATR_TRAIL"),
}
_DNR_PREMIUM_PROFILE = {**_DNR_FADE_PROFILE, "stack": "DANISH_ROUTER_PREMIUM", "time_sec": 3600}
_DANISH_ROUTER_EXIT = {
    "family": GS_REGIME_EXIT_FAMILY, "max_duration_sec": 5400,
    "hard_stop_bps": 40.0, "hard_stop_margin_pct": 40.0,
    "profiles": {"FADE": dict(_DNR_FADE_PROFILE), "PREMIUM": dict(_DNR_PREMIUM_PROFILE)},
    "regime_profiles": {"QUIET": "FADE", "TREND": "FADE", "VIOLENT": "PREMIUM"},
    "take_profit_fill": "NONE - no take-profit (composite fade/premium protections only)",
    "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_CROSSED_THE_STOP",
    "atr_source": "REGIME_BARS_3M_ATR14_BP_AT_SIGNAL_DEFAULT_4BP",
    "max_open_positions": 3,
    "exit_order": ("HARD_STOP", "THESIS_CUT", "BREAKEVEN_LOCK", "ATR_TRAIL", "TIME_BACKSTOP"),
}

_DANISH_ROUTER_ENTRY = _gs_entry(
    mode="REGIME_ADAPTIVE", direction_source="INVERTED_SCORE_LED_SIDE", ai_role="FEATURE_ONLY",
    regime=GSB_REGIME, regime_exec={"QUIET": "TAKER", "TREND": "TAKER", "VIOLENT": "TAKER"},
    regime_trigger={"QUIET": "COMMITTED_FADE", "TREND": "COMMITTED_FADE", "VIOLENT": "CROSS_VENUE_PREMIUM"},
    premium_clock_trigger="CROSS_VENUE_PREMIUM", min_submit_interval_sec=5, max_submissions_per_hour=60,
    evaluator_id_prefix="dnrxvp",
    **_COMMITTED_FADE_KEYS,
    leader_venues=("binance", "bybit"),
    premium_mean_window_sec=3600, premium_min_mean_samples=1200,
    premium_long_threshold_bps=1.75, premium_short_threshold_bps=-1.88,
    max_fill_forward_sec=5, max_venue_age_sec=2.0, shadow_entry_delay_sec=1,
)
_DANISH_ROUTER_ENTRY.update({"max_spread_bps": 3.0, "max_bbo_age_sec": 5.0})

# Fade pool = committed fade (shared AI) OR premium reversion (evaluator), any
# regime; H-A's composite protections (40 bp stop, BE +20 -> +5, trail 1.5 armed
# at +2 ATR, -12 bp / 5 min cut, 90-min backstop), up to three open.
_FADE_POOL_PROFILE = {
    "stack": "FADE_POOL_COMPOSITE", "hard_bp": 40.0, "cut_bp": 12.0, "cut_win_sec": 300, "cut_close_sec": 1,
    # Conditional early cut (CONDITIONAL_EARLY_CUT_12_5M): only while MFE <= +2 bp.
    "cut_max_peak_bp": CONDITIONAL_EARLY_CUT_12_5M["max_peak_margin_pct"],
    "time_sec": 5400, "be_atr": 0.0, "be_floor": 20.0, "lock_bp": 5.0,
    "trail_atr": 1.5, "trail_arm_atr": 2.0, "trail_floor": 0.0, "trail_arm_floor": 0.0,
    "tp_atr": None, "tp_floor": 8.0, "gb_arm": None, "gb_frac": None,
    "order": ("HARD_STOP", "THESIS_CUT", "BREAKEVEN_LOCK", "ATR_TRAIL"),
}
_FADE_POOL_EXIT = {
    "family": GS_REGIME_EXIT_FAMILY, "max_duration_sec": 5400,
    "hard_stop_bps": 40.0, "hard_stop_margin_pct": 40.0,
    "profiles": {"ALL": dict(_FADE_POOL_PROFILE)},
    "regime_profiles": {"QUIET": "ALL", "TREND": "ALL", "VIOLENT": "ALL"},
    "take_profit_fill": "NONE - no take-profit (pooled composite fade/premium exits)",
    "stop_fill": "SIDE_CORRECT_BBO_TICK_THAT_CROSSED_THE_STOP",
    "atr_source": "REGIME_BARS_3M_ATR14_BP_AT_SIGNAL_DEFAULT_4BP",
    "max_open_positions": 3,
    "exit_order": ("HARD_STOP", "THESIS_CUT", "BREAKEVEN_LOCK", "ATR_TRAIL", "TIME_BACKSTOP"),
}
_FADE_POOL_ENTRY = _gs_entry(
    mode="REGIME_ADAPTIVE", direction_source="INVERTED_SCORE_LED_SIDE", ai_role="FEATURE_ONLY",
    regime=None, regime_exec={"QUIET": "TAKER", "TREND": "TAKER", "VIOLENT": "TAKER"},
    premium_clock_trigger="CROSS_VENUE_PREMIUM", min_submit_interval_sec=5, max_submissions_per_hour=60,
    evaluator_id_prefix="fdpxvp",
    **_COMMITTED_FADE_KEYS,
    leader_venues=("binance", "bybit"),
    premium_mean_window_sec=3600, premium_min_mean_samples=1200,
    premium_long_threshold_bps=1.75, premium_short_threshold_bps=-1.88,
    max_fill_forward_sec=5, max_venue_age_sec=2.0, shadow_entry_delay_sec=1,
)
_FADE_POOL_ENTRY.update({"max_spread_bps": 3.0, "max_bbo_age_sec": 5.0})

COMBO_LANE_SPECS.update({
    RESEARCH_LANE_FAMILY_GS07_FAST_PREMIUM_FADE: _tile(
        lane=RESEARCH_LANE_FAMILY_GS07_FAST_PREMIUM_FADE,
        label="GS-07 Fast premium fade · 60s cross-venue premium mean, taker, 20-min scalp, late BE + ATR trail, 40 bp stop",
        raw_policy_id=("GS07_XVENUE_PREMIUM_DEV60S_L1.75_S1.88BP_TAKER_CAP5BPS"
                       "|TIME_1200_BE20TO5_TRAIL1.5ATR_ARM2ATR_HARD40BP_CAP2"),
        id_prefix="gs7",
        module="paper_policy_family_gs07_fast_premium_fade.py",
        test_module="test_paper_policy_family_gs07_fast_premium_fade.py",
        entry=dict(_GS07_ENTRY), exit_policy=dict(_GS07_EXIT),
        hypothesis_result={"status": "GS20261007_MID_EPOCH_IN_SAMPLE_PATTERN", "hypothesis_id": "GS-20261007-07",
                           "in_sample": ("Grok Strategist: ~+0.8 bp/trade expected at 110-135 trades/day "
                                         "(fast premium fade); frequency test, not an OOS edge")},
        pre_registration=_phase3_pre(
            "GS-20261007-07",
            spec_source="operator transcript (Grok Strategist Tile 14 GS-07 fast premium fade)",
            decisions=("fast premium fade: fade premium off its 60-second mean (>+1.75 / <-1.88 bp), 20-min scalp",),
        ),
        admission_treatment=GS07_FAST_PREMIUM_FADE_ADMISSION_POLICY_ID,
        max_active_signals=2, entry_ttl_sec=3, default_enabled=False,
        signal_clock=CROSS_VENUE_SIGNAL_CLOCK, policy_epoch=FREEZE21_POLICY_EPOCH,
        subtitle="GS-07 — fast cross-venue premium fade — PAPER ONLY — RELAY INELIGIBLE",
        signal_summary=("the Binance/Bybit premium over Bitfinex leaves its own 60-second mean by >= +1.75 / "
                        "<= -1.88 bp; take Bitfinex toward convergence as a taker and hold 20 minutes"),
        live_exit_order=registry_live_exit_order(_GS07_EXIT),
        shadow_exits=ALL_SHADOW_EXITS,
        early_cut_shadow_reason="pure fast-signal test: every protection, including the early cut, is shadow-only",
    ),
    RESEARCH_LANE_FAMILY_DANISH_REGIME_ROUTER: _tile(
        lane=RESEARCH_LANE_FAMILY_DANISH_REGIME_ROUTER,
        label="Danish regime router · QUIET/TREND committed fade, VIOLENT premium reversion, taker, late BE + ATR trail",
        raw_policy_id=("DANISH_ROUTER_QUIET_TREND_COMMITTED_FADE_VIOLENT_PREMIUM_REVERSION_TAKER_CAP5BPS"
                       "|DYN_FADE90M_PREMIUM60M_BE20TO5_TRAIL1.5ATR_ARM1.5ATR_CUT12BP5M_HARD40BP_CAP3"),
        id_prefix="dnr",
        module="paper_policy_family_danish_regime_router.py",
        test_module="test_paper_policy_family_danish_regime_router.py",
        entry=dict(_DANISH_ROUTER_ENTRY), exit_policy=dict(_DANISH_ROUTER_EXIT),
        hypothesis_result={"status": "GS20261007_MID_EPOCH_DESIGN_HYPOTHESIS", "hypothesis_id": "GS-20261007-DNR",
                           "in_sample": ("regime-conditional edge (quiet/trend fade, violent premium) from "
                                         "DANISH-REGIME-TILE-DESIGN-20261007.md; not OOS")},
        pre_registration=_phase3_pre(
            "GS-20261007-DNR",
            spec_source="diagnostics/DANISH-REGIME-TILE-DESIGN-20261007.md",
            decisions=("QUIET/TREND route to the committed fade; VIOLENT routes to cross-venue premium reversion",
                       "uses the frozen 3 m regime classifier (ATR percentile + ADX), not the design doc's 60 s RV classifier"),
        ),
        admission_treatment=DANISH_REGIME_ROUTER_ADMISSION_POLICY_ID,
        max_active_signals=3, entry_ttl_sec=3, default_enabled=False, policy_epoch=FREEZE21_POLICY_EPOCH,
        subtitle="Danish regime router — PAPER ONLY — RELAY INELIGIBLE",
        signal_summary=("QUIET/TRENDING: fade the shared AI's committed call (H-A definition); "
                        "VIOLENT: fade the cross-venue premium (Binance/Bybit vs Bitfinex) off its 60-min mean"),
        live_exit_order=tuple(_DANISH_ROUTER_EXIT["exit_order"]),
        shadow_exits=ALL_SHADOW_EXITS,
    ),
    RESEARCH_LANE_FAMILY_FADE_POOL: _tile(
        lane=RESEARCH_LANE_FAMILY_FADE_POOL,
        label="Fade pool · committed fade OR premium reversion, taker, H-A composite protections, up to 3 open",
        raw_policy_id=("FADE_POOL_COMMITTED_FADE_OR_PREMIUM_REVERSION_TAKER_CAP5BPS"
                       "|DYN_ALL_BE20TO5_TRAIL1.5ATR_ARM2ATR_CUT12BP5M_HARD40BP_T90M_CAP3"),
        id_prefix="fdp",
        module="paper_policy_family_fade_pool.py",
        test_module="test_paper_policy_family_fade_pool.py",
        entry=dict(_FADE_POOL_ENTRY), exit_policy=dict(_FADE_POOL_EXIT),
        hypothesis_result={"status": "GS20261007_MID_EPOCH_POOL_HYPOTHESIS", "hypothesis_id": "GS-20261007-FDP",
                           "in_sample": ("Grok Strategist: pool dilutes H-A (all-winner pool +1.93 bp vs H-A +3.0); "
                                         "built per owner order, not an OOS edge")},
        pre_registration=_phase3_pre(
            "GS-20261007-FDP",
            spec_source="operator transcript (fade-pool proposal; Grok Strategist FADE-POOL-REANALYSIS-20261007)",
            decisions=("OR-gate pool of the committed fade and premium reversion signals (any regime)",
                       "H-A's composite protections and 90-min backstop"),
        ),
        admission_treatment=FADE_POOL_ADMISSION_POLICY_ID,
        max_active_signals=3, entry_ttl_sec=3, default_enabled=False, policy_epoch=FREEZE21_POLICY_EPOCH,
        subtitle="Fade pool — PAPER ONLY — RELAY INELIGIBLE",
        signal_summary=("pool the committed AI fade (H-A definition) and the cross-venue premium reversion "
                        "(H-C definition) signals; either trigger fires a taker toward the fade side"),
        live_exit_order=tuple(_FADE_POOL_EXIT["exit_order"]),
        shadow_exits=ALL_SHADOW_EXITS,
    ),
})

COMPARISON_BENCHMARK_LANE = None
PRIMARY_PRODUCTION_LANE = COMBO_EXECUTION_LANES[0]
BENCHMARK_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_PROFILE_ID = "CONTINUOUS_BENCHMARK_v1"
BENCHMARK_ROLE = "BENCHMARK"
PRIMARY_PRODUCTION_ROLE = "BENCHMARK"
RESEARCH_CANDIDATE_LANE = COMBO_EXECUTION_LANES[0]
RESEARCH_CANDIDATE_ROLE = "RESEARCH_CANDIDATE"

RESEARCH_STACK_FEATURES = (
    "21-day research freeze FREEZE21B-20261004 (owner-approved 2026-10-04 15:02 AEDT, re-declared 17:53/17:54 AEDT with every strategy as a visible paper tile): one declared data epoch (ce-20261004-v31-freeze21b), no tile adds, removals or resets until day 21 unless the documented freeze override is used (research_freeze.py). PHASE02 (owner, 2026-10-07, CODE_OVERRIDE) retired the five losers - H-B No-trade follow (taker), GS-05, GS-02, GS-04, GS-03 - leaving eight paper-only, relay-ineligible tiles, default ON: H-A Committed fade (taker), H-C Premium reversion (no AI), the random Control (baseline, no orders), GS-01 premium follow with an ATR maker take-profit and break-even (own evaluator of H-C's trigger), B1 (CVD divergence, regime-managed), B2 (regime switcher), B3 (committed fade, regime-managed) and GS-06 (committed fade, patient exit). Each surviving GS/B tile carries its own pre-registration (>= 30 fills and n_eff >= 30 distinct hours, harm/futility/give-back kills, Bonferroni within its family, day-21 decision); discovery-only evidence, CI spans 0. Earlier cohorts and the retired PHASE02 tiles remain quarantined"
)
EXECUTION_FIX_VERSION = RESEARCH_STACK_VERSION
ANALYZER_SYNC_ID = RESEARCH_STACK_VERSION
RESEARCH_DASHBOARD_VERSION = RESEARCH_STACK_VERSION
EXPECTED_EXCHANGE = "bitfinex"
EXPECTED_BOT_VERSION = EXECUTION_FIX_VERSION

ACTIVE_TILE_REGISTRY = {lane: dict(COMBO_LANE_SPECS[lane]) for lane in COMBO_EXECUTION_LANES}
ACTIVE_TILE_ORDER = COMBO_EXECUTION_LANES
# EXECUTION_FIX_VERSION / bot_version is the FREEZE21B cohort identity stamped on
# every evidence row; its "8t" is historical (eight tiles at PHASE02) and it may
# not change mid-freeze (test_research_freeze) without splitting the cohort.
# The live tile count is always derived from the registry instead:
ACTIVE_TILE_COUNT = len(ACTIVE_TILE_ORDER)
BOT_VERSION_LABEL = f"{EXECUTION_FIX_VERSION} ({ACTIVE_TILE_COUNT} active tiles)"


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
    # Retired 2026-10-04 (owner, SYSTEM-REVIEW-20261004, PR-A): 8 tiles
    # collapsed to 3 hypotheses + 1 control for the 21-day freeze. The Danish,
    # maker and no-early-stop fade variants fold into H-A; the maker NO_TRADE
    # follow never filled (H-B is its taker version); the cross-venue session
    # follow and the Continuous baseline give way to H-C and the random control.
    "FAMILY_DANISH_CF", "FAMILY_DANISH_CF_NOES", "FAMILY_DANISH_CF_ALL_SESSIONS",
    "FAMILY_CONTINUOUS_AUG_ORIGINAL", "FAMILY_COMMITTED_FADE_MAKER_90",
    "FAMILY_NOTRADE_FOLLOW_MAKER_60", "FAMILY_XVENUE_SESSION_FOLLOW_60M",
    # Retired 2026-10-07 (owner, PHASE02 atomic transaction): the five losers
    # in freeze21b paper evidence. H-B (NO_TRADE follow, worst at -$3.86) plus
    # GS-05 / GS-02 / GS-04 / GS-03. Generic execution and evidence primitives
    # (cross-venue evaluator, CVD-divergence evaluator, regime bars, dynlib)
    # remain; only tile-specific policy code was removed.
    "FAMILY_NOTRADE_FOLLOW_TAKER_60",
    "FAMILY_GS02_NOTRADE_REGIME_ENTRY", "FAMILY_GS03_CVD_DIV_TAKER",
    "FAMILY_GS04_NOTRADE_ATR_TP", "FAMILY_GS05_PREMIUM_REGIME_MANAGED",
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
    "DANISH_CONFIRMED_FADE_COMMITTED_ASIA_EU_SPREADLE3BP_MAKER_0.10_CONFIRM3BP_TAKER_CAP5BPS_TTL1800|TIME_5400_BE20TO5_CUT12BP5M_MFE2_HARD40BP_CAP3",
    "DANISH_CONFIRMED_FADE_COMMITTED_ASIA_EU_SPREADLE3BP_MAKER_0.10_CONFIRM3BP_TAKER_CAP5BPS_TTL1800|TIME_5400_BE20TO5_HARD40BP_CAP3",
    "DANISH_CONFIRMED_FADE_COMMITTED_ALL_SESSIONS_SPREADLE3BP_MAKER_0.10_CONFIRM3BP_TAKER_CAP5BPS_TTL1800|TIME_5400_BE20TO5_CUT12BP5M_MFE2_HARD40BP_CAP3",
    "AUG_V3_OWN_AI_GAP5_OFFSET_0.10_CHASE_s25_i60_10M|SCENARIO_C_THESIS12_SL30_EF32_PNL40_10_120M",
    "INVERT_COMMITTED_SCORE_LED_SIDE_MAKER_OFFSET_0.10_NOCHASE_TTL1800|TIME_5400_HARD40BP",
    "SCORE_LED_SIDE_ON_RAW_AI_NO_TRADE_MAKER_OFFSET_0.15_CHASE_w234_s25_i180_TTL3600|TIME_3600_BE20TO5_TRAIL1.5ATR_ARM2ATR_HARD40BP_CAP10",
    "XVENUE_LEAD8BP_OR_PREMIUM_L1.75_S1.88BP_SESSIONMAP_ASIA_EU_US_SPREADLE3BP_TAKER_CAP5BPS|TIME_3600_BE20TO5_HARD40BP_CAP3",
    # Retired 2026-10-07 (owner, PHASE02): the five freeze21b losers.
    "SCORE_LED_SIDE_ON_RAW_AI_NO_TRADE_SPREADLE3BP_TAKER_CAP5BPS|TIME_3600_BE20TO5_TRAIL1.5ATR_ARM2ATR_HARD40BP_CAP10",
    "GS02_SCORE_LED_SIDE_ON_RAW_AI_NO_TRADE_QUIET_TAKER_VIOLENT_LIMIT1ATR_W23_S25_I180_TTL1800|GS_BE1.5ATR_TRAIL2ATR_ARM2ATR_CUT8BP5M_HARD35BP_T60M_CAP1",
    "GS03_CVD_DIVERGENCE_20BAR_3M_TRANSITION_TAKER_CAP5BPS|GS_BE1.5ATR_TRAIL2ATR_ARM2ATR_CUT8BP5M_HARD35BP_T60M_CAP1",
    "GS04_SCORE_LED_SIDE_ON_RAW_AI_NO_TRADE_TAKER_CAP5BPS|GS_TP2.5ATR_BE2ATR_LOCK1_CUT8BP5M_HARD35BP_T60M_CAP1",
    "GS05_XVENUE_PREMIUM_DEV60M_L1.75_S1.88BP_QUIET_ASIDE_TREND_HC_15M_SPACING_VIOLENT_GS01_TAKER_CAP5BPS|TREND_HC_T60M_HARD40BP_VIOLENT_GS_TP2.5ATR_BE2ATR_LOCK1_CUT8BP5M_HARD35BP_T60M_CAP1",
})




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
            or spec.get("relay_capability") not in ("BLOCKED_UNQUALIFIED", PARTIAL_EXIT_RELAY_CAPABILITY)
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
        try:
            recorder_specs = tile_shadow_exit_set(lane)
        except (KeyError, TypeError, ValueError):
            defects.append(f"{lane}:INVALID_SHADOW_EXIT_SET")
        else:
            if any(item.get("kind") not in SHADOW_EXIT_KINDS for item in recorder_specs):
                defects.append(f"{lane}:UNSUPPORTED_SHADOW_EXIT_KIND")
            recorder_ids = [item["id"] for item in recorder_specs]
            if len(recorder_ids) != len(set(recorder_ids)) or any(
                    member not in recorder_ids for item in recorder_specs for member in item.get("members") or ()):
                defects.append(f"{lane}:SHADOW_EXIT_COMPOSITE_UNKNOWN_MEMBER")
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


def tile_pre_registration_summary(lane: str) -> dict:
    """Public pre-registration view of one active tile, read from this registry only.

    Deliberately not part of ``active_tile_lifecycle_manifest``: adding it there
    would change ``active_tile_registry_signature`` without a policy change.
    """
    spec = ACTIVE_TILE_REGISTRY.get(lane) or {}
    pre = spec.get("pre_registration") or {}
    hypothesis = spec.get("hypothesis_result") or {}
    return {
        "declared": bool(pre),
        "schema": pre.get("schema"),
        "hypothesis_id": pre.get("hypothesis_id") or hypothesis.get("hypothesis_id"),
        "status": hypothesis.get("status"),
        "registered_at": pre.get("registered_utc"),
        "registered_cohort": pre.get("registered_cohort"),
        "evidence_world": pre.get("evidence_world"),
        "ci_method": pre.get("ci_method"),
        "honest_label": pre.get("honest_label"),
        "promote": {"summary": spec.get("promotion_criteria"), "thresholds": dict(pre.get("promotion") or {})},
        "kill": {"summary": spec.get("kill_criteria"), "thresholds": dict(pre.get("kill") or {})},
    }


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


def frozen_roster_registry_signature(lanes) -> str:
    """``active_tile_registry_signature`` restricted to ``lanes`` (same payload shape and tile numbers).

    With tiles appended after a frozen roster this reproduces the roster's
    original signature byte-for-byte only if every frozen tile (and its tile
    number) is unchanged - the mid-epoch-addition proof used by research_freeze.
    """
    wanted = set(lanes)
    payload = {
        "schema": TILE_REGISTRY_SCHEMA,
        "architecture_version": TILE_ARCHITECTURE_VERSION,
        "tiles": tuple(tile for tile in active_tile_lifecycle_manifest() if tile["lane"] in wanted),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
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


def is_evaluator_clock_lane(lane: str) -> bool:
    """Tiles triggered only by an evaluator clock (cross-venue or 3 m bar close), never by an AI call."""
    spec = ACTIVE_TILE_REGISTRY.get(str(lane or "").upper()) or {}
    return spec.get("signal_clock") in EVALUATOR_SIGNAL_CLOCKS


def evaluator_loop_lanes() -> tuple[str, ...]:
    """Lanes the 1 Hz evaluator loop drives: evaluator-clock tiles plus shared-AI tiles whose
    entry also declares a ``bar_clock_trigger`` (GS-B2: CVD events in QUIET/VIOLENT) or a
    ``premium_clock_trigger`` (PHASE03 Danish router / fade pool: cross-venue premium events)."""
    return tuple(
        lane for lane in ACTIVE_TILE_ORDER
        if is_evaluator_clock_lane(lane)
        or ((ACTIVE_TILE_REGISTRY[lane].get("entry_policy") or {}).get("bar_clock_trigger"))
        or ((ACTIVE_TILE_REGISTRY[lane].get("entry_policy") or {}).get("premium_clock_trigger"))
    )


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
