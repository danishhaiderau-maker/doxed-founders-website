from __future__ import annotations

import inspect
import os

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


def _complete_depth(*, best: float, avg: float, qty: float, cost: float) -> dict:
    return {
        "is_taker": True,
        "best_price": best,
        "avg_price": avg,
        "filled_qty": qty,
        "slippage_usd": cost,
        "fully_filled": True,
        "levels_consumed": 2,
        "partial_fill": False,
        "unfilled_qty": 0.0,
    }


def test_depth_slippage_receipt_uses_total_usd_for_exact_btc_quantity():
    receipt = bot._paper_depth_slippage_receipt(
        _complete_depth(best=100.0, avg=100.25, qty=2.0, cost=0.5),
        phase="ENTRY",
        execution_price=100.25,
        quantity_btc=2.0,
        entry_type="MARKET",
    )
    assert receipt["status"] == "COMPLETE"
    assert receipt["baseline"] == "SIDE_CORRECT_BEST_EXECUTABLE_BBO"
    assert receipt["price_unit"] == "USD_PER_BTC"
    assert receipt["quantity_unit"] == "BTC"
    assert receipt["unit"] == "USD"
    assert receipt["quantity_btc"] == 2.0
    assert receipt["slippage_cost_usd"] == 0.5
    assert receipt["recomputed_slippage_cost_usd"] == 0.5


def test_missing_depth_and_maker_touch_remain_unknown_not_zero():
    missing = bot._paper_depth_slippage_receipt(
        {}, phase="ENTRY", execution_price=100.0, quantity_btc=1.0,
        entry_type="MARKET",
    )
    assert missing["status"] == "UNKNOWN"
    assert missing["slippage_cost_usd"] is None
    assert missing["blockers"] == ["ENTRY_DEPTH_RECEIPT_MISSING"]

    maker = bot._paper_depth_slippage_receipt(
        {
            "best_price": 100.0, "avg_price": 100.0,
            "filled_qty": 1.0, "slippage_usd": 0.0,
            "fully_filled": True,
        },
        phase="ENTRY", execution_price=100.0, quantity_btc=1.0,
        entry_type="LIMIT",
    )
    assert maker["status"] == "UNKNOWN"
    assert maker["slippage_cost_usd"] is None
    assert maker["blockers"] == ["ENTRY_MAKER_TOUCH_HAS_NO_DEPTH_WALK"]


def test_partial_depth_walk_cannot_claim_complete_exit_cost():
    receipt = bot._paper_depth_slippage_receipt(
        {
            "best_price": 100.0, "avg_price": 99.5,
            "filled_qty": 0.5, "slippage_usd": 0.25,
            "fully_filled": False, "partial_fill": True,
        },
        phase="EXIT", execution_price=99.5, quantity_btc=1.0,
    )
    assert receipt["status"] == "UNKNOWN"
    assert receipt["slippage_cost_usd"] is None
    assert receipt["blockers"] == ["EXIT_DEPTH_WALK_NOT_FULLY_FILLED"]


def test_terminal_costs_are_attribution_only_and_net_subtracts_fees_funding_once():
    pos = {
        "entry_type": "MARKET",
        "entry_filled_qty": 2.0,
        "entry_fill_sim": _complete_depth(
            best=100.0, avg=100.25, qty=2.0, cost=0.5,
        ),
        "partial_exit_receipts": [],
    }
    exit_sim = _complete_depth(
        best=102.0, avg=101.75, qty=2.0, cost=0.5,
    )
    gross = 3.0
    fees = 0.4
    funding = 0.1
    net = 2.5
    evidence = bot._paper_terminal_cost_evidence(
        pos, exit_sim,
        entry_price=100.25, exit_price=101.75,
        original_qty=2.0, remaining_qty=2.0,
        gross_pnl=gross, trading_fees=fees,
        funding_total=funding, net_pnl=net,
    )
    assert evidence["entry_slippage_cost_usd"] == 0.5
    assert evidence["exit_slippage_cost_usd"] == 0.5
    assert evidence["slippage_cost_usd"] == 1.0
    assert evidence["latency_cost_usd"] is None
    assert evidence["latency_cost_receipt"]["status"] == "UNKNOWN"
    accounting = evidence["execution_cost_accounting"]
    assert accounting["gross_pnl_basis"] == "ACTUAL_EXECUTION_PRICES_INCLUDES_PRICE_IMPACT"
    assert accounting["separately_subtracted_from_gross"] == [
        "trading_fees_usd", "funding_fees_usd",
    ]
    assert accounting["expected_net_pnl_usd"] == 2.5
    assert accounting["reconciliation_delta_usd"] == 0.0
    assert accounting["reconciled"] is True


def test_partial_exit_without_depth_receipt_keeps_total_exit_cost_unknown():
    pos = {
        "entry_type": "MARKET",
        "entry_filled_qty": 2.0,
        "entry_fill_sim": _complete_depth(
            best=100.0, avg=100.25, qty=2.0, cost=0.5,
        ),
        "partial_exit_receipts": [{
            "closed_qty": 1.0, "remaining_fraction": 0.5,
            "price": 101.0,
        }],
    }
    evidence = bot._paper_terminal_cost_evidence(
        pos,
        _complete_depth(best=102.0, avg=101.75, qty=1.0, cost=0.25),
        entry_price=100.25, exit_price=101.75,
        original_qty=2.0, remaining_qty=1.0,
        gross_pnl=2.0, trading_fees=0.4,
        funding_total=0.1, net_pnl=1.5,
    )
    assert evidence["entry_slippage_cost_usd"] == 0.5
    assert evidence["exit_slippage_cost_usd"] is None
    assert evidence["slippage_cost_usd"] is None
    assert "PARTIAL_EXIT_DEPTH_COST_RECEIPTS_MISSING" in (
        evidence["exit_slippage_cost_receipt"]["blockers"]
    )
    assert evidence["exit_slippage_cost_receipt"]["terminal_leg_receipt"]["status"] == "COMPLETE"


def test_terminal_trade_row_passes_evidence_to_v3_close_without_repricing_trade():
    build_source = inspect.getsource(bot._build_open_position)
    close_source = inspect.getsource(bot.close_position)
    assert '"entry_fill_sim": copy.deepcopy(order.get("fill_sim") or {})' in build_source
    assert "terminal_cost_evidence = _paper_terminal_cost_evidence(" in close_source
    assert "**terminal_cost_evidence" in close_source
    assert close_source.index("terminal_cost_evidence = _paper_terminal_cost_evidence(") < close_source.index(
        "dual_write_paper_close("
    )
