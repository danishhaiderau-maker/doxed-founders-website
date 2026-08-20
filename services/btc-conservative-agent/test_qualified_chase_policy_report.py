import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import analyzer_research_engine_v62 as analyzer


DASHBOARD_SOURCE = (Path(__file__).resolve().parent / "research" / "research_dashboard.py").read_text(encoding="utf-8")


def _row(index):
    stamp = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)).isoformat()
    return {
        "trade_id": f"cont-{index:03d}",
        "actual_bitfinex_realized_pnl_usd": 1.0 if index % 3 else -0.25,
        "policy_comparability_key": "policy-one",
        "bitfinex_evidence": {
            "chase_history": [{"chase_count": 1, "ack_at": stamp}],
            "ack_history": [{"order_id": f"order-{index}", "ack_at": stamp}],
        },
    }


def test_qualified_chase_report_uses_train_selected_holdout_but_never_changes_live_policy():
    evidence = {row["trade_id"]: row for row in (_row(index) for index in range(70))}
    originals = analyzer._analysis_eligible_trade_ids, analyzer._load_jsonl_by_trade_id
    with tempfile.TemporaryDirectory() as tmp:
        analyzer._set_analyzer_report_subdir(tmp)
        try:
            analyzer._analysis_eligible_trade_ids = lambda _cohort: (set(evidence), {}, len(evidence))
            analyzer._load_jsonl_by_trade_id = lambda _path: evidence
            report = analyzer.qualified_chase_policy_report()
        finally:
            analyzer._analysis_eligible_trade_ids, analyzer._load_jsonl_by_trade_id = originals
            analyzer._set_analyzer_report_subdir(None)
        written = json.loads((Path(tmp) / analyzer.QUALIFIED_CHASE_POLICY_REPORT_FILE).read_text())

    assert report["chronological_split"] == {"train_n": 49, "holdout_n": 21, "train_pct": 70}
    assert report["selected_on_train"] == "1"
    assert report["descriptive_conclusion_allowed"] is True
    assert report["verdict"] == "QUALIFIED_DESCRIPTIVE_HOLDOUT"
    assert report["live_policy_change_allowed"] is False
    assert report["question_outputs"]["chase"]["recommendation"] is None
    assert written["producer_gaps"] == [
        "ALL_OPPORTUNITY_COUNTERFACTUAL_ENTRY_REPLAY",
        "COUNTERFACTUAL_TOUCH_AND_FILL_BY_CHASE_POLICY",
    ]


def test_qualified_chase_report_fails_closed_without_chronological_identity():
    evidence = {
        "cont-missing-time": {
            "trade_id": "cont-missing-time",
            "actual_bitfinex_realized_pnl_usd": 1.0,
            "policy_comparability_key": "policy-one",
            "bitfinex_evidence": {"chase_history": []},
        }
    }
    originals = analyzer._analysis_eligible_trade_ids, analyzer._load_jsonl_by_trade_id
    with tempfile.TemporaryDirectory() as tmp:
        analyzer._set_analyzer_report_subdir(tmp)
        try:
            analyzer._analysis_eligible_trade_ids = lambda _cohort: (set(evidence), {}, 1)
            analyzer._load_jsonl_by_trade_id = lambda _path: evidence
            report = analyzer.qualified_chase_policy_report()
        finally:
            analyzer._analysis_eligible_trade_ids, analyzer._load_jsonl_by_trade_id = originals
            analyzer._set_analyzer_report_subdir(None)

    assert report["qualified_rows"] == 0
    assert report["report_exclusion_reason_counts"] == {"CHRONOLOGICAL_TIMESTAMP_MISSING": 1}
    assert report["descriptive_conclusion_allowed"] is False
    assert report["live_policy_change_allowed"] is False


def test_chase_report_remains_available_but_dashboard_uses_one_best_policy_gate():
    assert analyzer.QUALIFIED_CHASE_POLICY_REPORT_FILE in analyzer.ANALYZER_JSON_REPORT_FILES
    assert analyzer.QUALIFIED_CHASE_POLICY_REPORT_FILE in {row[1] for row in analyzer.DEEP_DIVE_REPORT_CATALOG}
    assert 'BEST_POLICY_RESEARCH_REPORT_FILE = "best_policy_research_report.json"' in DASHBOARD_SOURCE
    assert '"status": "QUALIFIED" if qualified else "NO QUALIFIED POLICY"' in DASHBOARD_SOURCE
    assert 'blockers.append("INDEPENDENT_OOS_EVIDENCE_MISSING")' in DASHBOARD_SOURCE
    assert 'blockers.append("REPLAY_INELIGIBLE_PATHS_PRESENT")' in DASHBOARD_SOURCE
    assert '"question": "Best Policy Research"' in DASHBOARD_SOURCE
