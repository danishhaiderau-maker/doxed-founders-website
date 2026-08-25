from __future__ import annotations

import os

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


def test_terminal_close_derives_entry_execution_type_from_frozen_position_fee_type():
    import inspect

    source = inspect.getsource(bot.close_position)
    assignment = 'entry_is_maker = pos.get("entry_fee_type") == "MAKER"'
    receipt = '"execution_entry_type": "MAKER" if entry_is_maker else "TAKER"'
    assert assignment in source
    assert receipt in source
    assert source.index(assignment) < source.index(receipt)


def test_terminal_pnl_adds_partials_and_values_only_remaining_runner():
    pos = {
        "dir": "LONG",
        "qty": 0.5,
        "policy_original_qty": 1.0,
        "entry_fee_type": "MAKER",
        "partial_exit_receipts": [
            {
                "closed_qty": 0.25,
                "price": 101.0,
                "realized_gross_usd": 0.25,
            },
            {
                "closed_qty": 0.25,
                "price": 101.5,
                "realized_gross_usd": 0.375,
            },
        ],
    }
    result = bot._paper_terminal_pnl_components(
        pos,
        entry=100.0,
        exit_price=102.5,
        exit_is_maker=False,
        maker_fee=0.0,
        taker_fee=0.0,
        funding_total=0.0,
    )
    assert result["partial_gross_pnl"] == 0.625
    assert result["runner_gross_pnl"] == 1.25
    assert result["gross_pnl"] == 1.875
    assert result["net_pnl"] == 1.875


def test_terminal_pnl_charges_original_entry_and_all_exit_quantities():
    pos = {
        "dir": "SHORT",
        "qty": 0.5,
        "policy_original_qty": 1.0,
        "entry_fee_type": "MAKER",
        "partial_exit_receipts": [
            {"closed_qty": 0.5, "price": 99.0, "realized_gross_usd": 0.5},
        ],
    }
    result = bot._paper_terminal_pnl_components(
        pos,
        entry=100.0,
        exit_price=98.0,
        exit_is_maker=False,
        maker_fee=0.001,
        taker_fee=0.002,
        funding_total=0.05,
    )
    expected_fees = (100.0 * 1.0 * 0.001) + (99.0 * 0.5 * 0.002) + (98.0 * 0.5 * 0.002)
    assert abs(result["trading_fees"] - expected_fees) < 1e-12
    assert abs(result["gross_pnl"] - 1.5) < 1e-12
    assert abs(result["net_pnl"] - (1.5 - expected_fees - 0.05)) < 1e-12


def test_terminal_pnl_without_partials_matches_quantity_based_position_pnl():
    pos = {
        "dir": "LONG",
        "qty": 2.0,
        "entry_fee_type": "TAKER",
        "partial_exit_receipts": [],
    }
    result = bot._paper_terminal_pnl_components(
        pos,
        entry=100.0,
        exit_price=101.0,
        exit_is_maker=True,
        maker_fee=0.0,
        taker_fee=0.0,
        funding_total=0.0,
    )
    assert result["partial_gross_pnl"] == 0.0
    assert result["runner_gross_pnl"] == 2.0
    assert result["net_pnl"] == 2.0
