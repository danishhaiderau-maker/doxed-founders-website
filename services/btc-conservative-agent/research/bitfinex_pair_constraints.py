"""Bitfinex public pair constraints for perpetual intents (read-only, no auth).

``conf/pub:info:pair:futures`` publishes per-pair minimum and maximum order
amounts.  Bitfinex does not publish a minimum order value, so none is inferred.
Amount precision (8 decimals) and price precision (5 significant digits) are
venue-documented constants, labelled as such in every receipt.  Missing or
malformed data fails closed; an intent quantity is never rounded up.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Callable, Mapping

PUBLIC_PAIR_INFO_URL = "https://api-pub.bitfinex.com/v2/conf/pub:info:pair:futures"
CONSTRAINTS_SCHEMA = "bitfinex_pair_constraints_v1"
SOURCE = "BITFINEX_PUBLIC_CONF:pub:info:pair:futures"
AMOUNT_PRECISION = 8
AMOUNT_PRECISION_SOURCE = "BITFINEX_DOCS_AMOUNT_8_DECIMALS"
PRICE_SIG_DIGITS = 5
PRICE_SIG_DIGITS_SOURCE = "BITFINEX_DOCS_PRICE_5_SIGNIFICANT_DIGITS"
DEFAULT_TTL_SEC = 900.0
FETCH_TIMEOUT_SEC = 5.0
# api-pub rejects the default Python-urllib User-Agent with HTTP 403.
USER_AGENT = "doxxed-btc-conservative-agent/1.0"
_MIN_AMOUNT_INDEX = 3
_MAX_AMOUNT_INDEX = 4

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _positive(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def pair_from_symbol(symbol: str) -> str:
    text = str(symbol or "").strip()
    return text[1:] if text.startswith("t") and ":" in text else text


def parse_pair_info(payload: Any, pair: str) -> tuple[dict[str, str] | None, list[str]]:
    """Return ``{min_amount, max_amount}`` for ``pair`` from the public payload."""
    rows = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else None
    if rows is None:
        return None, ["PAIR_INFO_PAYLOAD_INVALID"]
    for row in rows:
        if not (isinstance(row, list) and len(row) == 2 and row[0] == pair):
            continue
        info = row[1]
        if not isinstance(info, list) or len(info) <= _MAX_AMOUNT_INDEX:
            return None, ["PAIR_INFO_ROW_INVALID"]
        min_amount = _positive(info[_MIN_AMOUNT_INDEX])
        max_amount = _positive(info[_MAX_AMOUNT_INDEX])
        reasons = []
        if min_amount is None:
            reasons.append("VENUE_MIN_AMOUNT_UNAVAILABLE")
        if max_amount is None:
            reasons.append("VENUE_MAX_AMOUNT_UNAVAILABLE")
        if not reasons and max_amount < min_amount:
            reasons.append("VENUE_AMOUNT_BOUNDS_INVERTED")
        if reasons:
            return None, reasons
        return {"min_amount": str(min_amount), "max_amount": str(max_amount)}, []
    return None, ["PAIR_NOT_PUBLISHED"]


def _default_fetch(url: str) -> Any:
    request = urllib.request.Request(
        url, method="GET", headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SEC) as response:
        return json.loads(response.read().decode("utf-8"))


def _utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def fetch_pair_constraints(
    symbol: str,
    *,
    now: float | None = None,
    ttl_sec: float = DEFAULT_TTL_SEC,
    fetch: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Cached public constraints: ``{supported, constraints, reasons, cache}``."""
    now = time.time() if now is None else float(now)
    pair = pair_from_symbol(symbol)
    with _cache_lock:
        cached = _cache.get(pair)
        if cached and now - cached["fetched_ts"] < ttl_sec:
            return {"supported": True, "constraints": dict(cached["constraints"]), "reasons": [], "cache": "HIT"}
    try:
        payload = (fetch or _default_fetch)(PUBLIC_PAIR_INFO_URL)
    except Exception as exc:
        return {"supported": False, "constraints": None,
                "reasons": [f"VENUE_PAIR_INFO_UNAVAILABLE:{type(exc).__name__}"], "cache": "MISS"}
    amounts, reasons = parse_pair_info(payload, pair)
    if amounts is None:
        return {"supported": False, "constraints": None, "reasons": reasons, "cache": "MISS"}
    constraints = {
        "schema": CONSTRAINTS_SCHEMA,
        "symbol": f"t{pair}",
        **amounts,
        "amount_precision": AMOUNT_PRECISION,
        "amount_precision_source": AMOUNT_PRECISION_SOURCE,
        "price_sig_digits": PRICE_SIG_DIGITS,
        "price_sig_digits_source": PRICE_SIG_DIGITS_SOURCE,
        "min_notional": None,
        "min_notional_status": "NOT_PUBLISHED_BY_VENUE",
        "source": SOURCE,
        "fetched_at": _utc_iso(now),
    }
    with _cache_lock:
        _cache[pair] = {"fetched_ts": now, "constraints": constraints}
    return {"supported": True, "constraints": dict(constraints), "reasons": [], "cache": "MISS"}


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def check_intent_quantity(quantity: Any, constraints: Mapping[str, Any] | None) -> dict[str, Any]:
    """Fail-closed admissibility of one intent quantity; never rounds up."""
    out = {"accepted": False, "requested_quantity": quantity, "executable_quantity": None,
           "rounded_down": False, "reasons": []}
    if not isinstance(constraints, Mapping) or constraints.get("schema") != CONSTRAINTS_SCHEMA:
        out["reasons"] = ["VENUE_QUANTITY_CONSTRAINTS_UNAVAILABLE"]
        return out
    qty = _positive(quantity)
    min_amount = _positive(constraints.get("min_amount"))
    max_amount = _positive(constraints.get("max_amount"))
    precision = constraints.get("amount_precision")
    if qty is None:
        out["reasons"] = ["INTENT_QUANTITY_INVALID"]
        return out
    if min_amount is None or max_amount is None or not isinstance(precision, int) or precision < 0:
        out["reasons"] = ["VENUE_QUANTITY_CONSTRAINTS_INVALID"]
        return out
    executable = qty.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_DOWN)
    out["executable_quantity"] = str(executable)
    out["rounded_down"] = executable != qty
    if executable < min_amount:
        out["reasons"] = [f"INTENT_QUANTITY_BELOW_VENUE_MIN_AMOUNT:{executable}<{min_amount}"]
        return out
    if executable > max_amount:
        out["reasons"] = [f"INTENT_QUANTITY_ABOVE_VENUE_MAX_AMOUNT:{executable}>{max_amount}"]
        return out
    out["accepted"] = True
    return out