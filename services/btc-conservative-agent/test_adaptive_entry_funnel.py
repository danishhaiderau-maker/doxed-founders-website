"""Adaptive entry funnel report and the analyzer fee receipt input path."""
import csv
import json
import os

import pytest

import adaptive_entry_funnel as funnel

V2 = "v31-dynamic-adaptive-ladder-paper-v4"
V2_DEFECT = "v31-dynamic-adaptive-paper-v2"
LANE = "FAMILY_ADAPTIVE_REGIME"
LADDER = "FAMILY_ADAPTIVE_REGIME_LADDER"


def _decision(call, action, reason, regime="CALM", version=V2, lane=LANE):
    row = {"research_lane": lane, "shared_ai_call_id": call, "action": action,
           "reason": reason, "regime": regime}
    if version:
        row["bot_version"] = version
    return row


def _cf(call, m60, m300, latency=1.0):
    return {"shared_ai_call_id": call, "latency_sec": latency, "markouts": {
        "60s": {"markout_exit_touch_bps": m60}, "300s": {"markout_exit_touch_bps": m300}}}


def test_every_decision_is_scored_against_the_taker_counterfactual():
    report = funnel.build_report(
        decisions=[
            _decision("c1", "MAKER", "CALM_MAKER"),
            _decision("c2", "MAKER", "CALM_MAKER"),
            _decision("c3", "TAKER", "NORMAL_TAKER", "NORMAL"),
            _decision("c4", "STAND_ASIDE", "AI_NO_TRADE"),
            _decision("c5", "MAKER", "CALM_MAKER", version=None),
            _decision("c6", "MAKER", "CALM_MAKER", version="v31-dynamic-adaptive-paper-v1"),
            _decision("c7", "TAKER", "NORMAL_TAKER", "NORMAL", version=V2_DEFECT),
        ],
        taker_counterfactuals=[_cf("c1", 2.0, 4.0), _cf("c2", -1.0, -3.0), _cf("c3", 1.0, 1.0),
                               _cf("c4", -5.0, -2.0), _cf("c4", 99.0, 99.0, latency=0.25)],
        trades=[{"research_lane": LANE, "shared_ai_call_id": "c1", "net_pnl_usd": "0.012", "exit_reason": "TRAIL"},
                {"research_lane": "OTHER", "shared_ai_call_id": "c2", "net_pnl_usd": "5"}],
        expired=[{"research_lane": LANE, "shared_ai_call_id": "c2"}],
        current_version=V2,
    )
    current = report["current_cohort"]
    assert current["decisions"] == 4
    assert current["by_outcome"] == {"FILLED_CLOSED": 1, "EXPIRED": 1, "NO_TRADE_OR_EXPIRY_ROW": 1, "STOOD_ASIDE": 1}
    assert current["counterfactual_coverage"] == 1.0
    groups = {(g["action"], g["outcome"]): g for g in current["stand_aside_vs_trade"]}
    aside = groups[("STAND_ASIDE", "STOOD_ASIDE")]
    assert aside["taker_counterfactual_60s"]["mean_bps"] == pytest.approx(-5.0)
    assert groups[("MAKER", "EXPIRED")]["taker_counterfactual_300s"]["mean_bps"] == pytest.approx(-3.0)
    assert groups[("MAKER", "FILLED_CLOSED")]["realized_net_pnl_usd"] == pytest.approx(0.012)
    quarantined = report["quarantined_cohorts"]
    assert quarantined["v31-dynamic-adaptive-paper-v1"]["reason"] == "ADAPTIVE_DECISION_PLUMBING_DEFECT"
    assert quarantined[V2_DEFECT]["reason"] == "ADAPTIVE_DECISION_PLUMBING_DEFECT"
    assert quarantined[funnel.UNSTAMPED_COHORT]["decisions"] == 1
    assert quarantined[funnel.UNSTAMPED_COHORT]["reason"] == "ADAPTIVE_DECISION_PLUMBING_DEFECT"


def test_report_from_paths_records_absolute_inputs(tmp_path):
    decisions = tmp_path / funnel.DECISIONS_FILE
    decisions.write_text(json.dumps(_decision("c1", "MAKER", "CALM_MAKER")) + "\n", encoding="utf-8")
    report = funnel.build_report_from_paths(
        decisions_path=str(decisions), counterfactual_path=str(tmp_path / "missing.jsonl"),
        trades_path=str(tmp_path / "missing.csv"), expired_path=str(tmp_path / "missing2.csv"),
        current_version=V2,
    )
    assert report["current_cohort"]["decisions"] == 1
    assert all(os.path.isabs(p) for p in report["inputs"].values())


def test_fee_receipt_reads_the_canonical_trades_file_from_any_cwd(tmp_path, monkeypatch):
    import analyzer_research_engine_v62 as engine

    data = tmp_path / "data"
    data.mkdir()
    trades = data / "trades_3factor.csv"
    with open(trades, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trade_id", "fee_profile"])
        writer.writeheader()
        writer.writerow({"trade_id": "t1", "fee_profile": engine.EXPECTED_FEE_PROFILE})
        writer.writerow({"trade_id": "t2", "fee_profile": engine.EXPECTED_FEE_PROFILE})
    report_cwd = tmp_path / "reports"
    report_cwd.mkdir()
    monkeypatch.chdir(report_cwd)
    monkeypatch.setattr(engine, "TRADES_FILE", str(trades))
    receipt = engine._generation_fee_profile_receipt()
    assert receipt["input_trades_path"] == str(trades)
    assert receipt["input_trade_fee_profiles"] == {engine.EXPECTED_FEE_PROFILE: 2}
    assert receipt["non_bitfinex_input_rows"] == 0

    monkeypatch.setattr(engine, "TRADES_FILE", "trades_3factor.csv")
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(data))
    assert engine._generation_fee_profile_receipt()["input_trade_fee_profiles"] == {engine.EXPECTED_FEE_PROFILE: 2}


def test_analyzer_registers_the_adaptive_funnel_report():
    import analyzer_research_engine_v62 as engine
    from research_reset_inventory import ANALYZER_REPORT_FILES

    name = engine.ADAPTIVE_ENTRY_FUNNEL_REPORT_FILE
    assert name == funnel.REPORT_FILE
    assert name in engine.ANALYZER_JSON_REPORT_FILES
    assert name in {row[1] for row in engine.DEEP_DIVE_REPORT_CATALOG}
    assert name in ANALYZER_REPORT_FILES


def test_tiles_sharing_one_ai_call_are_separate_lane_cohorts():
    report = funnel.build_report(
        decisions=[
            _decision("c1", "MAKER", "CALM_MAKER"),
            _decision("c1", "MAKER", "CALM_MAKER", lane=LADDER),
            _decision("c2", "STAND_ASIDE", "AI_NO_TRADE"),
            _decision("c2", "STAND_ASIDE", "AI_NO_TRADE", lane=LADDER),
            _decision("c0", "MAKER", "CALM_MAKER", version="v31-dynamic-adaptive-paper-v3"),
        ],
        taker_counterfactuals=[_cf("c1", 2.0, 4.0), _cf("c2", -1.0, -3.0)],
        trades=[{"research_lane": LANE, "shared_ai_call_id": "c1", "net_pnl_usd": "-0.02", "exit_reason": "INITIAL_ATR_STOP"},
                {"research_lane": LADDER, "shared_ai_call_id": "c1", "net_pnl_usd": "0.001", "exit_reason": "BREAKEVEN_LOCK"}],
        expired=[],
        current_version=V2,
    )
    assert report["current_cohort"]["decisions"] == 4
    by_lane = report["current_cohort_by_lane"]
    assert set(by_lane) == {LANE, LADDER}
    for lane, pnl in ((LANE, -0.02), (LADDER, 0.001)):
        groups = {(g["action"], g["outcome"]): g for g in by_lane[lane]["stand_aside_vs_trade"]}
        assert by_lane[lane]["decisions"] == 2
        assert groups[("MAKER", "FILLED_CLOSED")]["realized_net_pnl_usd"] == pytest.approx(pnl)
        assert groups[("STAND_ASIDE", "STOOD_ASIDE")]["n"] == 1
    prior = report["quarantined_cohorts"]["v31-dynamic-adaptive-paper-v3"]
    assert prior["reason"] == "SUPERSEDED_SINGLE_TILE_STACK"
