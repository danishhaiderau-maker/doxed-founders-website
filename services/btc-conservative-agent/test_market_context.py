import calendar
import json
import os
import time

import pytest

import market_context_collector as mcc
import market_context_tape as mct
import market_session_calendar as msc
import research_segment_selection as rss
import trailing_regime as tr


T0 = 1_800_000_000  # minute aligned


def _utc(*parts):
    return float(calendar.timegm(parts + (0,) * (6 - len(parts))))


# --------------------------------------------------------------------------
# Session flags and macro calendar
# --------------------------------------------------------------------------
def test_us_open_follows_dst_rule():
    summer = msc.session_flags(_utc(2026, 7, 1, 13, 30))
    winter = msc.session_flags(_utc(2026, 1, 15, 14, 30))
    assert summer["us_dst"] is True and winter["us_dst"] is False
    for flags in (summer, winter):
        assert flags["us_cash_open"] is True
        assert flags["min_from_us_open"] == 0.0
        assert flags["us_open_window"] is True
    assert msc.session_flags(_utc(2026, 1, 15, 13, 30))["us_cash_open"] is False


def test_weekend_and_funding_window():
    sat = msc.session_flags(_utc(2026, 10, 3, 12, 0))
    assert sat["is_weekend"] is True and sat["us_cash_open"] is False
    near = msc.session_flags(_utc(2026, 10, 1, 7, 55))
    assert near["funding_window"] is True and near["min_to_funding_utc_8h"] == 5.0
    assert msc.session_flags(_utc(2026, 10, 1, 7, 30))["funding_window"] is False


def test_macro_window_and_missing_year_fails_closed():
    fomc = msc.macro_flags(_utc(2026, 1, 28, 19, 10))
    assert fomc["macro_window"] is True and fomc["macro_window_kind"] == "FOMC"
    assert msc.macro_flags(_utc(2026, 1, 28, 20, 30))["macro_window"] is False
    missing = msc.macro_flags(_utc(2099, 1, 5))
    assert missing["calendar_status"] == "CALENDAR_MISSING_YEAR"
    assert missing["macro_window"] is False and missing["next_macro_kind"] is None


def test_macro_table_is_sorted_and_covers_registered_year():
    events = msc.macro_events_utc(2026)
    assert events and [e[0] for e in events] == sorted(e[0] for e in events)
    kinds = {k for _, k in events}
    assert kinds == {"FOMC", "CPI", "NFP"}
    assert sum(1 for _, k in events if k == "FOMC") == 8


# --------------------------------------------------------------------------
# Trailing regime is causal
# --------------------------------------------------------------------------
def test_trailing_percentile_ranks_against_history_only():
    p = tr.TrailingPercentile(window=10, min_history=5)
    obs = [p.observe(v) for v in (1, 2, 3, 4, 5)]
    assert all(o["label"] == "WARMUP" for o in obs)
    assert p.observe(100)["label"] == "EXTREME"
    assert p.observe(0.5)["label"] == "CALM"
    assert p.observe(None)["rank_pct"] is None


def test_trailing_labels_do_not_see_the_future():
    values = [1.0] * 20 + [50.0] * 5
    prefix = tr.trailing_labels(values[:21], window=50, min_history=10)
    full = tr.trailing_labels(values, window=50, min_history=10)
    assert full[:21] == prefix


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------
def test_liquidation_side_conventions():
    b = mct.parse_binance_liq({"e": "forceOrder", "o": {"s": "BTCUSDT", "S": "SELL", "z": "0.5",
                                                         "ap": "100000", "T": 1}})
    assert b[0][1][1]["liq_side"] == "LONG_LIQUIDATED"
    assert b[0][1][1]["notional_usd"] == 50000.0
    y = mct.parse_bybit_liq({"topic": "allLiquidation.BTCUSDT",
                             "data": [{"s": "BTCUSDT", "S": "Buy", "v": "0.2", "p": "100000", "T": 1}]})
    assert y[0][1][1]["liq_side"] == "LONG_LIQUIDATED" and y[0][1][1]["order_side"] == "SELL"
    o = mct.parse_okx_liq({"arg": {"channel": "liquidation-orders"},
                           "data": [{"instId": "BTC-USDT-SWAP",
                                     "details": [{"side": "buy", "sz": "10", "bkPx": "100000", "ts": "1"}]},
                                    {"instId": "ETH-USDT-SWAP",
                                     "details": [{"side": "buy", "sz": "10", "bkPx": "3000", "ts": "1"}]},
                                    {"instId": "BTC-USD-SWAP",
                                     "details": [{"side": "sell", "sz": "5", "bkPx": "100000", "ts": "1"}]}]})
    assert [e[1][1]["liq_side"] for e in o] == ["SHORT_LIQUIDATED", "LONG_LIQUIDATED"]
    assert o[0][1][1]["qty_btc"] == pytest.approx(0.1)
    assert o[1][1][1]["notional_usd"] == pytest.approx(500.0)


def test_coinbase_ticker_routes_and_ignores_other_products():
    ev = mct.parse_coinbase({"type": "ticker", "product_id": "BTC-USD", "best_bid": "100",
                             "best_ask": "101", "time": "2026-10-01T00:00:00.000Z"})
    assert ev[0][0] == "coinbase" and ev[0][1][:3] == ("bbo", 100.0, 101.0)
    assert mct.parse_coinbase({"type": "ticker", "product_id": "ETH-USD"}) == []


def test_rest_parse_and_deltas_never_bridge_stale_snapshots():
    cur = {**mct.parse_rest("binance", {"premium": {"lastFundingRate": "0.0001", "markPrice": "100100",
                                                    "indexPrice": "100000", "nextFundingTime": 1},
                                        "oi": {"openInterest": "1010"}}),
           "status": "OK", "fetched_ts": 1000.0}
    assert cur["basis_bp"] == pytest.approx(10.0)
    prev = {**cur, "oi_btc": 1000.0, "fetched_ts": 940.0}
    d = mct.deriv_deltas(cur, prev)
    assert d["oi_delta_btc"] == 10.0 and d["oi_delta_pct"] == pytest.approx(1.0)
    assert mct.deriv_deltas(cur, {**prev, "fetched_ts": 700.0})["oi_delta_btc"] is None
    assert mct.deriv_deltas(cur, {**prev, "status": "ERROR"})["oi_delta_btc"] is None
    bfx = mct.parse_rest("bitfinex", {"status": [["tBTCF0:USTF0", 1, None, 100050, 100000, None, 0, None,
                                                  2, 0.00002, 0, None, 0.00001, None, None, 100040,
                                                  None, None, 1234.5]]})
    assert bfx["funding_rate"] == 0.00001 and bfx["oi_btc"] == 1234.5 and bfx["mark"] == 100040


def test_minute_encoding_masks_down_seconds_and_round_trips():
    mids = {"coinbase": [100000.0 + i * 0.01 for i in range(60)], "binance_spot": [99990.0] * 60}
    ups = {"coinbase": [True] * 30 + [False] * 30, "binance_spot": [True] * 60}
    row = mct.encode_minute(T0, mids, ups, bfx_mids=[99995.0] * 60, binance_perp_mids=[None] * 60)
    assert row["spot"]["coinbase"]["up_sec"] == 30
    assert row["spot"]["coinbase"]["dm"][30:] == [None] * 30
    assert row["premium"]["coinbase_vs_binance_perp"] == [None] * 60
    assert row["premium"]["coinbase_vs_bfx"][45] is None
    dec = mct.decode_minute(json.loads(json.dumps(row)))
    assert dec["spot"]["coinbase"][T0 + 10] == pytest.approx(100000.10, abs=0.005)
    assert dec["spot"]["coinbase"][T0 + 40] is None
    assert dec["up"]["coinbase"][T0 + 40] is False
    assert dec["premium"]["coinbase_vs_bfx"][T0] == pytest.approx(0.5, abs=0.01)


def test_health_marks_stale_feeds_and_never_affects_orders():
    now = 10_000.0
    live = {"schema": mct.LIVE_SCHEMA, "written_ts": now - 2,
            "feeds": {"coinbase": {"connected": True, "last_msg_ts": now - 1},
                      "liq_okx": {"connected": True, "last_msg_ts": now - 600}},
            "derivatives": {"binance": {"status": "OK", "fetched_ts": now - 30},
                            "okx": {"status": "OK", "fetched_ts": now - 900}}}
    h = mct.health_from_live(live, now)
    assert h["affects_orders"] is False
    assert h["status"] == "DEGRADED" and h["stale_feeds"] == ["deriv_okx", "liq_okx"]
    assert mct.health_from_live(None, now)["status"] == "COLLECTOR_DOWN"
    assert mct.health_from_live(live, now, enabled=False)["status"] == "DISABLED"


def test_live_file_is_excluded_but_tapes_ship():
    assert mct.LIVE_FILE in rss.EXCLUDED_NAMES
    assert mct.FILE_NAME not in rss.EXCLUDED_NAMES
    assert mct.LIQ_FILE_NAME not in rss.EXCLUDED_NAMES


def test_collector_has_no_order_or_key_surface():
    src = open(mcc.__file__, encoding="utf-8").read() + open(mct.__file__, encoding="utf-8").read()
    for token in ("api_key", "API_SECRET", "submit_order", "place_order", "/auth/", "private"):
        assert token not in src
    for url in [c["url"] for c in (spec["connection"] for spec in mct.FEEDS.values())]:
        assert url.startswith("wss://")


# --------------------------------------------------------------------------
# Collector end to end (fake clock, no network)
# --------------------------------------------------------------------------
class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


def _fake_fetch(url):
    if "premiumIndex" in url:
        return {"lastFundingRate": "0.0001", "markPrice": "100100", "indexPrice": "100000",
                "nextFundingTime": 1}
    if "openInterest" in url:
        return {"openInterest": "1000"}
    raise OSError("offline")


def test_collector_writes_minute_without_forward_fill(tmp_path):
    clock = _Clock(T0 + 0.1)
    c = mcc.Collector(str(tmp_path), clock=clock, fetch=_fake_fetch, start_workers=False)
    c._feed_connected = lambda feed: True
    for i in range(66):
        clock.t = T0 + i + 0.5
        if i < 30:
            c.routers["coinbase"].on_events([("coinbase", ("bbo", 100000.0, 100001.0, None))], clock.t)
        c.routers["binance_spot"].on_events([("binance_spot", ("bbo", 99990.0, 99991.0, None))], clock.t)
        if i == 20:
            c.rest.poll_once(now=clock.t)
            c.routers["liq_bybit"].on_events(
                mct.parse_bybit_liq({"topic": "allLiquidation.BTCUSDT",
                                     "data": [{"s": "BTCUSDT", "S": "Sell", "v": "1.5", "p": "100000",
                                               "T": 1}]}), clock.t)
        clock.t = T0 + i + 1.4
        c.tick(clock.t)
    with open(tmp_path / mct.FILE_NAME, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    assert [r["minute_ts"] for r in rows] == [T0]
    row = rows[0]
    assert row["schema"] == mct.SCHEMA
    assert row["spot"]["binance_spot"]["up_sec"] >= 59
    cb_up = row["spot"]["coinbase"]["up_sec"]
    assert 29 <= cb_up <= 34, cb_up
    assert row["spot"]["coinbase"]["dm"][-1] is None
    assert row["derivatives"]["binance"]["status"] == "OK"
    assert row["derivatives"]["bybit"]["status"] == "ERROR"
    assert row["liquidations"]["bybit"]["short_n"] == 1
    assert row["liquidations"]["bybit"]["short_usd"] == 150000.0
    assert row["flags"]["schema"] == msc.SCHEMA
    assert row["regime"]["schema"] == tr.SCHEMA
    with open(tmp_path / mct.LIQ_FILE_NAME, encoding="utf-8") as fh:
        liqs = [json.loads(line) for line in fh]
    assert len(liqs) == 1 and liqs[0]["schema"] == mct.LIQ_SCHEMA
    live = json.load(open(tmp_path / mct.LIVE_FILE, encoding="utf-8"))
    assert live["schema"] == mct.LIVE_SCHEMA
    assert c.rest.for_minute(T0 + 600)["binance"]["status"] == "MISSING"


def test_collector_rotates_without_deleting(tmp_path, monkeypatch):
    monkeypatch.setattr(mct, "ROTATE_BYTES", 10)
    c = mcc.Collector(str(tmp_path), clock=_Clock(T0), fetch=_fake_fetch, start_workers=False)
    for i in range(3):
        assert c._append(c.path, [{"i": i, "pad": "x" * 20}])
    names = sorted(os.listdir(tmp_path))
    assert mct.FILE_NAME in names
    assert f"{mct.FILE_NAME}.1" in names and f"{mct.FILE_NAME}.2" in names
    total = 0
    for n in names:
        if n.startswith(mct.FILE_NAME):
            total += sum(1 for _ in open(tmp_path / n, encoding="utf-8"))
    assert total == 3


# --------------------------------------------------------------------------
# Restart-safe trailing regime (rehydrated from durable files on boot)
# --------------------------------------------------------------------------
def _tape_row(sec, mid, fresh=True):
    return json.dumps({"schema": "market_microstructure_1s_v1", "bucket_ts": sec, "fresh": fresh,
                       "valid_bbo": True, "bid": mid - 0.5, "ask": mid + 0.5, "bid_qty": 1.0})


def _closes(n, start=100000.0):
    out, px = [], start
    for i in range(n):
        px *= 1.0 + (0.0004 if (i * 7919) % 13 < 6 else -0.00035) * (1 + (i % 5) / 4)
        out.append(round(px, 2))
    return out


def _write_tape(tmp_path, first_minute, closes, split=None):
    lines = []
    for i, px in enumerate(closes):
        minute = first_minute + i * 60
        lines.append(_tape_row(minute + 10, px * 0.999))
        lines.append(_tape_row(minute + 59, px))
        lines.append(_tape_row(minute + 59, px * 2, fresh=False))
    split = len(lines) // 2 if split is None else split
    base = tmp_path / "market_microstructure_1s.jsonl"
    (tmp_path / "market_microstructure_1s.jsonl.1").write_text("\n".join(lines[:split]) + "\n", encoding="utf-8")
    base.write_text("\n".join(lines[split:]) + "\n", encoding="utf-8")


def test_regime_seed_from_tape_survives_restart_and_matches_live(tmp_path):
    n = tr.DEFAULT_MIN_HISTORY + 60
    first = T0 - n * 60
    closes = _closes(n)
    _write_tape(tmp_path, first, closes)
    c = mcc.Collector(str(tmp_path), clock=_Clock(T0 + 5), fetch=_fake_fetch, start_workers=False)
    seed = c.regime_seed
    assert seed["tape_minutes"] == n and seed["labels_ready"] is True
    assert seed["last_minute_ts"] == T0 - 60
    # An uninterrupted collector would have produced exactly these values live.
    os.makedirs(tmp_path / "empty")
    live = mcc.Collector(str(tmp_path / "empty"), clock=_Clock(T0), fetch=_fake_fetch, start_workers=False)
    assert len(live._regime) == 0
    for px in closes:
        live._regime_for([None] * 59 + [px])
    assert c._regime._sorted == live._regime._sorted
    assert list(c._bfx_closes) == list(live._bfx_closes)
    nxt = c._regime_for([None] * 59 + [closes[-1] * 1.0003])
    ref = live._regime_for([None] * 59 + [closes[-1] * 1.0003])
    assert nxt["label"] != tr.WARMUP and nxt == ref
    health = mct.health_from_live(c.live_payload(T0 + 5), T0 + 5)
    assert health["regime"]["seed"]["labels_ready"] is True
    assert health["regime"]["last"]["label"] == nxt["label"]


def test_regime_seed_never_reads_at_or_after_the_first_live_minute(tmp_path):
    n = 40
    closes = _closes(n)
    _write_tape(tmp_path, T0 - 30 * 60, closes)
    c = mcc.Collector(str(tmp_path), clock=_Clock(T0 + 5), fetch=_fake_fetch, start_workers=False)
    assert c.regime_seed["tape_minutes"] == 30
    assert c.regime_seed["labels_ready"] is False


def test_regime_seed_uses_stamped_context_rv_only_before_tape_coverage(tmp_path):
    tape_first = T0 - 100 * 60
    _write_tape(tmp_path, tape_first, _closes(100))
    ctx = []
    for i in range(50):
        minute = tape_first - (50 - i) * 60
        ctx.append(json.dumps({"schema": mct.SCHEMA, "minute_ts": minute,
                               "regime": {"rv15_bps": 1.0 + i / 100}}))
    ctx.append(json.dumps({"schema": mct.SCHEMA, "minute_ts": tape_first + 60,
                           "regime": {"rv15_bps": 99.0}}))
    (tmp_path / mct.FILE_NAME).write_text("\n".join(ctx) + "\n", encoding="utf-8")
    c = mcc.Collector(str(tmp_path), clock=_Clock(T0 + 5), fetch=_fake_fetch, start_workers=False)
    assert c.regime_seed["context_rv_n"] == 50
    assert 99.0 not in c._regime._sorted
    assert c.regime_seed["first_minute_ts"] == tape_first - 50 * 60
    assert len(c._regime) == 50 + c.regime_seed["tape_rv_n"]
