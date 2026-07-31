"""Contract tests for one AI call feeding two independent strategy tiles."""
import os
import copy
import inspect
import json
import tempfile
import threading
import time

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
    structural_limit, structural_reason = bot.resolve_ai_direct_limit(
        "LONG",
        64000.0,
        {},
        market_structure={"micro_support": 63900.0},
        support_resistance={},
    )
    check("direction-only call gets local-support limit", structural_limit == 63915.0)
    check("direction-only limit is labelled structural", structural_reason == "LOCAL_SUPPORT_LIMIT")
    missing_limit, missing_reason = bot.resolve_ai_direct_limit(
        "LONG",
        64000.0,
        {},
        market_structure={},
        support_resistance={},
    )
    check("direction-only call fails closed without local support", missing_limit is None)
    check("missing local support is explicit", missing_reason == "LOCAL_SUPPORT_UNAVAILABLE")
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

    continuous_spawns = []
    original_spawn_combo_lane = bot._spawn_combo_lane
    original_continuous_enabled = bot.continuous_ai_research_enabled
    try:
        bot._spawn_combo_lane = lambda *args: continuous_spawns.append(args)
        bot.continuous_ai_research_enabled = lambda: False
        bot.spawn_continuous_lane_from_ai_scan(
            {"trade_id": "scan-continuous-off"},
            {"direction": "LONG", "decision": "APPROVE", "long_score": 80, "short_score": 20},
            4.0,
            {"price": 100.0},
            bot.RESEARCH_LANE_AI_SCAN,
        )
    finally:
        bot._spawn_combo_lane = original_spawn_combo_lane
        bot.continuous_ai_research_enabled = original_continuous_enabled
    check("Continuous OFF uses the LAB-capable shared spawner", len(continuous_spawns) == 1)
    check("Continuous OFF retains the CONTINUOUS lane", continuous_spawns[0][4] == bot.RESEARCH_LANE_CONTINUOUS)

    continuous_labs = []
    original_lab = bot._spawn_lab_combo_shadow
    old_continuous_flag = bot.state.get("continuous_ai_research_enabled")
    try:
        bot._spawn_lab_combo_shadow = lambda *args, **kwargs: continuous_labs.append((args, kwargs))
        with bot.state_lock:
            bot.state["continuous_ai_research_enabled"] = False
        bot._spawn_combo_lane(
            {"trade_id": "scan-continuous-lab"},
            {"direction": "LONG", "decision": "APPROVE", "win_prob": 0},
            4.0,
            {"price": 100.0},
            bot.RESEARCH_LANE_CONTINUOUS,
            "TEST_CONTINUOUS_OFF",
        )
    finally:
        bot._spawn_lab_combo_shadow = original_lab
        with bot.state_lock:
            bot.state["continuous_ai_research_enabled"] = old_continuous_flag
    check("Continuous OFF opens a LAB shadow instead of DATA_COLLECT_ONLY", len(continuous_labs) == 1)

    page_source = inspect.getsource(bot)
    check(
        "Continuous benchmark can display OFF-tile LAB stats",
        "&& !spec.is_benchmark && spec.status" not in page_source,
    )

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
    check(
        "Type B remains paper research and is never platform-relay eligible",
        tile.get("platform_relay_eligible") is False,
    )
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
    check(
        "Continuous is the second visible tile after retired S/R removal",
        continuous_tile.get("tile_number") == 2,
    )
    check(
        "Continuous card uses the configured 12-to-10 Scenario C ladder",
        str(continuous_tile.get("exit", {}).get("ladder", "")).startswith("12"),
    )
    execute_source = inspect.getsource(bot.execute_order)
    check(
        "Continuous and Type B are both fail-safe limit-only policies",
        "force_policy_limit = is_type_b or is_continuous" in execute_source
        and "use_instant = (not force_policy_limit)" in execute_source,
    )
    relay_source = inspect.getsource(bot.api_relay_state)
    check("relay-state uses a single in-flight snapshot refresh", "_RELAY_STATE_REFRESH_LOCK.acquire" in relay_source)
    check("relay-state request path is cache-only", "if not force_rebuild" in relay_source)
    server_source = inspect.getsource(bot._create_dashboard_server)
    check("dashboard normal client I/O is bounded", "_client_io_timeout_sec = 15.0" in server_source)
    check("dashboard overload rejection I/O is bounded", "_overload_io_timeout_sec = 0.1" in server_source)
    check(
        "dashboard overload send cannot block accept loop",
        "request.settimeout(self._overload_io_timeout_sec)" in server_source,
    )
    check(
        "background refresher owns relay snapshot rebuilds",
        "api_relay_state(force_rebuild=True)" in inspect.getsource(bot._relay_state_cache_refresher_loop),
    )
    check(
        "relay refresh is independent from the heavy dashboard snapshot",
        "api_relay_state" not in inspect.getsource(bot._api_state_cache_refresher_loop)
        and "_build_api_state_snapshot" not in inspect.getsource(bot._relay_state_cache_refresher_loop),
    )
    dashboard_refresher_source = inspect.getsource(bot._api_state_cache_refresher_loop)
    check(
        "active trading bootstraps presentation once then uses bounded execution overlays",
        "if manual_admin_pause_active()" in dashboard_refresher_source
        and "ACTIVE_EXECUTION_OVERLAY" in dashboard_refresher_source
        and "_cached_relay_execution_snapshot()" in dashboard_refresher_source
        and "not base.get(\"bot_start_time\")" in dashboard_refresher_source
        and 'base_source in ("", "booting")' in dashboard_refresher_source
        and dashboard_refresher_source.count("_build_api_state_snapshot()") == 2
        and '"manual_admin_pause"' in dashboard_refresher_source
        and '"continuous_ai_research_enabled"' in dashboard_refresher_source
        and '"research_lane_enabled"' in dashboard_refresher_source
        and "relay_expired[-_DASHBOARD_HISTORY_MAX:]" in dashboard_refresher_source
        and '"expired_orders_total"' in dashboard_refresher_source
        and '"ai_history"' in dashboard_refresher_source
        and '"pathway_lane_specs"' in dashboard_refresher_source,
    )
    original_api_cache = copy.deepcopy(bot._api_state_cache)
    try:
        with bot._api_state_cache_lock:
            bot._api_state_cache["payload"] = {
                "continuous_ai_research_enabled": False,
                "research_lane_enabled": {},
            }
        bot._patch_api_state_cache_fields(
            continuous_ai_research_enabled=True,
            research_lane_enabled={RESEARCH_LANE_TYPE_B_HUNTER_V1: True},
        )
        with bot._api_state_cache_lock:
            patched_cache = copy.deepcopy(bot._api_state_cache["payload"])
        check(
            "control POST cache patch makes toggle state immediately observable",
            patched_cache.get("continuous_ai_research_enabled") is True
            and patched_cache.get("research_lane_enabled", {}).get(
                RESEARCH_LANE_TYPE_B_HUNTER_V1
            ) is True,
        )
    finally:
        with bot._api_state_cache_lock:
            bot._api_state_cache.clear()
            bot._api_state_cache.update(original_api_cache)
    dashboard_snapshot_source = inspect.getsource(bot._build_api_state_snapshot)
    dashboard_html_source = bot.DASHBOARD_JS
    check(
        "dashboard snapshot excludes unbounded state collections before deepcopy",
        "if key not in _DASHBOARD_STATE_DEEPCOPY_EXCLUDED_KEYS" in dashboard_snapshot_source
        and "order_book" in bot._DASHBOARD_STATE_DEEPCOPY_EXCLUDED_KEYS
        and "ai_history" in bot._DASHBOARD_STATE_DEEPCOPY_EXCLUDED_KEYS
        and 'snapshot.pop("order_book"' not in dashboard_snapshot_source,
    )
    bounded_map_source = inspect.getsource(bot._snapshot_bounded_trades_map_locked)
    check(
        "relay trades-map work is bounded under the money-path lock",
        "itertools.islice(reversed(trades_map.items()), remaining)" in bounded_map_source
        and bot._RELAY_TRADES_MAP_MAX >= 64,
    )
    original_trades_map = bot.trades_map
    try:
        bot.trades_map = {
            f"history-{i}": {
                "signal_ref": {
                    "trade_id": f"history-{i}",
                    "status": "BLOCKED",
                    "created_ts_ts": i,
                    "research_lane": "CONTINUOUS",
                    "oversized_research_payload": "x" * 10_000,
                }
            }
            for i in range(bot._RELAY_TRADES_MAP_MAX * 4)
        }
        bot.trades_map["active-lock-test"] = {
            "signal_ref": {
                "trade_id": "active-lock-test",
                "status": "ORDERED",
                "order_placed": True,
                "created_ts_ts": 1,
                "research_lane": "CONTINUOUS",
                "oversized_research_payload": "x" * 1_000_000,
            }
        }
        started = time.monotonic()
        with bot.trade_lock:
            bounded = bot._snapshot_bounded_trades_map_locked(
                [{"trade_id": "active-lock-test"}],
                [],
            )
        elapsed = time.monotonic() - started
        check(
            "bounded relay snapshot preserves active ID and strips research payload",
            "active-lock-test" in bounded
            and len(bounded) <= bot._RELAY_TRADES_MAP_MAX
            and "oversized_research_payload"
            not in bounded["active-lock-test"]["signal_ref"]
            and elapsed < 0.5,
        )
    finally:
        bot.trades_map = original_trades_map
    check(
        "dashboard presentation refresh is not an aggressive hot loop",
        bot._API_STATE_REFRESH_INTERVAL_SEC >= 5.0,
    )
    api_state_source = inspect.getsource(bot.api_state)
    check(
        "dashboard requests never build the heavy snapshot synchronously",
        "_build_api_state_snapshot" not in api_state_source
        and "dashboard snapshot is warming" in api_state_source,
    )
    original_payload = bot._api_state_cache["payload"]
    original_built_at = bot._api_state_cache["built_at"]
    original_building = bot._api_state_cache["building"]
    original_builder = bot._build_api_state_snapshot
    try:
        with bot._api_state_cache_lock:
            bot._api_state_cache["payload"] = None
            bot._api_state_cache["built_at"] = 0.0
            bot._api_state_cache["building"] = True

        def _must_not_build_on_request():
            raise AssertionError("/api/state invoked the heavy snapshot builder")

        bot._build_api_state_snapshot = _must_not_build_on_request
        client = bot.app.test_client()
        started = time.monotonic()
        response = client.get("/api/state")
        elapsed = time.monotonic() - started
        check("cold /api/state fails fast", response.status_code == 503 and elapsed < 2.0)
    finally:
        bot._build_api_state_snapshot = original_builder
        with bot._api_state_cache_lock:
            bot._api_state_cache["payload"] = original_payload
            bot._api_state_cache["built_at"] = original_built_at
            bot._api_state_cache["building"] = original_building

    check(
        "dashboard snapshot lock waits are bounded",
        "state_lock.acquire(timeout=_API_STATE_LOCK_TIMEOUT_SEC)" in dashboard_snapshot_source
        and "trade_lock.acquire(timeout=_API_STATE_LOCK_TIMEOUT_SEC)" in dashboard_snapshot_source,
    )
    check(
        "dashboard snapshot defers raw research ledger aggregation",
        "if _API_STATE_INLINE_RESEARCH_AGGREGATES" in dashboard_snapshot_source
        and "get_pathway_lane_specs_cached(for_api=True)" in dashboard_snapshot_source
        and '"source": "analyzer"' in dashboard_snapshot_source,
    )
    check(
        "dashboard card active counts include the Continuous benchmark",
        "PATHWAY_LAB_LANES + (RESEARCH_LANE_CONTINUOUS,)"
        in dashboard_snapshot_source,
    )
    check(
        "dashboard cards honor the direct manual-pause state",
        "d.manual_admin_pause === true" in dashboard_html_source
        and "d.execution_reason === 'ADMIN_MANUAL'" in dashboard_html_source,
    )
    original_lane_specs = bot._cached_pathway_lane_specs
    original_merge_specs = bot._merge_pathway_specs_with_session_stats
    original_getcwd = bot.os.getcwd
    try:
        with tempfile.TemporaryDirectory() as td:
            analyzer_specs = {
                "generated_at": "2026-07-28T12:44:26Z",
                "lanes": [{
                    "lane": bot.RESEARCH_LANE_CONTINUOUS,
                    "session_stats": {
                        "approves": 531,
                        "real_fills": 85,
                        "net_pnl_real": 9.98,
                    },
                }],
            }
            with open(os.path.join(td, bot.PATHWAY_LANE_SPECS_FILE), "w", encoding="utf-8") as f:
                json.dump(analyzer_specs, f)
            bot._cached_pathway_lane_specs = {}
            bot.os.getcwd = lambda: td

            def _must_not_scan_research_ledgers(*_args, **_kwargs):
                raise AssertionError("API lane specs scanned raw research ledgers")

            bot._merge_pathway_specs_with_session_stats = _must_not_scan_research_ledgers
            api_lane_specs = bot.get_pathway_lane_specs_cached(for_api=True)
            continuous_stats = next(
                row.get("session_stats")
                for row in api_lane_specs.get("lanes") or []
                if row.get("lane") == bot.RESEARCH_LANE_CONTINUOUS
            )
            check(
                "API lane specs load lightweight analyzer-owned statistics",
                api_lane_specs.get("session_stats_source") == "analyzer"
                and api_lane_specs.get("session_stats_generated_at") == analyzer_specs["generated_at"]
                and continuous_stats.get("real_fills") == 85
                and continuous_stats.get("net_pnl_real") == 9.98,
            )
    finally:
        bot._cached_pathway_lane_specs = original_lane_specs
        bot._merge_pathway_specs_with_session_stats = original_merge_specs
        bot.os.getcwd = original_getcwd
    execution_source = inspect.getsource(bot._build_relay_execution_state_snapshot)
    check(
        "relay active-signal rendering runs after releasing trade_lock",
        execution_source.find("trade_lock.release()")
        < execution_source.find("_collect_dashboard_active_signals("),
    )
    check(
        "execution relay snapshot excludes presentation ledgers",
        "build_paper_order_book" not in execution_source
        and "build_state_integrity" not in execution_source,
    )
    check(
        "execution relay snapshot lock waits are bounded",
        "state_lock.acquire(timeout=_RELAY_EXECUTION_LOCK_TIMEOUT_SEC)" in execution_source
        and "trade_lock.acquire(timeout=_RELAY_EXECUTION_LOCK_TIMEOUT_SEC)" in execution_source,
    )
    process_positions_source = inspect.getsource(bot.process_positions)
    cleanup_expired_source = inspect.getsource(bot.cleanup_expired_orders)
    cancel_pending_source = inspect.getsource(bot._cancel_pending_order_confirmed)
    close_position_source = inspect.getsource(bot.close_position)
    check(
        "position exits run after releasing trade_lock",
        process_positions_source.find("with trade_lock:")
        < process_positions_source.find("for pos in positions:")
        and process_positions_source.find("for pos in positions:")
        < process_positions_source.find("_apply_position_exits("),
    )
    check(
        "position lifecycle has one non-blocking evaluation owner",
        "position_evaluation_lock.acquire(blocking=False)" in process_positions_source
        and "position_evaluation_lock.release()" in process_positions_source,
    )
    bot.position_evaluation_lock.acquire()
    try:
        started = time.perf_counter()
        bot.process_positions()
        check(
            "redundant position lifecycle worker skips a busy owner",
            time.perf_counter() - started < 0.25,
        )
    finally:
        bot.position_evaluation_lock.release()
    check(
        "expired-order persistence runs after releasing trade_lock",
        "_cancel_pending_order_confirmed(" in cleanup_expired_source
        and cancel_pending_source.find("with trade_lock:")
        < cancel_pending_source.find('result["confirmed"] = True')
        < cancel_pending_source.find("if record_expired:")
        < cancel_pending_source.find("_record_expired_order("),
    )
    check(
        "position close uses a dedicated serialization lock",
        "with position_close_lock:" in close_position_source
        and "with state_lock:" not in close_position_source.split("trade_row = {", 1)[0]
        and "with trade_lock:" not in close_position_source.split("trade_row = {", 1)[0],
    )
    check(
        "fee filter never vetoes a protective profit-lock exit",
        not bot.should_skip_unprofitable_profit_exit("PROFIT_LOCK_LADDER", -0.01),
    )
    check(
        "fee filter may still defer an optional fixed take-profit below fees",
        bot.should_skip_unprofitable_profit_exit("TAKE_PROFIT", -0.01),
    )
    check(
        "fee filter never vetoes stop, thesis, or time exits",
        all(
            not bot.should_skip_unprofitable_profit_exit(reason, -1.0)
            for reason in (
                "STOP_LOSS",
                "THESIS_INVALIDATED",
                "THESIS_FAST_CUT",
                "EARLY_FAIL",
                "TIME_EXIT",
                "EMERGENCY_MAX_HOLD",
            )
        ),
    )
    pending_orders_source = inspect.getsource(bot.process_pending_orders)
    pending_touch_source = inspect.getsource(bot._pending_limit_touched)
    check(
        "pending fill snapshots BBO before taking trade_lock",
        pending_orders_source.find("with state_lock:")
        < pending_orders_source.find("with trade_lock:")
        and "bid=fill_bid" in pending_orders_source
        and "ask=fill_ask" in pending_orders_source,
    )
    check(
        "pending touch accepts a pre-captured BBO without nested state locking",
        "bid: float | None = None" in pending_touch_source
        and "ask: float | None = None" in pending_touch_source,
    )

    state_held = threading.Event()
    release_state = threading.Event()
    def _hold_state_for_fill_race():
        with bot.state_lock:
            state_held.set()
            release_state.wait(timeout=5)

    state_holder = threading.Thread(target=_hold_state_for_fill_race, daemon=True)
    state_holder.start()
    try:
        check("fill-race state-lock fixture started", state_held.wait(timeout=1))
        started = time.perf_counter()
        with bot.trade_lock:
            touched = bot._pending_limit_touched(
                {
                    "side": "buy",
                    "limit_price": 65000.0,
                    "min_price_since_order": 64999.0,
                    "max_price_since_order": 65001.0,
                },
                65000.0,
                bid=64999.0,
                ask=65000.0,
            )
        check(
            "fill touch never waits for state_lock while trade_lock is held",
            touched and (time.perf_counter() - started) < 0.25,
        )
    finally:
        release_state.set()
        state_holder.join(timeout=1)

    original_positions_file = bot.POSITIONS_FILE
    original_open_positions = bot.open_positions
    original_lane_open_positions = bot.lane_open_positions
    original_strategy_mode = bot.state.get("strategy_mode")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            bot.POSITIONS_FILE = os.path.join(tmp, "open_positions.json")
            restart_position = {
                "trade_id": "paper-restart-lifecycle",
                "status": "OPEN",
                "dir": "LONG",
                "entry": 65000.0,
                "qty": 0.03,
                "research_lane": bot.RESEARCH_LANE_TYPE_B_HUNTER_V1,
            }
            with open(bot.POSITIONS_FILE, "w", encoding="utf-8") as handle:
                json.dump([restart_position], handle)
            bot.open_positions = []
            bot.lane_open_positions = {
                **original_lane_open_positions,
                bot.RESEARCH_LANE_TYPE_B_HUNTER_V1: [],
            }
            with bot.state_lock:
                bot.state["strategy_mode"] = "RESEARCH"
            bot.load_positions()
            bot.load_positions()
            check(
                "research restart restores one persisted open paper lifecycle",
                len(bot.open_positions) == 1
                and bot.open_positions[0].get("trade_id") == "paper-restart-lifecycle"
                and len(
                    bot.lane_open_positions.get(
                        bot.RESEARCH_LANE_TYPE_B_HUNTER_V1,
                        [],
                    )
                ) == 1,
            )
    finally:
        bot.POSITIONS_FILE = original_positions_file
        bot.open_positions = original_open_positions
        bot.lane_open_positions = original_lane_open_positions
        with bot.state_lock:
            bot.state["strategy_mode"] = original_strategy_mode

    original_refresh_bbo = bot.refresh_bbo_state
    original_refresh_book = bot.refresh_order_book_state
    original_funding = bot.process_funding_accrual
    original_apply_exits = bot._apply_position_exits
    original_positions = bot.open_positions
    exit_started = threading.Event()
    release_exit = threading.Event()
    try:
        bot.refresh_bbo_state = lambda: None
        bot.refresh_order_book_state = lambda: None
        bot.process_funding_accrual = lambda: None
        with bot.state_lock:
            bot.state["price"] = 65000.0
        bot.open_positions = [{"trade_id": "lock-soak", "status": "OPEN"}]

        def _slow_exit(*_args, **_kwargs):
            exit_started.set()
            release_exit.wait(timeout=5)

        bot._apply_position_exits = _slow_exit
        worker = threading.Thread(target=bot.process_positions, daemon=True)
        worker.start()
        check("slow position-exit fixture started", exit_started.wait(timeout=1))
        acquired = bot.trade_lock.acquire(timeout=0.5)
        check("slow position-exit work does not hold trade_lock", acquired)
        if acquired:
            bot.trade_lock.release()
    finally:
        release_exit.set()
        if "worker" in locals():
            worker.join(timeout=1)
        bot.refresh_bbo_state = original_refresh_bbo
        bot.refresh_order_book_state = original_refresh_book
        bot.process_funding_accrual = original_funding
        bot._apply_position_exits = original_apply_exits
        bot.open_positions = original_positions

    original_record_expired = bot._record_expired_order
    original_pending = bot.pending_orders
    original_lane_pending = bot.lane_pending_orders
    expiry_started = threading.Event()
    release_expiry = threading.Event()
    try:
        expired_order = {
            "trade_id": "lock-expiry",
            "status": "PENDING",
            "created_ts": time.time() - bot.LIMIT_ORDER_MAX_AGE_SEC - 10,
            "research_lane": bot.RESEARCH_LANE_CONTINUOUS,
        }
        bot.pending_orders = [expired_order]
        bot.lane_pending_orders = {
            **original_lane_pending,
            bot.RESEARCH_LANE_CONTINUOUS: [expired_order],
        }

        def _slow_expiry(*_args, **_kwargs):
            expiry_started.set()
            release_expiry.wait(timeout=5)

        bot._record_expired_order = _slow_expiry
        expiry_worker = threading.Thread(target=bot.cleanup_expired_orders, daemon=True)
        expiry_worker.start()
        check("slow order-expiry fixture started", expiry_started.wait(timeout=1))
        acquired = bot.trade_lock.acquire(timeout=0.5)
        check("slow order-expiry persistence does not hold trade_lock", acquired)
        if acquired:
            bot.trade_lock.release()
    finally:
        release_expiry.set()
        if "expiry_worker" in locals():
            expiry_worker.join(timeout=1)
        bot._record_expired_order = original_record_expired
        bot.pending_orders = original_pending
        bot.lane_pending_orders = original_lane_pending
    relay_position = bot._relay_position_row_lite(
        {
            "trade_id": "cont-pre-arm",
            "status": "OPEN",
            "dir": "LONG",
            "entry": 65000.0,
            "created_ts": 1_774_608_490.315,
            "signal_created_ts": 1_774_608_490.315,
            "order_created_ts": 1_774_608_492.003,
            "entry_ts": 1_774_609_094.056,
            "signal_age_sec": 603.741,
            "leverage": 100,
            "funding_fees": 0.05,
        },
        65010.0,
    )
    check(
        "relay position preserves source birth watermark after fill",
        relay_position.get("created_ts") == 1_774_608_490.315
        and relay_position.get("signal_created_ts") == 1_774_608_490.315
        and relay_position.get("order_created_ts") == 1_774_608_492.003,
    )
    check(
        "relay position overlay preserves dashboard unrealized PnL",
        abs(float(relay_position.get("pnl_pct_margin") or 0.0) - 1.538461538) < 0.000001
        and abs(float(relay_position.get("unreal_usd") or 0.0) - 0.2577) < 0.0001,
    )
    check(
        "open position builder persists source birth lineage",
        '"created_ts": signal_ts or order_created or fill_ts' in inspect.getsource(bot._build_open_position)
        and '"signal_created_ts": signal_ts or order.get("signal_created_ts")' in inspect.getsource(bot._build_open_position),
    )
    lock_held = threading.Event()
    release_lock = threading.Event()

    def _hold_state_lock():
        with bot.state_lock:
            lock_held.set()
            release_lock.wait(timeout=5)

    holder = threading.Thread(target=_hold_state_lock, daemon=True)
    holder.start()
    check("execution lock saturation fixture acquired state lock", lock_held.wait(timeout=1))
    try:
        started = time.monotonic()
        execution_response = bot.app.test_client().get("/api/relay-execution-state")
        elapsed = time.monotonic() - started
        check(
            "execution relay endpoint fails closed without starving a request worker",
            execution_response.status_code == 503 and elapsed < 1.5,
        )
    finally:
        release_lock.set()
        holder.join(timeout=1)

    old_bootstrap_complete = bot._DASHBOARD_BOOTSTRAP_COMPLETE
    try:
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
        with bot.app.test_client() as client:
            execution_response = client.get("/api/relay-execution-state")
            check("execution relay endpoint returns canonical JSON", execution_response.status_code == 200)
            check(
                "execution relay endpoint proves cached bounded source",
                execution_response.headers.get("X-Relay-State-Cache") == "EXECUTION_BACKGROUND"
                and execution_response.get_json().get("relay_cache", {}).get("mode") == "EXECUTION_DIRECT",
            )
    finally:
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = old_bootstrap_complete
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
