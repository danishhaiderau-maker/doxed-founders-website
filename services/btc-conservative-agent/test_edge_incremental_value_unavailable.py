import pandas as pd

import analyzer_research_engine_v62 as analyzer


def test_edge_incremental_value_tolerates_unavailable_execution_metrics(
    monkeypatch, tmp_path
):
    """Approved-but-unfilled cohorts must publish unavailable EV, not crash."""
    cohort = pd.DataFrame(
        [
            {
                "trade_id": f"decision-{index}",
                "edge_score": 3.0,
                "executed": False,
            }
            for index in range(12)
        ]
    )
    monkeypatch.setattr(
        analyzer,
        "_build_ai_calibration_cohort",
        lambda _trades, _session: cohort,
    )
    monkeypatch.setattr(
        analyzer,
        "EDGE_INCREMENTAL_VALUE_REPORT_FILE",
        tmp_path / "edge_incremental_value_report.json",
    )

    payload = analyzer.edge_incremental_value_report(
        trades=pd.DataFrame(),
        session={"fresh_collection": {"enabled": True}},
    )

    assert payload["verdict"] == "edge_no_incremental_value"
    assert payload["baseline_ai_only"]["ev_usd"] is None
    assert payload["best_ev_filter"]["ev_usd"] is None
    assert (tmp_path / "edge_incremental_value_report.json").is_file()
