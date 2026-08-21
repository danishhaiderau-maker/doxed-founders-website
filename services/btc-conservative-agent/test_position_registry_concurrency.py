import threading
from pathlib import Path

from position_registry import (
    claim_position_close,
    finalize_position_close,
    promote_pending_to_open,
)


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _api_trade_snapshot(trade_lock, pending, opened):
    """The lock-sensitive portion shared by state/reset status snapshots."""
    with trade_lock:
        return {"pending": len(pending), "open": len(opened)}


def test_concurrent_duplicate_touches_create_exactly_one_open_position():
    trade_lock = threading.RLock()
    orders = [
        {"trade_id": "same", "status": "FILLED", "touch": number}
        for number in (1, 2)
    ]
    pending = list(orders)
    lane_pending = {"CONTINUOUS": list(orders)}
    opened = []
    lane_opened = {"CONTINUOUS": []}
    barrier = threading.Barrier(3)
    results = []

    def promote(number):
        barrier.wait()
        results.append(promote_pending_to_open(
            orders[number - 1],
            {"trade_id": "same", "status": "OPEN", "candidate": number},
            trade_lock=trade_lock, pending_orders=pending,
            lane_pending_orders=lane_pending, open_positions=opened,
            lane_open_positions=lane_opened, lane="CONTINUOUS",
        ))

    threads = [threading.Thread(target=promote, args=(number,)) for number in (1, 2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(1)
    assert len(opened) == 1
    assert lane_opened["CONTINUOUS"] == opened
    assert pending == []
    assert lane_pending["CONTINUOUS"] == []
    assert sorted(created for _row, created in results) == [False, True]


def test_slow_fill_callback_does_not_block_trade_snapshot():
    trade_lock = threading.RLock()
    order = {"trade_id": "fill-1"}
    position = {"trade_id": "fill-1", "status": "OPEN"}
    opened = []
    promote_pending_to_open(
        order, position, trade_lock=trade_lock, pending_orders=[order],
        lane_pending_orders={"PATIENT": [order]}, open_positions=opened,
        lane_open_positions={"PATIENT": []}, lane="PATIENT",
    )
    callback_started = threading.Event()
    callback_release = threading.Event()

    def slow_callback():
        callback_started.set()
        assert callback_release.wait(1)

    thread = threading.Thread(target=slow_callback)
    thread.start()
    assert callback_started.wait(1)
    assert _api_trade_snapshot(trade_lock, [], opened) == {"pending": 0, "open": 1}
    callback_release.set()
    thread.join(1)


def test_slow_close_callback_keeps_snapshot_available_and_close_exactly_once():
    trade_lock = threading.RLock()
    close_lock = threading.RLock()
    position = {"trade_id": "close-1", "status": "OPEN"}
    opened = [position]
    lanes = {"CONTINUOUS": [position]}
    assert claim_position_close(
        position, position_close_lock=close_lock, open_positions=opened,
    ) is True
    assert claim_position_close(
        position, position_close_lock=close_lock, open_positions=opened,
    ) is False

    callback_started = threading.Event()
    callback_release = threading.Event()

    def slow_persistence():
        callback_started.set()
        assert callback_release.wait(1)

    thread = threading.Thread(target=slow_persistence)
    thread.start()
    assert callback_started.wait(1)
    assert _api_trade_snapshot(trade_lock, [], opened) == {"pending": 0, "open": 1}
    assert close_lock.acquire(timeout=0.1)
    close_lock.release()
    callback_release.set()
    thread.join(1)

    assert finalize_position_close(
        position, position_close_lock=close_lock, trade_lock=trade_lock,
        open_positions=opened, lane_open_positions=lanes, lane="CONTINUOUS",
    ) is True
    assert finalize_position_close(
        position, position_close_lock=close_lock, trade_lock=trade_lock,
        open_positions=opened, lane_open_positions=lanes, lane="CONTINUOUS",
    ) is False
    assert position["status"] == "CLOSED"
    assert opened == []
    assert lanes["CONTINUOUS"] == []


def test_fill_and_close_callbacks_are_after_narrow_registry_transitions():
    fill_start = BOT_SOURCE.index("def fill_order(order):")
    fill_end = BOT_SOURCE.index("\ndef _observable_exit_price", fill_start)
    fill_body = BOT_SOURCE[fill_start:fill_end]
    assert "promote_pending_to_open(" in fill_body
    assert "with trade_lock:\n        if order in pending_orders:" not in fill_body
    assert "lane_register_open_position(pos)" not in fill_body
    assert fill_body.index("promote_pending_to_open(") < fill_body.index(
        "dual_write_paper_fill("
    )

    close_start = BOT_SOURCE.index("def close_position(pos: dict, exit_reason: str):")
    close_end = BOT_SOURCE.index("\ndef _client_ip(", close_start)
    close_body = BOT_SOURCE[close_start:close_end]
    assert "with PositionCloseClaimScope(" in close_body
    assert "with position_close_lock:" not in close_body
    assert "finalize_position_close(" in close_body
    assert close_body.index("finalize_position_close(") < close_body.index(
        "log_trade_outcome_jsonl("
    )
