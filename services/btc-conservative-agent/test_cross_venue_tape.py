"""Cross-venue leader tape: parsers, encoding, collector, leader rule, health, shipping."""

import json
import os
import random
import tempfile
import threading
from pathlib import Path

import pytest

import cross_venue_collector as collector
import cross_venue_tape as cvt


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------
def test_binance_parser_reads_depth_aggtrade_and_mark_price():
    depth = {"stream": "btcusdt@depth5@100ms", "data": {
        "e": "depthUpdate", "T": 1000, "b": [["83514.50", "2.9"]], "a": [["83514.60", "1.0"]]}}
    events = cvt.parse_binance(depth)
    assert events[0] == ("bbo", 83514.5, 83514.6, 1000.0)
    assert events[1][:2] == ("depth", "imb5") and events[1][2] == pytest.approx((2.9 - 1.0) / 3.9)
    sell = {"data": {"e": "aggTrade", "p": "83447.2", "q": "3.316", "T": 5, "m": True}}
    buy = {"data": {"e": "aggTrade", "p": "83447.2", "q": "0.5", "T": 6, "m": False}}
    assert cvt.parse_binance(sell) == [("trade", 83447.2, 3.316, "SELL", 5.0)]
    assert cvt.parse_binance(buy)[0][3] == "BUY"
    mark = {"data": {"e": "markPriceUpdate", "p": "83452.1", "i": "83503.6", "r": "0.00005036",
                     "T": 1790841600000}}
    assert cvt.parse_binance(mark) == [("deriv", {"funding_rate": 5.036e-05, "mark": 83452.1,
                                                  "index": 83503.6, "next_funding_ms": 1790841600000.0})]


def test_binance_depth20_emits_imbalance_only_and_never_moves_the_bbo():
    bids = [[str(100 - i * 0.1), "1.0"] for i in range(20)]
    asks = [[str(100.1 + i * 0.1), "3.0"] for i in range(20)]
    msg = {"stream": cvt.DEPTH20_STREAM, "data": {"e": "depthUpdate", "T": 5, "b": bids, "a": asks}}
    events = cvt.parse_binance(msg)
    assert events == [("depth", "imb20", pytest.approx((20.0 - 60.0) / 80.0))]
    assert cvt.book_imbalance([], [], 5) is None
    assert cvt.book_imbalance([["1", "0"]], [["2", "0"]], 5) is None
    public = next(c for c in cvt.VENUES["binance"]["connections"] if c["name"] == "binance_public")
    assert cvt.DEPTH5_STREAM in public["url"] and cvt.DEPTH20_STREAM in public["url"]


def test_binance_imbalance_is_encoded_per_second_and_carried_forward_boundedly():
    acc = cvt.VenueAccumulator("binance")
    acc.on_events([("bbo", 100.0, 100.1, None), ("depth", "imb5", 0.25), ("depth", "imb20", -0.5)], 10.2)
    first = acc.close_second(10, None)
    assert first["imb5"] == 0.25 and first["imb20"] == -0.5
    assert acc.close_second(11, None)["imb5"] == 0.25
    assert acc.close_second(20, None)["imb5"] is None
    samples = {"binance": [dict(first, sec=0)] + [{"sec": s, "mid": None, "buy": 0.0, "sell": 0.0}
                                                   for s in range(1, 60)]}
    row = cvt.encode_minute(0, samples, [100.0] * 60)
    assert row["imbalance_unit"] == cvt.IMBALANCE_UNIT
    assert row["venues"]["binance"]["imb5"][0] == 250
    assert row["venues"]["binance"]["imb20"][0] == -500
    assert row["venues"]["binance"]["imb5"][1] is None


def test_bybit_ticker_deltas_merge_and_every_push_reconfirms_the_book():
    acc = cvt.VenueAccumulator("bybit")
    snap = {"topic": "tickers.BTCUSDT", "type": "snapshot", "ts": 1,
            "data": {"bid1Price": "100.0", "ask1Price": "100.2", "fundingRate": "0.0001",
                     "openInterest": "55409.7", "markPrice": "100.1", "indexPrice": "100.0"}}
    acc.on_events(cvt.parse_bybit(snap), 10.2)
    delta = {"topic": "tickers.BTCUSDT", "type": "delta", "ts": 2, "data": {"ask1Price": "100.4"}}
    acc.on_events(cvt.parse_bybit(delta), 11.5)
    quiet = {"topic": "tickers.BTCUSDT", "type": "delta", "ts": 3, "data": {"markPrice": "100.2"}}
    acc.on_events(cvt.parse_bybit(quiet), 14.4)
    s10 = acc.close_second(10, None)
    s11 = acc.close_second(11, s10["quote"])
    s14 = acc.close_second(14, s11["quote"])
    assert s10["mid"] == pytest.approx(100.1)
    assert s11["mid"] == pytest.approx(100.2)
    assert s14["mid"] == pytest.approx(100.2) and s14["quote"][2] == 14.4
    assert acc.derivatives()["open_interest"] == 55409.7
    trades = {"topic": "publicTrade.BTCUSDT", "data": [
        {"T": 1, "S": "Buy", "v": "0.005", "p": "100.1"}, {"T": 2, "S": "Sell", "v": "0.002", "p": "100.0"}]}
    assert [e[3] for e in cvt.parse_bybit(trades)] == ["BUY", "SELL"]


def test_okx_parser_converts_contracts_and_ignores_subscribe_acks():
    ack = {"event": "subscribe", "arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"}}
    assert cvt.parse_okx(ack) == []
    trade = {"arg": {"channel": "trades"}, "data": [{"px": "100", "sz": "25", "side": "sell", "ts": "7"}]}
    assert cvt.parse_okx(trade) == [("trade", 100.0, pytest.approx(0.25), "SELL", 7.0)]
    tick = {"arg": {"channel": "tickers"}, "data": [{"bidPx": "99.9", "askPx": "100.1", "ts": "8"}]}
    assert cvt.parse_okx(tick)[0][:3] == ("bbo", 99.9, 100.1)
    oi = {"arg": {"channel": "open-interest"}, "data": [{"oi": "2800000", "oiCcy": "28000.5"}]}
    assert cvt.parse_okx(oi) == [("deriv", {"open_interest": 28000.5})]


# --------------------------------------------------------------------------
# Accumulator and encoding
# --------------------------------------------------------------------------
def test_accumulator_buckets_on_receive_second_and_expires_stale_quotes():
    acc = cvt.VenueAccumulator("binance", keep_seconds=4)
    acc.on_events([("bbo", 100.0, 100.2, None)], 50.1)
    acc.on_events([("bbo", 101.0, 101.2, None)], 50.9)
    acc.on_events([("trade", 101.1, 0.4, "BUY", None), ("trade", 101.0, 0.1, "SELL", None)], 50.95)
    acc.on_events([("bbo", 200.0, 200.2, None)], 51.05)
    s50 = acc.close_second(50, None)
    assert s50["mid"] == pytest.approx(101.1)
    assert (s50["buy"], s50["sell"], s50["last"]) == (0.4, 0.1, 101.0)
    s51 = acc.close_second(51, s50["quote"])
    assert s51["mid"] == pytest.approx(200.1)
    carried = acc.close_second(53, s51["quote"])
    assert carried["mid"] == pytest.approx(200.1)
    assert acc.close_second(56, carried["quote"])["mid"] is None
    for sec in range(100, 200):
        acc.on_events([("bbo", 1.0, 1.1, None), ("trade", 1.0, 1.0, "BUY", None)], sec + 0.5)
    assert len(acc._quote_by_sec) <= 4 and len(acc._flow_by_sec) <= 4


def test_minute_encoding_round_trips_within_one_price_unit():
    t0 = 1_790_840_340
    rnd = random.Random(1)
    mids = [83500.0 + rnd.uniform(-40, 40) for _ in range(60)]
    samples = [{"sec": t0 + i, "mid": None if i == 7 else mids[i], "last": mids[i] + 0.05,
                "buy": 0.0123 * i, "sell": 0.5} for i in range(60)]
    bfx = [None if i == 3 else m + 4.0 for i, m in enumerate(mids)]
    row = cvt.encode_minute(t0, {"binance": samples}, bfx, derivatives={"binance": {"funding_rate": 1e-4}})
    decoded = cvt.decode_minute(json.loads(json.dumps(row)))
    assert decoded["binance"][t0 + 7]["mid"] is None
    assert t0 + 3 not in decoded["bfx"]
    for i in (0, 10, 59):
        assert decoded["binance"][t0 + i]["mid"] == pytest.approx(mids[i], abs=cvt.PRICE_UNIT / 2 + 1e-9)
        assert decoded["bfx"][t0 + i] == pytest.approx(mids[i] + 4.0, abs=cvt.PRICE_UNIT / 2 + 1e-9)
        assert decoded["binance"][t0 + i]["buy"] == pytest.approx(0.0123 * i, abs=cvt.QTY_UNIT / 2 + 1e-9)
    assert row["basis_bp_mean"]["binance"] == pytest.approx(-4.0 / 83504 * 1e4, abs=0.02)
    assert len(json.dumps(row, separators=(",", ":"))) < 2500


# --------------------------------------------------------------------------
# Leader rule (causal) and health
# --------------------------------------------------------------------------
def _live(start, mids_by_venue, written_ts=None, bbo_ts=None):
    return {
        "schema": cvt.LIVE_SCHEMA, "written_ts": written_ts, "history_start_ts": start,
        "mids": mids_by_venue,
        "venues": {v: {"connected": True, "last_bbo_ts": bbo_ts, "msgs": 5} for v in mids_by_venue},
    }


def test_leader_features_use_only_closed_buckets_and_respect_priority():
    decision = 1000.4
    anchor = 999
    start = anchor - 60
    base = 80000.0
    rising = [base * (1 + 0.0003 * max(0, s - (anchor - 10)) / 10) for s in range(start, anchor + 1)]
    future = rising + [base * 0.9] * 5
    live = _live(start, {"binance": future, "bybit": [base] * len(future)})
    feats = cvt.leader_features(live, decision, bfx_mid_at=lambda s: base)
    assert feats["anchor_bucket_ts"] == anchor
    assert feats["leader_venue"] == "binance"
    assert feats["leader_ret_bp"] == pytest.approx(3.0, abs=0.01)
    assert feats["side"] == cvt.LONG and feats["reason"] == "LEADER_MOVE"
    assert feats["venues"]["binance"]["gap_10s_bp"] == pytest.approx(3.0, abs=0.01)
    no_binance = _live(start, {"binance": [None] * len(future), "bybit": future})
    assert cvt.leader_features(no_binance, decision)["leader_venue"] == "bybit"
    flat = _live(start, {"binance": [base] * len(future)})
    assert cvt.leader_features(flat, decision)["side"] == cvt.NONE
    assert cvt.leader_features(None, decision)["reason"] == "NO_LIVE_STATE"


def test_health_states():
    now = 5000.0
    live = _live(0, {"binance": [], "bybit": []}, written_ts=now - 1, bbo_ts=now - 2)
    assert cvt.health_from_live(live, now)["status"] == "OK"
    live["venues"]["bybit"]["last_bbo_ts"] = now - 120
    degraded = cvt.health_from_live(live, now)
    assert degraded["status"] == "DEGRADED" and degraded["stale_venues"] == ["bybit"]
    live["venues"]["binance"]["last_bbo_ts"] = None
    assert cvt.health_from_live(live, now)["status"] == "STALE"
    live["written_ts"] = now - 300
    assert cvt.health_from_live(live, now)["status"] == "DOWN"
    assert cvt.health_from_live(None, now)["status"] == "DOWN"
    assert cvt.health_from_live(None, now, enabled=False)["status"] == "DISABLED"


# --------------------------------------------------------------------------
# Collector
# --------------------------------------------------------------------------
class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_collector_writes_aligned_minute_rows_live_state_and_rotates(monkeypatch):
    tmp = tempfile.mkdtemp()
    t0 = 1_790_840_340
    with open(os.path.join(tmp, "market_microstructure_1s.jsonl"), "w", encoding="utf-8") as fh:
        for s in range(t0 - 5, t0 + 60):
            fh.write(json.dumps({"schema": "market_microstructure_1s_v1", "bucket_ts": s, "fresh": True,
                                 "valid_bbo": True, "bid": 99.0 + (s - t0) * 0.1,
                                 "ask": 99.2 + (s - t0) * 0.1}) + "\n")
    clock = _Clock(t0 + 0.1)
    col = collector.Collector(tmp, ["binance", "bybit"], clock=clock, start_workers=False)
    col.last_closed = t0 - 1
    for s in range(t0, t0 + 60):
        col.acc["binance"].on_events([("bbo", 100.0 + (s - t0) * 0.1, 100.2 + (s - t0) * 0.1, None),
                                      ("trade", 100.1, 0.25, "BUY", None)], s + 0.5)
        clock.t = s + 1 + collector.CLOSE_LAG_SEC + 0.01
        col.tick(clock.t)
    assert not os.path.exists(col.tape_path)
    clock.t = t0 + 60 + collector.MINUTE_FINALIZE_LAG_SEC + 0.5
    col.tick(clock.t)
    rows = [json.loads(l) for l in open(col.tape_path, encoding="utf-8")]
    assert len(rows) == 1 and rows[0]["minute_ts"] == t0
    decoded = cvt.decode_minute(rows[0])
    assert decoded["binance"][t0 + 30]["mid"] == pytest.approx(103.1, abs=0.03)
    assert decoded["binance"][t0 + 30]["buy"] == pytest.approx(0.25)
    assert decoded["bfx"][t0 + 30] == pytest.approx(102.1, abs=0.03)
    assert all(c["mid"] is None for c in decoded["bybit"].values())
    assert rows[0]["meta"]["collector_version"] == cvt.COLLECTOR_VERSION
    live = cvt.read_live(col.live_path)
    assert live["history_end_ts"] == col.last_closed
    assert cvt.live_mid_at(live, "binance", t0 + 30) == pytest.approx(103.1, abs=0.01)
    health = cvt.health_from_live(live, clock.t)
    assert health["status"] == "DEGRADED" and health["stale_venues"] == ["bybit"]
    assert col.stats["bytes_today"] == os.path.getsize(col.tape_path)
    monkeypatch.setattr(cvt, "ROTATE_BYTES", 10)
    col._append({**rows[0], "minute_ts": t0 + 60})
    assert os.path.exists(col.tape_path + ".1")
    assert len(open(col.tape_path, encoding="utf-8").readlines()) == 1


def test_collector_bounds_catch_up_after_a_stall():
    col = collector.Collector(tempfile.mkdtemp(), ["okx"], clock=_Clock(0), start_workers=False)
    col.last_closed = 1000
    col.close_seconds(5000.0)
    assert col.last_closed == 4998
    assert col.stats["seconds_skipped"] > 3000
    assert len(col._history["okx"]) <= cvt.LIVE_HISTORY_SEC
    assert sum(len(v["okx"]) for v in col._minutes.values()) <= collector.MAX_CATCHUP_SEC


def test_backoff_is_capped_and_jittered():
    rng = random.Random(3)
    delays = [collector.backoff_delay(a, rng) for a in range(12)]
    assert delays[0] <= 1.2 and max(delays) <= collector.BACKOFF_MAX_SEC * 1.2
    assert delays[-1] >= collector.BACKOFF_MAX_SEC * 0.8


def test_connection_worker_parses_reconnects_with_backoff_and_stops():
    import websocket

    stop = threading.Event()
    acc = cvt.VenueAccumulator("bybit")
    sessions, slept, sent = [], [], []

    class FakeWS:
        def __init__(self):
            self.queue = [
                json.dumps({"topic": "tickers.BTCUSDT", "ts": 1,
                            "data": {"bid1Price": "10", "ask1Price": "10.2"}}),
                "pong",
                "not json",
            ]

        def send(self, payload):
            sent.append(payload)

        def settimeout(self, _):
            pass

        def recv(self):
            if self.queue:
                return self.queue.pop(0)
            raise ConnectionResetError("peer closed")

        def close(self):
            pass

    def factory(url):
        sessions.append(url)
        if len(sessions) == 2:
            raise websocket.WebSocketTimeoutException("handshake timeout")
        return FakeWS()

    def fake_sleep(delay):
        slept.append(delay)
        if len(slept) >= 3:
            stop.set()

    spec = cvt.VENUES["bybit"]["connections"][0]
    worker = collector.ConnectionWorker("bybit", spec, acc, stop, ws_factory=factory,
                                        sleep=fake_sleep, seed=1)
    worker.run()
    assert len(sessions) == 3 and worker.reconnects == 3
    assert json.loads(sent[0]) == spec["subscribe"]
    assert acc.msgs == 2 and acc._bid == 10.0
    assert slept[0] <= 1.2 and slept[1] > slept[0]
    assert "ConnectionResetError" in worker.last_error and worker.connected is False


# --------------------------------------------------------------------------
# Shipping and wiring contracts
# --------------------------------------------------------------------------
def test_segment_shipper_ships_the_tape_but_not_the_live_file():
    import research_segment_shipper as shipper
    from research_segment_store import LocalDirectoryStore

    root = Path(tempfile.mkdtemp())
    runtime = root / "runtime"
    runtime.mkdir()
    (runtime / cvt.FILE_NAME).write_text("{}\n")
    (runtime / cvt.LIVE_FILE).write_text("{}")
    (runtime / (cvt.LIVE_FILE + ".tmp")).write_text("{}")
    s = shipper.SegmentShipper(store=LocalDirectoryStore(root / "store"), volume_root=root,
                               runtime_root=runtime, state_dir=root / "state",
                               rules=shipper.load_selection_rules())
    names = {Path(k).name for k in s.scan()}
    assert cvt.FILE_NAME in names
    assert cvt.LIVE_FILE not in names and cvt.LIVE_FILE + ".tmp" not in names


def test_entrypoint_and_image_start_the_isolated_collector():
    here = Path(__file__).resolve().parent
    entry = (here / "fly-entrypoint.sh").read_text(encoding="utf-8")
    assert "nice -n 10 python /app/cross_venue_collector.py" in entry
    assert 'CROSS_VENUE_COLLECTOR_ENABLED:-1' in entry
    assert "import cross_venue_tape, cross_venue_collector" in (here / "Dockerfile").read_text(encoding="utf-8")
    source = (here / "cross_venue_collector.py").read_text(encoding="utf-8")
    for forbidden in ("import bot", "from bot", "flask", "api_key", "apiKey", "signature"):
        assert forbidden not in source
