import json
import tempfile
from pathlib import Path

import pytest

import analyzer_research_engine_v62 as analyzer
from research import r3_quarantine


@pytest.fixture(autouse=True)
def _legacy_tick_sim_unquarantined(monkeypatch):
    # These tests pin the legacy tick-sim math; production has TICK_SIM retired (R17, RK #847 cond 4).
    monkeypatch.setattr(r3_quarantine, "ACTIVE", set(r3_quarantine.ACTIVE) - {"TICK_SIM"})


def test_physical_hard_stop_cannot_be_suppressed_by_mfe_guard():
    # After the 5-minute thesis window, hard stop at -30% must fire even if MFE
    # protection would have skipped a thesis fast cut during the window.
    ticks = [
        {"seq": 1, "t": 301, "price": 102, "unreal_pct": 2},
        {"seq": 2, "t": 302, "price": 70, "unreal_pct": -31},
    ]
    result = analyzer._simulate_ticks_fast_cut_ladder(
        ticks, 100, "LONG", 1, 20, -12,
        [(8, 5), (12, 10)], 0, fill_t=0, mfe_protect_pct=2,
    )
    assert result[1] == "HARD_STOP"
    assert result[0] == analyzer._margin_pct_to_usd(-30, 20)


def test_thesis_fast_cut_fires_immediately_in_thesis_window():
    ticks = [
        {"seq": 1, "t": 1, "price": 102, "unreal_pct": 2},
        {"seq": 2, "t": 2, "price": 86, "unreal_pct": -14},
    ]
    result = analyzer._simulate_ticks_fast_cut_ladder(
        ticks, 100, "LONG", 1, 20, -12,
        [(8, 5), (12, 10)], 0, fill_t=0, mfe_protect_pct=5,
    )
    assert result[1] == "THESIS_FAST_CUT"
    assert result[0] == analyzer._margin_pct_to_usd(-12, 20)


def test_grid_fails_closed_without_real_copy_cohort():
    originals = (
        analyzer._load_jsonl_replays,
        analyzer._analysis_eligible_trade_ids,
        analyzer._load_jsonl_by_trade_id,
    )
    with tempfile.TemporaryDirectory() as tmp:
        analyzer._set_analyzer_report_subdir(tmp)
        try:
            analyzer._load_jsonl_replays = lambda: {"cont-unqualified": {"ticks": []}}
            analyzer._analysis_eligible_trade_ids = lambda cohort: (
                set(), {"BITFINEX_LINKAGE_MISSING": 1}, 1
            )
            analyzer._load_jsonl_by_trade_id = lambda _path: {}
            report = analyzer.qualified_exit_policy_grid_report()
        finally:
            (
                analyzer._load_jsonl_replays,
                analyzer._analysis_eligible_trade_ids,
                analyzer._load_jsonl_by_trade_id,
            ) = originals
            analyzer._set_analyzer_report_subdir(None)
        written = json.loads(
            (Path(tmp) / analyzer.QUALIFIED_EXIT_POLICY_GRID_REPORT_FILE).read_text(encoding="utf-8")
        )
    assert report["conclusions_allowed"] is False
    assert report["live_policy_change_allowed"] is False
    assert report["verdict"] == "INSUFFICIENT_QUALIFIED_HOLDOUT"
    assert report["eligible_ids"] == 0
    assert written["cohort_exclusion_reason_counts"]["BITFINEX_LINKAGE_MISSING"] == 1
    assert written["physical_hard_stop_invariant_pct"] == 30.0
    assert "fixed 30% invariant" in written["qualified_live_recommendation"]["note"]
    assert "13%" not in written["qualified_live_recommendation"]["note"]
    assert written["actual_bitfinex"]["n"] == 0


if __name__ == "__main__":
    test_grid_fails_closed_without_real_copy_cohort()
    print("PASS: qualified exit grid fails closed")


def test_grid_reads_source_retired_r17_without_replays():
    """HM C9: with the signal_replay writer retired, the grid says "source retired (R17)", not INSUFFICIENT."""
    originals = (
        analyzer._load_jsonl_replays,
        analyzer._analysis_eligible_trade_ids,
        analyzer._load_jsonl_by_trade_id,
    )
    with tempfile.TemporaryDirectory() as tmp:
        analyzer._set_analyzer_report_subdir(tmp)
        try:
            analyzer._load_jsonl_replays = lambda: {}
            analyzer._analysis_eligible_trade_ids = lambda cohort: (set(), {}, 0)
            analyzer._load_jsonl_by_trade_id = lambda _path: {}
            report = analyzer.qualified_exit_policy_grid_report()
        finally:
            (
                analyzer._load_jsonl_replays,
                analyzer._analysis_eligible_trade_ids,
                analyzer._load_jsonl_by_trade_id,
            ) = originals
            analyzer._set_analyzer_report_subdir(None)
    assert report["verdict"] == "SOURCE_RETIRED_R17"
    assert report["source_status"] == "source retired (R17)"
    assert report["live_policy_change_allowed"] is False
