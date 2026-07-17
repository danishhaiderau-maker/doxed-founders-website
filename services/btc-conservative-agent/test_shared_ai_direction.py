"""Contract tests for one AI call feeding two independent strategy tiles."""
import os

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot
from combo_pathway_config import (
    COMBO_LANE_SPECS,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    is_independent_ai_lane,
    is_shared_ai_direction_lane,
)


def run():
    passed = 0

    def check(name, condition):
        nonlocal passed
        if not condition:
            raise AssertionError(name)
        passed += 1

    spec = COMBO_LANE_SPECS[RESEARCH_LANE_TYPE_B_HUNTER_V1]
    check("Type B is not independent AI", not is_independent_ai_lane(RESEARCH_LANE_TYPE_B_HUNTER_V1))
    check("Type B consumes shared direction", is_shared_ai_direction_lane(RESEARCH_LANE_TYPE_B_HUNTER_V1))
    check("Type B cadence offset is zero", spec.get("ai_cadence_offset_sec") == 0)
    check("shared prompt does not request win probability", "Win probability:" not in bot.AI_PROMPT_TEMPLATE)
    check("shared prompt does not request confidence JSON", '"confidence"' not in bot.AI_PROMPT_TEMPLATE)

    low_conf = bot.derive_research_decision_tier(1, 70, 50, "LONG")
    high_conf = bot.derive_research_decision_tier(99, 70, 50, "LONG")
    check("confidence cannot change tier", low_conf == high_conf == "STRONG_APPROVE")
    check("direction gap below five rejects", bot.derive_research_decision_tier(99, 52, 50, "LONG") == "REJECT")
    parsed = bot.parse_ai_response_fields(
        '{"direction":"LONG","long_score":72,"short_score":55,'
        '"bull_score":7,"bear_score":5,"reasons_for_trade":["trend"],'
        '"reasons_against_trade":["resistance"]}'
    )
    check("direction-only JSON parses", parsed["direction"] == "LONG")
    check("direction-only JSON derives tier", parsed["decision"] == "STRONG_APPROVE")
    check("missing confidence stays neutral", parsed["win_prob"] == 0)

    captured = []
    original = bot._spawn_independent_v1_lane_after_ai
    try:
        bot._spawn_independent_v1_lane_after_ai = lambda *args: captured.append(args)
        bot.spawn_type_b_lane_from_shared_ai(
            {"trade_id": "scan-1"},
            {"direction": "SHORT", "decision": "APPROVE", "win_prob": 71},
            4.0,
            {"price": 100.0},
            bot.RESEARCH_LANE_AI_SCAN,
        )
    finally:
        bot._spawn_independent_v1_lane_after_ai = original
    check("shared result invokes Type B once", len(captured) == 1)
    shared_ai = captured[0][1]
    check("shared AI is marked", shared_ai.get("shared_ai_call") is True)
    check("confidence is neutralized", shared_ai.get("win_prob") == 0)
    check("direction is preserved", shared_ai.get("direction") == "SHORT")
    print(f"PASS: {passed} shared AI contract checks")


if __name__ == "__main__":
    run()
