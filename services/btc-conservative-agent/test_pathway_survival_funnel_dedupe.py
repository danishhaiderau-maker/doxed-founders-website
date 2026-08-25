import analyzer_research_engine_v62 as analyzer


def test_funnel_stage_dedupe_keeps_one_logical_close_per_trade():
    rows = [
        {"trade_id": "trade-a", "stage": "APPROVE"},
        {"trade_id": "trade-a", "stage": "CLOSED", "ts": "first"},
        {"trade_id": "trade-a", "stage": "CLOSED", "ts": "retry-1"},
        {"trade_id": "trade-a", "stage": "CLOSED", "ts": "retry-2"},
        {"trade_id": "trade-b", "stage": "CLOSED", "ts": "other"},
    ]

    logical, integrity = analyzer._dedupe_funnel_stage_rows(rows)

    assert [(row["trade_id"], row["stage"]) for row in logical] == [
        ("trade-a", "APPROVE"),
        ("trade-a", "CLOSED"),
        ("trade-b", "CLOSED"),
    ]
    assert integrity == {
        "raw_stage_rows": 5,
        "logical_stage_rows": 3,
        "duplicate_stage_rows_excluded": 2,
        "duplicates_by_stage": {"CLOSED": 2},
    }
