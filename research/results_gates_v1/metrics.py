"""Net-per-signal, per-fill, concentration, hold and drawdown metrics.

Missed, unfilled and rejected signals contribute zero and stay in the
denominator. Unresolved positions stay incomplete. No confidence claim is emitted.
"""

from __future__ import annotations

from fractions import Fraction

from . import validate as validate_mod
from .validate import number_str, parse_number


def compute(accounting: dict) -> dict:
    signals = list(accounting["signals"])
    signals.sort(key=lambda row: row["order_t"])
    orders = [row["order_t"] for row in signals]
    if orders != sorted(orders) or len(orders) != len(set(orders)):
        return _blocked("ORDER")
    unresolved = [row for row in signals if row["state"] == "unresolved"]
    resolved = [row for row in signals if row["state"] != "unresolved"]
    fills = [row for row in resolved if row["state"] == "filled"]
    misses = [row for row in resolved if row["state"] == "missed"]
    rejected = [row for row in resolved if row["state"] == "rejected"]
    unfilled = [row for row in resolved if row["state"] == "unfilled"]
    if any(row["state"] not in {"filled", "missed", "rejected", "unfilled", "unresolved"} for row in signals):
        return _blocked("STATE")
    if unresolved:
        return {
            "status": "INCOMPLETE",
            "reason": "UNRESOLVED_POSITION",
            "signal_count": len(signals),
            "unresolved_count": len(unresolved),
            "net_sum": None,
            "net_per_signal": None,
            "per_fill_net": None,
            "fill_rate": None,
            "median_hold": None,
            "day_share": None,
            "week_share": None,
            "drawdown": None,
            "giveback_abs": None,
            "includes_miss_zeros": True,
        }
    for row in resolved:
        validate_mod.assert_nonfill_zero(row["state"], row["net"])
    for row in fills:
        if row["hold_seconds"] is None or row["day"] is None or row["week"] is None:
            return _blocked("MISSING_FILL_FIELDS")
    nets = [parse_number(row["net"]) for row in resolved]
    net_sum = sum(nets, Fraction(0))
    slippage_values = [parse_number(row["slippage"]) for row in fills]
    slippage_sum = sum(slippage_values, Fraction(0))
    holds = [parse_number(row["hold_seconds"]) for row in fills]
    median = _median(holds) if holds else None
    day_share, week_share, concentration = _concentration(fills)
    drawdown, giveback = _path(nets)
    denominator = len(resolved)
    return {
        "status": "COMPLETE",
        "reason": None,
        "signal_count": denominator,
        "resolved_count": denominator,
        "unresolved_count": 0,
        "fill_count": len(fills),
        "miss_count": len(misses),
        "rejected_count": len(rejected),
        "unfilled_count": len(unfilled),
        "net_sum": number_str(net_sum),
        "net_sum_fraction": net_sum,
        "net_per_signal": {"numerator": number_str(net_sum), "denominator": str(denominator)},
        "per_fill_net": {
            "numerator": number_str(sum((parse_number(row["net"]) for row in fills), Fraction(0))),
            "denominator": str(len(fills)),
        },
        "fill_rate": {"numerator": str(len(fills)), "denominator": str(denominator)},
        "slippage_sum": number_str(slippage_sum),
        "slippage_fraction": slippage_sum,
        "median_hold": None if median is None else number_str(median),
        "median_hold_fraction": median,
        "day_share": None if day_share is None else number_str(day_share),
        "week_share": None if week_share is None else number_str(week_share),
        "day_share_fraction": day_share,
        "week_share_fraction": week_share,
        "concentration_status": concentration,
        "drawdown": number_str(drawdown),
        "giveback_abs": number_str(giveback),
        "includes_miss_zeros": True,
        "holds_filled": [number_str(value) for value in holds],
    }


def _median(values: list[Fraction]) -> Fraction | None:
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _concentration(fills: list[dict]) -> tuple[Fraction | None, Fraction | None, str]:
    positive = []
    for row in fills:
        net = parse_number(row["net"])
        if net > 0:
            if not row["day"] or not row["week"]:
                return None, None, "AMBIGUOUS"
            positive.append(row)
    denominator = sum((parse_number(row["net"]) for row in positive), Fraction(0))
    if denominator <= 0:
        return None, None, "AMBIGUOUS"
    day_share = _max_share(positive, "day", denominator)
    week_share = _max_share(positive, "week", denominator)
    return day_share, week_share, "COMPUTED"


def _max_share(rows: list[dict], key: str, denominator: Fraction) -> Fraction:
    buckets: dict[str, Fraction] = {}
    for row in rows:
        buckets[row[key]] = buckets.get(row[key], Fraction(0)) + parse_number(row["net"])
    return max(buckets.values()) / denominator


def _path(nets: list[Fraction]) -> tuple[Fraction, Fraction]:
    equity = Fraction(0)
    peak = Fraction(0)
    drawdown = Fraction(0)
    for net in nets:
        equity += net
        if equity > peak:
            peak = equity
        gap = peak - equity
        if gap > drawdown:
            drawdown = gap
    giveback = peak - equity
    return drawdown, giveback


def _blocked(reason: str) -> dict:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "net_sum": None,
        "net_per_signal": None,
        "per_fill_net": None,
        "fill_rate": None,
        "median_hold": None,
        "day_share": None,
        "week_share": None,
        "drawdown": None,
        "giveback_abs": None,
        "includes_miss_zeros": True,
        "concentration_status": "BLOCKED",
        "net_sum_fraction": None,
        "slippage_fraction": None,
        "median_hold_fraction": None,
        "day_share_fraction": None,
        "week_share_fraction": None,
    }
