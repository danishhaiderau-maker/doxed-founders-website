"""Same-trade 1 s exit replay: path_1s on every filled shadow_exit_path record, bounded and forward-filled."""
import shadow_exit_paths as sxp


def test_second_path_forward_fills_and_bounds():
    series = [(0.2, 1.0), (1.5, 2.0), (4.0, -1.0), (2000.0, 5.0)]
    out = sxp.second_path(series, hold_end_t=3.0)
    assert out["step_sec"] == 1 and out["seconds"] == 124 and out["exit_second"] == 3
    assert out["close"][:5] == [10, 20, 20, 20, -10]
    assert len(sxp.second_path(series)["close"]) == sxp.SECOND_PATH_MAX_SEC


def test_build_record_carries_path_1s():
    ticks = [(1000.0 + i, 100.0 + i * 0.01, 100.02 + i * 0.01, None) for i in range(300)]
    rec = sxp.build_record(source="t", trade_id="x", direction="LONG", ticks=ticks, signal_ts=999.0,
                           fill_ts=1000.0, entry_price=100.0, shadow_set=[], exit_ts=1060.0)
    p = rec["path_1s"]
    assert p["exit_second"] == 60 and p["seconds"] == 181 and p["close"][0] == 0
