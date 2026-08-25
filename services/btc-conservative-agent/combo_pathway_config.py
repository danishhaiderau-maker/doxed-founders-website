"""Canonical tile registry for the active paper-research architecture.

Adding or retiring a tile starts here. Runtime, API, dashboards, analyzer and
monitoring consume this registry (or the roster derived from it); they must not
maintain an independent list of active tiles. Policy-specific implementation
code may still live in its own module, but its lifecycle metadata and ownership
surfaces are declared here so retirement can be audited instead of merely
hiding a card.
"""
from __future__ import annotations

RESEARCH_LANE_AI_SCAN = "AI_SCAN"
RESEARCH_LANE_OFFSET_029_ATR_TP_25 = "OFFSET_029_ATR_TP_25"
RESEARCH_LANE_PROTECTED_W234 = "PROTECTED_W234_SCENARIO_C"
TILE_REGISTRY_SCHEMA = "research_tile_registry_v1"
TILE_ARCHITECTURE_VERSION = 1
TILE_COMPONENT_SURFACES = (
    "runtime",
    "authenticated_api",
    "production_dashboard",
    "collector",
    "mirror_manifest",
    "analyzer_loader",
    "analyzer_reports",
    "analyzer_api",
    "analyzer_dashboard",
    "monitoring",
    "regression_tests",
)
TILE_LIFECYCLE_STATES = frozenset({"ACTIVE", "PAPER_ONLY", "BENCHMARK"})

# Active paper-research lanes (CONTINUOUS is configured separately as the benchmark).
COMBO_EXECUTION_LANES = (
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
    RESEARCH_LANE_PROTECTED_W234,
)

COMBO_TILE_DISPLAY_ORDER = (
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
    RESEARCH_LANE_PROTECTED_W234,
)

COMBO_LANE_SPECS = {
    RESEARCH_LANE_OFFSET_029_ATR_TP_25: {
        "label": "0.29% Patient Chase · ATR 2.5×",
        "subtitle": (
            "PAPER when ON — independent order, position, capacity and ledger; "
            "relay-copy eligible only while the separate operator relay is armed"
        ),
        "combo_key": "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
        "raw_policy_id": "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
        "ai_min": 0,
        "ai_max": 101,
        "spread_min": -99,
        "spread_max": 99,
        "entry_mode": "IMMEDIATE",
        "is_benchmark": False,
        "is_research_candidate": True,
        "is_legacy": False,
        "is_independent_ai": False,
        "uses_shared_ai_direction": True,
        "paper_only": False,
        "platform_relay_eligible": True,
        "default_enabled": True,
        "id_prefix": "o29atr",
        "lifecycle_state": "ACTIVE",
        "implementation_modules": ("paper_policy_offset029.py",),
        "dedicated_test_modules": ("test_paper_policy_offset029.py",),
        "entry_offset_pct": 0.29,
        "initial_rest_sec": 600,
        "chase_windows": (2, 3, 4),
        "chase_age_sec": (600, 1500),
        "chase_interval_sec": 60,
        "chase_remaining_gap_step_pct": 25.0,
        "entry_ttl_sec": 1800,
        "margin_usd": 0.25,
        "atr_tp_multiple": 2.5,
        "atr_source": "frozen fill-time 3m ATR(14)",
        "path_end_sec": 7200,
        "exit_profile_id": "ATR_TP_2.5X_PATH_END_120M_V1",
        "promotion_criteria": (
            "PAPER RESEARCH: independent OOS evidence across multiple regimes, "
            "conservative execution parity and explicit operator authorization"
        ),
        "kill_criteria": (
            "Stop new entries on integrity, lifecycle, or evidence mismatch"
        ),
        "hypothesis": (
            "A patient 0.29% maker anchor followed by 25% remaining-gap reprices may "
            "retain entry quality while a frozen 3m ATR 2.5x target captures movement."
        ),
        "research_question": (
            "Does OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5 retain positive "
            "out-of-sample EV under conservative paper execution?"
        ),
    },
    RESEARCH_LANE_PROTECTED_W234: {
        "label": "Protected W234 Chase · Scenario C + ATR stop",
        "subtitle": (
            "PAPER RESEARCH when ON — shared AI direction, independent order/position/ledger; "
            "not live-copy eligible until explicitly qualified"
        ),
        "combo_key": "OFFSET_0.28_CHASE_w234_s10_i180|ATR_TP_2.5_SCENARIO_C_ATR_SL_2.5",
        "raw_policy_id": "OFFSET_0.28_CHASE_w234_s10_i180|ATR_TP_2.5_SCENARIO_C_ATR_SL_2.5",
        "ai_min": 0, "ai_max": 101, "spread_min": -99, "spread_max": 99,
        "entry_mode": "IMMEDIATE",
        "is_benchmark": False, "is_research_candidate": True,
        "is_legacy": False, "is_independent_ai": False,
        "uses_shared_ai_direction": True,
        "paper_only": True, "platform_relay_eligible": False,
        "default_enabled": False,
        "id_prefix": "pwch",
        "lifecycle_state": "PAPER_ONLY",
        "implementation_modules": ("paper_policy_protected_w234.py",),
        "dedicated_test_modules": ("test_protected_w234_policy.py",),
        "entry_offset_pct": 0.28,
        "initial_rest_sec": 600,
        "chase_windows": (2, 3, 4),
        "chase_age_sec": (600, 1500),
        "chase_interval_sec": 180,
        "chase_remaining_gap_step_pct": 10.0,
        "entry_ttl_sec": 1800,
        "margin_usd": 0.25,
        "atr_tp_multiple": 2.5,
        "atr_stop_multiple": 2.5,
        "atr_source": "frozen fill-time 3m ATR(14)",
        "path_end_sec": 7200,
        "ladder": ((8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120)),
        "ladder_label": "Scenario C",
        "ladder_profile_id": "SCENARIO_C_RUNNER_8_v8_20260820",
        "exit_profile_id": "ATR_TP_2.5_SCENARIO_C_ATR_SL_2.5_V1",
        "promotion_criteria": "PAPER ONLY until independent multi-regime OOS evidence has positive LCB and bounded drawdown",
        "kill_criteria": "Stop new entries on any identity, lifecycle, stop, or evidence mismatch",
        "hypothesis": "The descriptive W234 chased cell retains fillability while Scenario C and a frozen 2.5 ATR stop bound losses.",
        "research_question": "Does the protected W234 chase remain positive under conservative paper execution across regimes?",
    },
}
COMPARISON_BENCHMARK_LANE = "CONTINUOUS"
CONTINUOUS_PROXY_LANES = ()
PRIMARY_PRODUCTION_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_PROFILE_ID = "CONTINUOUS_BENCHMARK_v1"
BENCHMARK_ROLE = "BENCHMARK"
PRIMARY_PRODUCTION_ROLE = "BENCHMARK"
RESEARCH_CANDIDATE_LANE = RESEARCH_LANE_OFFSET_029_ATR_TP_25
RESEARCH_CANDIDATE_ROLE = "RESEARCH_CANDIDATE"

RESEARCH_STACK_VERSION = "v31-three-tile-protected-w234"
RESEARCH_STACK_FEATURES = (
    "CONTINUOUS benchmark + OFFSET_029_ATR_TP_25 + PROTECTED_W234_SCENARIO_C share one "
    "direction-only 3-minute AI call; three-tile paper-research roster; all retired lanes "
    "are analyzer-only; registered Patient Chase paper lifecycles; "
    "independent lane capacity, orders, positions and ledgers; "
    "fail-closed relay executor watchdog"
)
EXECUTION_FIX_VERSION = RESEARCH_STACK_VERSION
ANALYZER_SYNC_ID = RESEARCH_STACK_VERSION
RESEARCH_DASHBOARD_VERSION = RESEARCH_STACK_VERSION
EXPECTED_EXCHANGE = "bitfinex"
EXPECTED_BOT_VERSION = EXECUTION_FIX_VERSION

# The benchmark is a first-class tile even though its execution policy is
# intentionally implemented outside COMBO_LANE_SPECS. This merged registry is
# the sole UI/analyzer roster contract.
BENCHMARK_TILE_SPEC = {
    "label": "Continuous Benchmark — Paper Orders",
    "subtitle": "Shared-AI benchmark with an independent paper lifecycle",
    "combo_key": "CONTINUOUS",
    "raw_policy_id": "CONTINUOUS",
    "is_benchmark": True,
    "is_research_candidate": False,
    "uses_shared_ai_direction": True,
    "paper_only": False,
    "platform_relay_eligible": True,
    "id_prefix": "cont",
    "toggle_key": "continuous_ai_research_enabled",
    "lifecycle_state": "BENCHMARK",
    "implementation_modules": (),
    "dedicated_test_modules": (),
}

ACTIVE_TILE_REGISTRY = {
    RESEARCH_LANE_OFFSET_029_ATR_TP_25: {
        **COMBO_LANE_SPECS[RESEARCH_LANE_OFFSET_029_ATR_TP_25],
        "toggle_key": "research_lane_enabled",
    },
    COMPARISON_BENCHMARK_LANE: dict(BENCHMARK_TILE_SPEC),
    RESEARCH_LANE_PROTECTED_W234: {
        **COMBO_LANE_SPECS[RESEARCH_LANE_PROTECTED_W234],
        "toggle_key": "research_lane_enabled",
    },
}
ACTIVE_TILE_ORDER = (
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
    COMPARISON_BENCHMARK_LANE,
    RESEARCH_LANE_PROTECTED_W234,
)

# Retiring a tile means removing it from ACTIVE_TILE_REGISTRY and recording its
# lane token here for one release. The registry audit then fails while that
# token remains on any active execution/UI/analyzer surface. Historical data is
# quarantined separately and never keeps runtime code alive.
RETIRED_TILE_LANES = frozenset()


def validate_tile_registry() -> tuple[str, ...]:
    """Return registry defects; an empty tuple is the only deployable state."""
    defects = []
    lanes = tuple(ACTIVE_TILE_REGISTRY)
    if tuple(ACTIVE_TILE_ORDER) != tuple(dict.fromkeys(ACTIVE_TILE_ORDER)):
        defects.append("DUPLICATE_TILE_IN_DISPLAY_ORDER")
    if set(ACTIVE_TILE_ORDER) != set(lanes):
        defects.append("DISPLAY_ORDER_REGISTRY_MISMATCH")
    required = {
        "label", "raw_policy_id", "id_prefix", "toggle_key",
        "lifecycle_state", "implementation_modules", "dedicated_test_modules",
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
        if spec.get("paper_only") and spec.get("platform_relay_eligible"):
            defects.append(f"{lane}:PAPER_ONLY_RELAY_CONTRADICTION")
        state = str(spec.get("lifecycle_state") or "")
        if state not in TILE_LIFECYCLE_STATES:
            defects.append(f"{lane}:INVALID_LIFECYCLE_STATE:{state}")
        if state == "PAPER_ONLY" and not spec.get("paper_only"):
            defects.append(f"{lane}:PAPER_ONLY_STATE_WITHOUT_GATE")
        if state == "BENCHMARK" and not spec.get("is_benchmark"):
            defects.append(f"{lane}:BENCHMARK_STATE_WITHOUT_ROLE")
    overlap = set(lanes).intersection(RETIRED_TILE_LANES)
    if overlap:
        defects.append("ACTIVE_RETIRED_OVERLAP:" + ",".join(sorted(overlap)))
    return tuple(defects)


def active_tile_lifecycle_manifest() -> tuple[dict, ...]:
    """Stable cross-layer roster used by audits, APIs, dashboards and analyzers."""
    return tuple(
        {
            "lane": lane,
            "display_order": index,
            "label": ACTIVE_TILE_REGISTRY[lane]["label"],
            "raw_policy_id": ACTIVE_TILE_REGISTRY[lane]["raw_policy_id"],
            "id_prefix": ACTIVE_TILE_REGISTRY[lane]["id_prefix"],
            "toggle_key": ACTIVE_TILE_REGISTRY[lane]["toggle_key"],
            "lifecycle_state": ACTIVE_TILE_REGISTRY[lane]["lifecycle_state"],
            "paper_only": bool(ACTIVE_TILE_REGISTRY[lane].get("paper_only", False)),
            "relay_eligible": bool(ACTIVE_TILE_REGISTRY[lane].get("platform_relay_eligible", False)),
            "implementation_modules": tuple(ACTIVE_TILE_REGISTRY[lane]["implementation_modules"]),
            "dedicated_test_modules": tuple(ACTIVE_TILE_REGISTRY[lane]["dedicated_test_modules"]),
        }
        for index, lane in enumerate(ACTIVE_TILE_ORDER, start=1)
    )

COMBO_CHASE_DELAY_LANES = ()
COMBO_CHASE_ISOLATION_PAIRS = ()
ACTIVE_CHASE_ISOLATION_PAIRS = ()
ACTIVE_CHASE_ISOLATION_LANES = (COMPARISON_BENCHMARK_LANE,)
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
    if any(merged.values()):
        return True
    # experimental_pathway_config purged 2026-07-11 — no experimental lanes remain
    return bool(continuous_enabled)
