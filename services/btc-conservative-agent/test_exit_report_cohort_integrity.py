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
    assert "_load_exit_evidence_worlds" in combos
    assert '"LOW_SAMPLE_N1"' in combos
    assert '"qualification_eligible": False' in combos
    assert "_load_exit_evidence_worlds" in leakage


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
    assert shadow_report["top"][0]["left_on_table_usd"] is None
    assert shadow_report["top"][0]["lane"] == "UNKNOWN"

    leakage = analyzer.exit_leakage_by_reason_report(
        trades=pd.DataFrame(),
        session={"mode": "FRESH-COLLECTION"},
    )
    shadow_leakage = leakage["evidence_classes"]["shadow_lab"]
    assert shadow_leakage["terminal_rows"] == 6
    assert shadow_leakage["reasons"][0]["avg_left_usd"] is None
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

    assert report["schema"] == "exit_combinations_v4"
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
    assert "Family-balanced descriptive exit-combo EV — unqualified" in DASHBOARD
    assert "Small or unmatched samples" in DASHBOARD
    assert "Best exit combos (by EV)" not in DASHBOARD


def test_ladder_zero_sample_profiles_are_never_rendered_as_comparable():
    assert "const noReplayEvidence = ['NO_REPLAYS','NO_ELIGIBLE_REPLAYS'].includes(d.data_status);" in DASHBOARD
    assert "const noComparableProfiles = noReplayEvidence || overlapZero || noSim;" in DASHBOARD
    assert "['Best profile', noComparableProfiles ? 'n/a'" in DASHBOARD
    assert "if (noComparableProfiles)" in DASHBOARD
    assert "Profiles with zero simulated trades are not ranked or displayed as results." in DASHBOARD


def test_ladder_distinguishes_missing_file_from_ineligible_current_epoch_replays(tmp_path, monkeypatch):
    raw = {
        "legacy-trade": {
            "trade_id": "legacy-trade",
            "ticks": [{"t": 0, "price": 100.0}],
        }
    }
    monkeypatch.setattr(analyzer, "_load_jsonl_replays", lambda: raw)
    monkeypatch.setattr(analyzer, "_filter_policy_analysis_replays", lambda rows, label: {})
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))

    report = analyzer.exit_ladder_simulator_report(
        trades=pd.DataFrame(),
        session={"mode": "FRESH-COLLECTION"},
    )

    assert report["data_status"] == "NO_ELIGIBLE_REPLAYS"
    assert report["raw_replays_available"] == 1
    assert report["eligible_replays_available"] == 0
    assert "none belongs to a current-epoch eligible executed trade" in report["empty_reason"]
    assert "No signal_replay.jsonl" not in report["empty_reason"]


def test_empty_exit_views_explain_insufficient_terminal_evidence_not_analyzer_failure():
    assert "Run analyzer for exit combos." not in DASHBOARD
    assert "Run analyzer for exit reason leakage." not in DASHBOARD
    assert "Run analyzer to generate action items." not in DASHBOARD
    assert "Analyzer completed: no current-epoch terminal exit paths exist yet" in DASHBOARD
    assert "Analyzer completed: no current-epoch terminal exits exist yet" in DASHBOARD
    assert "No current-epoch terminal exits exist yet; validation action items are unavailable." in DASHBOARD


def test_exit_world_loader_includes_lane_shadow_and_four_non_additive_worlds(monkeypatch):
    monkeypatch.setattr(analyzer, "_load_descriptive_shadow_exit_df", lambda session=None: pd.DataFrame())
    monkeypatch.setattr(analyzer, "_report_exit_rows", lambda *args: pd.DataFrame())
    worlds = analyzer._load_exit_evidence_worlds(pd.DataFrame(), {})
    assert set(worlds) == {
        "executed_paper", "shadow_lab", "conservative_bbo_depth", "ideal_touch_diagnostic",
    }
    assert analyzer.SHADOW_LANE_OUTCOME_FILE in worlds["shadow_lab"]["source_files"]
    assert all(world["pnl_semantics"] for world in worlds.values())


def test_four_world_reports_keep_missing_metrics_unavailable(tmp_path, monkeypatch):
    sparse = pd.DataFrame([{"trade_id": "terminal-1", "exit_reason": "TIME_EXIT"}])
    worlds = {
        "executed_paper": {"evidence_class": "EXECUTED_PAPER_DESCRIPTIVE", "source_files": ["paper"], "frame": sparse, "pnl_semantics": "OBSERVED"},
        "shadow_lab": {"evidence_class": "SHADOW_LAB_DESCRIPTIVE", "source_files": ["shadow"], "frame": sparse, "pnl_semantics": "SIMULATED"},
        "conservative_bbo_depth": {"evidence_class": "CONSERVATIVE_BBO_DEPTH_DESCRIPTIVE", "source_files": ["conservative"], "frame": sparse, "pnl_semantics": "CONSERVATIVE"},
        "ideal_touch_diagnostic": {"evidence_class": "IDEAL_TOUCH_DIAGNOSTIC_ONLY", "source_files": ["ideal"], "frame": sparse, "pnl_semantics": "DIAGNOSTIC"},
    }
    monkeypatch.setattr(analyzer, "_load_exit_evidence_worlds", lambda trades=None, session=None: worlds)
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))
    combos = analyzer.exit_combinations_report(pd.DataFrame(), {})
    leakage = analyzer.exit_leakage_by_reason_report(pd.DataFrame(), {})
    assert combos["schema"] == "exit_combinations_v4"
    assert leakage["schema"] == "exit_leakage_by_reason_v5"
    for world in worlds:
        combo = combos["evidence_worlds"][world]
        leak = leakage["evidence_worlds"][world]
        assert combo["top"][0]["pnl_usd"] is None
        assert combo["top"][0]["ev_usd"] is None
        assert combo["pnl_aggregation_allowed"] is False
        assert leak["reasons"][0]["booked_profit_usd"] is None
        assert leak["reasons"][0]["left_on_table_usd"] is None
    json.dumps(combos, allow_nan=False)
    json.dumps(leakage, allow_nan=False)


def test_dashboard_renders_all_four_exit_worlds_and_null_kpis_as_na():
    for element_id in (
        "exit-conservative-combos-body", "exit-ideal-touch-combos-body",
        "exit-reason-conservative-body", "exit-reason-ideal-touch-body",
    ):
        assert element_id in DASHBOARD
    assert "['Left on table', money(d.overall_left_on_table_usd)]" in DASHBOARD
    assert "['Hindsight gap', money(d.overall_left_usd)]" in DASHBOARD
    for element_id in (
        "exit-causal-policy-body",
        "exit-causal-risk-body",
        "exit-causal-market-body",
        "exit-causal-entry-body",
        "exit-causal-direction-quality-body",
        "exit-causal-sr-geometry-body",
        "exit-causal-execution-quality-body",
        "exit-causal-partial-profit-body",
        "exit-causal-chase-detail-body",
        "exit-causal-excursion-timing-body",
        "exit-causal-regime-transition-body",
        "exit-causal-fill-revalidation-body",
        "exit-causal-terminal-order-body",
    ):
        assert element_id in DASHBOARD
    assert "renderCausalView('exit_policy')" in DASHBOARD
    assert "renderCausalView('risk_and_chase')" in DASHBOARD
    assert "renderCausalView('market_context')" in DASHBOARD
    assert "renderCausalView('entry_execution')" in DASHBOARD
    assert "renderCausalView('direction_quality')" in DASHBOARD
    assert "renderCausalView('sr_geometry')" in DASHBOARD
    assert "renderCausalView('execution_quality')" in DASHBOARD
    assert "renderCausalView('partial_profit_path')" in DASHBOARD
    assert "renderCausalView('chase_detail')" in DASHBOARD
    assert "renderCausalView('excursion_timing')" in DASHBOARD
    assert "renderCausalView('regime_transition')" in DASHBOARD
    assert "renderCausalView('fill_revalidation')" in DASHBOARD
    assert "renderCausalView('terminal_order_outcome')" in DASHBOARD
    assert "coverage ${coverage}%" in DASHBOARD
    assert "['CONSERVATIVE BBO/DEPTH', conservative]" in DASHBOARD
    assert "['IDEAL TOUCH DIAGNOSTIC', idealTouch]" in DASHBOARD


def test_causal_exit_views_use_only_explicit_available_dimensions():
    rows = pd.DataFrame([
        {
            "trade_id": "paper-1", "opportunity_id": "opp-1",
            "exit_reason": "ATR_TARGET", "cfg_family": "FIXED_TARGET",
            "cfg_exit_profile_id": "ATR_TP_2_5", "final_direction": "LONG",
            "regime": "BULL", "cfg_initial_stop_atr_k": 1.5,
            "cfg_hard_stop_margin_pct": 30.0, "limit_chase_count": 3,
            "cfg_entry_offset_fraction": 0.0003, "entry_delay_min": 12,
            "source_fill_status": "FILLED", "net_pnl_usd": 0.2,
        },
        {
            "trade_id": "paper-2", "opportunity_id": "opp-2",
            "exit_reason": "ATR_STOP", "cfg_family": "FIXED_TARGET",
            "cfg_exit_profile_id": "ATR_TP_2_5", "final_direction": "LONG",
            "regime": "BULL", "cfg_initial_stop_atr_k": 1.5,
            "cfg_hard_stop_margin_pct": 30.0, "limit_chase_count": 4,
            "cfg_entry_offset_fraction": 0.0003, "entry_delay_min": 18,
            "source_fill_status": "FILLED", "net_pnl_usd": -0.1,
        },
    ])
    views = analyzer._exit_causal_combination_views(rows, "EXECUTED_PAPER_DESCRIPTIVE")
    assert set(views) == {
        "exit_policy", "risk_and_chase", "market_context", "entry_execution",
        "market_microstructure", "profit_path", "cost_and_fill",
        "direction_quality", "sr_geometry", "execution_quality",
        "partial_profit_path", "chase_detail", "excursion_timing",
        "regime_transition",
        "fill_revalidation", "terminal_order_outcome",
    }
    assert views["exit_policy"]["rows"]
    assert views["market_context"]["rows"]
    assert views["entry_execution"]["rows"]
    assert views["direction_quality"]["empty_reason"] == "INSUFFICIENT_EXPLICIT_DIMENSIONS"
    assert views["exit_policy"]["coverage_pct"] == 100.0
    assert all(row["qualification_eligible"] is False for row in views["risk_and_chase"]["rows"])


def test_high_value_exit_views_use_collected_v31_fields_and_report_coverage():
    row = {
        "trade_id": "paper-rich", "opportunity_id": "opp-rich",
        "exit_reason": "ATR_TARGET", "cfg_family": "FIXED_TARGET",
        "cfg_exit_profile_id": "ATR_TP_2_5", "final_direction": "LONG",
        "adx_at_entry": 24, "mtf_agreement_at_entry": .8,
        "structure_bias_at_entry": "BULLISH", "sr_state": "NEAR_SUPPORT",
        "distance_to_support": .2, "distance_to_resistance": 1.2,
        "execution_entry_type": "MAKER", "execution_exit_type": "TAKER",
        "entry_partial_fill": True, "fill_model": "CONSERVATIVE_BBO_DEPTH_TAPE",
        "book_slippage_usd_total": .001, "limit_chase_count": 4,
        "urgent_chase_tier": "NORMAL", "entry_delay_sec": 900,
        "cfg_entry_offset_fraction": .0003,
        "partial_exit_receipts": [{"fraction": .25}],
        "policy_remaining_fraction": 0, "net_pnl_usd": .2,
        "path_extrema": {"mae_pct": -.2, "mfe_pct": .8,
                         "time_to_mae_sec": 30, "time_to_mfe_sec": 420},
        "exit_context": {"regime": "SIDEWAYS", "adx": 16},
        "fill_time_revalidation": {"result": "PASSED", "reason": "CURRENT",
                                   "signal_age_sec": 900},
        "terminal_no_fill": False, "terminal_ttl_expired": False,
        "terminal_reason": "ATR_TARGET",
    }
    views = analyzer._exit_causal_combination_views(
        pd.DataFrame([row]), "EXECUTED_PAPER_DESCRIPTIVE"
    )
    for name in (
        "direction_quality", "sr_geometry", "execution_quality",
        "partial_profit_path", "chase_detail",
        "excursion_timing", "regime_transition",
        "fill_revalidation", "terminal_order_outcome",
    ):
        assert views[name]["rows"], name
        assert views[name]["source_terminal_rows"] == 1
        assert views[name]["eligible_rows"] == 1
        assert views[name]["coverage_pct"] == 100.0
        assert views[name]["rows"][0]["evidence_class"] == "EXECUTED_PAPER_DESCRIPTIVE"


def test_execution_quality_does_not_treat_string_false_as_partial_fill():
    row = {
        "trade_id": "paper-full", "opportunity_id": "opp-full",
        "exit_reason": "ATR_TARGET", "execution_entry_type": "MAKER",
        "entry_partial_fill": "false", "net_pnl_usd": .1,
    }
    view = analyzer._exit_causal_combination_views(
        pd.DataFrame([row]), "EXECUTED_PAPER_DESCRIPTIVE"
    )["execution_quality"]
    assert view["rows"]
    assert "FULL" in view["rows"][0]["combination"]
    assert "PARTIAL" not in view["rows"][0]["combination"]


def test_exit_views_consume_nested_v31_execution_receipts_without_inference():
    row = {
        "trade_id": "paper-nested", "opportunity_id": "opp-nested",
        "exit_reason": "TIME_EXIT", "cfg_family": "ATR_TRAIL",
        "final_direction": "SHORT", "net_pnl_usd": -.1,
        "entry_context": {"regime": "BEAR", "adx": 34,
                          "sr_state": "AT_RESISTANCE",
                          "dist_to_support": 1.4, "dist_to_resistance": .1},
        "exit_context": {"regime": "SIDEWAYS", "adx": 17},
        "path_extrema": {"mae_pct": -.4, "mfe_pct": .6,
                         "time_to_mae_sec": 120, "time_to_mfe_sec": 600},
        "protection_trajectory": {"terminal_remaining_fraction": 0,
                                  "partial_exit_count": 2},
        "partial_exits": [{"sequence": 1}, {"sequence": 2}],
        "fill_time_revalidation": {"result": "PASSED", "reason": "CURRENT",
                                   "signal_age_sec": 120},
        "terminal_no_fill": False, "terminal_ttl_expired": False,
        "terminal_reason": "TIME_EXIT",
    }
    views = analyzer._exit_causal_combination_views(
        pd.DataFrame([row]), "EXECUTED_PAPER_DESCRIPTIVE"
    )
    for name in ("direction_quality", "sr_geometry", "partial_profit_path",
                 "excursion_timing", "regime_transition"):
        assert views[name]["rows"], name
        assert views[name]["coverage_pct"] == 100.0


def test_causal_exit_views_do_not_rank_unknown_or_missing_dimensions():
    sparse = pd.DataFrame([{"trade_id": "x", "exit_reason": "TIME_EXIT"}])
    views = analyzer._exit_causal_combination_views(sparse, "SHADOW_LAB_DESCRIPTIVE")
    assert all(view["rows"] == [] for view in views.values())
    assert all(view["empty_reason"] == "INSUFFICIENT_EXPLICIT_DIMENSIONS" for view in views.values())


def test_family_balanced_exit_ranking_caps_each_family(tmp_path, monkeypatch):
    rows = pd.DataFrame([
        {
            "trade_id": f"trade-{index}", "opportunity_id": f"opp-{index}",
            "exit_reason": f"EXIT_{index}", "research_lane": family,
            "cfg_family": family, "net_pnl_usd": pnl,
        }
        for index, (family, pnl) in enumerate([
            ("FIXED_TARGET", .50), ("FIXED_TARGET", .40), ("FIXED_TARGET", .30),
            ("ATR_TRAIL", .20), ("ATR_TRAIL", .10), ("CHANDELIER", .05),
        ])
    ])
    monkeypatch.setattr(analyzer, "_load_descriptive_shadow_exit_df", lambda session=None: pd.DataFrame())
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))
    report = analyzer.exit_combinations_report(rows, {"mode": "FRESH-COLLECTION"})
    paper = report["evidence_worlds"]["executed_paper"]
    balanced = paper["top_family_balanced"]
    assert [row["family"] for row in balanced].count("FIXED_TARGET") == 2
    assert {row["family"] for row in balanced} == {"FIXED_TARGET", "ATR_TRAIL", "CHANDELIER"}
    assert paper["family_balance"] == {"max_per_family": 2, "families_represented": 3}


def test_missing_peak_data_never_becomes_zero_capture(tmp_path, monkeypatch):
    sparse = pd.DataFrame([{
        "trade_id": "sparse", "opportunity_id": "opp-sparse",
        "exit_reason": "TIME_EXIT", "net_pnl_usd": 0.01,
    }])
    monkeypatch.setattr(analyzer, "_load_exit_evidence_worlds", lambda trades=None, session=None: {
        name: {"evidence_class": label, "source_files": [name], "frame": sparse, "pnl_semantics": name}
        for name, label in {
            "executed_paper": "EXECUTED_PAPER_DESCRIPTIVE",
            "shadow_lab": "SHADOW_LAB_DESCRIPTIVE",
            "conservative_bbo_depth": "CONSERVATIVE_BBO_DEPTH_DESCRIPTIVE",
            "ideal_touch_diagnostic": "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
        }.items()
    })
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))
    report = analyzer.exit_leakage_by_reason_report(sparse, {})
    assert report["reasons"][0]["capture_ratio_pct"] is None


def test_dashboard_renders_expanded_exit_views_and_family_balanced_heading():
    for element_id in (
        "exit-causal-microstructure-body", "exit-causal-profit-path-body",
        "exit-causal-cost-body",
    ):
        assert element_id in DASHBOARD
    assert "renderCausalView('market_microstructure')" in DASHBOARD
    assert "renderCausalView('profit_path')" in DASHBOARD
    assert "renderCausalView('cost_and_fill')" in DASHBOARD
    assert "Family-balanced descriptive exit-combo EV" in DASHBOARD
    assert 'executed.get("top_family_balanced") or executed.get("top")' in DASHBOARD


def test_legacy_empty_states_are_explicit_and_do_not_request_analyzer_rerun():
    assert "No legacy spread-performance evidence exists in the current cohort." in DASHBOARD
    assert "No spread performance data - run analyzer after fresh collection." not in DASHBOARD
    assert "No legacy hindsight exit-leakage evidence exists in the current cohort." in DASHBOARD
