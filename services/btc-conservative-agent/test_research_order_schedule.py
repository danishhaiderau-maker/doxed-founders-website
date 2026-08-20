import json
from pathlib import Path

from collector_v22 import build_research_event
from research_order_schedule import (
    append_reprice_interval,
    close_order_schedule,
    initialize_order_schedule,
    schedule_snapshot,
)


def pending():
    return {
        "trade_id": "cont-1", "status": "PENDING", "signal_dir": "LONG",
        "qty": .25, "limit_price": 99, "signal_price": 100,
        "limit_chase_count": 3,
    }


def test_actual_registration_initializes_authoritative_interval_on_order_and_signal():
    order, signal = pending(), {"trade_id": "cont-1", "final_direction": "LONG", "signal_price": 100}
    schedule = initialize_order_schedule(order, signal, now=100.25, registered=True)
    assert schedule["authoritative"] is True
    assert schedule["source"] == "SHOWCASE_PAPER_PENDING_ORDER"
    assert schedule["requested_qty"] == .25
    assert schedule["intervals"][0] == {
        "bucket_id": "cont-1:chase:3:0", "start_ts": 100, "start_ts_exact": 100.25,
        "end_ts": None, "chase_step_index": 3, "generation": 3,
        "reference_price": 100.0, "limit_price": 99.0, "offset_pct": 1.0,
        "reason": "ORDER_REGISTERED",
    }
    assert order["research_chase_schedule"] is signal["research_chase_schedule"]


def test_multiple_real_reprices_close_prior_and_append_next():
    order, signal = pending(), {}
    initialize_order_schedule(order, signal, now=100.1, registered=True)
    append_reprice_interval(order, signal, now=200.2, chase_step_index=4,
                            reference_price=101, limit_price=99.5, reason="LIMIT_CHASE")
    append_reprice_interval(order, signal, now=300.3, chase_step_index=5,
                            reference_price=102, limit_price=100, reason="LIMIT_CHASE")
    rows = order["research_chase_schedule"]["intervals"]
    assert [(r["start_ts"], r["end_ts"], r["chase_step_index"]) for r in rows] == [
        (100, 200, 3), (200, 300, 4), (300, None, 5),
    ]
    assert rows[1]["reference_price"] == 101.0 and rows[1]["offset_pct"] == 1.48514851


def test_fill_cancel_or_ttl_closes_final_interval():
    for reason in ("FILLED", "CANCELLED", "TTL_EXPIRED"):
        order, signal = pending(), {}
        initialize_order_schedule(order, signal, now=100, registered=True)
        close_order_schedule(order, signal, now=180.75, reason=reason)
        schedule = order["research_chase_schedule"]
        assert schedule["intervals"][-1]["end_ts"] == 180
        assert schedule["terminal_reason"] == reason
        assert schedule["terminal_ts"] == 180


def test_json_restart_and_provisional_style_copy_preserve_schedule():
    order, signal = pending(), {}
    initialize_order_schedule(order, signal, now=100, registered=True)
    append_reprice_interval(order, signal, now=200, chase_step_index=4,
                            reference_price=100, limit_price=99.5, reason="LIMIT_CHASE")
    restored = json.loads(json.dumps({
        "qty": order["qty"],
        "research_chase_schedule": schedule_snapshot(order),
        "chase_schedule_authoritative": True,
    }))
    assert restored["research_chase_schedule"] == order["research_chase_schedule"]
    restored_order = {**pending(), **restored}
    restored_signal = {}
    initialize_order_schedule(restored_order, restored_signal, now=250, registered=True)
    assert len(restored_order["research_chase_schedule"]["intervals"]) == 2
    assert restored_signal["research_chase_schedule"] is restored_order["research_chase_schedule"]


def test_virtual_only_wait_never_becomes_authoritative():
    virtual = {
        "trade_id": "virtual-1", "status": "AWAITING_DASHBOARD_CHASE",
        "limit_price": 99, "qty": 1, "dashboard_virtual_chase_count": 3,
    }
    assert initialize_order_schedule(virtual, {}, now=100, registered=False) is None
    assert "research_chase_schedule" not in virtual
    assert append_reprice_interval(virtual, {}, now=200, chase_step_index=4,
                                   reference_price=100, limit_price=99.5,
                                   reason="VIRTUAL_CHASE") is None


def test_real_order_hidden_then_registered_again_keeps_prior_closed_interval():
    first, signal = pending(), {"signal_price": 100}
    initialize_order_schedule(first, signal, now=100, registered=True)
    close_order_schedule(first, signal, now=150, reason="VIRTUAL_CHASE_HIDE")
    second = {**pending(), "limit_price": 99.5, "limit_chase_count": 4}
    initialize_order_schedule(second, signal, now=200, registered=True)
    rows = signal["research_chase_schedule"]["intervals"]
    assert len(rows) == 2
    assert rows[0]["end_ts"] == 150
    assert rows[1]["start_ts"] == 200 and rows[1]["end_ts"] is None
    assert second["research_chase_schedule"] is signal["research_chase_schedule"]


def test_exact_qty_and_closed_schedule_reach_new_v22_record():
    order, signal = pending(), {}
    initialize_order_schedule(order, signal, now=100, registered=True)
    append_reprice_interval(order, signal, now=200, chase_step_index=4,
                            reference_price=100, limit_price=99.5, reason="LIMIT_CHASE")
    close_order_schedule(order, signal, now=300, reason="TTL_EXPIRED")
    event = build_research_event(
        trade_id="cont-1", epoch_id="epoch", signal_ts=100, signal_price=100,
        direction="LONG", requested_qty=order["qty"],
        chase_schedule=order["research_chase_schedule"], candles_1m=[],
    )
    assert event["research_execution_basis"]["requested_qty"] == .25
    assert event["research_execution_basis"]["requested_qty_provenance"] == "SOURCE_TICKET_QTY"
    assert event["research_chase_schedule"]["authoritative"] is True
    assert len(event["research_chase_schedule"]["intervals"]) == 2
    assert event["research_chase_schedule"]["intervals"][-1]["end_ts"] == 300


def test_bot_wires_only_post_registration_and_real_lifecycle_boundaries():
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    lane_start = source.index("def lane_register_pending_order")
    lane_body = source[lane_start:lane_start + 5_000]
    assert "initialize_research_order_schedule" in lane_body
    assert "registered=True" in lane_body
    assert "def _commit_relay_limit_chase" in source
    assert "append_research_reprice_interval" in source[source.index("def _commit_relay_limit_chase"):source.index("def _apply_urgent_marketable_chase")]
    assert "close_research_order_schedule" in source[source.index("def _cancel_pending_order_confirmed"):source.index("def _log_shadow_vs_live_entry")]
