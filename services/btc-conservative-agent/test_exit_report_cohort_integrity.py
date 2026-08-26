from pathlib import Path
import json
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


def test_exit_dashboard_labels_current_separated_evidence_and_sample_status():
    assert "'exit-combos': ['CURRENT EXECUTED PAPER + SHADOW/LAB — SEPARATED'" in DASHBOARD
    assert "'exit-reason-leak': ['CURRENT EXECUTED PAPER + SHADOW/LAB — SEPARATED'" in DASHBOARD
    assert "c.sample_status||'DESCRIPTIVE'" in DASHBOARD
    assert "c.type||''" not in DASHBOARD


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

    leakage = analyzer.exit_leakage_by_reason_report(
        trades=pd.DataFrame(),
        session={"mode": "FRESH-COLLECTION"},
    )
    shadow_leakage = leakage["evidence_classes"]["shadow_lab"]
    assert shadow_leakage["terminal_rows"] == 6
    assert shadow_leakage["reasons"][0]["avg_left_usd"] == 0.0
    json.dumps(leakage, allow_nan=False)


def test_exit_gap_converts_margin_percentage_to_usd_before_subtracting_booked_pnl(tmp_path, monkeypatch):
    shadow = pd.DataFrame([
        {
            "trade_id": "shadow-unit-check",
            "exit_reason": "TIME_EXIT",
            "research_lane": "FAMILY_ATR_TRAIL",
            "direction": "LONG",
            "mfe_margin_pct": 40.0,
            "margin_usdt": 0.25,
            "net_pnl_usd": 0.03,
        }
    ])
    monkeypatch.setattr(analyzer, "_load_descriptive_shadow_exit_df", lambda session=None: shadow)
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))

    report = analyzer.exit_combinations_report(
        trades=pd.DataFrame(),
        session={"mode": "FRESH-COLLECTION"},
    )

    row = report["evidence_classes"]["shadow_lab"]["top"][0]
    assert row["left_on_table_usd"] == 0.07


def test_exit_family_and_stop_scorecards_keep_evidence_worlds_separate(tmp_path, monkeypatch):
    executed = pd.DataFrame([
        {
            "trade_id": "paper-1", "opportunity_id": "opp-paper-1",
            "exit_reason": "INITIAL_ATR_STOP", "research_lane": "FAMILY_ATR_TARGET_2_5",
            "cfg_family": "ATR_TARGET", "cfg_initial_stop_atr_k": 1.5,
            "cfg_hard_stop_margin_pct": 30.0, "limit_chase_count": 3,
            "net_pnl_usd": -0.10, "gross_pnl_usd": -0.09,
            "trading_fees_usd": 0.01, "funding_fees_usd": 0.0,
            "book_slippage_usd_total": 0.002, "mae_margin_pct": -31.0,
        },
        {
            "trade_id": "paper-2", "opportunity_id": "opp-paper-2",
            "exit_reason": "ATR_TP_2_5X", "research_lane": "FAMILY_ATR_TARGET_2_5",
            "cfg_family": "ATR_TARGET", "cfg_initial_stop_atr_k": 1.5,
            "cfg_hard_stop_margin_pct": 30.0, "limit_chase_count": 4,
            "net_pnl_usd": 0.20, "gross_pnl_usd": 0.21,
            "trading_fees_usd": 0.01, "funding_fees_usd": 0.0,
            "book_slippage_usd_total": 0.003, "mae_margin_pct": -4.0,
        },
    ])
    shadow = pd.DataFrame([{
        "trade_id": "shadow-1", "opportunity_id": "opp-shadow-1",
        "exit_reason": "TIME_EXIT", "research_lane": "FAMILY_CHANDELIER_3",
        "cfg_family": "CHANDELIER", "cfg_initial_stop_atr_k": 2.0,
        "cfg_hard_stop_margin_pct": 30.0, "limit_chase_count": 5,
        "net_pnl_usd": 0.50, "mae_margin_pct": -2.0,
    }])
    monkeypatch.setattr(analyzer, "_load_descriptive_shadow_exit_df", lambda session=None: shadow)
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))

    report = analyzer.exit_combinations_report(executed, {"mode": "FRESH-COLLECTION"})
    paper = report["evidence_classes"]["executed_paper"]
    lab = report["evidence_classes"]["shadow_lab"]

    assert report["schema"] == "exit_combinations_v3"
    assert paper["exit_family_scorecard"][0]["exit_family"] == "ATR_TARGET"
    assert paper["exit_family_scorecard"][0]["terminal_rows"] == 2
    assert paper["exit_family_scorecard"][0]["independent_episodes"] == 2
    assert paper["exit_family_scorecard"][0]["net_pnl_usd"] == 0.1
    assert paper["exit_family_scorecard"][0]["qualification_eligible"] is False
    assert lab["exit_family_scorecard"][0]["exit_family"] == "CHANDELIER"
    assert lab["exit_family_scorecard"][0]["net_pnl_usd"] == 0.5
    assert all(row["evidence_class"] == "EXECUTED_PAPER_DESCRIPTIVE" for row in paper["stop_effectiveness_matrix"])
    assert all(row["evidence_class"] == "SHADOW_LAB_DESCRIPTIVE" for row in lab["stop_effectiveness_matrix"])
    assert all(row["evidence_status"] == "LOW_SAMPLE_LT5_INDEPENDENT" for row in paper["stop_effectiveness_matrix"])


def test_exit_scorecard_surfaces_explicit_missing_counts(tmp_path, monkeypatch):
    rows = pd.DataFrame([{
        "trade_id": "missing-fields", "exit_reason": "TIME_EXIT",
        "research_lane": "FAMILY_ATR_TRAIL", "net_pnl_usd": 0.01,
    }])
    monkeypatch.setattr(analyzer, "_load_descriptive_shadow_exit_df", lambda session=None: pd.DataFrame())
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))

    report = analyzer.exit_combinations_report(rows, {"mode": "FRESH-COLLECTION"})
    family = report["evidence_classes"]["executed_paper"]["exit_family_scorecard"][0]
    stop = report["evidence_classes"]["executed_paper"]["stop_effectiveness_matrix"][0]
    assert family["missing_identity_rows"] == 1
    assert family["missing_cost_rows"] == 1
    assert family["missing_slippage_rows"] == 1
    assert stop["missing_mae_rows"] == 1
    assert stop["missing_stop_slippage_rows"] == 1


def test_exit_dashboard_exposes_family_and_stop_tables_for_both_worlds():
    for element_id in (
        "exit-family-scorecard-body", "exit-family-scorecard-shadow-body",
        "stop-effectiveness-body", "stop-effectiveness-shadow-body",
    ):
        assert element_id in DASHBOARD
    assert "EV is divided by independent shared opportunities" in DASHBOARD
    assert "NOT QUALIFIED" in DASHBOARD


def test_exit_combo_heading_is_explicitly_descriptive_and_unqualified():
    assert "Highest descriptive exit-combo EV — unqualified" in DASHBOARD
    assert "Small or unmatched samples" in DASHBOARD
    assert "Best exit combos (by EV)" not in DASHBOARD


def test_ladder_zero_sample_profiles_are_never_rendered_as_comparable():
    assert "const noReplayEvidence = d.data_status === 'NO_REPLAYS';" in DASHBOARD
    assert "const noComparableProfiles = noReplayEvidence || overlapZero || noSim;" in DASHBOARD
    assert "['Best profile', noComparableProfiles ? 'n/a'" in DASHBOARD
    assert "if (noComparableProfiles)" in DASHBOARD
    assert "Profiles with zero simulated trades are not ranked or displayed as results." in DASHBOARD


def test_empty_exit_views_explain_insufficient_terminal_evidence_not_analyzer_failure():
    assert "Run analyzer for exit combos." not in DASHBOARD
    assert "Run analyzer for exit reason leakage." not in DASHBOARD
    assert "Run analyzer to generate action items." not in DASHBOARD
    assert "Analyzer completed: no current-epoch terminal exit paths exist yet" in DASHBOARD
    assert "Analyzer completed: no current-epoch terminal exits exist yet" in DASHBOARD
    assert "No current-epoch terminal exits exist yet; validation action items are unavailable." in DASHBOARD
