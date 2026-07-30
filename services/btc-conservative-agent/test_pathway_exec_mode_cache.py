"""The tile banner cache must follow runtime toggle/execution-mode changes."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


bot.save_persistent_config = lambda: None


def _set_type_b(enabled: bool) -> None:
    with bot.state_lock:
        lanes = dict(bot.state.get("research_lane_enabled") or {})
        lanes[bot.RESEARCH_LANE_TYPE_B_HUNTER_V1] = enabled
        bot.state["research_lane_enabled"] = lanes


def _type_b_spec() -> dict:
    payload = bot.get_pathway_lane_specs_cached(for_api=True)
    return next(
        row
        for row in payload.get("lanes") or []
        if row.get("lane") == bot.RESEARCH_LANE_TYPE_B_HUNTER_V1
    )


def main() -> None:
    bot._cached_pathway_lane_specs = {}
    _set_type_b(True)
    on_spec = _type_b_spec()
    assert on_spec["exec_mode"] == bot.EXEC_MODE_PAPER
    assert "PAPER ORDERS ENABLED" in on_spec["exec_banner"]

    # This second read previously returned the cached PAPER banner even though
    # the runtime toggle had moved the lane to LAB_SHADOW.
    _set_type_b(False)
    off_spec = _type_b_spec()
    assert off_spec["exec_mode"] == bot.EXEC_MODE_LAB_SHADOW
    assert off_spec["exec_banner"] == "LAB SHADOW — no new orders"

    print("Pathway execution-mode cache test passed")


if __name__ == "__main__":
    main()
