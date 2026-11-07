"""Concurrent fixture protections with a frozen causal tie priority.

This order is a fixture identity. It is not REALISTIC_V1 and not live policy.
Triggering schedules an execution after the explicit delay. The fill price
comes from the pricer, not from the trigger price. Hindsight-best selection
is not used.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

FIXTURE_TIE_PRIORITY = (
    "HARD_STOP",
    "LIQUIDATION",
    "THESIS_CUT",
    "LADDER",
    "BREAKEVEN",
    "TRAIL",
    "GIVEBACK",
    "TARGET",
    "TIME",
)


@dataclass
class ExitState:
    be_armed: bool = False
    be_lock: Decimal | None = None
    trail_armed: bool = False
    peak: Decimal | None = None
    trail_stop: Decimal | None = None
    ladder_index: int = 0


def _hit(long_side: bool, price: Decimal, level: Decimal, *, at_or_through_down: bool) -> bool:
    if at_or_through_down:
        return price <= level if long_side else price >= level
    return price >= level if long_side else price <= level


def evaluate_exit(
    *,
    side: str,
    price: Decimal | None,
    state: ExitState,
    protections: dict,
    liq_price: Decimal | None,
    thesis: bool,
    time_due: bool,
) -> list[dict]:
    long_side = side == "LONG"
    triggers: list[dict] = []
    if price is not None:
        if state.peak is None:
            state.peak = price
        else:
            state.peak = max(state.peak, price) if long_side else min(state.peak, price)
        stop = protections.get("hard_stop")
        if stop is not None and _hit(long_side, price, stop, at_or_through_down=True):
            triggers.append({"name": "HARD_STOP", "trigger_price": stop, "qty_mode": "ALL"})
        arm = protections.get("breakeven_arm")
        lock = protections.get("breakeven_lock")
        if arm is not None and lock is not None:
            if _hit(long_side, price, arm, at_or_through_down=False):
                state.be_armed = True
                state.be_lock = lock
            if state.be_armed and state.be_lock is not None and _hit(
                long_side, price, state.be_lock, at_or_through_down=True
            ):
                triggers.append(
                    {"name": "BREAKEVEN", "trigger_price": state.be_lock, "qty_mode": "ALL"}
                )
        trail_arm = protections.get("trail_arm")
        distance = protections.get("trail_distance")
        if trail_arm is not None and distance is not None and state.peak is not None:
            if _hit(long_side, price, trail_arm, at_or_through_down=False):
                state.trail_armed = True
            if state.trail_armed:
                state.trail_stop = state.peak - distance if long_side else state.peak + distance
                if _hit(long_side, price, state.trail_stop, at_or_through_down=True):
                    triggers.append(
                        {"name": "TRAIL", "trigger_price": state.trail_stop, "qty_mode": "ALL"}
                    )
        giveback = protections.get("giveback")
        if giveback is not None and state.peak is not None:
            gap = state.peak - price if long_side else price - state.peak
            if gap >= giveback:
                level = state.peak - giveback if long_side else state.peak + giveback
                triggers.append({"name": "GIVEBACK", "trigger_price": level, "qty_mode": "ALL"})
        target = protections.get("target")
        if target is not None and _hit(long_side, price, target, at_or_through_down=False):
            triggers.append({"name": "TARGET", "trigger_price": target, "qty_mode": "ALL"})
        ladder = protections.get("ladder") or []
        if state.ladder_index < len(ladder):
            rung = ladder[state.ladder_index]
            if _hit(long_side, price, rung["price"], at_or_through_down=False):
                triggers.append(
                    {
                        "name": "LADDER",
                        "trigger_price": rung["price"],
                        "qty_mode": "LADDER",
                        "qty": rung["qty"],
                    }
                )
        if liq_price is not None and _hit(long_side, price, liq_price, at_or_through_down=True):
            triggers.append(
                {"name": "LIQUIDATION", "trigger_price": liq_price, "qty_mode": "ALL"}
            )
    if thesis:
        triggers.append({"name": "THESIS_CUT", "trigger_price": None, "qty_mode": "ALL"})
    if time_due:
        triggers.append({"name": "TIME", "trigger_price": None, "qty_mode": "ALL"})
    return triggers


def choose_winner(triggers: list[dict]) -> dict | None:
    if not triggers:
        return None
    rank = {name: index for index, name in enumerate(FIXTURE_TIE_PRIORITY)}
    ordered = sorted(triggers, key=lambda item: rank[item["name"]])
    winner = dict(ordered[0])
    winner["suppressed"] = [item["name"] for item in ordered[1:]]
    winner["considered"] = [item["name"] for item in ordered]
    return winner
