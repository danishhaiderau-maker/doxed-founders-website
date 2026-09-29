import ast
from pathlib import Path

from research_order_schedule import append_action_timing_receipt, initialize_order_schedule


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _function_source(name):
    node = next(
        item for item in BOT_TREE.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    return ast.get_source_segment(BOT_SOURCE, node)


def _scheduled_order():
    order = {
        "trade_id": "paper-timing-1", "status": "PENDING", "signal_dir": "LONG",
        "qty": 1.0, "remaining_qty": 1.0, "limit_price": 100.0,
    }
    signal = {"trade_id": order["trade_id"], "final_direction": "LONG"}
    initialize_order_schedule(order, signal, now=10.0, registered=True)
    return order, signal


def _append(order, signal, generation, action, ts):
    return append_action_timing_receipt(
        order, signal,
        action_generation=generation, action_type=action,
        policy_due_ts=ts, eligibility_ts=ts,
        dispatch_start_ts=ts, acknowledgement_ts=ts,
        remaining_qty=order["remaining_qty"], limit_price=order["limit_price"],
        non_intentional_delay={
            "classification": "UNKNOWN",
            "cause": "UNSUPPORTED_RUNTIME_DELAY_ATTRIBUTION",
        },
    )


def test_successful_action_chronology_is_immutable_and_unknown_by_default():
    order, signal = _scheduled_order()
    rows = [
        _append(order, signal, 0, "INITIAL_SUBMIT", 10.0),
        _append(order, signal, 1, "CHASE_REPRICE", 20.0),
        _append(order, signal, 2, "FILL_PROMOTED", 30.0),
        _append(order, signal, 3, "CLOSE_CLAIMED", 40.0),
    ]
    assert all(rows)
    assert [row["action_type"] for row in rows] == [
        "INITIAL_SUBMIT", "CHASE_REPRICE", "FILL_PROMOTED", "CLOSE_CLAIMED"
    ]
    assert all(row["non_intentional_delay"]["classification"] == "UNKNOWN" for row in rows)
    assert all("seconds" not in row["non_intentional_delay"] for row in rows)


def test_failed_or_conflicting_action_does_not_append_and_replay_is_idempotent():
    order, signal = _scheduled_order()
    first = _append(order, signal, 0, "INITIAL_SUBMIT", 10.0)
    replay = _append(order, signal, 0, "INITIAL_SUBMIT", 10.0)
    conflict = _append(order, signal, 0, "INITIAL_SUBMIT", 11.0)
    invalid = append_action_timing_receipt(
        order, signal, action_generation=1, action_type="CHASE_REPRICE",
        policy_due_ts=20.0, eligibility_ts=20.0, dispatch_start_ts=19.0,
    )
    assert replay is first
    assert conflict is None
    assert invalid is None
    assert len(order["research_chase_schedule"]["action_timing_receipts"]) == 1


def test_runtime_emits_only_after_success_boundaries():
    register = _function_source("lane_register_pending_order")
    create = _function_source("create_limit_order")
    chase = _function_source("_apply_limit_chase")
    cancel = _function_source("_cancel_pending_order_confirmed")
    assert register.index("pending_orders.append(order)") < register.index('action_type="INITIAL_SUBMIT"')
    assert create.index("registered = lane_register_pending_order(order)") < create.index("if not registered:")
    assert chase.index("if relay_event is None:") < chase.index('action_type="CHASE_REPRICE"')
    assert cancel.index('failure_reason="CANCEL_UNCONFIRMED"') < cancel.index('action_type="CANCEL_CONFIRMED"')


def test_terminal_filled_schedule_is_synchronized_before_fill_and_close_writes():
    fill = _function_source("fill_order")
    close = _function_source("close_position")
    open_finalize = fill.index("fill_snapshot = _finalize_position_open_lifecycle(")
    lost_open_guard = fill.index("if fill_snapshot is None:")
    lost_open_return = fill.index("return", lost_open_guard)
    fill_commit = fill.index("fill_commit_ts = time.time()", lost_open_return)
    schedule_close = fill.index("schedule_close(", fill_commit)
    position_sync = fill.index('pos["research_chase_schedule"] = terminal_schedule')
    receipt = fill.index('action_type="FILL_PROMOTED"')
    dual_fill = fill.index("dual_write_paper_fill(")
    assert open_finalize < lost_open_guard < lost_open_return < fill_commit < schedule_close
    assert schedule_close < position_sync < receipt < dual_fill
    assert close.index('action_type="CLOSE_CLAIMED"') < close.index("dual_write_paper_close(")
    assert 'action_type="CLOSE_CLAIMED"' in close


def test_lost_open_race_returns_before_any_filled_evidence_side_effect():
    fill = _function_source("fill_order")
    guard = fill.index("if fill_snapshot is None:")
    guarded_return = fill.index("return", guard)
    fill_commit = fill.index("fill_commit_ts = time.time()", guarded_return)
    assert guarded_return < fill_commit
    assert fill_commit < fill.index("schedule_close(", fill_commit)
    assert guarded_return < fill.index('action_type="FILL_PROMOTED"')
    assert guarded_return < fill.index("dual_write_paper_fill(")


def test_full_fill_preserves_explicit_zero_remaining_quantity():
    order, signal = _scheduled_order()
    order["remaining_qty"] = 0.0
    receipt = append_action_timing_receipt(
        order, signal,
        action_generation=1, action_type="FILL_PROMOTED",
        policy_due_ts=20.0, eligibility_ts=20.0,
        dispatch_start_ts=20.0, acknowledgement_ts=20.0,
        fill_ts=20.0, fill_price=100.0, filled_qty=1.0,
        remaining_qty=0.0, limit_price=100.0,
        non_intentional_delay={"classification": "UNKNOWN"},
    )
    assert receipt is not None
    assert receipt["remaining_qty"] == 0.0


def test_confirmed_cancel_has_distinct_terminal_identity():
    order, signal = _scheduled_order()
    cancel = _append(order, signal, 1, "CANCEL_CONFIRMED", 20.0)
    raced_fill = _append(order, signal, 1, "CANCEL_CONFIRMED", 21.0)
    assert cancel is not None
    assert raced_fill is None
    assert len(order["research_chase_schedule"]["action_timing_receipts"]) == 1
