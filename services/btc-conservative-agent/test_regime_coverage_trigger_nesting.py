import tile_integrity as ti


def test_regime_stamp_nested_under_trigger_counts_as_present():
    rows = [{"trade_id": "a", "entry_ts": 100.0, "trigger": {"regime_at_signal": "QUIET"}},
            {"trade_id": "b", "entry_ts": 100.0, "regime_at_signal": "TREND"}]
    out = ti.regime_coverage_check(rows, 50.0)
    assert out["checked"] == 2 and out["missing"] == 0 and not out["reasons"]


def test_genuinely_missing_regime_is_still_flagged():
    rows = [{"trade_id": "a", "entry_ts": 100.0, "trigger": {"regime_at_signal": None}},
            {"trade_id": "b", "entry_ts": 100.0}]
    out = ti.regime_coverage_check(rows, 50.0)
    assert out["missing"] == 2 and any("REGIME_MISSING 2/2" in r for r in out["reasons"])
