import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from research.conservative_limit_fill import (  # noqa: E402
    FILL_MODEL, SHADOW_FILL_MODEL, SHADOW_ROLE, evaluate_limit_fill,
)
from research.quantity_execution import build_signed_quantity_constraints  # noqa: E402

CONSTRAINTS = build_signed_quantity_constraints(
    symbol="BTC", quantity_step="0.0001", quantity_precision=4, min_lot="0.0001", min_notional="0.01",
    captured_at="2026-10-03T00:00:00Z", source_revision="test", source="TEST_FIXTURE")


def row(ts, *, bid=99.0, ask=101.0, bid_qty=2.0, ask_qty=2.0, buy_qty=0.0, sell_qty=0.0,
        buy_vwap=None, sell_vwap=None):
    return {"schema": "market_microstructure_1s_v1", "symbol": "BTC", "bucket_ts": ts, "fresh": True,
            "valid_bbo": True, "bid": bid, "ask": ask, "bid_qty": bid_qty, "ask_qty": ask_qty,
            "buy_qty": buy_qty, "sell_qty": sell_qty, "buy_vwap": buy_vwap, "sell_vwap": sell_vwap,
            "trade_count": int(buy_qty > 0) + int(sell_qty > 0), "trade_bucket_complete": True,
            "source_ts": ts + 0.2, "observed_at_ts": ts + 0.3}


def fill(rows, side="LONG", qty=1.0, start=100, end=106, limit=100.0, **kw):
    return evaluate_limit_fill(rows, direction=side, requested_qty=qty, symbol="BTC", quantity_constraints=CONSTRAINTS,
                               chase_schedule=[{"bucket_id": "c1", "start_ts": start, "end_ts": end,
                                                "limit_price": limit}], **kw)


def test_bbo_cross_without_print_is_not_a_realistic_fill_but_is_the_shadow():
    rows = [row(t) for t in range(97, 106)]
    rows[6] = row(103, ask=100.0)  # resting buy at 100: ask drops to the limit with no sell print
    got = fill(rows)
    assert got["fill_model"] == FILL_MODEL and got["outcome"] == "NO_FILL"
    assert got["diagnostics"]["bbo_cross_without_print"] == 1
    assert "BBO_CROSS_WITHOUT_PRINT_NOT_A_FILL" in got["negative_reasons"]
    shadow = got["optimistic_shadow"]
    assert shadow["fill_model"] == SHADOW_FILL_MODEL and shadow["fill_model_role"] == SHADOW_ROLE
    assert shadow["outcome"] == "FILL" and shadow["trigger_bucket_ts"] == 103


def test_trade_through_print_fills_bounded_by_print_quantity():
    rows = [row(t) for t in range(97, 106)]
    rows[6] = row(103, ask=100.0, sell_qty=0.4, sell_vwap=99.5)
    got = fill(rows)
    assert got["outcome"] == "PARTIAL_FILL" and got["filled_qty"] == 0.4
    assert got["fill_basis"] == "TRADE_THROUGH_PRINT" and got["fill_price"] == 100.0
    assert got["aggressor_time_semantics"] == "PRINT_IS_FILL_TRIGGER"
    assert got["optimistic_shadow"]["outcome"] == "FILL"


def test_marketable_at_placement_fills_against_visible_depth():
    rows = [row(t) for t in range(97, 106)]
    rows[5] = row(102, ask=99.5, ask_qty=3.0)
    got = fill(rows)
    assert got["outcome"] == "FILL" and got["fill_basis"] == "MARKETABLE_AT_PLACEMENT"
    assert abs(got["fill_latency_sec"] - 2.3) < 1e-9


def test_short_mirror_and_explicit_shadow_mode():
    rows = [row(t) for t in range(97, 106)]
    rows[6] = row(103, bid=100.0, buy_qty=1.5, buy_vwap=100.5)
    got = fill(rows, side="SHORT")
    assert got["outcome"] == "FILL" and got["fill_basis"] == "TRADE_THROUGH_PRINT"
    legacy = fill(rows, side="SHORT", fill_model=SHADOW_FILL_MODEL)
    assert legacy["fill_model"] == SHADOW_FILL_MODEL and legacy["optimistic_shadow"] is None
    assert fill(rows, fill_model="IDEAL")["negative_reasons"] == ["UNKNOWN_FILL_MODEL"]


def test_live_tape_rows_without_trade_completeness_flag_still_count_prints():
    # microstructure_tape.build_bucket (the live writer) never emits
    # trade_bucket_complete; only an explicit non-True value marks a partial bucket.
    import microstructure_tape

    def live(ts, **kw):
        trades = kw.pop("trades", ())
        return microstructure_tape.build_bucket(bucket_ts=ts, bid=kw.get("bid", 99.0), ask=kw.get("ask", 101.0),
                                                bid_qty=2.0, ask_qty=2.0, last=100.0, source_ts=ts + 0.2,
                                                trades=trades, symbol="BTC")

    rows = [live(t) for t in range(97, 106)]
    rows[6] = live(103, ask=100.0, trades=[{"received_ts": 103.5, "p": 99.5, "v": 0.4, "S": "SELL"}])
    assert "trade_bucket_complete" not in rows[6]
    got = fill(rows)
    assert got["outcome"] == "PARTIAL_FILL" and got["fill_basis"] == "TRADE_THROUGH_PRINT"
    assert got["filled_qty"] == 0.4

    rows[6] = {**rows[6], "trade_bucket_complete": False}
    partial = fill(rows)
    assert partial["outcome"] == "NO_FILL" and partial["diagnostics"]["bbo_cross_without_print"] == 1