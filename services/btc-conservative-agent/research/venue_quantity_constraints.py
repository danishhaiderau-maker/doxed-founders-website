"""Fail-closed capture of venue-published quantity constraints.

This module only serializes metadata already returned by the configured CCXT
market.  Missing or ambiguous fields remain unsupported; no trading minimum or
precision is inferred from a fallback.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from research.quantity_execution import build_signed_quantity_constraints


def _positive(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _decimal_precision_and_step(value: Any) -> tuple[int, Decimal] | None:
    """Accept explicit decimal-place precision or an exact power-of-ten step."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) or (
        isinstance(value, float) and value.is_integer() and value >= 0
    ):
        precision = int(value)
        return precision, Decimal(1).scaleb(-precision)
    step = _positive(value)
    if step is None:
        return None
    normalized = step.normalize()
    if normalized.as_tuple().digits != (1,):
        return None
    precision = -normalized.as_tuple().exponent
    if precision < 0 or step != Decimal(1).scaleb(-precision):
        return None
    return precision, step


def capture_quantity_constraints(
    exchange: Any,
    *,
    ccxt_symbol: str,
    evidence_symbol: str,
    captured_at: str,
    source_revision: str,
) -> dict[str, Any]:
    """Return a supported integrity receipt or explicit unsupported reasons."""
    reasons: list[str] = []
    try:
        market = exchange.market(ccxt_symbol)
    except Exception:
        market = None
        reasons.append("VENUE_MARKET_METADATA_UNAVAILABLE")
    if not isinstance(market, Mapping):
        return {"supported": False, "receipt": None, "reasons": reasons or ["VENUE_MARKET_METADATA_INVALID"]}

    amount_precision = (market.get("precision") or {}).get("amount")
    precision = _decimal_precision_and_step(amount_precision)
    min_lot = _positive(((market.get("limits") or {}).get("amount") or {}).get("min"))
    min_notional = _positive(((market.get("limits") or {}).get("cost") or {}).get("min"))
    if precision is None:
        reasons.append("VENUE_QUANTITY_PRECISION_OR_STEP_UNAVAILABLE")
    if min_lot is None:
        reasons.append("VENUE_MIN_LOT_UNAVAILABLE")
    if min_notional is None:
        reasons.append("VENUE_MIN_NOTIONAL_UNAVAILABLE")
    if not str(source_revision or "").strip():
        reasons.append("SOURCE_REVISION_UNAVAILABLE")
    if not str(captured_at or "").strip():
        reasons.append("CAPTURE_TIME_UNAVAILABLE")
    if reasons:
        return {"supported": False, "receipt": None, "reasons": reasons}

    quantity_precision, quantity_step = precision
    market_id = str(market.get("id") or ccxt_symbol)
    receipt = build_signed_quantity_constraints(
        symbol=evidence_symbol,
        quantity_step=str(quantity_step),
        quantity_precision=quantity_precision,
        min_lot=str(min_lot),
        min_notional=str(min_notional),
        captured_at=captured_at,
        source_revision=source_revision,
        source=f"CCXT_BITFINEX_MARKET_METADATA:{market_id}",
    )
    return {"supported": True, "receipt": receipt, "reasons": []}
