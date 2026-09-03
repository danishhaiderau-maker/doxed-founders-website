from discovery_cohort_scorecard import build_episode_matched_scorecard


def test_three_worlds_never_sum_pnl():
    rows = [
        {"evidence_world": "OBSERVED_PAPER", "episode_id": "e1", "adx_bucket": "STRONG",
         "offset_pct": 0.30, "chase_policy": "w234", "exit_family": "ATR_TRAIL", "net_pnl_usd": 1.0},
        {"evidence_world": "IDEAL_TOUCH", "episode_id": "e1", "adx_bucket": "STRONG",
         "offset_pct": 0.30, "chase_policy": "w234", "exit_family": "ATR_TRAIL", "net_pnl_usd": 2.0},
        {"evidence_world": "CONSERVATIVE_BBO", "episode_id": "e1", "adx_bucket": "STRONG",
         "offset_pct": 0.30, "chase_policy": "w234", "exit_family": "ATR_TRAIL", "net_pnl_usd": 0.5},
    ]
    report = build_episode_matched_scorecard(rows)
    assert report["pnl_sum_across_worlds"] is False
    assert report["worlds"]["OBSERVED_PAPER"]["cells"][0]["mean_net_pnl_usd"] == 1.0
    assert report["worlds"]["IDEAL_TOUCH"]["cells"][0]["mean_net_pnl_usd"] == 2.0
    assert report["worlds"]["CONSERVATIVE_BBO"]["cells"][0]["mean_net_pnl_usd"] == 0.5
    assert report["relay_eligible"] is False
