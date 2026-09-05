"""Execute real pause/cancellation functions without importing a trading owner."""
import ast
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest


BOT = Path(__file__).with_name("bot.py")


def fixture(status="PENDING", oid=None, record_failure=False, exchange_result=False):
    order = {"trade_id": "raced-paper", "status": status, "research_lane": "TEST",
             "fill_handoff_in_progress": True, "fill_price": 64000.0, "qty": 0.01}
    if oid:
        order["bitfinex_order_id"] = oid
    pending = [order]
    buckets = {"TEST": [order]}
    events = []
    handoffs = {order["trade_id"]}

    def record(row, reason):
        # Removal must happen only after the durable recorder returns.
        assert row in pending and row in buckets["TEST"]
        assert row["status"] not in ("CANCELLED", "EXPIRED")
        events.append(("record", reason))
        if record_failure:
            raise RuntimeError("DURABLE_PREPARE_FAILED")
        return {"recorded": True}

    def cancel(row):
        assert not ns["trade_lock"]._is_owned()
        events.append(("private_cancel", row["bitfinex_order_id"]))
        if exchange_result == "fill_race":
            row["status"] = "FILLED_ON_EXCHANGE"
            return True
        return exchange_result

    ns = {
        "threading": threading, "time": time, "trade_lock": threading.RLock(),
        "pending_orders": pending, "lane_pending_orders": buckets,
        "fill_handoff_trade_ids": handoffs, "trades_map": {},
        "manual_admin_pause_active": lambda: True,
        "logger": SimpleNamespace(warning=lambda *a: None, critical=lambda *a: None),
        "pipeline_state_sync": lambda: events.append(("sync", None)),
        "_record_expired_order": record,
        "expire_signal_for_order": lambda row, reason: events.append(("expire", reason)),
        "_normalize_lane_key": lambda row: "TEST", "_ensure_lane_bucket": lambda row: "TEST",
        "_append_paper_action_receipt": lambda *a, **k: events.append(("action", k["action_type"])),
        "close_research_order_schedule": lambda *a, **k: events.append(("schedule", k["reason"])),
        "_refresh_collector_v22_registered_order_evidence": lambda *a, **k: events.append(("refresh", k["lifecycle_final"])),
        "_maybe_bitfinex_cancel": cancel,
    }
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name in ("fill_order", "_cancel_pending_order_confirmed")]
    assert len(selected) == 2
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT), "exec"), ns)
    return ns, order, events


def assert_released(ns, order):
    assert order["trade_id"] not in ns["fill_handoff_trade_ids"]
    assert "fill_handoff_in_progress" not in order


def test_paper_pause_cancels_through_recorder_before_book_removal():
    ns, order, events = fixture()
    assert ns["fill_order"](order) is None
    assert not ns["pending_orders"] and not ns["lane_pending_orders"]["TEST"]
    assert order["status"] == "CANCELLED"
    assert events.count(("record", "ADMIN_MANUAL_PAUSE")) == 1
    assert events.count(("schedule", "ADMIN_MANUAL_PAUSE")) == 1
    assert events.count(("expire", "ADMIN_MANUAL_PAUSE")) == 1
    assert not any(event[0] == "private_cancel" for event in events)
    assert_released(ns, order)


def test_durable_failure_retains_order_and_does_not_publish_signal_expiry():
    ns, order, events = fixture(record_failure=True)
    with pytest.raises(RuntimeError, match="DURABLE_PREPARE_FAILED"):
        ns["fill_order"](order)
    assert order in ns["pending_orders"] and order in ns["lane_pending_orders"]["TEST"]
    assert order["status"] == "PENDING"
    assert not any(event[0] == "expire" for event in events)
    assert_released(ns, order)


def test_unconfirmed_exchange_cancel_retains_handle_without_terminal_evidence():
    ns, order, events = fixture(oid="exchange-123")
    assert ns["fill_order"](order) is None
    assert order in ns["pending_orders"] and order in ns["lane_pending_orders"]["TEST"]
    assert order["status"] == "CANCEL_PENDING_LIVE"
    assert order["bitfinex_order_id"] == "exchange-123"
    assert not any(event[0] in ("record", "schedule", "refresh", "expire") for event in events)
    assert_released(ns, order)


def test_exchange_fill_winning_cancel_race_is_never_relabelled_cancelled():
    ns, order, events = fixture(oid="exchange-123", exchange_result="fill_race")
    assert ns["fill_order"](order) is None
    assert order in ns["pending_orders"]
    assert order["status"] == "FILLED_ON_EXCHANGE" and order["bitfinex_order_id"] == "exchange-123"
    assert not any(event[0] in ("record", "schedule", "refresh", "expire") for event in events)
    assert_released(ns, order)


@pytest.mark.parametrize("status", ["FILLED", "FILLED_ON_EXCHANGE", "OPEN"])
def test_already_filled_state_is_not_fabricated_into_cancellation(status):
    ns, order, events = fixture(status=status, oid="exchange-123")
    assert ns["fill_order"](order) is None
    assert order["status"] == status and order in ns["pending_orders"]
    assert not any(event[0] in ("private_cancel", "record", "schedule", "expire") for event in events)
    assert_released(ns, order)


def test_fixture_matches_current_touch_handoff_before_open_commit():
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    process = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "process_pending_orders")
    source = ast.get_source_segment(BOT.read_text(encoding="utf-8"), process)
    handoff = source[source.index('order["fill_handoff_in_progress"] = True'):]
    assert "fills.append((order, fill_signal))" in handoff
    assert "fill_order(order)" in handoff
    assert 'order["status"] = "FILLED"' not in handoff
    assert "lane_unregister_pending_order(order)" not in handoff
