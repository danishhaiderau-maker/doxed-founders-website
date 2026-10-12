import exit_latency_slo as e


def test_guard_split_lag_vs_paper_and_fill_to_exit():
    by = {"dsl-1": [
        {"type": "ORDER_FILLED", "lane": "FAMILY_DISLOC_BFX_DISLOCATION_1M", "event_at_ts": 1000.0},
        {"type": "POSITION_CLOSED", "lane": "FAMILY_DISLOC_BFX_DISLOCATION_1M", "event_at_ts": 1063.5}]}
    twins = {"dsl-1": {"exit_ts": 1061.0}}
    out = e.live_follow(by, twins, since=0)
    lane = next(iter(out["lanes"].values()))
    assert lane["metric"] == "lag_vs_paper_sec"
    assert lane["p50_sec"] == 2.5          # lag behind paper: GREEN
    assert lane["fill_to_exit"]["p50_sec"] == 63.5  # hold, reported separately
    assert lane["status"] == "GREEN"
