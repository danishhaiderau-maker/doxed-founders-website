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
    c.on_bfx([1, "hb"], chans, 112.0)  # heartbeat: connection alive, book unchanged for 12 s
    for sec in (105, 108, 111):  # book.ts is older than MAX_QUOTE_AGE_SEC for all of them
        c.close_second(sec)
    rows = _rows(tmp_path / ssc.BOOK_FILE)
    assert [r["s"] for r in rows] == [105, 108, 111]
    assert all(r["carried"] is True and r["b"] == [[100, 0.5]] and r["a"] == [[200, 0.5]] for r in rows)


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
