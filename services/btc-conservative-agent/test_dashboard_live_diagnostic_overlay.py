"""Regression guard for stale diagnostics in the active dashboard overlay."""

from __future__ import annotations

import ast
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _function(name: str) -> ast.FunctionDef:
    return next(
        node
        for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_relay_snapshot_carries_live_dashboard_diagnostics() -> None:
    body = ast.get_source_segment(
        BOT_SOURCE, _function("_build_relay_execution_state_snapshot")
    )
    assert body is not None
    for expected in (
        '"heartbeat": state.get("heartbeat")',
        '"last_heartbeat": state.get("last_heartbeat")',
        'state.get("pipeline_funnel_counters")',
        'state.get("lane_opportunity_counters")',
        '"debug_state": copy.deepcopy(debug_state)',
    ):
        assert expected in body


def test_active_overlay_refreshes_and_reprojects_diagnostics() -> None:
    body = ast.get_source_segment(
        BOT_SOURCE, _function("_api_state_cache_refresher_loop")
    )
    assert body is not None
    for key in (
        '"heartbeat"',
        '"last_heartbeat"',
        '"pipeline_funnel_counters"',
        '"lane_opportunity_counters"',
    ):
        assert key in body
    assert "snap.update(build_dashboard_display(snap))" in body
    assert 'snap["strategy_progress"] = _strategy_progress_health_snapshot()' in body
