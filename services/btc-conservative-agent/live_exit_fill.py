"""HM R4 B1 + RK 08:03 (R4 one helper, R17): THE exit-price provenance of a closed live trade on Fly.

Mirror of apps/api/src/trading-agents/live-row-countability.ts liveExitFillEvidence: the relay reporter sends
``close.price`` ONLY for a real Bitfinex fill and always ``close.price_source`` (exchange_fill | stop_fill | mark |
entry_fallback | none). Only exchange_fill / stop_fill are real. Everything else (ticker/mark, entry-price fallback,
failed or budget-refused fill read, partial close, non-USD fee, a report without price_source) has NO exit fill price:
the D3 caps read the trade as UNREADABLE (AMBER entry block, LEDGER_PNL_UNREADABLE) and the trade is non-countable.
Never booked as a $0 loss. Pure, no I/O.
"""
from __future__ import annotations

from typing import Any, Mapping

REAL_EXIT_FILL_SOURCES = frozenset({"exchange_fill", "stop_fill"})
EXIT_FILL_UNREADABLE = "EXIT_FILL_UNREADABLE"
# HM R5 nit 6: the entry side of every live P&L is the entry order's OWN trades only (reporter fill.source
# 'exchange_trades'); a limit / mark / position-base reference fallback is no entry fill price.
REAL_ENTRY_FILL_SOURCES = frozenset({"exchange_trades"})
ENTRY_FILL_UNREADABLE = "ENTRY_FILL_UNREADABLE"


def _pos(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x > 0 else None


def exit_fill_price(chain: Mapping[str, Any] | None) -> float | None:
    """The real Bitfinex exit fill price of a chain, else None. Never the top-level/ticker exit_price, never the entry."""
    c = chain if isinstance(chain, Mapping) else {}
    if str(c.get("exit_price_source") or "") not in REAL_EXIT_FILL_SOURCES:
        return None
    return _pos(c.get("exit_fill_price"))


def entry_fill_price(chain: Mapping[str, Any] | None) -> float | None:
    """HM R5 nit 6: the real Bitfinex entry fill price of a chain (fill_price_source exchange_trades), else None.
    Never the limit price, never a mark or position-base fallback."""
    c = chain if isinstance(chain, Mapping) else {}
    if str(c.get("fill_price_source") or "") not in REAL_ENTRY_FILL_SOURCES:
        return None
    return _pos(c.get("fill_price"))


def exit_fill_non_countable_reason(chain: Mapping[str, Any] | None) -> str | None:
    """R17: a CLOSED live chain without a real exit fill is non-countable: 'EXIT_FILL_UNREADABLE:<source>';
    HM R5 nit 6: one without a real entry fill is non-countable: 'ENTRY_FILL_UNREADABLE:<source>'."""
    c = chain if isinstance(chain, Mapping) else {}
    if not c.get("closed"):
        return None
    if exit_fill_price(c) is None:
        return f"{EXIT_FILL_UNREADABLE}:{str(c.get('exit_price_source') or 'none')}"
    if entry_fill_price(c) is None:
        return f"{ENTRY_FILL_UNREADABLE}:{str(c.get('fill_price_source') or 'none')}"
    return None
