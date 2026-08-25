from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    ACTIVE_TILE_REGISTRY,
    COMBO_EXECUTION_LANES,
    COMBO_TILE_DISPLAY_ORDER,
    RETIRED_TILE_LANES,
    TILE_COMPONENT_SURFACES,
    validate_tile_registry,
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


def test_retirement_contract_covers_all_cross_layer_surfaces():
    assert set(TILE_COMPONENT_SURFACES) == {
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
    }
