"""Immutable paper-only protection policy for Patient Chase Tile Three.

The entry path is intentionally identical to :mod:`paper_policy_offset029`.
Only the exit/risk treatment changes, so Tile One and Tile Three can be
compared without conflating entry quality with protection quality.
"""
from __future__ import annotations

from dataclasses import dataclass

import paper_policy_offset029 as entry_policy


POLICY_ID = (
    "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5|"
    "HYBRID_SL1_PT25_25_BE1.25_TRAIL1_TP2.5"
)
LANE = "OFFSET_029_ATR_PROTECTED"
EXIT_PROFILE_ID = "HYBRID_SL1_PT25_25_BE1.25_TRAIL1_TP2.5_120M_V1"

INITIAL_STOP_ATR = 1.0
FIRST_PARTIAL_ATR = 1.0
FIRST_PARTIAL_FRACTION = 0.25
BREAK_EVEN_ARM_ATR = 1.25
BREAK_EVEN_FLOOR_MARGIN_PCT = 0.5
SECOND_PARTIAL_ATR = 1.5
SECOND_PARTIAL_FRACTION = 0.25
TRAIL_ATR = 1.0
FINAL_TARGET_ATR = 2.5
PATH_END_SEC = 7200


@dataclass(frozen=True)
class ExitAction:
    reason: str
    close_fraction: float
    trigger_price: float
    stop_price: float
    remaining_fraction: float
    first_partial_done: bool
    second_partial_done: bool
    break_even_armed: bool
    peak_price: float


def _atr_abs(entry: float, atr_abs: float, atr_pct: float) -> float:
    entry = float(entry or 0)
    absolute = float(atr_abs or 0)
    if absolute <= 0 and entry > 0 and float(atr_pct or 0) > 0:
        absolute = entry * float(atr_pct) / 100.0
    return max(0.0, absolute)


def _direction_sign(direction: str) -> int:
    direction = str(direction or "").upper()
    if direction == "LONG":
        return 1
    if direction == "SHORT":
        return -1
    return 0


def level(entry: float, direction: str, atr: float, multiple: float) -> float | None:
    sign = _direction_sign(direction)
    entry = float(entry or 0)
    atr = float(atr or 0)
    if sign == 0 or entry <= 0 or atr <= 0:
        return None
    return entry + sign * atr * float(multiple)


def initial_stop(entry: float, direction: str, atr: float) -> float | None:
    return level(entry, direction, atr, -INITIAL_STOP_ATR)


def _hit_target(direction: str, price: float, target: float) -> bool:
    return price >= target if _direction_sign(direction) > 0 else price <= target


def _hit_stop(direction: str, price: float, stop: float) -> bool:
    return price <= stop if _direction_sign(direction) > 0 else price >= stop


def _profit_floor_price(entry: float, direction: str, leverage: float) -> float:
    """Translate the small margin-profit floor into an underlying price floor."""
    leverage = max(float(leverage or 0), 1.0)
    move = (BREAK_EVEN_FLOOR_MARGIN_PCT / 100.0) / leverage
    return entry * (1.0 + _direction_sign(direction) * move)


def exit_action(
    *,
    entry: float,
    direction: str,
    price: float,
    atr_abs: float = 0.0,
    atr_pct: float = 0.0,
    age_sec: float = 0.0,
    leverage: float = 100.0,
    remaining_fraction: float = 1.0,
    first_partial_done: bool = False,
    second_partial_done: bool = False,
    break_even_armed: bool = False,
    peak_price: float | None = None,
) -> ExitAction | None:
    """Return the next deterministic action for one observable paper tick.

    Stop checks precede profit checks. This means a trade that moves directly
    adverse is protected even though no profit milestone was ever reached.
    The returned state fields must be persisted before processing the next tick.
    """
    entry = float(entry or 0)
    price = float(price or 0)
    remaining = max(0.0, min(1.0, float(remaining_fraction or 0)))
    sign = _direction_sign(direction)
    atr = _atr_abs(entry, atr_abs, atr_pct)
    if entry <= 0 or price <= 0 or atr <= 0 or sign == 0 or remaining <= 0:
        return None

    prior_peak = float(peak_price if peak_price is not None else entry)
    peak = max(prior_peak, price) if sign > 0 else min(prior_peak, price)
    be_level = level(entry, direction, atr, BREAK_EVEN_ARM_ATR)
    armed = bool(break_even_armed or (be_level and _hit_target(direction, peak, be_level)))

    stop = initial_stop(entry, direction, atr)
    if armed:
        stop = _profit_floor_price(entry, direction, leverage)
        trail = peak - sign * atr * TRAIL_ATR
        stop = max(stop, trail) if sign > 0 else min(stop, trail)

    if stop is not None and _hit_stop(direction, price, stop):
        reason = "TRAIL_STOP_1_ATR" if armed else "INITIAL_STOP_1_ATR"
        return ExitAction(
            reason, remaining, stop, stop, 0.0, first_partial_done,
            second_partial_done, armed, peak,
        )

    first = level(entry, direction, atr, FIRST_PARTIAL_ATR)
    if not first_partial_done and first is not None and _hit_target(direction, price, first):
        close = min(FIRST_PARTIAL_FRACTION, remaining)
        return ExitAction(
            "PARTIAL_TP_1_ATR", close, first, stop, remaining - close,
            True, second_partial_done, armed, peak,
        )

    second = level(entry, direction, atr, SECOND_PARTIAL_ATR)
    if not second_partial_done and second is not None and _hit_target(direction, price, second):
        close = min(SECOND_PARTIAL_FRACTION, remaining)
        return ExitAction(
            "PARTIAL_TP_1_5_ATR", close, second, stop, remaining - close,
            first_partial_done, True, armed, peak,
        )

    final = level(entry, direction, atr, FINAL_TARGET_ATR)
    if final is not None and _hit_target(direction, price, final):
        return ExitAction(
            "FINAL_TP_2_5_ATR", remaining, final, stop, 0.0,
            first_partial_done, second_partial_done, armed, peak,
        )

    if float(age_sec or 0) >= PATH_END_SEC:
        return ExitAction(
            "PATH_END_120M", remaining, price, stop, 0.0,
            first_partial_done, second_partial_done, armed, peak,
        )
    return None


def entry_fields(direction: str, reference_price: float) -> dict:
    fields = entry_policy.entry_fields(direction, reference_price)
    fields.update({
        "entry_limit_policy": POLICY_ID,
        "raw_policy_id": POLICY_ID,
        "policy_id": POLICY_ID,
        "exit_profile_id": EXIT_PROFILE_ID,
        "initial_stop_atr_k": INITIAL_STOP_ATR,
        "partial_take_profit_atr": [FIRST_PARTIAL_ATR, SECOND_PARTIAL_ATR],
        "partial_take_profit_fractions": [FIRST_PARTIAL_FRACTION, SECOND_PARTIAL_FRACTION],
        "break_even_arm_atr_k": BREAK_EVEN_ARM_ATR,
        "break_even_floor_margin_pct": BREAK_EVEN_FLOOR_MARGIN_PCT,
        "trailing_stop_atr_k": TRAIL_ATR,
        "atr_tp_multiple": FINAL_TARGET_ATR,
        "path_end_sec": PATH_END_SEC,
    })
    return fields

