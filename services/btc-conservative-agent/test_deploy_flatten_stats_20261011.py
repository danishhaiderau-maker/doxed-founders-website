import pathlib
import monitor_tiles


def _row(net, bp, forced=False, deploy=False, side="LONG", ts=1.0):
    return {"status": "CLOSED", "side": side,
            "pnl": {"net_usd": net, "bp": bp},
            "exit": {"close_ts": ts, "forced_close": forced, "deploy_flatten": deploy}}


def test_lane_totals_separates_deploy_flatten():
    rows = [_row(1.0, 10.0, ts=1), _row(-0.5, -5.0, ts=2),
            _row(-2.0, -20.0, forced=True, deploy=True, ts=3),
            _row(0.3, 3.0, forced=True, deploy=True, ts=4),
            _row(9.0, 90.0, forced=True, deploy=False, ts=5)]
    t = monitor_tiles.lane_totals(rows, include_forced=False, be_band_bp=0.5)
    assert t["closes"] == 2
    assert abs(t["net_usd"] - 0.5) < 1e-9
    assert t["forced_closes"] == 3
    assert t["deploy_flatten_closes"] == 2
    df = t["deploy_flatten"]
    assert df["closes"] == 2 and df["excluded_from_strategy_stats"] is True
    assert abs(df["net_usd"] - (-1.7)) < 1e-9
    assert df["sum_bp"] == -17.0 and df["mean_bp"] == -8.5


def test_lane_totals_no_deploy_flatten():
    t = monitor_tiles.lane_totals([_row(1.0, 10.0)], include_forced=False, be_band_bp=0.5)
    assert t["deploy_flatten"] == {"closes": 0, "net_usd": 0.0, "sum_bp": 0.0,
                                   "mean_bp": None, "excluded_from_strategy_stats": True}


def test_bot_lanes_payload_has_separate_deploy_flatten_block():
    src = pathlib.Path(__file__).with_name("bot.py").read_text()
    assert "_MONITOR_LANE_DEPLOY_FLATTENED_NET" in src
    assert '"deploy_flatten": {' in src
    assert '"deploy_flatten": _forced_close_origin(exit_reason) == "DEPLOY_MAINTENANCE"' in src
