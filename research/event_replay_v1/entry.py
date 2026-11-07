"""Entry timing and passive limit construction.

The earliest entry fill is strictly later than the signal-bar close and
only after the explicit synthetic arrival delay. A missing delay is not
defaulted. Offset and regime values are fixture labels, not live policy.
"""

from __future__ import annotations

from decimal import Decimal


class EntryError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def arrival_time(bar_close_us: int, signal_time_us: int, delay_us: int) -> tuple[str, int | None]:
    if delay_us < 0:
        return "NEGATIVE_DELAY", None
    if signal_time_us < bar_close_us:
        return "INVALID_CAUSAL", None
    arrival = max(bar_close_us, signal_time_us) + delay_us
    if arrival <= bar_close_us:
        return "NOT_STRICTLY_AFTER_CLOSE", None
    return "OK", arrival


def passive_limit(
    *,
    side: str,
    explicit_limit: Decimal | None,
    reference_price: Decimal | None,
    offset_bps: Decimal,
) -> Decimal:
    if explicit_limit is not None:
        base = explicit_limit
    elif reference_price is not None:
        base = reference_price
    else:
        raise EntryError("LIMIT_REQUIRED")
    shift = offset_bps / Decimal(10000)
    if side == "BUY":
        return base * (Decimal(1) - shift)
    if side == "SELL":
        return base * (Decimal(1) + shift)
    raise EntryError("SIDE")


def observation_visible(obs: dict, decision_us: int) -> bool:
    """Publication, validity, and receive order. Source time is not enough.

    A null exchange timestamp stays null. Receive time is not written into it.
    """

    if obs.get("validity") != "VALID":
        return False
    if int(obs["publication_us"]) > decision_us:
        return False
    if int(obs["received_us"]) > decision_us:
        return False
    if "exchange_timestamp_utc" not in obs:
        return False
    return True
