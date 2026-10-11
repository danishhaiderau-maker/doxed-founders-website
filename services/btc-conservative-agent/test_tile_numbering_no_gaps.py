"""Item 9 (Danish 11 Oct 2026): dashboard tiles are numbered 1..N with no gaps."""
import bot
from combo_pathway_config import ACTIVE_TILE_ORDER, tile_number


def test_dashboard_tile_numbers_are_contiguous():
    specs = bot.build_static_pathway_lane_specs()
    lanes = specs["lanes"] if isinstance(specs, dict) and "lanes" in specs else specs
    nums = [row["tile_number"] for row in lanes]
    assert nums == list(range(1, len(ACTIVE_TILE_ORDER) + 1))
    for row in lanes:
        assert row["tile_number"] == tile_number(row["lane"]) == row["registry_position"]
