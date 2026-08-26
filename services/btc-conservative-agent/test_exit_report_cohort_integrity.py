from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analyzer_research_engine_v62 as analyzer

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


def test_exit_reports_do_not_use_live_copy_qualification_filter():
    combos = ANALYZER.split("def exit_combinations_report", 1)[1].split("EXIT_LEAK_ACTION_MAP", 1)[0]
    leakage = ANALYZER.split("def exit_leakage_by_reason_report", 1)[1].split("LADDER_SIM_PROFILES", 1)[0]
    assert "_filter_policy_analysis_df" not in combos
    assert "_filter_policy_analysis_df" not in leakage
    assert '"EXECUTED_PAPER_DESCRIPTIVE"' in combos
    assert '"SHADOW_LAB_DESCRIPTIVE"' in combos
    assert '"LOW_SAMPLE_N1"' in combos
    assert '"qualification_eligible": False' in combos
    assert '"EXECUTED_PAPER_DESCRIPTIVE"' in leakage
    assert '"SHADOW_LAB_DESCRIPTIVE"' in leakage


def test_exit_dashboard_renders_separated_shadow_terminal_evidence():
    assert "exit-shadow-combos-body" in DASHBOARD
    assert "exit-reason-shadow-body" in DASHBOARD
    assert "No explicit shadow/lab terminal exit evidence in this epoch." in DASHBOARD
    assert "Executed-paper and shadow/lab rows are never merged" in DASHBOARD


def test_sparse_shadow_exit_without_mfe_or_booked_pnl_does_not_abort(tmp_path, monkeypatch):
    # Six rows intentionally match the six grouping dimensions.  Without an
    # explicit fallback for the missing research_lane column, pandas may treat
    # the dimension-name list as a row-wise grouping vector.
    shadow = pd.DataFrame([
        {
            "trade_id": f"shadow-terminal-{index}",
            "exit_reason": "TIME_EXIT",
            "direction": "LONG",
        }
        for index in range(6)
    ])
    monkeypatch.setattr(analyzer, "_load_descriptive_shadow_exit_df", lambda session=None: shadow)
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))

    report = analyzer.exit_combinations_report(
        trades=pd.DataFrame(),
        session={"mode": "FRESH-COLLECTION"},
    )

    shadow_report = report["evidence_classes"]["shadow_lab"]
    assert shadow_report["terminal_rows"] == 6
    assert shadow_report["total_combos"] == 1
    assert shadow_report["top"][0]["left_on_table_usd"] == 0.0
    assert shadow_report["top"][0]["lane"] == "UNKNOWN"
