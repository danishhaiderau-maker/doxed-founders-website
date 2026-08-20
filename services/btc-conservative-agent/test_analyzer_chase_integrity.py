import pandas as pd

import analyzer_research_engine_v62 as analyzer


def _check(payload):
    return next(c for c in payload["checks"] if c["check"] == "chase_count_buckets")


def test_chase_integrity_excludes_all_decision_rows_from_completed_trade_cohort(tmp_path, monkeypatch):
    trades = pd.DataFrame([
        {"trade_id": "filled-1", "limit_chase_count": 1, "net_pnl_usd": 2.0},
        {"trade_id": "filled-2", "limit_chase_count": 5, "net_pnl_usd": -1.0},
    ])
    rows = [
        {"trade_id": "filled-1", "chase_count": 1, "net_pnl_usd": 2.0},
        {"trade_id": "filled-2", "chase_count": 5, "net_pnl_usd": -1.0},
    ] + [{"trade_id": f"unfilled-{i}", "chase_count": 0} for i in range(300)]
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))
    report = analyzer.run_integrity_checks(trades=trades, chase_payload={"trades": rows})
    check = _check(report)
    assert report["report_status"] == "VALID"
    assert check["passed"] is True
    assert check["comparison_status"] == "COMPARABLE"
    assert check["comparable_trade_ids"] == 2
    assert check["expected"] == "{'1': 1, '5+': 1}"
    assert check["found"] == "{'1': 1, '5+': 1}"


def test_chase_integrity_marks_disjoint_sources_non_comparable_without_invalid(tmp_path, monkeypatch):
    trades = pd.DataFrame([
        {"trade_id": "filled-1", "limit_chase_count": 2, "net_pnl_usd": 1.0},
    ])
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))
    report = analyzer.run_integrity_checks(
        trades=trades,
        chase_payload={"trades": [{"trade_id": "decision-only", "chase_count": 0}]},
    )
    check = _check(report)
    assert report["report_status"] == "VALID"
    assert check["passed"] is True
    assert check["comparison_status"] == "NON_COMPARABLE"
    assert check["found"] == "NON_COMPARABLE"


def test_chase_integrity_still_fails_real_mismatch_inside_shared_cohort(tmp_path, monkeypatch):
    trades = pd.DataFrame([
        {"trade_id": "filled-1", "limit_chase_count": 2, "net_pnl_usd": 1.0},
    ])
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))
    report = analyzer.run_integrity_checks(
        trades=trades,
        chase_payload={"trades": [{"trade_id": "filled-1", "chase_count": 4}]},
    )
    check = _check(report)
    assert report["report_status"] == "INVALID"
    assert check["passed"] is False
    assert check["comparison_status"] == "COMPARABLE"
