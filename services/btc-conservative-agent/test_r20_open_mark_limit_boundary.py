"""R20 boundary pins (test-only, additive): $9.99 vs $10.00 of loss where the loss is an OPEN position marked to the book.

test_r20_live_loss_limit.py pins the realised-loss boundary. These tests pin the open-position leg at the real, un-lowered
$10 limit through the three consumers of the one shared d3_open_mark_loss: daily_cap_entry_block (admit_entry),
d3_cap_violations and live_guardian.evaluate. The mark is the full-size exit-side book walk (LONG sells into the bid, SHORT
buys the ask), never mid, last or entry. No fixture lowers the limit here."""
import live_guardian as lg
import live_tile_gate as ltg

NOW = 1792800000.0 + 43200.0   # 12:00 UTC, inside one UTC day
CAP_CODES = {ltg.VIOL_TILE_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP}
BID, ASK, QTY = 100000.0, 100010.0, 0.1


def _book(age=0.5):
    return {"s": NOW - age - 1.0, "b": [[BID, 1.0]], "a": [[ASK, 1.0]]}


def _mirror(position):
    return {"ok": True, "read_age_s": 1.0, "fetched_at_ts": NOW, "positions": [position], "open_orders": [],
            "orphans": [], "balance": {"derivatives_available_usd": 206.0}}


def _long(loss):
    # LONG exits into the bid: loss = (entry - BID) * QTY
    return {"symbol": "tBTCF0:USTF0", "amount": QTY, "base_price": BID + loss / QTY}


def _short(loss):
    # SHORT exits by buying the ask: loss = (ASK - entry) * QTY
    return {"symbol": "tBTCF0:USTF0", "amount": -QTY, "base_price": ASK - loss / QTY}


def _closed_loss(loss, tile="gs1"):
    t = NOW - 600
    return {"correlation_id": f"{tile}-realised", "trade_id": f"{tile}-realised", "closed": True,
            "pnl_usd": -loss, "closed_at_ts": t, "exit_fill_at_ts": t, "fill_at_ts": t - 60,
            "fill_price": 100_000.0, "fill_qty": 0.01, "side": "LONG", "exit_fill_price": 100_000.0 - loss / 0.01,
            "exit_price_source": "exchange_fill", "fill_price_source": "exchange_trades"}


def _codes(v):
    return {x["code"] for x in v}


_FRESH = object()


def _all_paths(chains, position, book=_FRESH):
    book = _book() if book is _FRESH else book
    m = _mirror(position)
    blocks, info = ltg.daily_cap_entry_block(chains, m, book, NOW, "gs1")
    d3v, d3i = ltg.d3_cap_violations(chains, m, book, NOW)
    gv = lg.evaluate(chains, m, {}, NOW, lane_needle="", book=book)
    return _codes(blocks), info, _codes(d3v), d3i, _codes(gv), gv


def test_open_long_marked_to_bid_999_allowed_1000_trips_every_path():
    assert ltg.DAILY_LOSS_CAP_TOTAL_USD == ltg.DAILY_LOSS_CAP_PER_TILE_USD == 10.00   # un-lowered: no fixture in this file
    b, info, d, d3i, g, _ = _all_paths([], _long(9.99))
    assert abs(info["open_mark_loss"] - 9.99) < 1e-6 and abs(d3i["open_mark_loss"] - 9.99) < 1e-6
    assert not (CAP_CODES & b) and not (CAP_CODES & d) and not (CAP_CODES & g), (b, d, g)
    b, info, d, d3i, g, gv = _all_paths([], _long(10.00))
    assert abs(info["open_mark_loss"] - 10.00) < 1e-6 and abs(d3i["open_mark_loss"] - 10.00) < 1e-6
    assert CAP_CODES <= b and CAP_CODES <= d and CAP_CODES <= g, (b, d, g)
    red, _amber = lg.split_violations(gv)
    assert CAP_CODES <= _codes(red)     # R20: the breach is a disarm-all + force-flat, never only an entry block


def test_open_short_marked_to_ask_999_allowed_1000_trips_every_path():
    b, info, d, _, g, _ = _all_paths([], _short(9.99))
    assert abs(info["open_mark_loss"] - 9.99) < 1e-6
    assert not (CAP_CODES & b) and not (CAP_CODES & d) and not (CAP_CODES & g), (b, d, g)
    b, info, d, _, g, _ = _all_paths([], _short(10.00))
    assert abs(info["open_mark_loss"] - 10.00) < 1e-6
    assert CAP_CODES <= b and CAP_CODES <= d and CAP_CODES <= g, (b, d, g)


def test_open_mark_uses_the_exit_side_not_the_mid():
    # LONG entry 100100: marked to the bid (100000) the loss is exactly $10.00; a mid mark (100005) would be only $9.50
    _, info, _, _, g, _ = _all_paths([], _long(10.00))
    assert info["open_mark_loss"] > 9.99 and CAP_CODES <= g
    mid = (BID + ASK) / 2
    assert (BID + 10.00 / QTY - mid) * QTY < 10.00      # the book is wide enough that a mid mark would not trip


def test_realised_plus_open_sums_to_the_one_limit_999_vs_1000():
    realised = [_closed_loss(5.00)]
    b, info, d, d3i, g, _ = _all_paths(realised, _long(4.99))
    assert abs(d3i["d3_loss_usd"] - 9.99) < 1e-6
    assert not (CAP_CODES & b) and not (CAP_CODES & d) and not (CAP_CODES & g), (b, d, g)
    b, info, d, d3i, g, gv = _all_paths(realised, _long(5.00))
    assert abs(d3i["d3_loss_usd"] - 10.00) < 1e-6
    assert ltg.VIOL_TOTAL_LOSS_CAP in b and ltg.VIOL_TOTAL_LOSS_CAP in d and ltg.VIOL_TOTAL_LOSS_CAP in g, (b, d, g)
    assert ltg.VIOL_TOTAL_LOSS_CAP in _codes(lg.split_violations(gv)[0])


def test_realised_plus_open_is_counted_for_the_tile_and_the_account_separately():
    # realised loss on a DIFFERENT tile still counts in the account total together with the open mark
    other = [_closed_loss(5.00, tile="gf3")]
    _, _, d, _, _, _ = _all_paths(other, _long(4.99))
    assert ltg.VIOL_TOTAL_LOSS_CAP not in d
    _, _, d, _, _, _ = _all_paths(other, _long(5.00))
    assert ltg.VIOL_TOTAL_LOSS_CAP in d


def test_open_position_with_a_stale_or_missing_book_fails_closed_not_open():
    for stale in (_book(2.5), None):
        b, _, d, _, g, _ = _all_paths([], _long(0.01), book=stale)
        assert "OPEN_MARK_UNREADABLE" in b and "OPEN_MARK_UNREADABLE" in d, (b, d)
    b, _, d, _, _, _ = _all_paths([], _long(0.01), book=_book(0.5))
    assert "OPEN_MARK_UNREADABLE" not in b and "OPEN_MARK_UNREADABLE" not in d


def test_open_profit_does_not_trip_and_offsets_nothing_below_zero():
    b, info, d, _, g, _ = _all_paths([], _long(-9.99))     # a winning open position (negative loss)
    assert info["open_mark_loss"] < 0
    assert not (CAP_CODES & b) and not (CAP_CODES & d) and not (CAP_CODES & g)


def test_per_tile_realised_plus_open_999_vs_1000_names_the_tile():
    c = _closed_loss(5.00)
    key = ltg.chain_tile_key(c)
    assert key == "gs1"
    for open_loss, trips in ((4.99, False), (5.00, True)):
        m = _mirror(_long(open_loss))
        d3v, _ = ltg.d3_cap_violations([c], m, _book(), NOW)
        blocks, _ = ltg.daily_cap_entry_block([c], m, _book(), NOW, key)
        assert ((ltg.VIOL_TILE_LOSS_CAP, key) in {(v["code"], v.get("tile")) for v in d3v}) is trips, (open_loss, d3v)
        assert ((ltg.VIOL_TILE_LOSS_CAP, key) in {(v["code"], v.get("tile")) for v in blocks}) is trips, (open_loss, blocks)
