"""Truthfulness regressions for secondary analyzer reports with no terminal evidence."""

import importlib.util
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent


def _load_analyzer():
    spec = importlib.util.spec_from_file_location(
        "secondary_empty_evidence_analyzer",
        ROOT / "analyzer_research_engine_v62.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ai_calibration_and_direction_empty_stats_are_unavailable(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    monkeypatch.setattr(analyzer, "AI_CALIBRATION_REPORT_FILE", str(tmp_path / "ai.json"))
    monkeypatch.setattr(analyzer, "DIRECTION_REPORT_FILE", str(tmp_path / "direction.json"))
    monkeypatch.setattr(analyzer, "_load_jsonl_rows", lambda _path: [])
    monkeypatch.setattr(analyzer, "_build_ai_calibration_cohort", lambda *_args: pd.DataFrame())

    calibration = analyzer.ai_calibration_report(pd.DataFrame(), session={})
    direction = analyzer.direction_attribution_report(pd.DataFrame(), session={})

    assert calibration["sample_size"]["approve_rows"] == 0
    for edge_cells in calibration["confidence_edge_matrix"]["matrix"].values():
        for cell in edge_cells.values():
            assert cell == {"trades": 0, "win_rate_pct": None}
    for side in ("long", "short"):
        assert direction[side]["trades"] == 0
        assert direction[side]["win_rate_pct"] is None
        assert direction[side]["sum_pnl_usd"] is None
        assert direction[side]["ev_usd"] is None
        assert direction[side]["avg_edge"] is None
    assert direction["long_wr"] is None
    assert direction["short_pnl"] is None


def test_direction_report_accepts_a_one_sided_trade_cohort(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    monkeypatch.setattr(analyzer, "DIRECTION_REPORT_FILE", str(tmp_path / "direction.json"))
    monkeypatch.setattr(analyzer, "_load_jsonl_rows", lambda _path: [])

    report = analyzer.direction_attribution_report(
        pd.DataFrame([{
            "trade_id": "long-only",
            "final_direction": "LONG",
            "regime": "TREND",
            "net_pnl_usd": 0.02,
            "edge_score": 3.7,
        }]),
        session={},
    )

    assert report["long"]["trades"] == 1
    assert report["short"]["trades"] == 0
    assert report["short"]["win_rate_pct"] is None
    assert report["short"]["sum_pnl_usd"] is None


def test_pathway_empty_lanes_keep_counts_but_null_derived_metrics(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    monkeypatch.setattr(analyzer, "PATHWAY_SURVIVAL_REPORT_FILE", str(tmp_path / "pathway.json"))
    monkeypatch.setattr(analyzer, "_load_jsonl_rows", lambda _path: [])

    report = analyzer.pathway_survival_report(pd.DataFrame(), session={})

    assert report["benchmark_ev_per_fill_usd"] is None
    for lane in report["lanes"].values():
        assert lane["approves"] == 0
        assert lane["fills"] == 0
        assert lane["wins"] == 0
        assert lane["losses"] == 0
        assert lane["net_pnl_usd"] is None
        assert lane["win_rate_pct"] is None
        assert lane["approve_to_fill_pct"] is None
        assert lane["ev_per_fill_usd"] is None

    monkeypatch.setattr(
        analyzer,
        "_load_jsonl_rows",
        lambda _path: [{
            "trade_id": "pending-approval",
            "research_lane": analyzer.BENCHMARK_LANE,
            "stage": "APPROVE",
        }],
    )
    approvals_only = analyzer.pathway_survival_report(pd.DataFrame(), session={})
    benchmark = approvals_only["lanes"][analyzer.BENCHMARK_LANE]
    assert benchmark["approves"] == 1
    assert benchmark["fills"] == 0
    assert benchmark["approve_to_fill_pct"] is None


def test_top_leakage_is_null_without_leakage_eligible_terminal_evidence(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    monkeypatch.setattr(analyzer, "TOP_LEAKAGE_REPORT_FILE", str(tmp_path / "leakage.json"))

    empty = analyzer.top_leakage_report(pd.DataFrame(), session={})
    unavailable = analyzer.top_leakage_report(
        pd.DataFrame([{"trade_id": "t1", "net_pnl_usd": None}]),
        session={},
    )

    for report in (empty, unavailable):
        assert report["eligible_terminal_trades"] == 0
        assert report["overall_left_usd"] is None
        assert report["trades"] == []
        assert report["by_exit_reason"] == {}

    verified_zero = analyzer.top_leakage_report(
        pd.DataFrame([{
            "trade_id": "t2",
            "net_pnl_usd": 0.0,
            "max_profit": 0.0,
            "pnl": 0.0,
            "margin_usdt": 1.0,
        }]),
        session={},
    )
    assert verified_zero["eligible_terminal_trades"] == 1
    assert verified_zero["overall_left_usd"] == 0.0


def test_highlights_render_unavailable_derived_metrics_as_na():
    analyzer = _load_analyzer()
    payload = {
        "session_scope": "FRESH-COLLECTION",
        "highlights": {
            "best_lane": None,
            "worst_lane": None,
            "best_confidence": None,
            "worst_confidence": None,
            "fast_cut_damage_usd": 0.0,
            "blocked_opportunity_usd": 0.0,
            "edge_correlation": None,
            "best_exit": None,
        },
        "performance": {"trades": 0, "mfe_capture_pct": None},
        "benchmark": {"lanes": []},
        "ai_calibration": {"bands": [], "verdict": "NO_DATA"},
        "confidence_bands": [],
        "chase": {"assisted_fills": 0, "total_fills": 0, "buckets": []},
        "scenario_c": {"leakage_left_usd": None, "capture_pct": None, "fast_cut_trades": 0},
        "edge_validation": {},
        "real_edge": {
            "approve_attempts": 0,
            "executed": 0,
            "executed_pnl_usd": 0.0,
            "counterfactual_all_approve_usd": 0.0,
            "gate_damage_usd": 0.0,
        },
    }

    text = analyzer.format_research_highlights_text(payload)

    assert "Top Lane:     n/a  $n/a" in text
    assert "Best Conf:    n/a  n/a WR" in text
    assert "Fast-Cut PnL: $n/a" in text
    assert "Leakage left on table: $n/a" in text
    assert "MFE capture: n/a" in text
    assert "--- APPROVE funnel ---" in text
    assert "+0.00" not in text
    assert "Top Lane:     n/a  $+0.00" not in text
    assert "Best Conf:    n/a  0.0% WR" not in text


def test_empty_findings_do_not_invent_gate_or_blocked_profit():
    analyzer = _load_analyzer()
    payload = {
        "performance": {"trades": 0, "net_pnl_usd": None},
        "real_edge": {
            "approve_attempts": 0,
            "gate_damage_usd": 0.0,
            "blocked_shadow_pnl_usd": None,
        },
        "benchmark": {"best_lane": None, "worst_lane": None},
        "scenario_c": {"fast_cut_trades": 0, "leakage_left_usd": None},
        "coverage": {"confidence_status": "POOR", "confidence_note": "no outcomes"},
    }

    assert analyzer._blocked_opportunity_usd([], payload["real_edge"]) is None
    findings = analyzer._generate_research_findings(payload)
    assert not any("Gate damage" in finding for finding in findings)
    assert not any("Blocked shadow opportunity" in finding for finding in findings)


def test_static_dashboard_renders_unavailable_money_as_na(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    output = tmp_path / "analysis_dashboard.html"
    monkeypatch.setattr(analyzer, "ANALYSIS_DASHBOARD_HTML", str(output))

    empty_report = tmp_path / "empty.json"
    empty_report.write_text("{}", encoding="utf-8")
    for attr in (
        "BENCHMARK_VS_LANES_REPORT_FILE",
        "AI_CALIBRATION_REPORT_FILE",
        "CHASE_ATTRIBUTION_REPORT_FILE",
        "SCENARIO_C_LEAKAGE_REPORT_FILE",
        "HORIZON_PROFITABILITY_REPORT_FILE",
        "FAST_CUT_SURVIVOR_REPORT_FILE",
    ):
        monkeypatch.setattr(analyzer, attr, str(empty_report))

    payload = {
        "performance": {
            "trades": 0,
            "win_rate_pct": None,
            "net_pnl_usd": None,
            "expectancy_usd": None,
            "mfe_capture_pct": None,
        },
        "real_edge": {"approve_attempts": 0, "gate_damage_usd": None},
        "scenario_c": {"leakage_left_usd": None},
        "coverage": {},
        "analysis_provenance": {},
    }

    assert analyzer.write_analysis_dashboard_html(payload) is True
    html = output.read_text(encoding="utf-8")
    assert html.count("$n/a") >= 4
    assert "Net PnL</div><div class=\"val\">$+0.00" not in html
    assert "Gate Damage</div><div class=\"val\">$+0.00" not in html
    assert "Missed ladder profit: $0.00" not in html


def test_money_formatters_collapse_near_zero_negative_values(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    assert analyzer._fmt_usd(-0.0049) == "0.00"
    assert analyzer._fmt_usd(-0.0051) == "-0.01"
    assert analyzer._fmt_usd(0.0049) == "0.00"
    findings = analyzer._generate_research_findings({
        "performance": {"trades": 1, "net_pnl_usd": -0.0049},
        "real_edge": {"approve_attempts": 1, "gate_damage_usd": -0.0049},
        "benchmark": {"best_lane": None, "worst_lane": None},
        "scenario_c": {"fast_cut_trades": 0, "leakage_left_usd": -0.0049},
        "coverage": {},
        "blocked_opportunity_usd": -0.0049,
    })
    assert all("$-0.00" not in finding for finding in findings)
    assert any("Gate damage $0.00" in finding for finding in findings)

    output = tmp_path / "analysis_dashboard.html"
    monkeypatch.setattr(analyzer, "ANALYSIS_DASHBOARD_HTML", str(output))
    empty_report = tmp_path / "empty.json"
    empty_report.write_text("{}", encoding="utf-8")
    for attr in (
        "BENCHMARK_VS_LANES_REPORT_FILE",
        "AI_CALIBRATION_REPORT_FILE",
        "CHASE_ATTRIBUTION_REPORT_FILE",
        "SCENARIO_C_LEAKAGE_REPORT_FILE",
        "HORIZON_PROFITABILITY_REPORT_FILE",
        "FAST_CUT_SURVIVOR_REPORT_FILE",
    ):
        monkeypatch.setattr(analyzer, attr, str(empty_report))
    payload = {
        "performance": {
            "trades": 1,
            "win_rate_pct": 0,
            "net_pnl_usd": -0.0049,
            "expectancy_usd": -0.0049,
            "mfe_capture_pct": 0,
        },
        "real_edge": {"approve_attempts": 1, "gate_damage_usd": -0.0049},
        "scenario_c": {},
        "coverage": {},
        "analysis_provenance": {},
    }
    assert analyzer.write_analysis_dashboard_html(payload) is True
    html = output.read_text(encoding="utf-8")
    assert "$-0.00" not in html
    assert "Net PnL</div><div class=\"val\">$0.00" in html
    assert "Gate Damage</div><div class=\"val\">$0.00" in html

    dashboard_source = (ROOT / "research" / "research_dashboard.py").read_text(encoding="utf-8")
    assert "if (Math.abs(value) < 0.005) return '0.00';" in dashboard_source


def test_benchmark_relative_scorecard_keeps_empty_deltas_unavailable(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    monkeypatch.setattr(
        analyzer,
        "BENCHMARK_RELATIVE_SCORECARD_FILE",
        str(tmp_path / "scorecard.json"),
    )

    report = analyzer.benchmark_relative_scorecard_report(
        trades=pd.DataFrame(),
        session={},
        benchmark_report={
            "lanes": {
                lane: {
                    "approves": 0,
                    "real_fills": 0,
                    "net_pnl_real": None,
                    "per_approve_ev": None,
                    "approve_to_fill_pct": None,
                    "delta_approve_to_fill_pct": None,
                }
                for lane in analyzer.BENCHMARK_LANES
            }
        },
    )

    assert report["benchmark_summary"]["pnl_usd"] is None
    assert report["benchmark_summary"]["win_rate_pct"] is None
    assert report["benchmark_summary"]["ev_per_approve_usd"] is None
    for lane in report["lanes"]:
        assert lane["pnl_usd"] is None
        assert lane["win_rate_pct"] is None
        assert lane["ev_per_approve_usd"] is None
        assert lane["vs_benchmark"] == {
            "pnl_delta": None,
            "wr_delta": None,
            "ev_delta": None,
            "fill_pct_delta": None,
            "beats_benchmark": None,
        }
