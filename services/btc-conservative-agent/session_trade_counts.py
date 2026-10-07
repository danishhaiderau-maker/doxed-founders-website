"""Canonical session trade-count labelling (pure, importable, no bot imports).

The runtime's ``_session_trade_count`` reports every in-session entry (open,
unclosed, and force-closed rows included), while the lane ledger's ``closes``
counts only terminal non-forced closes. Those two numbers are intentionally
different; this module makes that difference explicit and keeps the labels in
one place so the dashboard cannot accidentally force them equal again.

Nothing here touches orders, relay, or Bitfinex.
"""
from __future__ import annotations

from typing import Iterable, Mapping


def classify_session_row_counts(
    rows: Iterable[Mapping],
    *,
    forced_reasons: Iterable[str],
) -> dict:
    """Label a session's trade rows as total / closed / forced / open.

    A row is *closed* when it carries a terminal exit marker (``exit_reason``
    or a close timestamp ``close_ts`` / ``closed_ts``). *forced* is the closed
    subset whose ``exit_reason`` is in ``forced_reasons``. *open* is everything
    not yet closed, and *strategy_closes* is ``closed - forced`` (the terminal
    non-forced closes the lane ledger counts).

    ``forced_reasons`` is injected (rather than imported from the bot) so this
    module stays free of heavy bot dependencies and is directly unit-testable.
    """
    forced_set = {str(r).strip().upper() for r in forced_reasons if r}
    closed = 0
    forced = 0
    total = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        total += 1
        terminal = bool(
            row.get("exit_reason")
            or row.get("close_ts")
            or row.get("closed_ts")
        )
        if terminal:
            closed += 1
            if str(row.get("exit_reason") or "").strip().upper() in forced_set:
                forced += 1
    return {
        "total": total,
        "closed": closed,
        "forced": forced,
        "open": total - closed,
        "strategy_closes": closed - forced,
    }
