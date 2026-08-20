"""Regression coverage for the 2026-08-21 Type B active-lane retirement.

Historical specs and labels intentionally remain available so immutable data can
still be decoded.  The lane must not be displayed, toggled, classified as
executable, or spawned from the shared AI path.
"""

from pathlib import Path

import combo_pathway_config as config
import pathway_lane_roster as roster


def test_type_b_is_historical_but_not_active() -> None:
    lane = config.RESEARCH_LANE_TYPE_B_HUNTER_V1

    assert lane in config.COMBO_LANE_SPECS
    assert config.COMBO_LANE_SPECS[lane]["is_legacy"] is True
    assert config.COMBO_LANE_SPECS[lane]["is_research_candidate"] is False
    assert lane not in config.COMBO_EXECUTION_LANES
    assert lane not in config.COMBO_TILE_DISPLAY_ORDER
    assert config.is_combo_execution_lane(lane) is False
    assert lane in roster.RETIRED_PATHWAY_LANES
    assert lane not in roster.DASHBOARD_PRIMARY_LANES


def test_shared_ai_path_cannot_spawn_type_b() -> None:
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    process_signal = source[source.index("def process_signal(") :]

    assert "spawn_type_b_lane_from_shared_ai(" not in process_signal
    assert 'RESEARCH_LANE_TYPE_B_HUNTER_V1: "RETIRED"' in source


def test_rendered_active_pathway_has_no_type_b_tile_or_control() -> None:
    import bot

    payload = bot.build_static_pathway_lane_specs()
    active_lanes = [row.get("lane") for row in payload.get("lanes") or []]
    assert config.RESEARCH_LANE_TYPE_B_HUNTER_V1 not in active_lanes

    active_text = " ".join(
        str(value)
        for row in payload.get("lanes") or []
        for value in row.values()
    )
    assert "Type B Hunter" not in active_text
