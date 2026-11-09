"""R7 cost config for the EXT2 screen. Paper only. Live is off.

Fee is zero and is not a P&L term. Funding is set aside: disclosed, not
modelled, never a gate, and not a measured zero. Friction per side is
max(1 bp, Roll half-spread + 0.5 bp). Roll for a test day is fit only on
fold-train days or days strictly earlier than that test day.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

FEE_BP = 0.0
FUNDING = "set_aside"
FUNDING_MODELLED = False
FUNDING_MEASURED_ZERO = False
FUNDING_GATES = False
FRICTION_FLOOR_BP = 1.0
ROLL_LATENCY_SLIP_BP = 0.5
STRESS_FRICTION_MULTIPLIER = 1.5
STRESS_TICK_ADVERSE_PER_FILL = 1
LEVERAGE = 100
MAINTENANCE_MARGIN = 0.005
LIQUIDATION_ADVERSE_BP = (1.0 / LEVERAGE - MAINTENANCE_MARGIN) * 10000.0


class Ext2Stop(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def friction_bp_per_side(roll_half_spread_bp: float) -> float:
    roll = float(roll_half_spread_bp)
    if not math.isfinite(roll) or roll < 0.0:
        raise Ext2Stop("STOP_ROLL")
    return max(FRICTION_FLOOR_BP, roll + ROLL_LATENCY_SLIP_BP)


def round_trip_bp(roll_half_spread_bp: float) -> float:
    side = friction_bp_per_side(roll_half_spread_bp)
    return side + side


def stress_friction_bp_per_side(roll_half_spread_bp: float) -> float:
    return friction_bp_per_side(roll_half_spread_bp) * STRESS_FRICTION_MULTIPLIER


def net_bp(gross_bp: float, roll_half_spread_bp: float) -> float:
    """Gross bp minus round-trip friction. Fee and funding are not terms."""
    side = friction_bp_per_side(roll_half_spread_bp)
    return float(gross_bp) - side - side


def tick_adverse_bp(price: float) -> float:
    """One $1 tick, in bp of the fill price. Adverse, so callers subtract it."""
    value = float(price)
    if not math.isfinite(value) or value <= 0.0:
        raise Ext2Stop("STOP_TICK")
    return 10000.0 / value


def stress_net_bp(gross_bp: float, roll_half_spread_bp: float, price: float) -> float:
    side = stress_friction_bp_per_side(roll_half_spread_bp)
    return float(gross_bp) - side - side - tick_adverse_bp(price)


def roll_half_spread_bp(prices: Sequence[float]) -> float:
    """Roll (1984) half-spread, in bp of the mean price. Needs at least 3 prints."""
    values = np.asarray(prices, dtype=np.float64)
    if values.ndim != 1 or values.size < 3 or not np.isfinite(values).all() or np.any(values <= 0.0):
        raise Ext2Stop("STOP_ROLL_PRICES")
    changes = np.diff(values)
    left = changes[1:]
    right = changes[:-1]
    left = left - left.mean()
    right = right - right.mean()
    cov = float(np.mean(left * right))
    if cov >= 0.0:
        return 0.0
    half = math.sqrt(-cov)
    return half / float(values.mean()) * 10000.0


def assert_roll_fit_days(fit_days: Sequence[date], *, test_day: date, test_days: Sequence[date],
                         fold_train_days: Sequence[date]) -> None:
    """Refuse same-day and test-window prices before any price is read."""
    if not isinstance(test_day, date):
        raise Ext2Stop("STOP_ROLL_LEAK")
    banned = set(test_days)
    banned.add(test_day)
    train = set(fold_train_days)
    for day in fit_days:
        if not isinstance(day, date) or day in banned or (day not in train and not day < test_day):
            raise Ext2Stop("STOP_ROLL_LEAK")


def fit_roll_half_spread_bp(prices_by_day: Mapping[date, Sequence[float]], *, fit_days: Sequence[date],
                            test_day: date, test_days: Sequence[date],
                            fold_train_days: Sequence[date]) -> float:
    assert_roll_fit_days(fit_days, test_day=test_day, test_days=test_days, fold_train_days=fold_train_days)
    parts = [np.asarray(prices_by_day[day], dtype=np.float64) for day in fit_days]
    if not parts:
        raise Ext2Stop("STOP_ROLL_EMPTY")
    return roll_half_spread_bp(np.concatenate(parts))


def cost_config() -> dict[str, Any]:
    return {
        "schema": "ext2_cost_config_v1",
        "fee_bp": FEE_BP,
        "fee_enters_pnl": False,
        "funding": FUNDING,
        "funding_modelled": FUNDING_MODELLED,
        "funding_measured_zero": FUNDING_MEASURED_ZERO,
        "funding_gates": FUNDING_GATES,
        "friction_floor_bp": FRICTION_FLOOR_BP,
        "roll_latency_slip_bp": ROLL_LATENCY_SLIP_BP,
        "default_round_trip_bp": round_trip_bp(0.0),
        "stress_friction_multiplier": STRESS_FRICTION_MULTIPLIER,
        "stress_tick_adverse_per_fill": STRESS_TICK_ADVERSE_PER_FILL,
        "leverage": LEVERAGE,
        "maintenance_margin": MAINTENANCE_MARGIN,
        "liquidation_adverse_bp": LIQUIDATION_ADVERSE_BP,
        "live": False,
    }


def dates_inclusive(start: date, end: date) -> tuple[date, ...]:
    if end < start:
        raise Ext2Stop("STOP_DATES")
    out = []
    day = start
    while day <= end:
        out.append(day)
        day += timedelta(days=1)
    return tuple(out)


def utc_ms(text: str) -> int:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise Ext2Stop("STOP_TIME")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)
