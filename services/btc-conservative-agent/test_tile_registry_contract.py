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

# Identities frozen at registration; card metadata (ENTRY / EXIT / RISK text)
# sits outside the signature, so surviving cohorts continue across roster changes.
# H-A keeps the v11 H11 committed-fade taker identity unchanged.
COMMITTED_FADE_TAKER_SIGNATURE_PREFIX = "874a620a51c7"
RETIRED_TREND_FADE_LANES = ("FAMILY_TREND_FADE_60", "FAMILY_TREND_FADE_60_COMMITTED")
RETIRED_XVENUE_LANES = ("FAMILY_XVENUE_LEAD_60S", "FAMILY_XVENUE_PREMIUM_60S")
RETIRED_FREEZE21_LANES = (
    "FAMILY_DANISH_CF", "FAMILY_DANISH_CF_NOES", "FAMILY_DANISH_CF_ALL_SESSIONS",
    "FAMILY_CONTINUOUS_AUG_ORIGINAL", "FAMILY_COMMITTED_FADE_MAKER_90",
    "FAMILY_NOTRADE_FOLLOW_MAKER_60", "FAMILY_XVENUE_SESSION_FOLLOW_60M",
)
EXPECTED_ORDER = (
    "FAMILY_COMMITTED_FADE_TAKER_90", "FAMILY_NOTRADE_FOLLOW_TAKER_60",
    "FAMILY_PREMIUM_REVERSION_60M", "FAMILY_RANDOM_CONTROL_TAKER_90",
)


def test_active_registry_is_three_hypotheses_then_the_control():
    import combo_pathway_config as registry

    assert ACTIVE_TILE_ORDER == EXPECTED_ORDER
    manifest = active_tile_lifecycle_manifest()
    assert [(row["lane"], row["display_order"], row["tile_number"]) for row in manifest] == [
        (lane, n, n) for n, lane in enumerate(EXPECTED_ORDER, start=1)
    ]
    assert [registry.tile_number(lane) for lane in EXPECTED_ORDER] == list(range(1, 5))
    tiles = [ACTIVE_TILE_REGISTRY[lane] for lane in EXPECTED_ORDER]
    fade, notrade, premium, control = tiles
    assert [t["id_prefix"] for t in tiles] == ["cft", "ntt", "pmr", "rnd"]
    assert fade["policy_signature"].startswith(COMMITTED_FADE_TAKER_SIGNATURE_PREFIX)
    assert len({t["policy_signature"] for t in tiles}) == 4
    assert registry.RESEARCH_STACK_VERSION == "v31-freeze21-3h1c-v12"
    assert registry.BENCHMARK_LANE is None
    for lane, tile in zip(ACTIVE_TILE_ORDER, tiles):
        assert tile["policy_epoch"] == tile["pre_registration"]["registered_cohort"] == registry.RESEARCH_STACK_VERSION
        # Every frozen tile defaults ON for the 21-day freeze (paper only, relay blocked).
        assert tile["default_enabled"] is True and combo_toggle_defaults()[lane] is True
        assert tile["paper_only"] is True and tile["platform_relay_eligible"] is False
        assert tile["live_copy_eligible"] is False
        assert tile.get("ladder") in (None, ())
        assert tile["entry_policy"]["mode"] == "TAKER_AT_SIGNAL"
        assert tile["max_active_signals"] == (10 if tile is notrade else 3)
    assert [t["pre_registration"]["role"] for t in tiles] == ["HYPOTHESIS"] * 3 + ["CONTROL"]
    assert fade["entry_policy"]["direction_source"] == "INVERTED_SCORE_LED_SIDE"
    assert notrade["entry_policy"]["direction_source"] == "SCORE_LED_SIDE"
    assert premium["entry_policy"]["direction_source"] == "CROSS_VENUE_PREMIUM"
    assert control["entry_policy"]["direction_source"] == "RANDOM_COIN_ON_COMMITTED_CALL"
    assert control["exit_policy"] == fade["exit_policy"]
    assert registry.PRIMARY_PRODUCTION_LANE == registry.RESEARCH_CANDIDATE_LANE == EXPECTED_ORDER[0]


def test_freeze21_retirement_is_one_atomic_retirement():
    import json

    for lane in RETIRED_FREEZE21_LANES:
        assert lane in RETIRED_TILE_LANES
        assert lane not in ACTIVE_TILE_REGISTRY and lane not in COMBO_EXECUTION_LANES
        assert lane not in json.dumps(ACTIVE_TILE_REGISTRY, default=list)
    for raw in (
        "AUG_V3_OWN_AI_GAP5_OFFSET_0.10_CHASE_s25_i60_10M|SCENARIO_C_THESIS12_SL30_EF32_PNL40_10_120M",
        "INVERT_COMMITTED_SCORE_LED_SIDE_MAKER_OFFSET_0.10_NOCHASE_TTL1800|TIME_5400_HARD40BP",
        "XVENUE_LEAD8BP_OR_PREMIUM_L1.75_S1.88BP_SESSIONMAP_ASIA_EU_US_SPREADLE3BP_TAKER_CAP5BPS|TIME_3600_BE20TO5_HARD40BP_CAP3",
    ):
        assert raw in RETIRED_POLICY_IDENTITIES
    service_dir = __import__("pathlib").Path(__file__).resolve().parent
    for name in ("danish_cf", "danish_cf_noes", "danish_cf_all_sessions", "continuous_aug_original",
                 "committed_fade_maker_90", "notrade_follow_maker_60", "xvenue_session_follow_60m"):
        for gone in (f"paper_policy_family_{name}.py", f"test_paper_policy_family_{name}.py"):
            assert not (service_dir / gone).exists(), gone
            assert not (service_dir.parent / "btc-signal-engine" / gone).exists(), gone


def test_cross_venue_lead_and_premium_are_one_atomic_retirement():
    import json

    import taker_time_exit_binding
    import tile_paired_comparison

    for lane in RETIRED_XVENUE_LANES:
        assert lane in RETIRED_TILE_LANES
        assert lane not in ACTIVE_TILE_REGISTRY and lane not in COMBO_EXECUTION_LANES
        assert lane not in json.dumps(ACTIVE_TILE_REGISTRY, default=list)
    assert taker_time_exit_binding.CROSS_VENUE_SOURCES == {"CROSS_VENUE_LEAD_OR_PREMIUM", "CROSS_VENUE_PREMIUM"}
    for schema in tile_paired_comparison.VERDICT_RULES:
        assert "xvenue_lead" not in schema and "xvenue_premium" not in schema
    service_dir = __import__("pathlib").Path(__file__).resolve().parent
    for gone in ("paper_policy_family_xvenue_lead.py", "paper_policy_family_xvenue_premium.py",
                 "test_paper_policy_family_xvenue_lead.py", "test_paper_policy_family_xvenue_premium.py"):
        assert not (service_dir / gone).exists(), gone
        assert not (service_dir.parent / "btc-signal-engine" / gone).exists(), gone
    # Generic evaluator primitives stay: the premium tile and research/lead_lag_report.py use them.
    for kept in ("cross_venue_lead.py", "cross_venue_premium.py", "cross_venue_session_follow.py", "cross_venue_tape.py"):
        assert (service_dir / kept).is_file(), kept


def test_every_tile_publishes_entry_exit_risk_card_sections():
    import combo_pathway_config as registry

    for lane in ACTIVE_TILE_ORDER:
        sections = registry.tile_card_sections(lane)
        assert sections["entry"] and sections["exit"]["live"] and sections["risk"], lane
        risk = " ".join(sections["risk"])
        assert "margin @100x" in risk and "notional" in risk, lane
        assert "max loss" not in " ".join(
            [*sections["entry"], *sections["exit"]["live"], *sections["exit"]["shadow"], risk]
        ).lower(), lane


def test_validator_fails_when_a_tile_lacks_card_metadata():
    lane = "FAMILY_NOTRADE_FOLLOW_TAKER_60"
    spec = ACTIVE_TILE_REGISTRY[lane]
    original = dict(spec)
    try:
        for key in ("signal_summary", "live_exit_order"):
            spec.clear()
            spec.update(original)
            spec[key] = "" if key == "signal_summary" else ()
            defects = validate_tile_registry()
            assert any(d.startswith(f"{lane}:") for d in defects), (key, defects)
        spec.clear()
        spec.update(original)
        spec["shadow_exits"] = ("NOT_A_SHADOW_EXIT",)
        assert any(d.startswith(f"{lane}:") for d in validate_tile_registry())
    finally:
        spec.clear()
        spec.update(original)
    assert validate_tile_registry() == ()


def test_composite_exit_order_matches_runtime_first_trigger_order():
    import combo_pathway_config as registry

    for lane in ACTIVE_TILE_ORDER:
        spec = ACTIVE_TILE_REGISTRY[lane]
        exit_policy = spec["exit_policy"]
        assert exit_policy["family"] == "COMPOSITE_FIRST_TRIGGER_WINS"
        order = tuple(registry.registry_live_exit_order(exit_policy))
        assert tuple(exit_policy["exit_order"]) == order == tuple(spec["live_exit_order"])
    premium = ACTIVE_TILE_REGISTRY["FAMILY_PREMIUM_REVERSION_60M"]
    assert "EARLY_CUT" not in premium["live_exit_order"]
    assert premium["early_cut_shadow_reason"]


def test_trend_fade_and_committed_fade_are_one_atomic_retirement():
    import json

    import combo_pathway_config as registry
    import taker_time_exit_binding
    import tile_paired_comparison

    for lane in RETIRED_TREND_FADE_LANES:
        assert lane in RETIRED_TILE_LANES
        assert lane not in ACTIVE_TILE_REGISTRY and lane not in COMBO_EXECUTION_LANES
        assert lane not in json.dumps(ACTIVE_TILE_REGISTRY, default=list)
    for raw in (
        "INVERT_SCORE_LED_SIDE_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP",
        "INVERT_COMMITTED_SCORE_LED_SIDE_GAP30_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP",
    ):
        assert raw in RETIRED_POLICY_IDENTITIES
    assert not hasattr(registry, "COMMITTED_FADE_MIN_SCORE_GAP")
    # The generic inverted-side and commit-rule primitives stay only while an
    # active tile (H-A, the committed fade) is registered on them.
    inverted_users = [lane for lane in ACTIVE_TILE_ORDER
                      if ACTIVE_TILE_REGISTRY[lane]["entry_policy"].get("direction_source") == "INVERTED_SCORE_LED_SIDE"]
    assert inverted_users == ["FAMILY_COMMITTED_FADE_TAKER_90"]
    assert "INVERTED_SCORE_LED_SIDE" in taker_time_exit_binding.DIRECTION_SOURCES
    assert callable(taker_time_exit_binding.committed_call_refusal)
    for schema in ("tile_pre_registration_trade_count_v1", "tile_pre_registration_committed_fade_v1"):
        assert schema not in tile_paired_comparison.VERDICT_RULES
        assert schema not in tile_paired_comparison.EXTRA_STATS
    service_dir = __import__("pathlib").Path(__file__).resolve().parent
    for gone in ("paper_policy_family_trend_fade_60.py", "paper_policy_family_trend_fade_60_committed.py",
                 "test_paper_policy_family_trend_fade_60.py", "test_paper_policy_family_trend_fade_60_committed.py"):
        assert not (service_dir / gone).exists(), gone
        assert not (service_dir.parent / "btc-signal-engine" / gone).exists(), gone


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
    lane = "FAMILY_NOTRADE_FOLLOW_TAKER_60"
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
