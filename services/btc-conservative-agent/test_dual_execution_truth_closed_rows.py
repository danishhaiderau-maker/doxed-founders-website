"""Closed paper ledger rows must render as filled in the Showcase column."""
import pytest

from research.dual_execution_truth import split_execution_truth


def _closed(**overrides):
    row = {
        "trade_id": "ftf-1", "research_lane": "FAMILY_TREND_FADE_60",
        "entry": 65000.0, "exit": 65006.0, "exit_reason": "BREAKEVEN_LOCK",
        "net_pnl_usd": 0.0018,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("reason", ["BREAKEVEN_LOCK", "INITIAL_ATR_STOP", "PROFIT_PROTECTION_STOP", "PATH_END_120M"])
def test_closed_paper_trade_is_filled_closed_not_unfilled(reason):
    truth = split_execution_truth(_closed(exit_reason=reason))
    show = truth["showcase_simulated"]
    assert show["executed"] is True
    assert show["status"] == "FILLED_CLOSED"
    assert show["fill_price"] == 65000.0
    assert truth["relationship"]["divergence_cohort"] == "SHOWCASE_ONLY"
    assert truth["bitfinex_authenticated"]["authenticated"] is False


@pytest.mark.parametrize("row", [
    _closed(exit_reason="NO_FILL"),
    _closed(exit_reason="SIGNAL_TTL_EXPIRED"),
    _closed(exit=None),
    _closed(exit_reason=None),
    _closed(executed=False),
    _closed(status="EXPIRED"),
])
def test_unfilled_or_explicit_rows_are_never_promoted_to_filled(row):
    show = split_execution_truth(row)["showcase_simulated"]
    assert show["status"] != "FILLED_CLOSED"
    assert show["executed"] is False
