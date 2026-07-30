from pathlib import Path


ROOT = Path(__file__).parent
ANALYZER = (ROOT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "research" / "research_dashboard.py").read_text(encoding="utf-8")


def test_ladder_delta_uses_only_the_matched_replay_cohort():
    assert "matched_actual_realized_usd" in ANALYZER
    assert "delta_vs_matched_actual_usd" in ANALYZER
    assert "sum_pnl - matched_actual_sum" in ANALYZER
    assert "sum_pnl - actual_sum" not in ANALYZER
    assert "matched_executed_trade_replay_cohort" in ANALYZER


def test_dashboard_does_not_claim_combined_reports_are_lane_filtered():
    ladder_section = DASHBOARD.split('<section id="sec-ladder-sim">', 1)[1].split("</section>", 1)[0]
    leak_section = DASHBOARD.split('<section id="sec-exit-reason-leak">', 1)[1].split("</section>", 1)[0]
    assert "chase-lane-filter" not in ladder_section
    assert "chase-lane-filter" not in leak_section
    assert "Combined Lanes" in ladder_section
    assert "Combined Lanes" in leak_section


def test_hindsight_gap_is_not_presented_as_capturable_profit():
    assert '"capturable profit and not evidence that a ladder change will improve PnL."' in ANALYZER
    assert "REPLAY REQUIRED" in ANALYZER
    assert "Expected gain:" not in DASHBOARD
