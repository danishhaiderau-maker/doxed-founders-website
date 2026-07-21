"""Contract tests for one AI call feeding two independent strategy tiles."""
import os
import inspect

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot
from combo_pathway_config import (
    COMBO_LANE_SPECS,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
    combo_lane_match_detail,
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
    for forbidden in ("entry_zone_low", "limit_price", "expected_mfe", "expected_mae", "exit_style"):
        check(f"shared prompt omits {forbidden}", forbidden not in bot.AI_PROMPT_TEMPLATE)

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
    no_trade = bot.parse_ai_response_fields(
        '{"direction":"NO_TRADE","long_score":55,"short_score":50,'
        '"reason":"Long evidence is marginally stronger."}'
    )
    check("NO_TRADE cannot suppress candidate direction", no_trade["direction"] == "LONG")
    check("raw NO_TRADE remains auditable", no_trade["raw_direction"] == "NO_TRADE")
    check("exact five-point gap is a Continuous soft approve", no_trade["decision"] == "SOFT_APPROVE")
    check("legacy confidence bands cannot block direction-only v12", not bot.dashboard_ai_band_blocks(0))
    fallback_limit, fallback_reason = bot.resolve_ai_direct_limit("LONG", 100.0, {})
    check("direction-only call gets deterministic pullback limit", 0 < fallback_limit < 100.0)
    check("direction-only limit is labelled deterministic", fallback_reason == "DIRECTIONAL_PULLBACK_LIMIT")
    check(
        "shared LONG scores normalize to the legacy Type B spread",
        bot.compute_directional_spread(
            "LONG",
            {"long_score": 65, "short_score": 35},
        ) == 3,
    )
    check(
        "shared SHORT scores normalize to the legacy Type B spread",
        bot.compute_directional_spread(
            "SHORT",
            {"long_score": 35, "short_score": 65},
        ) == 3,
    )
    check(
        "legacy bull/bear spread remains compatible",
        bot.compute_directional_spread(
            "LONG",
            {"bull_score": 7, "bear_score": 4},
        ) == 3,
    )
    type_b_combo_detail = combo_lane_match_detail(
        RESEARCH_LANE_TYPE_B_HUNTER_V1,
        {"direction": "SHORT", "long_score": 20, "short_score": 80},
        "SHORT",
        spread=6,
        features={},
    )
    check(
        "generic Type B matcher reads shared LONG/SHORT scores",
        type_b_combo_detail.get("passes") is True
        and type_b_combo_detail.get("directional_spread") == 6,
    )
    type_b_ok, type_b_reason = bot._apply_type_b_hunter_v1_entry_filter(
        {"direction": "LONG", "long_score": 65, "short_score": 35},
        {
            "adx": 31,
            "volume_ratio": 0.6,
            "regime": "BULL",
            "structure_score": 4,
            "delta": 22,
            "edge_score": 4,
            "ema_slope": "up",
        },
        4.0,
    )
    check("valid shared score reaches the Type B policy", type_b_ok is True)
    check(
        "valid shared score is no longer rejected as zero spread",
        "SPREAD_FLOOR" not in str(type_b_reason or ""),
    )

    with bot.state_lock:
        bot.state["ai_history"] = []
        bot.state["shared_ai_lane_counters"] = {
            bot.RESEARCH_LANE_CONTINUOUS: {"evaluated": 0, "accepted": 0, "rejected": 0, "reasons": {}},
            RESEARCH_LANE_TYPE_B_HUNTER_V1: {"evaluated": 0, "accepted": 0, "rejected": 0, "reasons": {}},
        }
    history_ai = {
        "trade_id": "scan-history-1",
        "shared_ai_call_id": "scan-history-1",
        "shared_ai_call_ts": "2026-07-21T00:16:38+00:00",
        "direction": "LONG",
        "candidate_direction": "LONG",
        "raw_direction": "NO_TRADE",
        "decision": "APPROVE",
        "long_score": 55,
        "short_score": 50,
        "factors": {"long_score": 55, "short_score": 50},
        "comment": '{"direction":"NO_TRADE","long_score":55,"short_score":50}',
    }
    bot._append_ai_history_row(history_ai)
    bot._append_ai_history_row({**history_ai, "source": "SPAWN"})
    bot._stamp_shared_ai_lane_verdict(
        "scan-history-1", bot.RESEARCH_LANE_CONTINUOUS, True, "SOFT_APPROVE"
    )
    bot._stamp_shared_ai_lane_verdict(
        "scan-history-1", RESEARCH_LANE_TYPE_B_HUNTER_V1, False, "BULL_NEEDS_ADX_28"
    )
    with bot.state_lock:
        history = list(bot.state["ai_history"])
        counters = bot.state["shared_ai_lane_counters"]
    check("one API call renders one history row", len(history) == 1)
    check("history preserves the canonical shared-call timestamp", history[0]["time"] == "2026-07-21T00:16:38+00:00")
    check("shared result carries its paid-call identity", history_ai["shared_ai_call_id"] == "scan-history-1")
    check("Continuous verdict is separate", history[0]["continuous_verdict"]["accepted"] is True)
    check("Type B verdict is separate", history[0]["type_b_verdict"]["accepted"] is False)
    check("Continuous counter counted once", counters[bot.RESEARCH_LANE_CONTINUOUS]["evaluated"] == 1)
    check("Type B counter counted once", counters[RESEARCH_LANE_TYPE_B_HUNTER_V1]["evaluated"] == 1)

    inherited_features = bot._prepare_shared_lane_spawn_features(
        {"features": {"adx": 31, "volume_ratio": 0.6}},
        {"adx": 10, "volume_ratio": 2.5},
        {"market_context": {}},
    )
    check("shared-lane spawn uses event features", inherited_features["adx"] == 31)
    check("shared-lane spawn keeps event volume", inherited_features["volume_ratio"] == 0.6)
    pre_signal_source = inspect.getsource(bot.process_signal).split("signal = {", 1)[0]
    check(
        "shared-lane pre-signal branch cannot read future signal object",
        'signal.get("features")' not in pre_signal_source,
    )

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

    counterfactuals = []
    original_lane_orders_allowed = bot.lane_orders_allowed
    original_start_replay_buffer = bot.start_replay_buffer
    original_append_replay_tick = bot.append_replay_tick
    original_log_lane_opportunity_event = bot.log_lane_opportunity_event
    try:
        bot.lane_orders_allowed = lambda lane: True
        bot.start_replay_buffer = lambda *args, **kwargs: counterfactuals.append((args, kwargs))
        bot.append_replay_tick = lambda *args, **kwargs: None
        bot.log_lane_opportunity_event = lambda *args, **kwargs: None
        with bot.state_lock:
            bot.state["price"] = 100.0
        bot._spawn_lab_combo_shadow(
            {"trade_id": "scan-counterfactual"},
            {"direction": "LONG", "decision": "REJECT", "win_prob": 0},
            2.0,
            RESEARCH_LANE_TYPE_B_HUNTER_V1,
            {"price": 100.0},
            collection_mode="CALIBRATION_COUNTERFACTUAL",
            is_counterfactual=True,
        )
    finally:
        bot.lane_orders_allowed = original_lane_orders_allowed
        bot.start_replay_buffer = original_start_replay_buffer
        bot.append_replay_tick = original_append_replay_tick
        bot.log_lane_opportunity_event = original_log_lane_opportunity_event
    check("Type B ON still collects rejected counterfactual replay", len(counterfactuals) == 1)
    check("counterfactual replay is explicitly tagged", counterfactuals[0][1].get("is_counterfactual") is True)

    tile = next(
        row for row in bot.build_static_pathway_lane_specs()["lanes"]
        if row.get("lane") == RESEARCH_LANE_TYPE_B_HUNTER_V1
    )
    check("Type B tile truthfully labels its chase entry", tile.get("entry_mode_label") == "Bounded Limit Chase")
    check("Type B tile is platform-relay eligible when ON", tile.get("platform_relay_eligible") is True)
    check(
        "Type B tile exposes raw and normalized score gates",
        "Raw score gap >=20/100" in tile.get("filter_chips", [])
        and "Normalized spread >=2" in tile.get("filter_chips", []),
    )
    continuous_tile = next(
        row for row in bot.build_static_pathway_lane_specs()["lanes"]
        if row.get("lane") == bot.RESEARCH_LANE_CONTINUOUS
    )
    check(
        "Continuous tile exposes its raw score gap gate",
        "Raw score gap >=5/100" in continuous_tile.get("filter_chips", []),
    )
    check(
        "Continuous tile exposes the no-confidence contract",
        "AI confidence not requested" in continuous_tile.get("filter_chips", []),
    )
    relay_source = inspect.getsource(bot.api_relay_state)
    check("relay-state uses a single in-flight snapshot refresh", "_RELAY_STATE_REFRESH_LOCK.acquire" in relay_source)
    check("relay-state request path is cache-only", "if not force_rebuild" in relay_source)
    check(
        "background refresher owns relay snapshot rebuilds",
        "api_relay_state(force_rebuild=True)" in inspect.getsource(bot._relay_state_cache_refresher_loop),
    )
    check(
        "relay refresh is independent from the heavy dashboard snapshot",
        "api_relay_state" not in inspect.getsource(bot._api_state_cache_refresher_loop)
        and "_build_api_state_snapshot" not in inspect.getsource(bot._relay_state_cache_refresher_loop),
    )
    check(
        "cache reads never wait on the expensive relay rebuild lock",
        "with _RELAY_STATE_CACHE_LOCK" in inspect.getsource(bot._cached_relay_state_response)
        and bot._RELAY_STATE_CACHE_LOCK is not bot._RELAY_STATE_REFRESH_LOCK,
    )
    check("relay-state exposes bounded cache evidence", "X-Relay-State-Cache" in inspect.getsource(bot._cached_relay_state_response))
    old_cache, old_at = bot._RELAY_STATE_CACHE, bot._RELAY_STATE_CACHE_AT
    try:
        bot._RELAY_STATE_CACHE = {"state_integrity": {"snapshot_age_sec": 0}}
        bot._RELAY_STATE_CACHE_AT = bot.time.monotonic()
        with bot.app.app_context():
            cached_response = bot._cached_relay_state_response("FRESH")
            check("fresh relay-state cache returns immediately", cached_response.status_code == 200)
            check("relay-state cache response is labeled", cached_response.headers.get("X-Relay-State-Cache") == "FRESH")
        bot._RELAY_STATE_CACHE_AT = bot.time.monotonic() - bot._RELAY_STATE_MAX_STALE_SEC - 1
        with bot.app.app_context():
            check("over-age relay-state cache fails closed", bot._cached_relay_state_response("STALE") is None)
    finally:
        bot._RELAY_STATE_CACHE, bot._RELAY_STATE_CACHE_AT = old_cache, old_at
    print(f"PASS: {passed} shared AI contract checks")


if __name__ == "__main__":
    run()
