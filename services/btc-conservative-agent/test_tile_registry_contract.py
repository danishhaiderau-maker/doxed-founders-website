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


def test_active_registry_is_the_exact_analyzer_hypothesis_experiment():
    expected = {
        "FAMILY_ATR_TARGET_2_5": "OFFSET_0.27_CHASE_w234_s50_i180|ATR_TP_2.5_SCENARIO_C",
        "FAMILY_HYBRID_RUNNER": "OFFSET_0.30_CHASE_w234_s50_i180|HYBRID_secure_25_25_runner_TRAIL_1",
        "FAMILY_ATR_TRAIL": "OFFSET_0.30_CHASE_w234_s50_i180|ATR_TRAIL_SL_1.5_ARM_0.75_TRAIL_1",
        "FAMILY_CHANDELIER_3": "OFFSET_0.30_CHASE_w234_s50_i180|CHANDELIER_1.5",
        "FAMILY_MFE_GIVEBACK": "OFFSET_0.30_CHASE_w234_s50_i180|ATR_TP_2.5_GIVEBACK_20PCT",
    }
    assert {lane: spec["raw_policy_id"] for lane, spec in ACTIVE_TILE_REGISTRY.items()} == expected
    for spec in ACTIVE_TILE_REGISTRY.values():
        assert spec["policy_epoch"] == "v31-analyzer-hypothesis-paper-v1"
        assert spec["entry_policy"]["chase_windows"] == (2, 3, 4)
        assert spec["entry_policy"]["remaining_gap_step_pct"] == 50.0
        assert spec["entry_policy"]["reprice_sec"] == 180
    fixed = ACTIVE_TILE_REGISTRY["FAMILY_ATR_TARGET_2_5"]
    assert fixed["ladder"] == ((8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120))
    assert fixed["exit_policy"]["thesis_cut_margin_pct"] == -12.0
    assert fixed["exit_policy"]["thesis_window_sec"] == 300
    chandelier = ACTIVE_TILE_REGISTRY["FAMILY_CHANDELIER_3"]
    assert chandelier["exit_policy"]["initial_stop_atr_k"] == 2.0
    assert chandelier["exit_policy"]["trail_activation_atr_k"] == 1.0
    hybrid = ACTIVE_TILE_REGISTRY["FAMILY_HYBRID_RUNNER"]
    assert hybrid["exit_policy"]["partial_take_profits"] == ((1.0, 0.25), (1.5, 0.25))
    manifest = {row["lane"]: row for row in active_tile_lifecycle_manifest()}
    assert manifest["FAMILY_ATR_TARGET_2_5"]["ladder"] == fixed["ladder"]
    for row in manifest.values():
        result = row["presentation"]["hypothesis_result"]
        assert result["status"] == "PROFITABLE_IN_ANALYZER_HYPOTHESIS_MODEL"
        assert result["oos_net_usd"] > 0


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
