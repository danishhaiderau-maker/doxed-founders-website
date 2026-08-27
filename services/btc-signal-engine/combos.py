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

RESEARCH_LANE_AI_SCAN = "AI_SCAN"
RESEARCH_LANE_FAMILY_CHANDELIER = "FAMILY_CHANDELIER_3"
RESEARCH_LANE_FAMILY_ATR_TARGET = "FAMILY_ATR_TARGET_2_5"
RESEARCH_LANE_FAMILY_ATR_TRAIL = "FAMILY_ATR_TRAIL"
RESEARCH_LANE_FAMILY_HYBRID_RUNNER = "FAMILY_HYBRID_RUNNER"
RESEARCH_LANE_FAMILY_MFE_GIVEBACK = "FAMILY_MFE_GIVEBACK"
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
    RESEARCH_LANE_FAMILY_CHANDELIER,
    RESEARCH_LANE_FAMILY_ATR_TARGET,
    RESEARCH_LANE_FAMILY_ATR_TRAIL,
    RESEARCH_LANE_FAMILY_HYBRID_RUNNER,
    RESEARCH_LANE_FAMILY_MFE_GIVEBACK,
)
COMBO_TILE_DISPLAY_ORDER = COMBO_EXECUTION_LANES


def _signature(raw_policy_id: str) -> str:
    return hashlib.sha256(raw_policy_id.encode("utf-8")).hexdigest()


def _tile(*, lane: str, label: str, raw_policy_id: str, id_prefix: str,
          module: str, test_module: str, entry: dict, exit_policy: dict,
          relay_capability: str = "BLOCKED_UNQUALIFIED") -> dict:
    return {
        "tile_id": lane,
        "label": label,
        "subtitle": "PAPER ONLY — shared AI direction, independent signed lifecycle",
        "combo_key": raw_policy_id,
        "raw_policy_id": raw_policy_id,
        "policy_signature": _signature(raw_policy_id),
        "policy_epoch": "v31-five-family-atomic-v1",
        "research_lane": lane,
        "execution_scope": "PAPER_ONLY",
        "paper_eligible": True,
        "live_copy_eligible": False,
        "relay_capability": relay_capability,
        "requested_margin_usd": 0.25,
        "risk_limits": {"account_risk_pct": 0.5, "hard_stop_margin_pct": 30.0},
        "analyzer_cohort": raw_policy_id,
        "presentation": {"family": exit_policy["family"], "evidence": "CONSERVATIVE_BBO_DEPTH_REQUIRED"},
        "retirement_status": "ACTIVE_RESEARCH",
        "component_surfaces": TILE_COMPONENT_SURFACES,
        "entry_policy": entry,
        "exit_policy": exit_policy,
        "ai_min": 0, "ai_max": 101, "spread_min": -99, "spread_max": 99,
        "entry_mode": "IMMEDIATE", "is_benchmark": False,
        "is_research_candidate": True, "is_legacy": False,
        "is_independent_ai": False, "uses_shared_ai_direction": True,
        "paper_only": True, "platform_relay_eligible": False,
        "default_enabled": False, "id_prefix": id_prefix,
        "toggle_key": "research_lane_enabled", "lifecycle_state": "PAPER_ONLY",
        "implementation_modules": (module,), "dedicated_test_modules": (test_module,),
        "entry_offset_pct": entry["offset_pct"],
        "initial_rest_sec": min(entry["chase_windows"]) * 300,
        "chase_windows": tuple(entry["chase_windows"]),
        "chase_age_sec": (min(entry["chase_windows"]) * 300, (max(entry["chase_windows"]) + 1) * 300),
        "chase_interval_sec": entry["reprice_sec"],
        "chase_remaining_gap_step_pct": entry["remaining_gap_step_pct"],
        "entry_ttl_sec": 1800, "margin_usd": 0.25,
        "account_risk_pct": 0.5, "path_end_sec": 7200,
        "exit_profile_id": raw_policy_id.split("|", 1)[1],
        "promotion_criteria": "Conservative chronological OOS, bounded drawdown, cross-world parity and every live gate GREEN",
        "kill_criteria": "Stop new entries on identity, fill, lifecycle, protection, mirror, analyzer or dashboard contradiction",
        "research_question": f"Does {raw_policy_id} retain positive conservative OOS EV with bounded drawdown?",
    }


COMBO_LANE_SPECS = {
    RESEARCH_LANE_FAMILY_CHANDELIER: _tile(
        lane=RESEARCH_LANE_FAMILY_CHANDELIER, label="Chandelier Family · 3 ATR",
        raw_policy_id="OFFSET_0.03_CHASE_w234_s25_i180|CHANDELIER_3", id_prefix="fc3",
        module="paper_policy_family_chandelier.py", test_module="test_paper_policy_family_chandelier.py",
        entry={"offset_pct": 0.03, "chase_windows": (2, 3, 4), "remaining_gap_step_pct": 25.0, "reprice_sec": 180},
        exit_policy={"family": "CHANDELIER", "initial_stop_atr_k": 2.0, "chandelier_atr_k": 3.0, "trail_activation_atr_k": 1.0, "hard_stop_margin_pct": 30.0, "max_duration_sec": 7200},
    ),
    RESEARCH_LANE_FAMILY_ATR_TARGET: _tile(
        lane=RESEARCH_LANE_FAMILY_ATR_TARGET, label="Fixed ATR Target · 2.5 / 1.5",
        raw_policy_id="OFFSET_0.02_CHASE_w234_s25_i180|ATR_TP_2.5_ATR_SL_1.5", id_prefix="fat",
        module="paper_policy_family_atr_target.py", test_module="test_paper_policy_family_atr_target.py",
        entry={"offset_pct": 0.02, "chase_windows": (2, 3, 4), "remaining_gap_step_pct": 25.0, "reprice_sec": 180},
        exit_policy={"family": "ATR_TARGET", "atr_tp_k": 2.5, "initial_stop_atr_k": 1.5, "hard_stop_margin_pct": 30.0, "max_duration_sec": 7200},
    ),
    RESEARCH_LANE_FAMILY_ATR_TRAIL: _tile(
        lane=RESEARCH_LANE_FAMILY_ATR_TRAIL, label="ATR Trail · arm 1.25 / trail 1",
        raw_policy_id="OFFSET_0.04_CHASE_all_on_s50_i60|ATR_TRAIL_SL_2_ARM_1.25_TRAIL_1", id_prefix="ftr",
        module="paper_policy_family_atr_trail.py", test_module="test_paper_policy_family_atr_trail.py",
        entry={"offset_pct": 0.04, "chase_windows": (0, 1, 2, 3, 4, 5), "remaining_gap_step_pct": 50.0, "reprice_sec": 60},
        exit_policy={"family": "ATR_TRAIL", "initial_stop_atr_k": 2.0, "trail_activation_atr_k": 1.25, "trail_atr_k": 1.0, "hard_stop_margin_pct": 30.0, "max_duration_sec": 7200},
    ),
    RESEARCH_LANE_FAMILY_HYBRID_RUNNER: _tile(
        lane=RESEARCH_LANE_FAMILY_HYBRID_RUNNER, label="Hybrid Runner · secure 33%",
        raw_policy_id="OFFSET_0.03_CHASE_w234_s25_i180|HYBRID_secure_33_runner_TRAIL_1", id_prefix="fhy",
        module="paper_policy_family_hybrid_runner.py", test_module="test_paper_policy_family_hybrid_runner.py",
        entry={"offset_pct": 0.03, "chase_windows": (2, 3, 4), "remaining_gap_step_pct": 25.0, "reprice_sec": 180},
        exit_policy={"family": "HYBRID_RUNNER", "initial_stop_atr_k": 1.5, "partial_take_profits": ((1.0, 0.33),), "trail_activation_atr_k": 1.0, "trail_atr_k": 1.0, "hard_stop_margin_pct": 30.0, "max_duration_sec": 7200},
        relay_capability="BLOCKED_PARTIAL_REDUCTION_UNPROVEN",
    ),
    RESEARCH_LANE_FAMILY_MFE_GIVEBACK: _tile(
        lane=RESEARCH_LANE_FAMILY_MFE_GIVEBACK, label="MFE Giveback · retain 80%",
        raw_policy_id="OFFSET_0.03_CHASE_w234_s25_i180|ATR_TP_2.5_GIVEBACK_20PCT", id_prefix="fmg",
        module="paper_policy_family_mfe_giveback.py", test_module="test_paper_policy_family_mfe_giveback.py",
        entry={"offset_pct": 0.03, "chase_windows": (2, 3, 4), "remaining_gap_step_pct": 25.0, "reprice_sec": 180},
        exit_policy={"family": "MFE_GIVEBACK", "initial_stop_atr_k": None, "mfe_giveback_fraction": 0.20, "hard_stop_margin_pct": 30.0, "max_duration_sec": 7200},
        relay_capability="BLOCKED_INITIAL_STOP_SWEEP_REQUIRED",
    ),
}
COMPARISON_BENCHMARK_LANE = "CONTINUOUS"
CONTINUOUS_PROXY_LANES = ()
PRIMARY_PRODUCTION_LANE = RESEARCH_LANE_FAMILY_CHANDELIER
BENCHMARK_LANE = COMPARISON_BENCHMARK_LANE
BENCHMARK_PROFILE_ID = "CONTINUOUS_BENCHMARK_v1"
BENCHMARK_ROLE = "BENCHMARK"
PRIMARY_PRODUCTION_ROLE = "BENCHMARK"
RESEARCH_CANDIDATE_LANE = RESEARCH_LANE_FAMILY_CHANDELIER
RESEARCH_CANDIDATE_ROLE = "RESEARCH_CANDIDATE"

RESEARCH_STACK_VERSION = "v31-five-family-atomic-paper"
RESEARCH_STACK_FEATURES = (
    "Five exit-family tiles share one direction-only three-minute AI call while retaining "
    "independent paper decisions, locks, capacity, orders, positions, ledgers and policy identities; "
    "all five are default-OFF, paper-only and relay-ineligible; ideal touch is diagnostic only; "
    "conservative BBO/depth receipts control execution evidence"
)
EXECUTION_FIX_VERSION = RESEARCH_STACK_VERSION
ANALYZER_SYNC_ID = RESEARCH_STACK_VERSION
RESEARCH_DASHBOARD_VERSION = RESEARCH_STACK_VERSION
EXPECTED_EXCHANGE = "bitfinex"
EXPECTED_BOT_VERSION = EXECUTION_FIX_VERSION

ACTIVE_TILE_REGISTRY = {lane: dict(COMBO_LANE_SPECS[lane]) for lane in COMBO_EXECUTION_LANES}
ACTIVE_TILE_ORDER = COMBO_EXECUTION_LANES

# Retiring a tile means removing it from ACTIVE_TILE_REGISTRY and recording its
# lane token here for one release. The registry audit then fails while that
# token remains on any active execution/UI/analyzer surface. Historical data is
# quarantined separately and never keeps runtime code alive.
RETIRED_TILE_LANES = frozenset({
    "OFFSET_029_ATR_TP_25", "OFFSET_029_ATR_PROTECTED",
    "OFFSET_029_ATR_REGIME", "PROTECTED_W234_SCENARIO_C",
})


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
        "component_surfaces",
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
        if not spec.get("paper_only") or spec.get("execution_scope") != "PAPER_ONLY":
            defects.append(f"{lane}:NOT_STRICTLY_PAPER_ONLY")
        if spec.get("live_copy_eligible") or spec.get("platform_relay_eligible"):
            defects.append(f"{lane}:LIVE_COPY_MUST_FAIL_CLOSED")
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
    return tuple(defects)


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
    return any(merged.values())
