"""Canonical paper-PnL rounding + forced-close inclusion rule (single source of truth).

Three surfaces — ``bot.py`` ``lane_pnl_ledger``, the analyzer ``live_paper_by_lane``
and the self-aware ``tile_stats`` — previously disagreed on (a) USD rounding and
(b) whether operator/guard forced closes count as strategy closes. This module
fixes both to one rule so the surfaces are parity-checkable.

Canonical rule (documented):

* Forced closes (``ADMIN_MANUAL_CLOSE`` / ``ADMIN_FORCE_FLAT`` /
  ``CIRCUIT_BREAKER_ADMIN_MANUAL``) are NOT strategy outcomes. They are excluded
  from every tile/strategy statistic (lane ledger, analyzer live-paper per lane,
  self-aware tile stats). They remain visible in the raw Trades table.
* Realized USD sums are rounded to 2 decimals at every aggregation step.

Observation/accounting only: importing or calling this module cannot place,
change or cancel an order, read toggles, touch the relay, or create a tile.
"""
from __future__ import annotations

from typing import Any

# Canonical USD rounding precision for every realized-PnL aggregate.
USD_ROUND = 2

# The single canonical forced-close exit-reason set.
FORCED_EXIT_REASONS = frozenset({
    "ADMIN_MANUAL_CLOSE",
    "ADMIN_FORCE_FLAT",
    "CIRCUIT_BREAKER_ADMIN_MANUAL",
})


def is_forced_close(exit_reason: Any) -> bool:
    """True when an exit reason is a forced close (never a strategy outcome)."""
    return str(exit_reason or "").strip().upper() in FORCED_EXIT_REASONS


def round_usd(value: Any) -> float:
    """Round a realized-USD value to the canonical precision (2 decimals)."""
    try:
        return round(float(value), USD_ROUND)
    except (TypeError, ValueError):
        return 0.0
