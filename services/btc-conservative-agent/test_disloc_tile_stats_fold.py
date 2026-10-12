import disloc_engine as d
import monitor_tiles


def _rows():
    base = {"schema": d.SCHEMA, "event": "DISLOC_CLOSED", "side": "LONG", "exit_reason": "TIME_60S"}
    return [
        {**base, "trade_id": "a", "pnl_bp_vs_touch": 4.0, "signal_to_fill_s": 0.3, "fill_ts": 10, "exit_ts": 70},
        {**base, "trade_id": "b", "pnl_bp_vs_touch": -2.0, "signal_to_fill_s": 3.0, "fill_ts": 100, "exit_ts": 160},
        {"schema": d.SCHEMA, "event": "DISLOC_CANCELLED", "trade_id": "c"},
    ]


def test_lane_metrics_side_and_delay_buckets():
    m = d.lane_metrics(_rows(), margin_usd=0.25, leverage=100)
    # Scored only on fills <= 2 s (Strategist 12 Oct); the 3 s fill stays in the delay buckets.
    assert m["closes"] == 1 and m["wins"] == 1 and m["losses"] == 0 and m["unscored_late_fills"] == 1
    assert abs(m["net_pnl_usd"] - 0.01) < 1e-9
    assert m["by_side"]["LONG"]["closes"] == 1
    assert m["by_side"]["LONG"]["tag"] == "LONG_CONFIRMED_BY_DEEP_SEARCH"
    assert m["by_delay_bucket"]["le_2s"]["closes"] == 1
    assert m["by_delay_bucket"]["2_to_5s"]["closes"] == 1


def test_tile_trade_rows_feed_lane_totals():
    rows = d.tile_trade_rows(_rows(), "FAMILY_DISLOC_BFX_DISLOCATION_1M", margin_usd=0.25, leverage=100)
    assert len(rows) == 2 and all(r["status"] == "CLOSED" for r in rows)
    assert rows[0]["disloc"]["delay_bucket"] == "le_2s"
    t = monitor_tiles.lane_totals(rows, include_forced=False, be_band_bp=0.0)
    assert t["closes"] == 2
