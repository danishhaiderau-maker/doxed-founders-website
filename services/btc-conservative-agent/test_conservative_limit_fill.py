import importlib.util
from pathlib import Path


MODULE = Path(__file__).parent / "research" / "conservative_limit_fill.py"
spec = importlib.util.spec_from_file_location("conservative_limit_fill", MODULE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)
evaluate = mod.evaluate_limit_fill


def row(ts, *, bid=99, ask=101, bid_qty=2, ask_qty=2, buy_qty=0, sell_qty=0,
        buy_vwap=None, sell_vwap=None, fresh=True, valid=True, trade_count=None):
    if trade_count is None:
        trade_count = int(buy_qty > 0) + int(sell_qty > 0)
    return {
        "schema": "market_microstructure_1s_v1", "symbol": "BTC", "bucket_ts": ts,
        "fresh": fresh, "valid_bbo": valid, "bid": bid, "ask": ask,
        "bid_qty": bid_qty, "ask_qty": ask_qty, "buy_qty": buy_qty,
        "sell_qty": sell_qty, "buy_vwap": buy_vwap, "sell_vwap": sell_vwap,
        "trade_count": trade_count,
    }


def schedule(limit=100, start=100, end=105, bucket="chase_3"):
    return [{"bucket_id": bucket, "start_ts": start, "end_ts": end,
             "limit_price": limit, "generation": 3}]


def test_long_side_correct_full_fill():
    rows = [row(100), row(101), row(102, ask=100, ask_qty=2, sell_qty=1, sell_vwap=99.5)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103), symbol="BTC")
    assert got["outcome"] == "FILL"
    assert got["chase_bucket_id"] == "chase_3"
    assert got["fill_price"] == 100
    assert got["queue_position_model"] == "NONE"


def test_short_side_correct_full_fill():
    rows = [row(100), row(101), row(102, bid=100, bid_qty=2, buy_qty=1, buy_vwap=100.5)]
    got = evaluate(rows, direction="SHORT", requested_qty=1, chase_schedule=schedule(end=103), symbol="BTC")
    assert got["outcome"] == "FILL"


def test_insufficient_visible_depth_is_partial_not_full():
    rows = [row(100), row(101), row(102, ask=100, ask_qty=.4, sell_qty=2, sell_vwap=99)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "PARTIAL_FILL"
    assert got["filled_qty"] == .4 and got["remaining_qty"] == .6


def test_marketable_bbo_with_visible_depth_fills_without_aggressor_print():
    rows = [row(100), row(101), row(102, ask=100)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "FILL"
    assert got["aggressor_corroborated"] is False
    assert got["matching_aggressor_qty"] == 0


def test_high_precision_requested_qty_is_not_rounded_into_dust_partial():
    qty = 0.027490275315107282
    rows = [row(100), row(101), row(102, ask=100, ask_qty=1)]
    got = evaluate(rows, direction="LONG", requested_qty=qty, chase_schedule=schedule(end=103))
    assert got["outcome"] == "FILL"
    assert got["filled_qty"] == qty
    assert got["remaining_qty"] == 0


def test_thin_visible_depth_with_matching_aggressor_is_explicit_partial():
    rows = [row(100), row(101), row(102, ask=100, ask_qty=.25, sell_qty=.25, sell_vwap=100)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "PARTIAL_FILL" and got["filled_qty"] == .25


def test_gap_and_stale_fail_closed():
    rows = [row(100), row(102, ask=100, sell_qty=1, sell_vwap=100, fresh=False)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "UNSUPPORTED"
    assert "EVIDENCE_GAP" in got["negative_reasons"]
    assert "STALE_EVIDENCE_BUCKET" in got["negative_reasons"]


def test_chase_interval_is_authoritative_and_reported():
    sched = schedule(limit=99, start=100, end=103, bucket="chase_3") + schedule(limit=101, start=103, end=106, bucket="chase_4")
    rows = [row(ts) for ts in range(100, 103)] + [row(103, ask=101, sell_qty=1, sell_vwap=101), row(104), row(105)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=sched, aggressor_window_sec=1)
    assert got["outcome"] == "FILL" and got["chase_bucket_id"] == "chase_4"
    assert got["limit_price"] == 101


def test_chase_boundary_window_ambiguity_fails_closed():
    sched = schedule(start=100, end=102, bucket="chase_3") + schedule(start=102, end=104, bucket="chase_4")
    rows = [row(100), row(101), row(102, ask=100, sell_qty=1, sell_vwap=100), row(103)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=sched)
    assert got["outcome"] == "UNSUPPORTED"
    assert "CHASE_INTERVAL_WINDOW_AMBIGUOUS" in got["negative_reasons"]


def test_missing_aggressor_price_does_not_override_marketable_bbo_proof():
    rows = [row(100), row(101), row(102, ask=100, sell_qty=1, sell_vwap=None)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "FILL"
    assert got["aggressor_corroborated"] is False


def test_multi_print_vwap_is_not_needed_when_marketable_bbo_is_proven():
    rows = [row(100), row(101), row(102, ask=100, sell_qty=2, sell_vwap=99, trade_count=2)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "FILL"
    assert got["aggressor_corroborated"] is False


def test_prior_uncrossed_print_does_not_matter_when_later_bbo_is_marketable():
    rows = [
        row(100, ask=101, sell_qty=2, sell_vwap=99),
        row(101, ask=101),
        row(102, ask=100),
    ]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "FILL"
    assert got["filled_qty"] == 1
    assert got["aggressor_corroborated"] is False


def test_prior_depth_does_not_support_current_print():
    rows = [
        row(100, ask=100, ask_qty=2),
        row(101, ask=100, ask_qty=2),
        row(102, ask=100, ask_qty=.2, sell_qty=2, sell_vwap=100),
    ]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "PARTIAL_FILL"
    assert got["filled_qty"] == .2
    assert got["trigger_bucket_ts"] == 102
    assert got["visible_executable_qty"] == .2


def test_last_price_touch_without_opposite_bbo_cross_is_not_a_fill():
    rows = [row(100), row(101), row(102, ask=101, sell_qty=1, sell_vwap=100)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "NO_FILL"
    assert got["filled_qty"] == 0
    assert "BBO_NEVER_CROSSED_LIMIT" in got["negative_reasons"]


def test_marketable_bbo_thin_depth_is_partial_without_print():
    rows = [row(100), row(101), row(102, ask=100, ask_qty=.125)]
    got = evaluate(rows, direction="LONG", requested_qty=1, chase_schedule=schedule(end=103))
    assert got["outcome"] == "PARTIAL_FILL"
    assert got["filled_qty"] == .125
    assert got["remaining_qty"] == .875
    assert got["aggressor_corroborated"] is False
