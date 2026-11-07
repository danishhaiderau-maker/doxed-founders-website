"""Gross, net, and per-signal accounting.

trading_fee must be the explicit account assumption 0. A missing fee is an
error, not a default of 0 and not a nonzero default. fee_verification_evidence
stays null and does not override that assumption. The assumption is not a
venue-wide claim. Funding is excluded and not modelled: its amount is null,
not zero, and there is no collection gate. Measured spread, depth, and
latency costs stay NULL/BLOCKED. Synthetic slippage memos are not charged
twice. Misses stay in the denominator at zero realized contribution.
Unresolved positions contribute null, not a fabricated closed zero.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .pricer import S


class FeeDispositionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def require_fee_disposition(fixture: dict) -> None:
    if "trading_fee" not in fixture:
        raise FeeDispositionError("TRADING_FEE_EXPLICIT_VALUE_REQUIRED")
    if fixture["trading_fee"] != "0":
        raise FeeDispositionError("TRADING_FEE_DISPOSITION_REQUIRES_EXPLICIT_ZERO")
    if "fee_verification_evidence" not in fixture:
        raise FeeDispositionError("FEE_VERIFICATION_EVIDENCE_REQUIRED")
    if fixture["fee_verification_evidence"] is not None:
        raise FeeDispositionError("FEE_VERIFICATION_EVIDENCE_MUST_STAY_NULL")
    funding = fixture.get("funding_charges")
    if (
        not isinstance(funding, dict)
        or funding.get("status") != "EXCLUDED_NOT_MODELLED"
        or "amount" not in funding
        or funding.get("amount") is not None
    ):
        raise FeeDispositionError("FUNDING_EXCLUDED_NOT_MODELLED_AMOUNT_MUST_BE_NULL")
    for key in ("measured_spread_cost", "measured_depth_cost", "measured_latency_cost"):
        if key not in fixture or fixture[key] is not None:
            raise FeeDispositionError("MEASURED_COST_NULL_BLOCKED")


def build_accounting(signals: list[dict], fills: list[dict], *, drop_misses: bool) -> dict[str, Any]:
    denominator_rows = [row for row in signals if not (drop_misses and row["is_miss"])]
    numerator = Decimal(0)
    for row in signals:
        if row["realized_pnl"] is not None:
            numerator += Decimal(row["realized_pnl"])
    fee = Decimal(0)
    net = numerator - fee
    depth = Decimal(0)
    delay = Decimal(0)
    for fill in fills:
        depth += Decimal(fill["depth_slippage"])
        delay += Decimal(fill["delay_gap_adverse"])
    denominator = len(denominator_rows)
    if denominator == 0:
        per_signal = None
    else:
        per_signal = str(net / Decimal(denominator))
    missed = [row["signal_id"] for row in signals if row["is_miss"]]
    unresolved = [
        row["signal_id"]
        for row in signals
        if row["incomplete"] or row["realized_pnl"] is None and not row["is_miss"]
    ]
    incomplete = any(row["incomplete"] for row in signals)
    return {
        "price_pnl": S(numerator),
        "gross_pnl": S(numerator),
        "trading_fee": "0",
        "trading_fee_explicit": True,
        "trading_fee_default_applied": False,
        "fee_verification_evidence": None,
        "fee_verification_status": "UNVERIFIED",
        "fee_scope": (
            "EXPLICIT_PER_ACCOUNT_STATEMENT_NOT_INDEPENDENTLY_EVIDENCED_NOT_VENUE_WIDE"
        ),
        "venue_wide_fee_claim": False,
        "funding_charges": {
            "status": "EXCLUDED_NOT_MODELLED",
            "amount": None,
            "modelled": False,
            "collection_gate": False,
        },
        "measured_spread_cost": None,
        "measured_spread_cost_status": "NULL/BLOCKED",
        "measured_depth_cost": None,
        "measured_depth_cost_status": "NULL/BLOCKED",
        "measured_latency_cost": None,
        "measured_latency_cost_status": "NULL/BLOCKED",
        "synthetic_depth_slippage_memo": S(depth),
        "synthetic_delay_gap_memo": S(delay),
        "queue_delay_value_class": "SYNTHETIC",
        "memos_subtracted_from_net": False,
        "net_pnl": S(net),
        "numerator": S(numerator),
        "denominator": str(denominator),
        "net_per_signal": per_signal,
        "missed_signal_ids": missed,
        "unresolved_signal_ids": unresolved,
        "signals": signals,
        "aggregate_status": "INCOMPLETE" if incomplete else "COMPLETE",
        "double_charged": False,
    }
