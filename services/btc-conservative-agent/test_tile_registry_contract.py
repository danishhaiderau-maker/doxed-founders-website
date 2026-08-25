from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    ACTIVE_TILE_REGISTRY,
    COMBO_EXECUTION_LANES,
    COMBO_TILE_DISPLAY_ORDER,
    RETIRED_TILE_LANES,
    TILE_COMPONENT_SURFACES,
    active_tile_lifecycle_manifest,
    active_tile_registry_signature,
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
    assert "CURRENT FOUR-TILE EVIDENCE" in source
