"""Edge is descriptive evidence, never a runtime trigger, AI gate or order gate."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import bot
import combo_pathway_config as config


BOT_PATH = Path(__file__).with_name("bot.py")


def test_ai_invocation_is_edge_invariant() -> None:
    source = inspect.getsource(bot.should_invoke_ai)
    assert "evaluate_pre_ai_gate" not in source
    assert "edge_range_allows" not in source
    assert "EDGE_" not in source

    # Exercise the ordinary non-forced path without cooldown interference.
    original = bot.ai_cooldown_remaining_sec
    original_force = bot.state.get("force_ai_every_signal")
    try:
        bot.ai_cooldown_remaining_sec = lambda *args, **kwargs: 0
        bot.state["force_ai_every_signal"] = False
        low = bot.should_invoke_ai({}, -999.0, True)
        high = bot.should_invoke_ai({}, 999.0, True)
    finally:
        bot.ai_cooldown_remaining_sec = original
        bot.state["force_ai_every_signal"] = original_force
    assert low == high


def test_process_signal_has_no_edge_trigger_or_gate_branch() -> None:
    source = inspect.getsource(bot.process_signal)
    forbidden = (
        "should_trigger_edge_event(",
        "edge_range_allows(",
        "evaluate_pre_ai_gate(",
        "EDGE_FAIL",
        "EDGE_SUSTAINED_NO_REARM",
    )
    for token in forbidden:
        assert token not in source


def test_edge_controls_and_mutation_routes_are_removed() -> None:
    source = BOT_PATH.read_text(encoding="utf-8")
    for token in (
        "@app.route('/api/set_edge_threshold'",
        "@app.route('/api/set_edge_range'",
        'id="edgeRangePreset"',
        'id="edgeThreshold"',
        'id="edgeThresholdMax"',
        "updateEdgeRangePreset(",
        "updateEdgeRangeCustom(",
        "window.updateEdge = updateEdge",
    ):
        assert token not in source


def test_current_three_tile_roster_survives_edge_retirement() -> None:
    payload = bot.build_static_pathway_lane_specs()
    lanes = {row["lane"] for row in payload["lanes"]}
    assert lanes == {
        config.COMPARISON_BENCHMARK_LANE,
        config.RESEARCH_LANE_OFFSET_029_ATR_TP_25,
        config.RESEARCH_LANE_PROTECTED_W234,
    }
    assert payload["benchmark_lane"] == config.COMPARISON_BENCHMARK_LANE


def test_only_one_ai_evaluator_call_site_remains() -> None:
    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "evaluate_signal_with_ai"
    ]
    assert len(calls) == 1
