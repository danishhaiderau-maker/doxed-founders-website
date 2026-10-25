"""DATA-GAPS-20261012 (Strategist c-h): streams health/ranges, per-trade field completeness."""
import json
from pathlib import Path

import data_gaps
import monitor_streams as ms
import monitor_tiles
import strategist_streams_collector as ssc

BOT = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")

DERIV = [1791799452000, None, 83587.04, 83535, None, 61480653.7, None, 1791820800000, 0.00015622, 2379,
         None, 0.00007265, None, None, 83531.33, None, None, 9173.22, None, None, None, 0.0005, 0.0025]


# (c) per-second funding / mark stream -------------------------------------------------------
def test_deriv_status_parsed_and_written_per_second(tmp_path):
    p = ssc.parse_deriv_status(DERIV)
    assert p["mark"] == 83531.33 and p["fund_cur"] == 0.00007265 and p["oi"] == 9173.22 and p["spot"] == 83535
    c = ssc.Collector(str(tmp_path))
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "status", "chanId": 7, "key": ssc.DERIV_KEY}, chans, 100.0)
    c.on_bfx([7, DERIV], chans, 100.2)
    c.on_bfx([7, "hb"], chans, 100.3)
    c.close_second(100)
    c.close_second(101)
    c.close_second(200)  # too old -> not written
    rows = [json.loads(x) for x in (tmp_path / ssc.DERIV_FILE).read_text().splitlines()]
    assert [r["s"] for r in rows] == [100, 101]
    assert rows[0]["mark"] == 83531.33 and rows[0]["fund_cur"] == 0.00007265 and rows[1]["age_s"] == 1.8
    assert ssc.parse_deriv_status([1, 2]) is None
    assert ms.STREAMS["bfx_deriv_status_1s"] == ssc.DERIV_FILE


def test_collector_subscribes_status_channel():
    src = Path(ssc.__file__).read_text()
    assert '"channel": "status", "key": DERIV_KEY' in src


# (g) xvp shadow stream restarted by GS-01 ----------------------------------------------------
def test_gs01_writes_xvp_shadow_and_other_instances_do_not():
    import regime_adaptive_binding as rab
    assert rab.GsPremiumEvaluator.SHADOW_FILE == "xvp_shadow_signals.jsonl"
    for cls in (rab.Gs5PremiumEvaluator, rab.Gs7PremiumEvaluator, rab.DanishRouterPremiumEvaluator,
                rab.FadePoolPremiumEvaluator):
        assert cls.SHADOW_FILE is None
    assert ms.STREAMS["xvp_shadow_signals"] == "xvp_shadow_signals.jsonl"


# (h) rotated parts + time range + health ------------------------------------------------------
def _write(path, start, n, key="minute_ts", step=60):
    with open(path, "w") as fh:
        for i in range(n):
            fh.write(json.dumps({key: start + i * step, "spot": {"x": 1}, "funding": 1, "oi": None}) + "\n")


def test_range_spans_rotated_parts_and_clips_until(tmp_path):
    f = ms.STREAMS["market_context"]
    _write(tmp_path / (f + ".1"), 1_790_000_000, 100)
    _write(tmp_path / (f + ".2"), 1_790_006_000, 100)
    _write(tmp_path / f, 1_790_012_000, 100)
    parts = ms.parts(tmp_path, "market_context")
    assert [p["part"] for p in parts] == [1, 2, 0]
    b = ms.part_bounds(tmp_path, "market_context", parts[1])
    assert b["first_ts"] == 1_790_006_000 and b["last_ts"] == 1_790_006_000 + 99 * 60
    since = 1_790_000_000 + 90 * 60
    h, body = ms.read_range(tmp_path, "market_context", since=since, limit_bytes=10_000_000)
    rows = [json.loads(x) for x in body.splitlines()]
    assert h["part"] == 1 and rows[0]["minute_ts"] == since and len(rows) == 10
    assert h["next_part"] == 2 and h["next_cursor"] == 0 and not h["done"]
    h2, body2 = ms.read_range(tmp_path, "market_context", since=since, until=1_790_006_000 + 5 * 60,
                              part=h["next_part"], cursor=h["next_cursor"])
    assert body2.count(b"\n") == 6 and h2["done"]
    h3, body3 = ms.read_range(tmp_path, "market_context", since=1_790_012_000 + 98 * 60)
    assert h3["part"] == 0 and body3.count(b"\n") == 2 and h3["done"]


def test_offset_for_ts_binary_search_large(tmp_path):
    f = tmp_path / "book.jsonl"
    with open(f, "w") as fh:
        for i in range(20000):
            fh.write(json.dumps({"bucket_ts": 1_791_000_000 + i, "bid": 1}) + "\n")
    off = ms.offset_for_ts(f, 1_791_012_345)
    with open(f, "rb") as fh:
        fh.seek(off)
        assert json.loads(fh.readline())["bucket_ts"] == 1_791_012_345


def test_health_reports_bounds_age_nulls_archive(tmp_path):
    f = ms.STREAMS["market_context"]
    _write(tmp_path / (f + ".1"), 1_790_000_000, 10)
    _write(tmp_path / f, 1_790_001_000, 10)
    out = ms.health(tmp_path, now=1_790_001_000 + 9 * 60 + 30)["streams"]
    mc = out["market_context"]
    assert mc["first_ts"] == 1_790_000_000 and mc["last_ts"] == 1_790_001_540 and mc["age_sec"] == 30
    assert mc["status"] == "OK" and mc["parts"] == 2 and mc["key_field_null_rate"]["oi"] == 1.0
    assert mc["key_field_null_rate"]["funding"] == 0.0 and f + ".1" in mc["archive"]["local_parts"]
    assert out["book_tape_1s"]["status"] == "MISSING"


def test_row_ts_handles_ms_and_keys():
    assert ms.row_ts({"s": 1_791_000_000}) == 1_791_000_000
    assert ms.row_ts({"ts": 1_791_000_000_500}) == 1_791_000_000.5
    assert ms.row_ts({"m": 5}) is None


# (e) tiles/trades newest-first paging ----------------------------------------------------------
def test_trades_page_descending_with_cursor():
    rows = [{"trade_id": f"t{i}", "sort_ts": float(i)} for i in range(10)]
    p1 = monitor_tiles.trades_page(rows, cursor=None, limit=3, max_bytes=10**6, epoch="e", descending=True)
    assert [r["trade_id"] for r in p1["rows"]] == ["t9", "t8", "t7"]
    p2 = monitor_tiles.trades_page(rows, cursor=p1["next_cursor"], limit=3, max_bytes=10**6, epoch="e",
                                   descending=True)
    assert [r["trade_id"] for r in p2["rows"]] == ["t6", "t5", "t4"]
    asc = monitor_tiles.trades_page(rows, cursor=None, limit=2, max_bytes=10**6, epoch="e")
    assert [r["trade_id"] for r in asc["rows"]] == ["t0", "t1"]


# (d)(f) main trade rows: ms timing + regime_at_signal -----------------------------------------
def _main_row(**extra):
    raw = {"trade_id": "gs1-x", "research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP", "dir": "LONG",
           "entry": 100.0, "exit": 101.0, "close_ts": 2000.0, "outcome_duration_sec": 60, "leverage": 100,
           "margin_usdt": 0.25, "exit_bid": 100.9, "exit_ask": 101.1,
           "event_timestamps": {"signal_ts": 1000.0, "order_sent_ts": 1000.2, "fill_ts": 1000.75}, **extra}
    return monitor_tiles.tile_trade_row(raw, status="closed", short_names={}, epoch_id="e", forced_reasons=())


def test_main_row_has_ms_timing_exit_bbo_and_regime():
    row = _main_row(regime_at_signal="VIOLENT", regime_at_signal_source="indicator_engine_regime_tag_at_order")
    assert row["timing"]["signal_to_fill_ms"] == 750 and row["timing"]["signal_to_fill_sec"] == 0.75
    assert row["exit"]["exit_bid"] == 100.9 and row["exit"]["exit_ask"] == 101.1
    assert row["trigger"]["regime_at_signal"] == "VIOLENT"
    assert row["trigger"]["regime_at_signal_source"] == "indicator_engine_regime_tag_at_order"
    # #663 (HM-approved): an unstamped row carries an explicit None / MISSING, never a silent absence.
    unset = _main_row()["trigger"]
    assert unset["regime_at_signal"] is None and unset["regime_at_signal_source"] == "MISSING"


def test_bot_stamps_regime_at_order_and_close():
    assert 'target_signal["regime_at_signal"] = _current_regime_tag()' in BOT
    assert "_stamp_regime_at_signal(trade_row, pos, master)" in BOT
    assert "regime_fn=lambda: _current_regime_tag()" not in BOT  # DISLOC engine wiring removed 23 Oct 2026
    for route in ("/api/monitor/streams/health", "/api/monitor/streams/<name>/parts",
                  "/api/monitor/tiles/field-completeness"):
        assert f"@app.route('{route}'" in BOT
    assert '"/api/monitor/tiles/field-completeness"' in BOT and "monitor_streams.read_range(" in BOT
    assert '"signal_to_fill_trades_ms": _signal_to_fill_trades_ms().get(lane)' in BOT


# Generic tile_trade_row_v1 rows (DISLOC rows/engine removed with Tiles 6/7, 23 Oct 2026) -------------
def test_field_completeness_and_s2f_stats():
    full = {"status": "CLOSED", "trade_id": "a", "exit": {"close_ts": 61.0, "exit_bid": 1, "exit_ask": 2},
            "timing": {"signal_to_fill_sec": 0.5}, "trigger": {"regime_at_signal": "QUIET"}}
    assert all(v is not None for v in data_gaps.field_values(full).values())
    empty = {"status": "CLOSED", "trade_id": "b", "exit": {"close_ts": 61.0}, "timing": {}, "trigger": {}}
    out = data_gaps.field_completeness({"L": [full, empty]})
    assert out["lanes"]["L"]["closed"] == 2 and out["lanes"]["L"]["coverage"]["exit_bid"] == 0.5
    assert out["lanes"]["L"]["sample_missing_trade_ids"]["regime_at_signal"] == ["b"]
    s = data_gaps.signal_to_fill_ms_by_lane({"L": [full, empty]}, since=0)
    assert s["L"]["n"] == 1 and s["L"]["p50_ms"] == 500.0


def test_r1_item5_exit_trigger_to_fill_by_lane_closed_rows_since_only():
    rows = [{"status": "CLOSED", "exit": {"close_ts": 200}, "timing": {"exit_trigger_to_fill_sec": 0.4}},
            {"status": "CLOSED", "exit": {"close_ts": 210}, "timing": {"exit_trigger_to_fill_sec": 0.8}},
            {"status": "CLOSED", "exit": {"close_ts": 50}, "timing": {"exit_trigger_to_fill_sec": 9.0}},
            {"status": "OPEN", "timing": {"exit_trigger_to_fill_sec": 7.0}}]
    out = data_gaps.exit_trigger_to_fill_by_lane({"L": rows}, since=100)
    assert out["L"]["n"] == 2 and out["L"]["stage"] == "exit_trigger_to_fill"
    assert 0.4 <= out["L"]["p50_s"] <= 0.8
