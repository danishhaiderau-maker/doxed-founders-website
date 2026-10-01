from __future__ import annotations

from research.dual_execution_truth import split_execution_truth


def _closed(**extra):
    row = {"trade_id": "t-1", "entry": 100.0, "exit": 101.0, "exit_reason": "TP", "net_pnl_usd": 0.4}
    row.update(extra)
    return row


def test_closed_trade_without_executed_flag_is_shown_filled():
    truth = split_execution_truth(_closed())
    assert truth["showcase_simulated"]["executed"] is True


def test_explicit_unexecuted_flag_is_never_overridden():
    truth = split_execution_truth(_closed(executed=False))
    assert truth["showcase_simulated"]["executed"] is False


def test_open_or_priceless_rows_are_not_promoted_to_filled():
    assert split_execution_truth({"trade_id": "t-2", "entry": 100.0})["showcase_simulated"]["executed"] is False
    assert split_execution_truth({"trade_id": "t-3", "exit_reason": "TTL"})["showcase_simulated"]["executed"] is False

