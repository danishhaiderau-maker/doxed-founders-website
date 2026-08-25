import ast
from pathlib import Path

import combo_pathway_config as config


BOT_PATH = Path(__file__).with_name("bot.py")


def _function_node(name: str) -> ast.FunctionDef:
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    return next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_v3_lane_policy_material_uses_executable_relay_allowlist():
    function = _function_node("_v3_lane_policy_material")
    assignments = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "relay_eligible" for target in node.targets)
    ]
    assert len(assignments) == 1
    value = assignments[0].value
    assert isinstance(value, ast.Compare)
    assert isinstance(value.ops[0], ast.In)
    assert isinstance(value.comparators[0], ast.Name)
    assert value.comparators[0].id == "PLATFORM_RELAY_ELIGIBLE_LANES"


def test_relay_registry_is_derived_and_protected_tiles_fail_closed():
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    assignments = {
        target.id: node.value
        for node in module.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"PLATFORM_RELAY_ELIGIBLE_LANES", "PLATFORM_RELAY_CONFIGURED_LANES"}
    }
    eligible = ast.unparse(assignments["PLATFORM_RELAY_ELIGIBLE_LANES"])
    configured = ast.unparse(assignments["PLATFORM_RELAY_CONFIGURED_LANES"])
    assert "RESEARCH_LANE_CONTINUOUS" in eligible
    assert "COMBO_LANE_SPECS.items()" in eligible
    assert "COMBO_EXECUTION_LANES" in configured
    assert config.COMBO_LANE_SPECS[config.RESEARCH_LANE_OFFSET_029_ATR_TP_25]["platform_relay_eligible"] is True
    assert config.COMBO_LANE_SPECS[config.RESEARCH_LANE_OFFSET_029_ATR_PROTECTED]["platform_relay_eligible"] is False
    assert config.COMBO_LANE_SPECS[config.RESEARCH_LANE_OFFSET_029_ATR_REGIME]["platform_relay_eligible"] is False
