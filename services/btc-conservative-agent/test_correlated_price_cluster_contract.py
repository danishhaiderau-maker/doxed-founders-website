from pathlib import Path
import ast
from collections import defaultdict
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")


def _cluster_stats():
    tree = ast.parse(SOURCE)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"_cluster_stats"}]
    namespace = {"defaultdict": defaultdict, "np": np}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "analyzer.py", "exec"), namespace)
    return namespace["_cluster_stats"]


def _qualification_helpers():
    tree = ast.parse(SOURCE)
    wanted = {"_cluster_float", "_cluster_ts", "_qualified_cluster_row"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {"np": np, "pd": pd}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "analyzer.py", "exec"), namespace)
    return namespace


def test_correlated_price_cluster_grid_and_denominator_are_predeclared():
    assert "CORRELATED_PRICE_DISTANCE_GRID_USD = (0.01, 5, 10, 15, 20, 25, 50, 75, 100, 150)" in SOURCE
    assert "CORRELATED_PRICE_CLUSTER_SIZE_GRID = (2, 3, 4, 5, 8, 10)" in SOURCE
    assert "CORRELATED_PRICE_TIME_WINDOW_GRID_SEC = (30, 60, 120, 300, 600, 900, 1800)" in SOURCE
    assert '"all_opportunity_denominator"' in SOURCE
    assert '"by_regime_direction_volatility"' in SOURCE


def test_cluster_research_requires_complete_120m_replay_and_costs():
    assert 'float(replay.get("post_exit_sec") or 0) < 7200' in SOURCE
    assert 'replay.get("post_exit_complete") is not True' in SOURCE
    for field in ("trading_fees_usd", "funding_fees_usd", "slippage_usd"):
        assert field in SOURCE
    assert '"legacy_price_dedupe_status": "UNQUALIFIED"' in SOURCE
    assert '"live_policy_change_allowed": False' in SOURCE
    assert '"signal_replay_resolved_path"' in SOURCE
    assert '"signal_replay_exists_in_working_directory"' in SOURCE


def test_cluster_report_is_manifested_and_run_but_never_live_policy():
    assert "CORRELATED_PRICE_CLUSTER_REPORT_FILE" in SOURCE
    assert '("Correlated Price Clusters", CORRELATED_PRICE_CLUSTER_REPORT_FILE' in SOURCE
    assert "correlated_price_cluster_report(session=session)" in SOURCE
    function = SOURCE.split("def correlated_price_cluster_report", 1)[1].split("def chase_efficiency_matrix_report", 1)[0]
    assert "state[" not in function
    assert "scenario_c_config" not in function


def test_deterministic_grid_finds_smaller_cluster_avoids_joint_losses():
    rows = [
        {"trade_id": "a", "ts": 1, "price": 100, "direction": "SHORT", "pnl": 10.0, "filled": True, "touched": True, "regime": "BEAR", "volatility": "LOW"},
        {"trade_id": "b", "ts": 2, "price": 101, "direction": "SHORT", "pnl": -8.0, "filled": True, "touched": True, "regime": "BEAR", "volatility": "LOW"},
        {"trade_id": "c", "ts": 3, "price": 102, "direction": "SHORT", "pnl": -9.0, "filled": False, "touched": True, "regime": "BEAR", "volatility": "UNKNOWN"},
    ]
    stats = _cluster_stats()
    tight = stats(rows, distance=5, max_size=2, window_sec=60)
    loose = stats(rows, distance=5, max_size=3, window_sec=60)
    assert tight["cluster_net_pnl_usd"] == 2.0
    assert tight["losses_avoided"] == 1
    assert loose["cluster_net_pnl_usd"] == -7.0
    assert loose["joint_loss_rate_pct"] == 100.0


def test_winner_blocking_and_unknown_segments_are_visible():
    rows = [
        {"trade_id": "a", "ts": 1, "price": 100, "direction": "LONG", "pnl": -1.0, "filled": True, "touched": True, "regime": "RANGE", "volatility": "UNKNOWN"},
        {"trade_id": "b", "ts": 2, "price": 100, "direction": "LONG", "pnl": 4.0, "filled": True, "touched": True, "regime": "RANGE", "volatility": "UNKNOWN"},
    ]
    result = _cluster_stats()(rows, distance=0.01, max_size=1, window_sec=30)
    assert result["winners_blocked"] == 1
    assert result["by_regime_direction_volatility"][0]["segment"] == "RANGE|LONG|UNKNOWN"


def test_cost_and_horizon_failures_have_precise_producer_gaps():
    assert '"EXECUTION_COST_EVIDENCE_MISSING"' in SOURCE
    assert '"REQUIRED_120M_HORIZON_INCOMPLETE"' in SOURCE
    assert '"producer_gap_fields"' in SOURCE
    assert '"live_policy_change_allowed": False' in SOURCE


def test_cost_missing_and_unknown_segment_fixtures_fail_or_preserve_truth():
    qualify = _qualification_helpers()["_qualified_cluster_row"]
    base = {
        "replay_complete": True, "post_exit_complete": True, "post_exit_sec": 7200,
        "trading_fees_usd": 0.0, "funding_fees_usd": 0.0, "slippage_usd": 0.0,
        "start_price": 100, "start_ts": "2026-08-16T00:00:00Z", "direction": "SHORT",
        "net_pnl_usd": 2.0, "filled": True,
    }
    missing_cost = dict(base)
    missing_cost.pop("slippage_usd")
    assert qualify("cost-gap", missing_cost, {}) == (None, "EXECUTION_COST_EVIDENCE_MISSING")
    row, reason = qualify("unknown-segment", base, {})
    assert reason is None
    assert row["regime"] == "UNKNOWN" and row["volatility"] == "UNKNOWN"


def test_numeric_epoch_seconds_are_not_interpreted_as_nanoseconds():
    helper = _qualification_helpers()["_cluster_ts"]
    assert helper(1786761203.6726258) == 1786761203.6726258


def test_transitive_cluster_never_exceeds_admission_limit():
    rows = [
        {"trade_id": "a", "ts": 1, "price": 100, "direction": "SHORT", "pnl": 1.0, "filled": True, "touched": True, "regime": "BEAR", "volatility": "LOW"},
        {"trade_id": "b", "ts": 2, "price": 104, "direction": "SHORT", "pnl": 1.0, "filled": True, "touched": True, "regime": "BEAR", "volatility": "LOW"},
        {"trade_id": "c", "ts": 3, "price": 108, "direction": "SHORT", "pnl": 1.0, "filled": True, "touched": True, "regime": "BEAR", "volatility": "LOW"},
    ]
    result = _cluster_stats()(rows, distance=5, max_size=2, window_sec=60)
    assert result["allowed"] == 2 and result["blocked"] == 1


def test_report_requires_shared_cohort_complete_denominator_revision_and_holdout():
    function = SOURCE.split("def correlated_price_cluster_report", 1)[1].split("def chase_efficiency_matrix_report", 1)[0]
    assert "_analysis_eligible_trade_ids" in function and "SHOWCASE_STRATEGY" in function
    assert 'row.get("decision") == "ALLOW_DISTINCT"' in function
    assert '(row.get("ai") or {}).get("approved") is True' in function
    assert "denominator_complete" in function
    assert '"ev_per_all_opportunity_usd"' in function
    assert "revision_qualified" in function
    assert "selected_on_train" in function and "holdout_confirmed" in function
