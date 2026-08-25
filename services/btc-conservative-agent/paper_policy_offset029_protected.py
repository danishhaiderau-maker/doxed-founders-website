"""Immutable static protection treatment for Patient Chase Tile Three."""
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
ACCOUNT_RISK_PCT = 0.5
MARGIN_CAP_USD = 0.25


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
    absolute = float(atr_abs or 0)
    if absolute <= 0 and float(entry or 0) > 0 and float(atr_pct or 0) > 0:
        absolute = float(entry) * float(atr_pct) / 100.0
    return max(0.0, absolute)


def _sign(direction: str) -> int:
    return 1 if str(direction).upper() == "LONG" else -1 if str(direction).upper() == "SHORT" else 0


def level(entry: float, direction: str, atr: float, multiple: float) -> float | None:
    sign = _sign(direction)
    if float(entry or 0) <= 0 or float(atr or 0) <= 0 or not sign:
        return None
    return float(entry) + sign * float(atr) * float(multiple)


def _target_hit(direction: str, price: float, target: float) -> bool:
    return float(price) >= target if _sign(direction) > 0 else float(price) <= target


def _stop_hit(direction: str, price: float, stop: float) -> bool:
    return float(price) <= stop if _sign(direction) > 0 else float(price) >= stop


def _break_even_floor(entry: float, direction: str, leverage: float) -> float:
    move = (BREAK_EVEN_FLOOR_MARGIN_PCT / 100.0) / max(float(leverage or 0), 1.0)
    return float(entry) * (1.0 + _sign(direction) * move)


def exit_action(*, entry: float, direction: str, price: float,
                atr_abs: float = 0.0, atr_pct: float = 0.0,
                age_sec: float = 0.0, leverage: float = 100.0,
                remaining_fraction: float = 1.0,
                first_partial_done: bool = False,
                second_partial_done: bool = False,
                break_even_armed: bool = False,
                peak_price: float | None = None) -> ExitAction | None:
    entry = float(entry or 0); price = float(price or 0)
    remaining = max(0.0, min(1.0, float(remaining_fraction or 0)))
    sign = _sign(direction); atr = _atr_abs(entry, atr_abs, atr_pct)
    if entry <= 0 or price <= 0 or atr <= 0 or not sign or remaining <= 0:
        return None
    prior_peak = float(peak_price if peak_price is not None else entry)
    peak = max(prior_peak, price) if sign > 0 else min(prior_peak, price)
    arm_level = level(entry, direction, atr, BREAK_EVEN_ARM_ATR)
    armed = bool(break_even_armed or (arm_level and _target_hit(direction, peak, arm_level)))
    stop = level(entry, direction, atr, -INITIAL_STOP_ATR)
    if armed:
        trail = peak - sign * atr * TRAIL_ATR
        floor = _break_even_floor(entry, direction, leverage)
        stop = max(floor, trail) if sign > 0 else min(floor, trail)
    if stop is not None and _stop_hit(direction, price, stop):
        return ExitAction("TRAIL_STOP_1_ATR" if armed else "INITIAL_STOP_1_ATR",
                          remaining, stop, stop, 0.0, first_partial_done,
                          second_partial_done, armed, peak)
    first = level(entry, direction, atr, FIRST_PARTIAL_ATR)
    if not first_partial_done and first is not None and _target_hit(direction, price, first):
        close = min(FIRST_PARTIAL_FRACTION, remaining)
        return ExitAction("PARTIAL_TP_1_ATR", close, first, stop, remaining - close,
                          True, second_partial_done, armed, peak)
    second = level(entry, direction, atr, SECOND_PARTIAL_ATR)
    if not second_partial_done and second is not None and _target_hit(direction, price, second):
        close = min(SECOND_PARTIAL_FRACTION, remaining)
        return ExitAction("PARTIAL_TP_1_5_ATR", close, second, stop, remaining - close,
                          first_partial_done, True, armed, peak)
    final = level(entry, direction, atr, FINAL_TARGET_ATR)
    if final is not None and _target_hit(direction, price, final):
        return ExitAction("FINAL_TP_2_5_ATR", remaining, final, stop, 0.0,
                          first_partial_done, second_partial_done, armed, peak)
    if float(age_sec or 0) >= PATH_END_SEC:
        return ExitAction("PATH_END_120M", remaining, price, stop, 0.0,
                          first_partial_done, second_partial_done, armed, peak)
    return None


def entry_fields(direction: str, reference_price: float) -> dict:
    fields = entry_policy.entry_fields(direction, reference_price)
    fields.update({
        "entry_limit_policy": POLICY_ID, "raw_policy_id": POLICY_ID,
        "policy_id": POLICY_ID, "exit_profile_id": EXIT_PROFILE_ID,
        "initial_stop_atr_k": INITIAL_STOP_ATR,
        "partial_take_profit_atr": [FIRST_PARTIAL_ATR, SECOND_PARTIAL_ATR],
        "partial_take_profit_fractions": [FIRST_PARTIAL_FRACTION, SECOND_PARTIAL_FRACTION],
        "break_even_arm_atr_k": BREAK_EVEN_ARM_ATR,
        "break_even_floor_margin_pct": BREAK_EVEN_FLOOR_MARGIN_PCT,
        "trailing_stop_atr_k": TRAIL_ATR, "atr_tp_multiple": FINAL_TARGET_ATR,
        "path_end_sec": PATH_END_SEC, "account_risk_pct": ACCOUNT_RISK_PCT,
        "margin_cap_usd": MARGIN_CAP_USD, "paper_only": True,
        "relay_eligible": False,
        "relay_copy_readiness": "BLOCKED_PARTIAL_CLOSE_UNPROVEN",
    })
    return fields


def account_risk_quantity(*, equity_usd: float, entry_price: float,
                          atr_abs: float, leverage: float = 100.0) -> dict:
    risk_budget = max(0.0, float(equity_usd)) * ACCOUNT_RISK_PCT / 100.0
    risk_qty = risk_budget / max(float(atr_abs or 0), 1e-12)
    margin_qty = MARGIN_CAP_USD * max(float(leverage or 0), 1.0) / max(float(entry_price or 0), 1.0)
    quantity = min(risk_qty, margin_qty)
    return {"quantity": quantity, "risk_budget_usd": risk_budget,
            "margin_cap_usd": MARGIN_CAP_USD,
            "capped_by": "MARGIN" if margin_qty <= risk_qty else "ACCOUNT_RISK"}


def exit_config(analyzer_sync_id: str) -> dict:
    return {
        "policy_snapshot_schema": "exit_policy_v1", "policy_source": "btc-conservative-agent",
        "policy_version": POLICY_ID, "raw_policy_id": POLICY_ID,
        "analyzer_sync_id": analyzer_sync_id, "exit_profile_id": EXIT_PROFILE_ID,
        "atr_source": "FILL_TIME_3M_ATR14", "initial_stop_atr_k": INITIAL_STOP_ATR,
        "partial_take_profit_atr": [FIRST_PARTIAL_ATR, SECOND_PARTIAL_ATR],
        "partial_take_profit_fractions": [FIRST_PARTIAL_FRACTION, SECOND_PARTIAL_FRACTION],
        "break_even_arm_atr_k": BREAK_EVEN_ARM_ATR,
        "trailing_stop_atr_k": TRAIL_ATR, "atr_tp_multiple": FINAL_TARGET_ATR,
        "path_end_sec": PATH_END_SEC, "scenario_c_exit_profile": False,
        "partial_reduction_required": True,
    }


def chase_due(**kwargs):
    return entry_policy.chase_due(**kwargs)


def marketable_quote_at_limit(**kwargs):
    return entry_policy.marketable_quote_at_limit(**kwargs)


CHASE_STEP = entry_policy.CHASE_STEP


def dashboard_policy() -> dict:
    return {
        "filter_chips": ["PAPER ONLY", "Offset 0.29%", "$0.25 margin cap", "0.5% account risk",
                         "SL 1 ATR", "25% @1 ATR", "BE @1.25 ATR", "25% @1.5 ATR",
                         "Trail 1 ATR", "Final 2.5 ATR", "120m cap"],
        "entry": {"trigger": "Every shared AI_SCAN APPROVE direction",
                  "entry_path": "OFFSET_029_PATIENT_CHASE", "fill_path": "CONSERVATIVE_PAPER_LIMIT",
                  "execution": "Independent paper lifecycle; live copy remains fail-closed",
                  "chase_detail": "Rest 10m; 25% remaining-gap reprice every 60s at age 10–25m"},
        "exit": {"profile": "Static protected ATR", "ladder": "25%@1 ATR, 25%@1.5 ATR, runner",
                 "thesis_stop_margin_pct": "n/a", "hard_stop_margin_pct": "1 ATR",
                 "mfe_protect_margin_pct": "BE arm 1.25 ATR", "thesis_pause_above_margin_pct": "n/a",
                 "fixed_time_exit": "120m"},
        "strategy_detail": ["Initial full stop 1 ATR from fill", "Account risk <=0.5% and margin <=$0.25",
                            "After profit milestones, stop/trail never widens",
                            "Partial reductions are paper-only until relay reconciliation is proven"],
    }
