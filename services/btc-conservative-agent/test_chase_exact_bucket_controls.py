import ast
import copy
import sys
import threading
import types
from datetime import datetime, timezone
from pathlib import Path


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def test_exact_chase_controls_are_rendered():
    expected = (
        "0_chases",
        "1_chase",
        "2_chases",
        "3_chases",
        "4_chases",
        "5+_chases",
    )
    for bucket in expected:
        assert f"'{bucket}'" in BOT_SOURCE or f'"{bucket}"' in BOT_SOURCE
    assert "select 3 and 4 only" in BOT_SOURCE


def test_grouped_chase_controls_are_migration_only():
    assert "def _normalize_chase_execution_buckets(raw)" in BOT_SOURCE
    assert 'if "3-5_chases" in raw:' in BOT_SOURCE
    assert 'if "6+_chases" in raw:' in BOT_SOURCE
    assert "state.get(\"chase_execution_buckets\")" in BOT_SOURCE
    assert "const order = ['0_chases','1_chase','2_chases','3_chases','4_chases','5+_chases'];" in BOT_SOURCE


def test_virtual_wait_and_cancel_paths_remain_wired():
    assert "dashboard_virtual_chase_submit_ready(signal)" in BOT_SOURCE
    assert "_cancel_pending_for_chase_gate(order" in BOT_SOURCE
    assert "process_awaiting_dashboard_virtual_chase_entries()" in BOT_SOURCE
    assert "next_chase_count = int(order.get(\"limit_chase_count\") or 0) + 1" in BOT_SOURCE
    assert "if not chase_bucket_allowed(next_chase_count):" in BOT_SOURCE
    assert "def _virtual_limit_would_fill(signal: dict, market_price: float)" in BOT_SOURCE
    assert "VIRTUAL_FILL_SKIPPED_CHASE_" in BOT_SOURCE


def test_production_chase_never_invokes_terminal_marketable_fallback():
    """Selected maker buckets must cancel/expire instead of forcing a fill."""
    tree = ast.parse(BOT_SOURCE)
    process_chase = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "process_limit_chase"
    )
    invoked = {
        node.func.id
        for node in ast.walk(process_chase)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_apply_marketable_limit_fallback" not in invoked
    assert "the next disallowed bucket cancels them" in ast.get_source_segment(
        BOT_SOURCE, process_chase
    )


def test_pending_order_registration_is_trade_id_idempotent():
    assert "def lane_register_pending_order(order: dict):" in BOT_SOURCE
    assert 'tid = str(order.get("trade_id") or "")' in BOT_SOURCE
    assert '"[ORDER IDEMPOTENCY] duplicate pending registration suppressed "' in BOT_SOURCE
    assert "registered = lane_register_pending_order(order)" in BOT_SOURCE
    assert "if not registered:" in BOT_SOURCE

    tree = ast.parse(BOT_SOURCE)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "lane_register_pending_order"
    )

    class QuietLogger:
        def warning(self, *_args, **_kwargs):
            pass

    namespace = {
        "pending_orders": [],
        "lane_pending_orders": {"CONTINUOUS": []},
        "trade_lock": threading.RLock(),
        "_ensure_lane_bucket": lambda order: order.get("research_lane") or "CONTINUOUS",
        "_emit_genome_execution_event": lambda *_args, **_kwargs: None,
        "logger": QuietLogger(),
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<lane-register-test>", "exec"), namespace)
    register = namespace["lane_register_pending_order"]
    first = {"trade_id": "cont-same", "status": "PENDING", "research_lane": "CONTINUOUS"}
    second = {
        "trade_id": "cont-same",
        "status": "PENDING",
        "research_lane": "CONTINUOUS",
        "created_ts": 2,
    }
    assert register(first) is True
    assert register(second) is False
    assert namespace["pending_orders"] == [first]
    assert namespace["lane_pending_orders"]["CONTINUOUS"] == [first]


def test_waiting_chase_is_not_reported_as_an_order():
    assert "def _account_registered_order_submission(signal: dict, ai: dict = None)" in BOT_SOURCE
    assert 'signal["_order_submission_accounted"] = True' in BOT_SOURCE
    assert '"CHASE_BUCKET_WAIT",' in BOT_SOURCE
    assert "_account_registered_order_submission(signal, ai)" in BOT_SOURCE
    assert '_emit_genome_execution_event("ORDER_FILLED", {"trade_id": trade_id' not in BOT_SOURCE

    tree = ast.parse(BOT_SOURCE)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_account_registered_order_submission"
    )
    events = []
    namespace = {
        "pending_orders": [],
        "trade_lock": threading.RLock(),
        "increment_pipeline_funnel": lambda stage: events.append(("funnel", stage)),
        "log_lane_opportunity_event": lambda *args, **_kwargs: events.append(("lane", args[1])),
        "relay_publishes_approve_outcome": lambda _lane: True,
        "record_approve_outcome": lambda *args, **_kwargs: events.append(("relay", args[1])),
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<submission-test>", "exec"), namespace)
    account = namespace["_account_registered_order_submission"]
    signal = {
        "trade_id": "cont-wait",
        "research_lane": "CONTINUOUS",
        "final_direction": "LONG",
    }
    assert account(signal, {"win_prob": None}) is False
    assert events == []
    namespace["pending_orders"].append(
        {"trade_id": "cont-wait", "status": "PENDING"}
    )
    assert account(signal, {"win_prob": None}) is True
    assert events == [
        ("funnel", "ORDER_SUBMITTED"),
        ("lane", "ORDER_SUBMITTED"),
        ("relay", "ORDER_PLACED"),
    ]
    assert account(signal, {"win_prob": None}) is False
    assert len(events) == 3


def test_specific_gate_failure_is_not_overwritten_by_order_failed():
    assert 'failure_reason = (' in BOT_SOURCE
    assert 'signal.get("exit_reason")' in BOT_SOURCE
    assert 'or signal.get("outcome")' in BOT_SOURCE
    assert 'or signal.get("block_reason")' in BOT_SOURCE
    assert 'exit_pipeline(signal, ai, failure_reason)' in BOT_SOURCE


def test_obsolete_confidence_controls_are_not_rendered():
    assert '<div id="aiBandControls"' not in BOT_SOURCE
    assert "<strong>AI execution bands:</strong>" not in BOT_SOURCE
    # Analytics matrix table removed from dashboard; hard-gate controls remain.
    assert "<h3>Directional gap analytics</h3>" not in BOT_SOURCE
    assert "<h3>Directional gap hard-gate</h3>" in BOT_SOURCE
    assert 'id="pathwayAnalytics"' not in BOT_SOURCE


def test_gap_analytics_matches_analyzer_matrix_schema():
    assert 'parts[0] not in ("0", "1", "2", "3", "4", "5+")' in BOT_SOURCE
    assert '"normalized directional score gap = abs(LONG score - SHORT score) // 10"' in BOT_SOURCE
    assert "raw gap 30 → execution bucket 3" in BOT_SOURCE
    assert "'3':'bucket 3 (raw gap 30 to 39)'" in BOT_SOURCE
    assert "<th>Raw gap (0–100)</th><th>Execution gap bucket</th>" in BOT_SOURCE
    assert "Math.floor(rawGap / 10)" in BOT_SOURCE
    assert '"""Compatibility wrapper over the one canonical dashboard gap gate."""' in BOT_SOURCE
    assert "return not spread_gate_allows(spread), bucket" in BOT_SOURCE
    assert "updates the same config-7002.json gate as the dashboard" in BOT_SOURCE
    assert 'for key in ("directional_spread", "conviction_spread"):' in BOT_SOURCE
    assert 'signal["directional_spread"] = spread' in BOT_SOURCE


def _compile_function(name, namespace):
    tree = ast.parse(BOT_SOURCE)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    exec(compile(ast.Module(body=[fn], type_ignores=[]), f"<{name}-test>", "exec"), namespace)
    return namespace[name]


def test_marketable_limit_fill_never_violates_hard_limit_price():
    market_walk_calls = []
    namespace = {
        "refresh_bbo_state": lambda: None,
        "refresh_order_book_state": lambda: None,
        "state_lock": threading.RLock(),
        "state": {"bid": 63167.0, "ask": 63177.0, "price": 63154.44},
        "simulate_market_fill": lambda side, qty: market_walk_calls.append((side, qty))
        or {
            "avg_price": 63154.44 if side == "sell" else 63189.0,
            "filled_qty": qty,
            "fully_filled": True,
            "partial_fill": False,
        },
    }
    resolve = _compile_function("resolve_sim_fill_with_depth", namespace)

    short_fill = resolve(
        {
            "side": "sell",
            "qty": 0.03163,
            "limit_price": 63167.0,
            "entry_type": "SIM_LIMIT",
        }
    )
    assert short_fill["fill_price"] == 63167.0
    assert short_fill["fill_price"] >= 63167.0
    assert short_fill["is_taker"] is True

    namespace["state"].update({"bid": 63157.0, "ask": 63167.0})
    long_fill = resolve(
        {
            "side": "buy",
            "qty": 0.03163,
            "limit_price": 63167.0,
            "entry_type": "SIM_LIMIT",
        }
    )
    assert long_fill["fill_price"] == 63167.0
    assert long_fill["fill_price"] <= 63167.0
    assert market_walk_calls == []

    market_fill = resolve(
        {
            "side": "sell",
            "qty": 0.03163,
            "limit_price": 63167.0,
            "entry_type": "SIM_MARKET",
        }
    )
    assert market_fill["fill_price"] == 63154.44
    assert market_walk_calls == [("sell", 0.03163)]


def test_marketable_fallback_requires_full_visible_depth_at_hard_limit():
    depth = _compile_function(
        "_marketable_limit_full_depth_available",
        {
            "state_lock": threading.RLock(),
            "state": {
                "order_book": {
                    "bids": [[63167.0, 1, 0.02], [63166.0, 1, 1.0]],
                    "asks": [[63177.0, 1, 0.05]],
                },
                "bid_size_btc": 0.02,
                "ask_size_btc": 0.05,
            },
        },
    )
    assert depth("SHORT", 63167.0, 0.03163) == (False, 0.02)
    assert depth("SHORT", 63166.0, 0.03163)[0] is True
    assert depth("LONG", 63177.0, 0.03163)[0] is True


def test_durable_relay_receipt_requires_persistence_and_platform_timestamp():
    receipt_ok = _compile_function("_relay_response_has_durable_receipt", {})
    expected = {
        "event_id": "cont-settle:LIMIT_UPDATED:4:token",
        "event_seq": 4,
        "trade_id": "cont-settle",
        "limit_price": 63167.0,
    }
    good = {
        "persisted": True,
        "intentCreated": True,
        "canonical_revision_applied": True,
        "canonical_event_id": expected["event_id"],
        "canonical_event_seq": 4,
        "canonical_trade_id": "cont-settle",
        "canonical_limit_price": 63167.0,
        "platform_received_at": "2026-08-02T13:05:00Z",
    }
    assert receipt_ok(good, expected) is True
    assert receipt_ok({**good, "canonical_limit_price": 63166.0}, expected) is False
    assert receipt_ok({**good, "canonical_revision_applied": False}, expected) is False
    assert receipt_ok({**good, "intentCreated": False}, expected) is False
    assert receipt_ok({**good, "persisted": False}, expected) is False
    assert receipt_ok({**good, "platform_received_at": None}, expected) is False
    assert receipt_ok(None) is False


def test_marketable_fallback_waits_for_relay_settlement_and_is_terminal():
    touched_calls = []
    ready = _compile_function(
        "_pending_limit_ready_for_fill",
        {
            "time": type("Clock", (), {"time": staticmethod(lambda: 110.0)}),
            "_pending_limit_touched": lambda *_args, **_kwargs: touched_calls.append(True)
            or False,
        },
    )
    order = {
        "marketable_fallback": True,
        "relay_settle_not_before_ts": 115.0,
        "relay_event_durable_ack": True,
    }
    assert ready(order, 63154.44, bid=63167.0, ask=63177.0, now=110.0) is False
    assert ready(order, 63154.44, bid=63167.0, ask=63177.0, now=115.0) is True
    order["relay_event_durable_ack"] = False
    assert ready(order, 63154.44, bid=63167.0, ask=63177.0, now=120.0) is False
    assert touched_calls == []

    tree = ast.parse(BOT_SOURCE)
    fallback = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_apply_marketable_limit_fallback"
    )
    fallback_source = ast.get_source_segment(BOT_SOURCE, fallback)
    assert 'order.get("marketable_fallback") or order.get("marketable_fallback_inflight")' in fallback_source
    assert "wait_for_durable_receipt=True" in fallback_source
    assert 'order["relay_event_durable_ack"] = True' in fallback_source
    assert 'signal["relay_settle_not_before_ts"] = order["relay_settle_not_before_ts"]' in fallback_source

    pending_source = ast.get_source_segment(
        BOT_SOURCE,
        next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "process_pending_orders"
        ),
    )
    assert "_pending_limit_ready_for_fill(" in pending_source


def test_marketable_fallback_requires_durable_receipt_before_mutation(monkeypatch):
    deliveries = []
    delivery_ok = {"value": False}
    block_delivery = {"value": False}
    delivery_entered = threading.Event()
    delivery_release = threading.Event()

    def push(event, trade_id, extra, *, wait_for_durable_receipt=False):
        deliveries.append((event, trade_id, dict(extra), wait_for_durable_receipt))
        if block_delivery["value"]:
            delivery_entered.set()
            assert delivery_release.wait(timeout=5)
        return delivery_ok["value"]

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

        def debug(self, *_args, **_kwargs):
            pass

    monkeypatch.setitem(
        sys.modules,
        "execution_funnel",
        types.SimpleNamespace(funnel_on_limit_chase=lambda *_args, **_kwargs: None),
    )
    apply_fallback = _compile_function(
        "_apply_marketable_limit_fallback",
        {
            "MARKETABLE_CHASE_SEC": 0,
            "MARKETABLE_CHASE_DISTANCE_USD": 10.0,
            "MARKETABLE_RELAY_SETTLE_SEC": 15,
            "_is_static_no_chase_order": lambda _order: False,
            "_normalize_order_side_to_dir": lambda _side: "SHORT",
            "_chase_structure_valid": lambda _direction: True,
            "_limit_chase_market_gap": lambda *_args: 20.0,
            "_marketable_limit_full_depth_available": lambda *_args: (True, 1.0),
            "refresh_bbo_state": lambda: None,
            "state_lock": threading.RLock(),
            "state": {"bid": 63167.0, "ask": 63177.0},
            "chase_bucket_allowed": lambda bucket: bucket == 4,
            "chase_count_bucket": lambda count: f"{count}_chases",
            "_cancel_pending_for_chase_gate": lambda *_args: None,
            "_push_showcase_relay_event": push,
            "_resolve_fill_model": lambda *_args: "AI_DIRECT_CHASE",
            "datetime": datetime,
            "timezone": timezone,
            "trade_lock": threading.RLock(),
            "uuid": __import__("uuid"),
            "time": types.SimpleNamespace(time=lambda: 100.5),
            "logger": Logger(),
            "fmt": str,
        },
    )
    touch_checks = []
    pending_limit_ready_for_fill = _compile_function(
        "_pending_limit_ready_for_fill",
        {
            "time": types.SimpleNamespace(time=lambda: 100.5),
            "_pending_limit_touched": (
                lambda *_args, **_kwargs: touch_checks.append(True) or True
            ),
        },
    )
    order = {
        "status": "PENDING",
        "created_ts": 1.0,
        "signal_dir": "SHORT",
        "side": "sell",
        "qty": 0.03163,
        "limit_price": 63225.8,
        "limit_chase_count": 3,
        "trade_id": "cont-settlement",
        "entry_limit_policy": "micro_sr_structural_limit_v1",
        "research_lane": "CONTINUOUS",
    }
    signal = {
        "entry_limit_policy": "micro_sr_structural_limit_v1",
        "research_lane": "CONTINUOUS",
    }

    assert apply_fallback(order, signal, 63154.44, 100.0) is False
    assert order["limit_price"] == 63225.8
    assert "marketable_fallback" not in order
    assert deliveries[-1][2]["marketable_fallback"] is True
    assert deliveries[-1][3] is True

    delivery_ok["value"] = True
    assert apply_fallback(order, signal, 63154.44, 100.0) is True
    assert order["limit_price"] == 63167.0
    assert order["limit_chase_count"] == 4
    assert order["marketable_fallback"] is True
    assert order["relay_event_durable_ack"] is True
    assert order["relay_settle_not_before_ts"] == 115.0
    assert signal["relay_event_durable_ack"] is True

    concurrent_order = {
        **order,
        "trade_id": "cont-concurrent",
        "status": "PENDING",
        "limit_price": 63225.8,
        "limit_chase_count": 3,
    }
    for key in (
        "marketable_fallback",
        "marketable_fallback_inflight",
        "relay_event_durable_ack",
        "relay_settle_not_before_ts",
    ):
        concurrent_order.pop(key, None)
    concurrent_signal = dict(signal)
    block_delivery["value"] = True
    delivery_release.clear()
    delivery_entered.clear()
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            apply_fallback(concurrent_order, concurrent_signal, 63154.44, 100.0)
        )
    )
    before = len(deliveries)
    worker.start()
    assert delivery_entered.wait(timeout=5)
    assert concurrent_order.get("marketable_fallback_inflight")
    assert (
        pending_limit_ready_for_fill(
            concurrent_order,
            63225.8,
            bid=63225.8,
            ask=63235.8,
            now=100.5,
        )
        is False
    )
    assert touch_checks == []
    assert apply_fallback(concurrent_order, concurrent_signal, 63154.44, 100.0) is False
    assert len(deliveries) == before + 1
    delivery_release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert results == [True]
    assert concurrent_order["marketable_fallback"] is True
    assert (
        pending_limit_ready_for_fill(
            concurrent_order,
            63167.0,
            bid=63167.0,
            ask=63177.0,
            now=114.999,
        )
        is False
    )
    assert (
        pending_limit_ready_for_fill(
            concurrent_order,
            63167.0,
            bid=63167.0,
            ask=63177.0,
            now=115.0,
        )
        is True
    )

    failed_order = {
        **concurrent_order,
        "trade_id": "cont-failed-inflight",
        "status": "PENDING",
        "limit_price": 63225.8,
        "limit_chase_count": 3,
    }
    for key in (
        "marketable_fallback",
        "marketable_fallback_inflight",
        "relay_event_durable_ack",
        "relay_settle_not_before_ts",
    ):
        failed_order.pop(key, None)
    failed_signal = dict(signal)
    delivery_ok["value"] = False
    delivery_entered.clear()
    delivery_release.clear()
    touch_checks.clear()
    failed_results = []
    failed_worker = threading.Thread(
        target=lambda: failed_results.append(
            apply_fallback(failed_order, failed_signal, 63154.44, 100.0)
        )
    )
    failed_worker.start()
    assert delivery_entered.wait(timeout=5)
    assert failed_order.get("marketable_fallback_inflight")
    assert (
        pending_limit_ready_for_fill(
            failed_order,
            63225.8,
            bid=63225.8,
            ask=63235.8,
            now=100.5,
        )
        is False
    )
    assert touch_checks == []
    delivery_release.set()
    failed_worker.join(timeout=5)
    assert not failed_worker.is_alive()
    assert failed_results == [False]
    assert "marketable_fallback_inflight" not in failed_order
    assert "marketable_fallback" not in failed_order
    assert (
        pending_limit_ready_for_fill(
            failed_order,
            63225.8,
            bid=63225.8,
            ask=63235.8,
            now=100.5,
        )
        is True
    )
    assert touch_checks == [True]

    cancelled_order = {
        **concurrent_order,
        "trade_id": "cont-cancel-race",
        "status": "PENDING",
        "limit_price": 63225.8,
        "limit_chase_count": 3,
    }
    for key in (
        "marketable_fallback",
        "marketable_fallback_inflight",
        "relay_event_durable_ack",
        "relay_settle_not_before_ts",
    ):
        cancelled_order.pop(key, None)
    delivery_entered.clear()
    delivery_release.clear()
    delivery_ok["value"] = True
    cancelled_results = []
    cancelled_worker = threading.Thread(
        target=lambda: cancelled_results.append(
            apply_fallback(cancelled_order, {}, 63154.44, 100.0)
        )
    )
    cancelled_worker.start()
    assert delivery_entered.wait(timeout=5)
    cancelled_order["status"] = "CANCELLED"
    delivery_release.set()
    cancelled_worker.join(timeout=5)
    assert cancelled_results == [False]
    assert cancelled_order["status"] == "CANCELLED"
    assert "marketable_fallback" not in cancelled_order


def test_deterministic_anchor_uses_0_1_pct_offset_not_local_support_resistance():
    """Danish decision 3 (2026-08-01) — the executable maker limit is always
    the deterministic 0.1% offset from the signal/reference price. Micro S/R
    levels must remain advisory only and never replace this anchor."""
    namespace = {
        "copy": copy,
        "state_lock": threading.RLock(),
        "state": {"support_resistance": {}},
        "build_micro_sr_levels": lambda: {},
        "MICRO_SR_ENTRY_BUFFER_USD": 15.0,
        "AI_DIRECT_MAX_DIST_PCT": 0.01,
        "DETERMINISTIC_ENTRY_OFFSET_PCT": 0.001,
    }
    resolve = _compile_function("resolve_local_structural_limit", namespace)

    long_limit, long_source, long_adv = resolve(
        "LONG",
        63000,
        market_structure={"micro_support": 62900},
        support_resistance={},
    )
    # LONG = 63000 * (1 - 0.001) = 62937.00 — NOT the micro support + buffer.
    assert long_limit == 62937.0
    assert long_source == "DETERMINISTIC_LONG_OFFSET"
    # Advisory micro S/R is still surfaced for the dashboard.
    assert long_adv["local_support_available"] is True
    assert long_adv["local_support_limit"] == 62915.0  # 62900 + 15 buffer

    short_limit, short_source, short_adv = resolve(
        "SHORT",
        63000,
        market_structure={"micro_resistance": 63100},
        support_resistance={},
    )
    # SHORT = 63000 * (1 + 0.001) = 63063.00 — NOT the micro resistance - buffer.
    assert short_limit == 63063.0
    assert short_source == "DETERMINISTIC_SHORT_OFFSET"
    assert short_adv["local_resistance_available"] is True
    assert short_adv["local_resistance_limit"] == 63085.0  # 63100 - 15 buffer

    # The deterministic anchor must NOT fail closed on missing micro S/R —
    # that was the bug that masked the wrong anchor. Direction + price is enough.
    no_sr_long, long_reason, no_sr_adv = resolve(
        "LONG",
        63000,
        market_structure={},
        support_resistance={},
    )
    assert no_sr_long == 62937.0
    assert long_reason == "DETERMINISTIC_LONG_OFFSET"
    assert no_sr_adv["local_support_available"] is False

    # Invalid direction / price still fails closed.
    invalid, invalid_reason, _ = resolve("LONG", 0, market_structure={})
    assert invalid is None
    assert invalid_reason == "INVALID_DIRECTION_OR_PRICE"

    # Advisory out-of-range flag is still recorded for transparency.
    far_long, _, far_adv = resolve(
        "LONG",
        64000,
        market_structure={"micro_support": 62000},
        support_resistance={},
    )
    assert far_long == 63936.0  # 64000 * 0.999
    assert far_adv["local_support_out_of_range"] is True


def test_exact_dashboard_chase_bucket_rests_before_next_chase():
    chase_calls = []
    namespace = {
        "is_virtual_chase_entry_lane": lambda _lane: False,
        "is_research_data_collection": lambda: True,
        "limit_chase_enabled": lambda: True,
        "state": {"price": 64000},
        "time": __import__("time"),
        "_limit_chase_eligible_order": lambda *_args: True,
        "_apply_limit_chase": lambda *_args: chase_calls.append(True) or True,
        "logger": type("Logger", (), {"info": staticmethod(lambda *_args: None)})(),
        "fmt": str,
    }
    first_chase = _compile_function("_try_immediate_first_chase", namespace)
    order = {
        "trade_id": "cont-chase3",
        "status": "PENDING",
        "entry_type": "SIM_LIMIT",
        "research_lane": "CONTINUOUS",
        "dashboard_exact_chase_managed": True,
        "limit_chase_count": 3,
    }
    assert first_chase(order, {}) is False
    assert chase_calls == []

    tree = ast.parse(BOT_SOURCE)
    place_fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_place_simulated_limit_order"
    )
    for variable in ("order_created_ts", "dashboard_exact_chase_managed"):
        stores = [
            node.lineno for node in ast.walk(place_fn)
            if isinstance(node, ast.Name)
            and node.id == variable
            and isinstance(node.ctx, ast.Store)
        ]
        loads = [
            node.lineno for node in ast.walk(place_fn)
            if isinstance(node, ast.Name)
            and node.id == variable
            and isinstance(node.ctx, ast.Load)
        ]
        assert stores
        assert loads
        assert min(stores) < min(loads)

    unrelated_tile = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_submit_tile2_paper_resting_limit"
    )
    assert not any(
        isinstance(node, ast.Name)
        and node.id in ("order_created_ts", "dashboard_exact_chase_managed")
        for node in ast.walk(unrelated_tile)
    )


def test_selected_virtual_chase_submits_chased_price_without_anchor_reset():
    """The first real/paper resting limit must be the selected virtual price.

    Regression for the live chase-2 observation where promotion recomputed the
    original deterministic anchor and therefore labelled an unchased price as
    chase 2.
    """
    namespace = {
        "Optional": __import__("typing").Optional,
        "ENTRY_MODE_AI_DIRECT": "AI_DIRECT_LIMIT",
        "dashboard_virtual_chase_submit_ready": lambda signal: int(
            signal.get("dashboard_virtual_chase_count") or 0
        ) in (2, 3, 4),
        "_signal_virtual_chase_count": lambda signal: int(
            signal.get("dashboard_virtual_chase_count") or 0
        ),
    }
    resolve = _compile_function("_resolve_selected_virtual_submit_limit", namespace)
    signal = {
        "dashboard_virtual_chase_count": 2,
        "chase_3plus_virtual_limit": 63214.54,
        "chase_3plus_original_limit": 63242.18,
        "limit_price": 63225.39,
        "entry_mode": "AI_DIRECT_LIMIT",
    }
    limit_price, entry_mode, smart_meta = resolve(signal)
    assert limit_price == 63214.54
    assert limit_price != signal["chase_3plus_original_limit"]
    assert entry_mode == "AI_DIRECT_LIMIT"
    assert smart_meta["original_planned"] == 63242.18
    assert smart_meta["preserve_original_limit"] is True
    assert smart_meta["selected_virtual_chase_count"] == 2

    captured = {}

    class QuietLogger:
        def info(self, *_args, **_kwargs):
            pass

    def place_order(_signal, submit_limit, submit_mode, smart_meta=None):
        captured.update(
            limit_price=submit_limit,
            entry_mode=submit_mode,
            smart_meta=dict(smart_meta or {}),
        )
        return True

    namespace.update(
        {
            "_manual_pause_block_entry": lambda *_args, **_kwargs: False,
            "state": {"price": 63000.0},
            "trades_map": {},
            "fills_first_continuous_enabled": lambda _signal: False,
            "evaluate_dashboard_execution_gate": lambda *_args, **_kwargs: (
                True,
                "ALLOWED",
                False,
            ),
            "_defer_dashboard_virtual_chase": lambda _signal: False,
            "_resolve_submit_limit_price": lambda _signal: (_ for _ in ()).throw(
                AssertionError("selected virtual promotion must not reset the anchor")
            ),
            "fmt": str,
            "logger": QuietLogger(),
            "_reject_duplicate_limit_order": lambda *_args, **_kwargs: False,
            "_place_simulated_limit_order": place_order,
        }
    )
    promote_runtime = _compile_function("_promote_signal_to_limit_order", namespace)
    assert promote_runtime(signal, skip_virtual_defer=True) is True
    assert captured["limit_price"] == 63214.54
    assert captured["entry_mode"] == "AI_DIRECT_LIMIT"
    assert captured["smart_meta"]["original_planned"] == 63242.18
    assert captured["smart_meta"]["preserve_original_limit"] is True

    tree = ast.parse(BOT_SOURCE)
    promote = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_promote_signal_to_limit_order"
    )
    promote_source = ast.get_source_segment(BOT_SOURCE, promote)
    assert "_resolve_selected_virtual_submit_limit(signal)" in promote_source
    assert "if skip_virtual_defer" in promote_source

    place = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_place_simulated_limit_order"
    )
    place_source = ast.get_source_segment(BOT_SOURCE, place)
    assert 'smart_meta.get("preserve_original_limit")' in place_source
    assert '"original_limit_price": original_limit_price' in place_source
    assert 'signal["original_limit_price"] = original_limit_price' in place_source


def test_active_shared_lanes_do_not_shift_the_qualified_structural_limit():
    assert "RESEARCH_LANE_CONTINUOUS: 0.0," in BOT_SOURCE
    assert "RESEARCH_LANE_TYPE_B_HUNTER_V1: 0.0," in BOT_SOURCE
    assert "A second lane offset here would make the" in BOT_SOURCE


def test_edge_is_telemetry_only_even_outside_paper_research_mode():
    profit_gate = _compile_function(
        "evaluate_profitability_entry_gates",
        {
            "EDGE_RESEARCH_TELEMETRY_ONLY": True,
            "get_edge_threshold": lambda: 3.0,
            "is_research_data_collection": lambda: False,
            "is_profit_gates_lane": lambda _lane: False,
            "dashboard_ai_band_blocks": lambda _prob: False,
        },
    )
    assert profit_gate({}, {}, 0.0, "CONTINUOUS") == (False, None)

    evidence_gate = _compile_function(
        "evaluate_evidence_entry_filter",
        {
            "EDGE_RESEARCH_TELEMETRY_ONLY": True,
            "is_research_data_collection": lambda: False,
            "_sole_ai_research_mode": lambda: False,
            "EDGE_DEAD_ZONE_LOW": 0.5,
            "EDGE_DEAD_ZONE_HIGH": 2.5,
            "RESEARCH_FREE_RUN_DISABLE_CHOP_GATE": True,
        },
    )
    assert evidence_gate("LONG", {}, {}, {}, 1.0) == (False, None)
    assert "if EDGE_RESEARCH_TELEMETRY_ONLY:" in BOT_SOURCE
    assert "not EDGE_RESEARCH_TELEMETRY_ONLY" in BOT_SOURCE


def test_pending_intent_is_non_executable_and_only_published_after_exact_limit_policy():
    tree = ast.parse(BOT_SOURCE)
    process_fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "process_signal"
    )
    execute_lines = [
        node.lineno
        for node in ast.walk(process_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "execute_simulated_order"
    ]
    pending_lines = [
        node.lineno
        for node in ast.walk(process_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "record_approve_outcome"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "PENDING"
    ]
    assert len(execute_lines) == 1
    assert pending_lines
    assert min(pending_lines) > execute_lines[0]

    webhook = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "emit_signal_webhook"
    )
    webhook_source = ast.get_source_segment(BOT_SOURCE, webhook)
    assert '"executable": event == "ORDER_PLACED"' in webhook_source
    assert '"entry_limit_policy": entry_limit_policy' in webhook_source
    # The webhook admits any canonical executable anchor policy (deterministic
    # 0.1% offset or the legacy structural limit) via is_executable_entry_policy.
    assert "is_executable_entry_policy(entry_limit_policy)" in webhook_source
    assert "EXECUTABLE_ENTRY_POLICY_VERSIONS" in BOT_SOURCE
    assert "DETERMINISTIC_ENTRY_POLICY_VERSION" in BOT_SOURCE
    assert '"pullback_pct"' not in webhook_source


def test_direction_only_current_ui_has_no_pullback_or_ai_confidence_control():
    assert 'id="pullbackThresh"' not in BOT_SOURCE
    assert "<th>AI Band</th>" not in BOT_SOURCE
    assert "AI Win Prob" not in BOT_SOURCE
    assert "continuous_shared_direction_gap_structural_v2" in BOT_SOURCE
    assert "DETERMINISTIC_LIMIT_BLOCKED" in BOT_SOURCE


def test_only_continuous_can_emit_platform_live_relay_lifecycle():
    tree = ast.parse(BOT_SOURCE)
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "PLATFORM_RELAY_ELIGIBLE_LANES"
            for target in node.targets
        )
    )
    assigned_source = ast.get_source_segment(BOT_SOURCE, assignment)
    assert "RESEARCH_LANE_CONTINUOUS" in assigned_source
    assert "RESEARCH_LANE_TYPE_B_HUNTER_V1" not in assigned_source


def test_market_bid_ask_spread_is_collected_separately():
    assert '"market_bid_ask_spread_usd_at_entry"' in BOT_SOURCE
    assert '"market_bid_ask_spread_bps_at_entry"' in BOT_SOURCE
    # Keep the contract tied to executable behavior rather than prose that can\n    # be reworded without changing how the legacy conviction spread is derived.\n    assert "def compute_directional_spread(direction: str, ai: dict) -> int:" in BOT_SOURCE\n    assert "return sign * (abs(raw_gap) // 10)" in BOT_SOURCE


def test_decision5_virtual_touch_before_selected_entry_state_exists():
    """Danish decision 5 — crossed virtual limit before first selected stage
    records VIRTUAL_TOUCH_BEFORE_SELECTED_ENTRY; no invented fill, no marketable
    order at the old price."""
    assert "VIRTUAL_TOUCH_BEFORE_SELECTED_ENTRY" in BOT_SOURCE
    assert '"virtual_touch_before_selected_entry"' in BOT_SOURCE
    assert "signal[\"virtual_touch_before_selected_entry\"] = (" in BOT_SOURCE

    # The crossed-limit path must remain wired into the virtual-chase loop.
    assert "if _virtual_limit_would_fill(signal, market):" in BOT_SOURCE
    assert "_expire_skipped_virtual_fill(signal)" in BOT_SOURCE

    # Decision 5 forbids: inventing an exchange fill or submitting a marketable
    # order at the old price. The expiring path must set EXPIRED + order_placed=False.
    tree = ast.parse(BOT_SOURCE)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_expire_skipped_virtual_fill"
    )
    src = ast.get_source_segment(BOT_SOURCE, fn)
    assert 'signal["status"] = "EXPIRED"' in src
    assert 'signal["order_placed"] = False' in src
    assert "min_enabled_chase_count()" in src
    assert "VIRTUAL_TOUCH_BEFORE_SELECTED_ENTRY" in src
    assert "VIRTUAL_FILL_SKIPPED_CHASE_" in src


def test_decision5_smart_submit_does_not_reset_chase_counter():
    """Smart-submit re-anchoring under the deterministic policy must NOT reset
    the chase counter or masquerade as Chase 0 (Danish decision 4 rule 10)."""
    tree = ast.parse(BOT_SOURCE)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_apply_smart_submit_limit"
    )
    src = ast.get_source_segment(BOT_SOURCE, fn)
    # The deterministic policy falls through to re-anchor (no bypass).
    assert "DETERMINISTIC_ENTRY_POLICY_VERSION" not in src.split("STRUCTURAL_ENTRY_POLICY_VERSION")[0]
    # Smart-submit sets smart_submit_reanchored True and planned_limit_price,
    # but must NOT touch limit_chase_count / dashboard_virtual_chase_count.
    assert "signal[\"smart_submit_reanchored\"] = True" in src
    assert "limit_chase_count" not in src
    assert "dashboard_virtual_chase_count" not in src


def test_decision6_dashboard_transparency_fields_are_emitted():
    """Danish decision 6 — every approved candidate exposes the 12 transparency
    fields on the dashboard snapshot."""
    required = (
        '"trade_id"',
        '"final_direction"',
        '"signal_price"',
        '"deterministic_initial_limit"',
        '"dashboard_virtual_chase_count"',
        '"limit_price"',
        '"virtual_touch_before_selected_entry"',
        '"smart_submit_reanchored"',
        '"exchange_order_id"',
        '"order_placed"',
        '"advisory_local_support_limit"',
        '"advisory_local_resistance_limit"',
    )
    for field in required:
        assert field in BOT_SOURCE, f"missing transparency field {field}"

    # Virtual candidate must never appear as an exchange pending order.
    assert "VIRTUAL ONLY" not in BOT_SOURCE
    assert "REAL_LIMIT_PENDING" in BOT_SOURCE
    assert "WAITING_VIRTUAL_CHASE" in BOT_SOURCE


def test_decision7_chase_effectiveness_prospective_reporting_preserved():
    """Danish decision 7 — chase effectiveness reporting remains observational
    and prospective (settings epoch recorded on chase change)."""
    assert "_record_execution_settings_epoch(\"CHASE_CHANGED\")" in BOT_SOURCE
    assert "CHASE_EFFECTIVENESS_REPORT_FILE" in BOT_SOURCE
    assert "CHASE_EFFICIENCY_MATRIX_FILE" in BOT_SOURCE
