"""Strategist streams: Bitfinex 10-level book, individual trades, Lighter + Paradex BBO; bounded rotation."""
import json
from pathlib import Path

import strategist_streams_collector as ssc


def _rows(p):
    return [json.loads(x) for x in Path(p).read_text().splitlines()]


def test_book_trades_and_venues(tmp_path):
    c = ssc.Collector(str(tmp_path))
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans, 100.0)
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2}, chans, 100.0)
    snap = [[100 + i, 1, 0.5] for i in range(12)] + [[200 + i, 1, -0.25] for i in range(12)]
    c.on_bfx([1, snap], chans, 100.1)
    c.on_bfx([1, [111, 0, 1]], chans, 100.2)  # delete best bid
    c.on_bfx([2, [[9, 1, 1, 1]]], chans, 100.2)  # history snapshot ignored
    c.on_bfx([2, "te", [10, 100200, -0.01, 150.5]], chans, 100.2)
    c.on_bfx([2, "tu", [10, 100200, -0.01, 150.5]], chans, 100.2)
    c.on_bfx([1, "hb"], chans, 100.3)
    c.on_lighter({"ticker": {"b": {"price": "99.5"}, "a": {"price": "100.5"}}}, 100.4)
    c.on_paradex({"bid": "99.0", "ask": "101.0"}, 100.4)
    c.close_second(100)
    c.close_second(160)  # next minute -> flush minute 60
    book = _rows(tmp_path / ssc.BOOK_FILE)[0]
    assert book["s"] == 100 and len(book["b"]) == 10 and book["b"][0] == [110, 0.5] and book["a"][0] == [200, 0.25]
    assert _rows(tmp_path / ssc.TRADES_FILE) == [{"s": 100, "tr": [[10, 100200, -0.01, 150.5]]}]
    v = _rows(tmp_path / ssc.VENUE_FILE)[0]
    assert v["m"] == 60 and v["lighter"][40] == [99.5, 100.5] and v["paradex"][40] == [99, 101]


def test_stale_book_not_written(tmp_path):
    c = ssc.Collector(str(tmp_path))
    c.book.snapshot([[1, 1, 1], [2, 1, -1]], 10.0)
    c.close_second(50)
    assert not (tmp_path / ssc.BOOK_FILE).exists()


def _quiet_book_collector(tmp_path):
    c = ssc.Collector(str(tmp_path))
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans, 100.0)
    c.on_bfx([1, [[100, 1, 0.5], [200, 1, -0.5]]], chans, 100.1)
    return c, chans


def test_quiet_book_on_a_live_connection_is_carried_forward_and_flagged(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.on_bfx([1, "hb"], chans, 112.0)  # heartbeat ON THE BOOK CHANNEL: delivery alive, book unchanged for 12 s
    for sec in (105, 108, 111):  # book.ts is older than MAX_QUOTE_AGE_SEC for all of them
        c.close_second(sec)
    rows = _rows(tmp_path / ssc.BOOK_FILE)
    assert [r["s"] for r in rows] == [105, 108, 111]
    assert all(r["carried"] is True and r["b"] == [[100, 0.5]] and r["a"] == [[200, 0.5]] for r in rows)


def test_heartbeat_on_another_channel_does_not_keep_the_book_alive(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2}, chans, 100.0)
    c.on_bfx([2, "hb"], chans, 112.0)  # trades-channel heartbeat only
    c.on_bfx([2, "te", [10, 112000, 0.01, 100.5]], chans, 112.0)
    c.close_second(125)  # last book-channel frame was at 100.1: 26 s ago
    assert not (tmp_path / ssc.BOOK_FILE).exists()


def test_disconnect_stops_carrying_and_a_crossed_book_is_never_carried(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.on_bfx([1, "hb"], chans, 105.0)
    c.on_bfx_disconnect()  # socket dropped: book channel gone
    c.close_second(106)
    assert not (tmp_path / ssc.BOOK_FILE).exists()
    c2 = ssc.Collector(str(tmp_path / "x"))
    chans2 = {}
    c2.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans2, 100.0)
    c2.on_bfx([1, [[200, 1, 0.5], [100, 1, -0.5]]], chans2, 100.1)  # bid 200 >= ask 100: crossed
    c2.on_bfx([1, "hb"], chans2, 105.0)
    c2.close_second(104)
    assert not (tmp_path / "x" / ssc.BOOK_FILE).exists()


def test_carry_is_capped_at_the_liveness_window(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.on_bfx([1, "hb"], chans, 105.0)
    c.close_second(int(105 + ssc.BOOK_LIVENESS_SEC) - 1)  # inside the cap: carried
    c.close_second(int(105 + ssc.BOOK_LIVENESS_SEC) + 1)  # beyond the cap with no further frame: nothing
    assert [r["s"] for r in _rows(tmp_path / ssc.BOOK_FILE)] == [int(105 + ssc.BOOK_LIVENESS_SEC) - 1]


def test_dead_connection_is_not_carried_forward(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.close_second(100 + int(ssc.BOOK_LIVENESS_SEC) + 5)  # nothing at all arrived for > liveness window
    assert not (tmp_path / ssc.BOOK_FILE).exists()


def test_fresh_second_is_not_flagged_carried(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.close_second(100)
    assert "carried" not in _rows(tmp_path / ssc.BOOK_FILE)[0]


def test_rotation_is_bounded(tmp_path):
    s = ssc.Sink(str(tmp_path / "x.jsonl"), rotate_bytes=50, keep=2)
    for i in range(40):
        s.write({"i": i, "pad": "x" * 30})
    assert len(list(tmp_path.glob("x.jsonl.*"))) == 2


def test_entrypoint_and_stream_catalog_wired():
    here = Path(__file__).parent
    assert "nice -n 10 python /app/strategist_streams_collector.py" in (here / "fly-entrypoint.sh").read_text()
    import monitor_streams
    assert {"bfx_book_10l", "bfx_trades", "venue_bbo_lighter_paradex"} <= set(monitor_streams.STREAMS)
    src = (here / "strategist_streams_collector.py").read_text()
    assert "import bot" not in src and "api_key" not in src.lower()


def _rows_or_empty(path):
    return _rows(path) if path.exists() else []


def test_row_s_has_no_look_ahead_into_s_plus_1(tmp_path):
    """RK 24 Oct: second s is closed at ~s+1.3-1.5; deltas/trades/deriv received after s+1 must not enter row s."""
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0)
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans, 100.0)
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2}, chans, 100.0)
    c.on_bfx({"event": "subscribed", "channel": "status", "chanId": 7, "key": ssc.DERIV_KEY}, chans, 100.0)
    c.on_bfx([1, [[100, 1, 0.5], [200, 1, -0.5]]], chans, 100.4)
    c.on_bfx([2, "te", [1, 100400, 0.01, 150]], chans, 100.4)
    # arrivals during s+1 = 101.0-101.4, before second 100 is closed
    c.on_bfx([1, [101, 1, 0.7]], chans, 101.2)  # better bid
    c.on_bfx([2, "te", [2, 101200, -0.02, 151]], chans, 101.2)
    deriv = [0] * 23
    deriv[0], deriv[14], deriv[11] = 101200, 150.5, 0.0001
    c.on_bfx([7, deriv], chans, 101.2)
    c.close_second(100, now=101.4)
    book = _rows(tmp_path / ssc.BOOK_FILE)
    assert book == [{"s": 100, "b": [[100, 0.5]], "a": [[200, 0.5]]}]
    assert _rows(tmp_path / ssc.TRADES_FILE) == [{"s": 100, "tr": [[1, 100400, 0.01, 150]]}]
    assert _rows_or_empty(tmp_path / ssc.DERIV_FILE) == []  # received at 101.2 > s+1
    c.close_second(101, now=102.4)
    book = _rows(tmp_path / ssc.BOOK_FILE)
    assert book[-1]["s"] == 101 and book[-1]["b"] == [[101, 0.7], [100, 0.5]]
    assert _rows(tmp_path / ssc.TRADES_FILE)[-1] == {"s": 101, "tr": [[2, 101200, -0.02, 151]]}
    assert _rows(tmp_path / ssc.DERIV_FILE)[-1]["s"] == 101


def test_ws_events_log_trades_hb_snapshot_and_controls_with_receive_time(tmp_path):
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0)
    chans = {}
    c.on_bfx({"event": "info", "version": 2}, chans, 99.5)
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2, "symbol": ssc.BFX_SYMBOL}, chans, 100.0)
    c.on_bfx([2, [[9, 99000, 0.5, 150], [8, 98000, -0.1, 149]]], chans, 100.05)  # post-(re)subscribe snapshot
    c.on_bfx([2, "hb"], chans, 115.0)
    c.on_bfx([2, "te", [10, 115100, 0.01, 151]], chans, 115.1234)
    c.on_bfx_disconnect(now=116.0)
    ev = _rows(tmp_path / ssc.EVENTS_FILE)
    assert [e["ev"] for e in ev] == ["info", "subscribed", "snapshot", "hb", "te", "disconnect"]
    assert ev[0]["r"] == 99.5 and ev[4]["r"] == 115.123 and ev[4]["id"] == 10 and ev[4]["mts"] == 115100
    assert ev[3] == {"r": 115.0, "ev": "hb", "ch": "trades"}
    assert ev[2]["tr"] == [[9, 99000, 0.5, 150], [8, 98000, -0.1, 149]]
    c.close_second(115, now=116.5)
    # the snapshot is history: never in bfx_trades rows; only the live print is
    assert _rows(tmp_path / ssc.TRADES_FILE) == [{"s": 115, "tr": [[10, 115100, 0.01, 151]]}]


def test_ws_events_file_is_registered_but_not_a_trades_stream():
    import full_reset_no_archive
    import monitor_streams
    assert monitor_streams.STREAMS["bfx_ws_events"] == ssc.EVENTS_FILE
    assert monitor_streams.STREAMS["bfx_trades"] == ssc.TRADES_FILE != ssc.EVENTS_FILE
    assert ssc.EVENTS_FILE in open(full_reset_no_archive.__file__, encoding="utf-8").read()
