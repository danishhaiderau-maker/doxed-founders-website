"""Parity tests for the canonical paper-PnL rule (paper_pnl_canon.py).

Guards the item-2 "number agreement" fix: bot.py ``lane_pnl_ledger``, the
analyzer ``live_paper_by_lane`` and the self-aware ``tile_stats`` must share ONE
forced-close inclusion set and ONE USD rounding precision.
"""

import pytest

import paper_pnl_canon


def test_forced_exit_reasons_are_the_canonical_three():
    assert paper_pnl_canon.FORCED_EXIT_REASONS == {
        "ADMIN_MANUAL_CLOSE", "ADMIN_FORCE_FLAT", "CIRCUIT_BREAKER_ADMIN_MANUAL",
    }


def test_is_forced_close_case_and_whitespace_insensitive():
    assert paper_pnl_canon.is_forced_close("ADMIN_MANUAL_CLOSE")
    assert paper_pnl_canon.is_forced_close("  admin_force_flat ")
    assert paper_pnl_canon.is_forced_close("CIRCUIT_BREAKER_ADMIN_MANUAL")
    assert not paper_pnl_canon.is_forced_close("ATR_TRAIL_STOP")
    assert not paper_pnl_canon.is_forced_close(None)
    assert not paper_pnl_canon.is_forced_close("")


def test_round_usd_uses_two_decimals():
    assert paper_pnl_canon.round_usd(0.44444) == 0.44
    assert paper_pnl_canon.round_usd(0.446) == 0.45
    assert paper_pnl_canon.round_usd(1.9999) == 2.0
    assert paper_pnl_canon.round_usd(3) == 3.0
    assert paper_pnl_canon.round_usd(None) == 0.0
    assert paper_pnl_canon.round_usd("n/a") == 0.0
    assert paper_pnl_canon.round_usd("1.005") == 1.0


def test_bot_stats_excluded_exit_reasons_alias_canonical():
    from research import close_origin
    # bot.py aliases STATS_EXCLUDED_EXIT_REASONS and the analyzer aliases
    # close_origin.FORCED_EXIT_REASONS to the same canonical frozenset.
    assert close_origin.FORCED_EXIT_REASONS == paper_pnl_canon.FORCED_EXIT_REASONS


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
