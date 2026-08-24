"""Fail-closed contract for the production paper-research execution graph.

Historical lane implementations and their immutable evidence may remain readable,
but only the frozen four-tile stack may produce new paper orders. All four
must consume the single shared AI_SCAN result; an alternate prompt/call path is a
release-blocking regression.
"""

from __future__ import annotations

import ast
from pathlib import Path

import combo_pathway_config as config
import experimental_pathway_config as experimental
import legacy_pathway_config as legacy
import pathway_lane_roster as roster


SERVICE_DIR = Path(__file__).resolve().parent
BOT_PATH = SERVICE_DIR / "bot.py"
ACTIVE_PAPER_LANES = {
    "CONTINUOUS",
    "OFFSET_029_ATR_TP_25",
    "OFFSET_029_ATR_PROTECTED",
    "OFFSET_029_ATR_REGIME",
}


def _bot_tree() -> ast.Module:
    return ast.parse(BOT_PATH.read_text(encoding="utf-8"), filename=str(BOT_PATH))


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return None


def test_exactly_four_active_order_producing_lanes() -> None:
    configured = set(config.COMBO_EXECUTION_LANES)
    assert configured == {
        config.RESEARCH_LANE_OFFSET_029_ATR_TP_25,
        config.RESEARCH_LANE_OFFSET_029_ATR_PROTECTED,
        config.RESEARCH_LANE_OFFSET_029_ATR_REGIME,
    }

    active = {config.COMPARISON_BENCHMARK_LANE, *configured}
    assert active == ACTIVE_PAPER_LANES
    assert set(roster.DASHBOARD_PRIMARY_LANES) == ACTIVE_PAPER_LANES

    for lane in configured:
        spec = config.COMBO_LANE_SPECS[lane]
        assert spec.get("is_legacy") is False
        assert spec.get("is_independent_ai") is False
        assert spec.get("uses_shared_ai_direction") is True


def test_historical_lanes_are_not_executable_or_primary() -> None:
    retired = set(roster.RETIRED_PATHWAY_LANES)
    assert retired
    assert retired.isdisjoint(config.COMBO_EXECUTION_LANES)
    assert retired.isdisjoint(roster.DASHBOARD_PRIMARY_LANES)

    for lane in retired:
        assert config.is_combo_execution_lane(lane) is False

    assert experimental.EXPERIMENTAL_EXECUTION_LANES == ()
    assert experimental.EXPERIMENTAL_TILE_DISPLAY_ORDER == ()
    assert roster.PATHWAY_SHADOW_COLLECTING_ENABLED is False
    assert set(legacy.SHADOW_COLLECTING_LANES).isdisjoint(ACTIVE_PAPER_LANES)


def test_only_process_signal_can_invoke_the_shared_ai_evaluator() -> None:
    tree = _bot_tree()
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    call_sites: list[tuple[int, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        if called == "evaluate_signal_with_ai":
            call_sites.append((node.lineno, _enclosing_function(node, parents)))

    assert len(call_sites) == 1, f"alternate AI evaluator calls found: {call_sites}"
    assert call_sites[0][1] == "process_signal"


def test_no_active_lane_declares_an_independent_prompt() -> None:
    for lane in config.COMBO_EXECUTION_LANES:
        assert config.is_independent_ai_lane(lane) is False
        spec = config.COMBO_LANE_SPECS[lane]
        assert not spec.get("prompt_id")
        assert not spec.get("prompt_template")


def test_paper_lane_toggles_cannot_disable_shared_research_observation() -> None:
    source = BOT_PATH.read_text(encoding="utf-8")
    tree = _bot_tree()
    wanted = {
        "shared_research_ai_observation_enabled",
        "is_research_lane_enabled",
        "lane_pipeline_allowed",
        "should_invoke_ai",
        "detect_event_light",
        "state_monitor_loop",
        "_spawn_combo_lane",
    }
    functions = {
        node.name: ast.get_source_segment(source, node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    }

    helper = functions["shared_research_ai_observation_enabled"]
    assert "_sole_ai_research_mode()" in helper
    assert "AI_RESEARCH_MODE_ENABLED" in helper
    assert "continuous_ai_research_enabled" not in helper
    assert "research_lane_enabled_map" not in helper

    for name in ("is_research_lane_enabled", "lane_pipeline_allowed", "should_invoke_ai", "detect_event_light"):
        fn = functions[name]
        assert "shared_research_ai_observation_enabled()" in fn
        assert "ALL_COMBO_OFF" not in fn

    monitor = functions["state_monitor_loop"]
    assert "and shared_research_ai_observation_enabled()" in monitor
    assert "any_combo_execution_enabled(research_lane_enabled_map(), continuous_ai_research_enabled())" not in monitor

    spawn = functions["_spawn_combo_lane"]
    assert "LANE_TOGGLE_OFF_LAB" in spawn
    assert "_spawn_lab_combo_shadow" in spawn
