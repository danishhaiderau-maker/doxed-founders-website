"""#731 exit_pricing_basis / exit_repriced_lag_s must reach /tiles/trades rows (pricing block) so repriced exits are distinguishable."""
import monitor_tiles as mt


def test_pricing_block_exposes_repriced_basis_and_lag():
    raw = {"fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "fill_model_version": 1, "exit_status": "PAPER_FILL",
           "exit_price_basis": "VWAP_ALL_LEGS", "exit_pricing_basis": "REPRICED_CURRENT_TIME", "exit_repriced_lag_s": "4.5"}
    b = mt.pricing_block(raw)
    assert b["exit_pricing_basis"] == "REPRICED_CURRENT_TIME" and b["exit_repriced_lag_s"] == 4.5


def test_model_time_rows_and_old_rows_report_none_not_a_repriced_claim():
    b = mt.pricing_block({"fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "exit_pricing_basis": "MODEL_TIME"})
    assert b["exit_pricing_basis"] == "MODEL_TIME" and b["exit_repriced_lag_s"] is None
    old = mt.pricing_block({"fill_model": "x"})
    assert old["exit_pricing_basis"] is None and old["exit_repriced_lag_s"] is None


def test_missing_book_seconds_reaches_tiles_trades_rows_as_a_number_or_none():
    assert mt.pricing_block({"fill_model": "x", "missing_book_seconds": "1.25"})["missing_book_seconds"] == 1.25
    assert mt.pricing_block({"fill_model": "x"})["missing_book_seconds"] is None
