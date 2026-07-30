import ast
import threading
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
    assert "<h3>Directional gap analytics</h3>" in BOT_SOURCE


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


def test_market_bid_ask_spread_is_collected_separately():
    assert '"market_bid_ask_spread_usd_at_entry"' in BOT_SOURCE
    assert '"market_bid_ask_spread_bps_at_entry"' in BOT_SOURCE
    assert "the older “conviction spread” is this same normalized AI gap" in BOT_SOURCE
