from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    ACTIVE_TILE_REGISTRY,
    COMBO_EXECUTION_LANES,
    COMBO_TILE_DISPLAY_ORDER,
    RETIRED_TILE_LANES,
    RETIRED_POLICY_IDENTITIES,
    TILE_COMPONENT_SURFACES,
    active_tile_lifecycle_manifest,
    active_tile_registry_signature,
    combo_toggle_defaults,
    validate_tile_registry,
    _policy_signature,
)
from pathway_lane_roster import DASHBOARD_PRIMARY_LANES


def test_tile_registry_is_valid_and_drives_every_active_roster():
    assert validate_tile_registry() == ()
    assert tuple(DASHBOARD_PRIMARY_LANES) == tuple(ACTIVE_TILE_ORDER)
    assert set(COMBO_EXECUTION_LANES) == set(COMBO_TILE_DISPLAY_ORDER)
    assert set(COMBO_EXECUTION_LANES).issubset(ACTIVE_TILE_REGISTRY)


def test_tile_registry_is_fail_closed_for_relay_and_retirement():
    assert not set(ACTIVE_TILE_REGISTRY).intersection(RETIRED_TILE_LANES)
    for lane, spec in ACTIVE_TILE_REGISTRY.items():
        assert spec["label"]
        assert spec["raw_policy_id"]
        assert spec["id_prefix"]
        assert spec["toggle_key"]
        assert not (spec.get("paper_only") and spec.get("platform_relay_eligible")), lane
    assert not {
        spec["raw_policy_id"] for spec in ACTIVE_TILE_REGISTRY.values()
    }.intersection(RETIRED_POLICY_IDENTITIES)


def test_partial_exit_tiles_can_never_be_relay_capable_while_reductions_are_unwired():
    import combo_pathway_config as registry

    partial = [lane for lane, spec in ACTIVE_TILE_REGISTRY.items() if registry.tile_has_partial_exits(spec)]
    assert partial == []
    lane = ACTIVE_TILE_ORDER[0]
    spec = ACTIVE_TILE_REGISTRY[lane]
    original = dict(spec)
    try:
        spec.update({
            "exit_policy": {**spec["exit_policy"], "partial_take_profits": ((1.0, 0.25),)},
            "platform_relay_eligible": True,
            "relay_capability": "QUALIFIED",
        })
        assert registry.tile_has_partial_exits(spec)
        defects = validate_tile_registry()
        assert f"{lane}:PARTIAL_EXIT_RELAY_REQUIRES_EXCHANGE_REDUCTIONS" in defects
        assert f"{lane}:PARTIAL_EXIT_RELAY_CAPABILITY_NOT_BLOCKED" in defects
    finally:
        spec.clear()
        spec.update(original)
    assert validate_tile_registry() == ()


RETIRED_ANALYZER_HYPOTHESIS_LANES = (
    "FAMILY_CHANDELIER_3",
    "FAMILY_ATR_TARGET_2_5",
    "FAMILY_ATR_TRAIL",
    "FAMILY_HYBRID_RUNNER",
    "FAMILY_MFE_GIVEBACK",
)


RETIRED_DYNAMIC_ADAPTIVE_LANES = (
    "FAMILY_ADAPTIVE_REGIME",
    "FAMILY_ADAPTIVE_REGIME_LADDER",
    "FAMILY_ADAPTIVE_REGIME_LADDER_BE",
)

# Trend Fade 60 identity frozen at registration; the v5 roster change must not
# alter it, so its cohort continues across the retirement.
TREND_FADE_60_SIGNATURE = "a0a04faefaba977b203ad0a84117612ca7d487b0ce22f9d55504f3554b927d65"
TREND_FADE_60_SCORE_LED_SIGNATURE = "1936f510d2ff7d0c3aaaa4a5c14431637a02362cb2980a24b2f474b91a4743e0"


def test_active_registry_is_trend_fade_committed_fade_lead_premium_then_baseline():
    import combo_pathway_config as registry

    assert ACTIVE_TILE_ORDER == (
        "FAMILY_TREND_FADE_60", "FAMILY_TREND_FADE_60_COMMITTED",
        "FAMILY_XVENUE_LEAD_60S", "FAMILY_XVENUE_PREMIUM_60S",
        "FAMILY_CONTINUOUS_AUG_ORIGINAL",
    )
    manifest = active_tile_lifecycle_manifest()
    assert [(row["lane"], row["display_order"]) for row in manifest] == [
        ("FAMILY_TREND_FADE_60", 1), ("FAMILY_TREND_FADE_60_COMMITTED", 2),
        ("FAMILY_XVENUE_LEAD_60S", 3), ("FAMILY_XVENUE_PREMIUM_60S", 4),
        ("FAMILY_CONTINUOUS_AUG_ORIGINAL", 5),
    ]
    fade = ACTIVE_TILE_REGISTRY["FAMILY_TREND_FADE_60"]
    committed = ACTIVE_TILE_REGISTRY["FAMILY_TREND_FADE_60_COMMITTED"]
    lead = ACTIVE_TILE_REGISTRY["FAMILY_XVENUE_LEAD_60S"]
    premium = ACTIVE_TILE_REGISTRY["FAMILY_XVENUE_PREMIUM_60S"]
    baseline = ACTIVE_TILE_REGISTRY["FAMILY_CONTINUOUS_AUG_ORIGINAL"]
    tiles = (fade, committed, lead, premium, baseline)
    assert len({t["policy_signature"] for t in tiles}) == 5
    assert [t["id_prefix"] for t in tiles] == ["ftf", "ftc", "xvl", "xvp", "caug"]
    expected = (TREND_FADE_60_SCORE_LED_SIGNATURE if registry.SCORE_LED_PAPER_RESEARCH_ENABLED
                else TREND_FADE_60_SIGNATURE)
    assert fade["policy_signature"] == expected
    assert fade["raw_policy_id"].endswith("INVERT_SCORE_LED_SIDE_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP")
    assert fade["toggle_key"] == "research_lane_enabled"
    assert fade["policy_epoch"] == "v31-dynamic-adaptive-ladder-paper-v4"
    assert lead["policy_epoch"] == registry.XVENUE_LEAD_POLICY_EPOCH == "v31-trend-fade-single-tile-v5"
    assert registry.RESEARCH_STACK_VERSION == "v31-continuous-aug-original-v7"
    assert committed["policy_epoch"] == premium["policy_epoch"] == "v31-committed-fade-premium-v6"
    assert committed["pre_registration"]["registered_cohort"] == "v31-committed-fade-premium-v6"
    assert premium["pre_registration"]["registered_cohort"] == "v31-committed-fade-premium-v6"
    assert baseline["policy_epoch"] == registry.RESEARCH_STACK_VERSION
    for lane, tile in zip(ACTIVE_TILE_ORDER, tiles):
        # Only the owner-requested baseline defaults ON; the deploy gate turns every tile ON.
        default_on = tile is baseline
        assert tile["default_enabled"] is default_on and combo_toggle_defaults()[lane] is default_on
        assert tile["paper_only"] is True and tile["platform_relay_eligible"] is False
        assert tile["live_copy_eligible"] is False
        if tile is baseline:
            # August capacity: the tile cap plus same-side duplicate suppression.
            assert tile["max_active_signals"] == 10
            assert tuple(map(tuple, tile["ladder"])) == registry.CONTINUOUS_AUG_LADDER
        else:
            assert tile["max_active_signals"] == 1
            assert tile.get("ladder") in (None, ())
    assert committed["exit_policy"] == fade["exit_policy"]
    assert committed["entry_policy"]["min_score_gap"] == 30.0
    assert committed["entry_policy"]["trades_raw_ai_no_trade"] is False
    assert committed["pre_registration"]["control_lane"] == "FAMILY_TREND_FADE_60"
    assert premium["entry_policy"]["direction_source"] == "CROSS_VENUE_PREMIUM"
    assert fade["entry_policy"]["direction_source"] == "INVERTED_SCORE_LED_SIDE"
    assert fade["entry_policy"]["mode"] == "TAKER_AT_SIGNAL"
    assert fade["exit_policy"]["max_duration_sec"] == 3600 and fade["exit_policy"]["hard_stop_bps"] == 40
    pre = fade["pre_registration"]
    assert pre["registered_cohort"] == "v31-dynamic-adaptive-ladder-paper-v4"
    assert pre["control_lane"] is None
    assert pre["promotion"] == {
        "meaning": "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY", "min_fills": 150,
        "per_fill_ev_lower_ci95_gt_bp": 0.0, "both_halves_positive": True,
        "max_2h_window_profit_share": 0.30,
    }
    assert pre["kill"] == {
        "k1_after_fills": 40, "k1_net_usd_at_or_below": -0.40,
        "k2_after_fills": 80, "k2_net_usd_at_or_below": 0.0,
        "k3_worst_trade_bp_below": -60.0, "k4_max_drawdown_usd": 1.0,
        "k5_max_days_without_promotion": 14,
    }
    assert registry.PRIMARY_PRODUCTION_LANE == registry.RESEARCH_CANDIDATE_LANE == "FAMILY_TREND_FADE_60"


def test_trend_fade_ladder_is_one_atomic_retirement():
    import json

    lane = "FAMILY_TREND_FADE_60_LADDER"
    assert lane in RETIRED_TILE_LANES
    assert lane not in ACTIVE_TILE_REGISTRY and lane not in COMBO_EXECUTION_LANES
    assert lane not in json.dumps(ACTIVE_TILE_REGISTRY, default=list)
    assert "INVERT_SCORE_LED_SIDE_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP_SCENARIO_C_CAP5" in RETIRED_POLICY_IDENTITIES
    service_dir = __import__("pathlib").Path(__file__).resolve().parent
    for gone in ("paper_policy_family_trend_fade_60_ladder.py", "test_paper_policy_family_trend_fade_60_ladder.py"):
        assert not (service_dir / gone).exists(), gone
        assert not (service_dir.parent / "btc-signal-engine" / gone).exists(), gone


def test_dynamic_adaptive_tiles_are_one_atomic_retirement():
    import json

    for lane in RETIRED_DYNAMIC_ADAPTIVE_LANES:
        assert lane in RETIRED_TILE_LANES
        assert lane not in ACTIVE_TILE_REGISTRY
        assert lane not in COMBO_EXECUTION_LANES
    assert not any(lane in json.dumps(ACTIVE_TILE_REGISTRY, default=list) for lane in RETIRED_DYNAMIC_ADAPTIVE_LANES)
    prefix = "ADAPTIVE_RV15_P40_P90_FZ1.5_T5BPS_M1TICK_G40|ATR_TRAIL_SL_1.5_ARM_0.75_TRAIL_1"
    for suffix in ("", "_SCENARIO_C_CAP1", "_SCENARIO_C_BE4_LOCK1_CAP1"):
        assert prefix + suffix in RETIRED_POLICY_IDENTITIES
    service_dir = __import__("pathlib").Path(__file__).resolve().parent
    for gone in ("paper_policy_family_adaptive_regime.py", "paper_policy_family_adaptive_regime_ladder.py",
                 "paper_policy_family_adaptive_regime_ladder_be.py", "adaptive_profit_lock_binding.py"):
        assert not (service_dir / gone).exists(), gone
        assert not (service_dir.parent / "btc-signal-engine" / gone).exists(), gone


def test_retired_family_tiles_and_continuous_are_one_atomic_retirement():
    for lane in (*RETIRED_ANALYZER_HYPOTHESIS_LANES, "CONTINUOUS"):
        assert lane in RETIRED_TILE_LANES
        assert lane not in ACTIVE_TILE_REGISTRY
        assert lane not in COMBO_EXECUTION_LANES
    for raw in (
        "OFFSET_0.27_CHASE_w234_s50_i180|ATR_TP_2.5_SCENARIO_C",
        "OFFSET_0.30_CHASE_w234_s50_i180|HYBRID_secure_25_25_runner_TRAIL_1",
        "OFFSET_0.30_CHASE_w234_s50_i180|ATR_TRAIL_SL_1.5_ARM_0.75_TRAIL_1",
        "OFFSET_0.30_CHASE_w234_s50_i180|CHANDELIER_1.5",
        "OFFSET_0.30_CHASE_w234_s50_i180|ATR_TP_2.5_GIVEBACK_20PCT",
    ):
        assert raw in RETIRED_POLICY_IDENTITIES


def test_default_on_is_refused_for_anything_but_a_paper_only_relay_blocked_tile():
    lane = "FAMILY_TREND_FADE_60_COMMITTED"
    spec = ACTIVE_TILE_REGISTRY[lane]
    original = dict(spec)
    try:
        spec["default_enabled"] = True
        assert validate_tile_registry() == ()
        spec["relay_capability"] = "QUALIFIED"
        assert f"{lane}:DEFAULT_ON_REQUIRES_PAPER_ONLY_RELAY_BLOCKED" in validate_tile_registry()
        spec.update(original)
        spec["default_enabled"] = True
        spec["platform_relay_eligible"] = True
        assert f"{lane}:DEFAULT_ON_REQUIRES_PAPER_ONLY_RELAY_BLOCKED" in validate_tile_registry()
    finally:
        spec.clear()
        spec.update(original)
    assert validate_tile_registry() == ()


def test_policy_signature_binds_execution_parameters_not_just_display_id():
    raw = "OFFSET_0.30_CHASE_w234_s50_i180|CHANDELIER_1.5"
    entry = {"offset_pct": 0.30, "chase_windows": (2, 3, 4), "remaining_gap_step_pct": 50.0, "reprice_sec": 180}
    exit_a = {"family": "CHANDELIER", "initial_stop_atr_k": 2.0, "chandelier_atr_k": 1.5}
    exit_b = {**exit_a, "initial_stop_atr_k": 1.5}
    assert _policy_signature(raw_policy_id=raw, entry=entry, exit_policy=exit_a) != _policy_signature(
        raw_policy_id=raw, entry=entry, exit_policy=exit_b,
    )
    assert _policy_signature(raw_policy_id=raw, entry=entry, exit_policy=exit_a, ladder=((8, 5),)) != _policy_signature(
        raw_policy_id=raw, entry=entry, exit_policy=exit_a, ladder=((12, 10),),
    )


def test_retirement_contract_covers_all_cross_layer_surfaces():
    assert TILE_COMPONENT_SURFACES == (
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
    assert len(TILE_COMPONENT_SURFACES) == 14
    for spec in ACTIVE_TILE_REGISTRY.values():
        assert tuple(spec["component_surfaces"]) == TILE_COMPONENT_SURFACES


def test_every_policy_module_is_owned_by_one_active_tile():
    service_dir = __import__("pathlib").Path(__file__).resolve().parent
    discovered = {path.name for path in service_dir.glob("paper_policy_*.py")}
    manifest = active_tile_lifecycle_manifest()
    declared = [
        module
        for tile in manifest
        for module in tile["implementation_modules"]
    ]
    assert len(declared) == len(set(declared)), "policy module has multiple tile owners"
    assert discovered == set(declared), (
        "orphan or unregistered policy module; register it or physically delete it: "
        f"discovered={sorted(discovered)} declared={sorted(declared)}"
    )
    for tile in manifest:
        for module in (*tile["implementation_modules"], *tile["dedicated_test_modules"]):
            assert (service_dir / module).is_file(), f"{tile['lane']} owns missing file {module}"


def test_lifecycle_manifest_is_ordered_complete_and_serializable():
    import json

    manifest = active_tile_lifecycle_manifest()
    assert tuple(tile["lane"] for tile in manifest) == tuple(ACTIVE_TILE_ORDER)
    assert tuple(tile["display_order"] for tile in manifest) == tuple(range(1, len(manifest) + 1))
    assert all(tuple(tile["component_surfaces"]) == TILE_COMPONENT_SURFACES for tile in manifest)
    json.dumps(manifest)
    signature = active_tile_registry_signature()
    assert len(signature) == 64
    assert signature == active_tile_registry_signature()


def test_runtime_and_sync_surfaces_publish_registry_receipt():
    service_dir = __import__("pathlib").Path(__file__).resolve().parent
    source = (service_dir / "bot.py").read_text(encoding="utf-8")
    assert source.count('"tile_registry_signature"') >= 3
    assert source.count('"active_tiles"') >= 3
    assert "active_tile_registry_signature()" in source


def test_analyzer_dashboard_does_not_override_the_registry_roster():
    service_dir = __import__("pathlib").Path(__file__).resolve().parent
    source = (service_dir / "research" / "research_dashboard.py").read_text(encoding="utf-8")
    assert 'ANALYZER_COMPARE_LANES = (\n    "CONTINUOUS"' not in source
    assert "tile_lanes=tuple(DASHBOARD_PRIMARY_LANES)" in source
    assert "{% for lane in tile_lanes %}" in source
    assert "CURRENT TWO-LANE EVIDENCE" not in source
    assert "CURRENT CANONICAL TILE EVIDENCE" in source
