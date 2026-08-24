"""Dynamic, preregistered protection for Patient Chase Tile Four."""
from __future__ import annotations

from dataclasses import dataclass

import paper_policy_offset029 as entry_policy

LANE = "OFFSET_029_ATR_REGIME"
POLICY_ID = "OFFSET_0.29_CHASE_w234_s25_i60|REGIME_ATR_PROTECTION_V1"
EXIT_PROFILE_ID = "REGIME_ATR_PROTECTION_V1"
PATH_END_SEC = 7200

PROFILES = {
    "SIDEWAYS": {"stop": .75, "partials": ((.75, .25), (1.25, .25)), "trail": .75, "final": 2.5},
    "ORDINARY_TREND": {"stop": 1.0, "partials": ((1.0, .25), (1.5, .25)), "trail": 1.0, "final": 2.5},
    "STRONG_ALIGNED_TREND": {"stop": 1.25, "partials": ((1.25, .25),), "trail": 1.25, "final": 2.5},
}
STRONG_TREND_ADX_MIN = 30.0


def classify_regime(*, direction: str, market_regime: str = "",
                    trend_state: str = "", base_state: str = "",
                    adx: float = 0.0) -> str:
    """Causal profile selection from fields available on the current tick."""
    direction = str(direction or "").upper()
    market = str(market_regime or "").upper()
    trend = str(trend_state or "").upper()
    base = str(base_state or "").upper()
    if market in {"RANGE", "SIDEWAYS", "CHOPPY"} or "COMPRESSION" in market or trend == "MIXED":
        return "SIDEWAYS"
    aligned = (direction == "LONG" and base == "BULL") or (direction == "SHORT" and base == "BEAR")
    weakening = trend.endswith("_WEAKENING")
    if aligned and not weakening and float(adx or 0.0) >= STRONG_TREND_ADX_MIN:
        return "STRONG_ALIGNED_TREND"
    return "ORDINARY_TREND"


def normalize_regime(value: str) -> str:
    value = str(value or "").upper().replace(" ", "_")
    aliases = {"RANGE": "SIDEWAYS", "CHOPPY": "SIDEWAYS", "TREND": "ORDINARY_TREND",
               "BULL": "ORDINARY_TREND", "BEAR": "ORDINARY_TREND", "STRONG_TREND": "STRONG_ALIGNED_TREND"}
    value = aliases.get(value, value)
    return value if value in PROFILES else "ORDINARY_TREND"


def transition(*, previous_regime: str, observed_regime: str,
               current_stop_distance_atr: float) -> dict:
    """Regime changes are live, but may never widen an existing stop."""
    old = normalize_regime(previous_regime)
    new = normalize_regime(observed_regime)
    requested = float(PROFILES[new]["stop"])
    applied = min(float(current_stop_distance_atr), requested)
    return {"from": old, "to": new, "requested_stop_atr": requested,
            "applied_stop_atr": applied, "risk_widened": False,
            "changed": old != new}


def account_risk_quantity(*, equity_usd: float, account_risk_pct: float,
                          entry_price: float, atr_abs: float,
                          leverage: float, margin_cap_usd: float = 0.25) -> dict:
    risk_budget = max(0.0, float(equity_usd)) * max(0.0, float(account_risk_pct)) / 100.0
    stop_move = max(0.0, float(atr_abs))
    risk_qty = risk_budget / stop_move if stop_move else 0.0
    cap_qty = max(0.0, float(margin_cap_usd)) * max(1.0, float(leverage)) / max(float(entry_price), 1.0)
    qty = min(risk_qty, cap_qty)
    return {"quantity": qty, "risk_budget_usd": risk_budget,
            "margin_cap_usd": margin_cap_usd, "capped_by": "MARGIN" if cap_qty <= risk_qty else "ACCOUNT_RISK"}


def entry_fields(direction: str, reference_price: float) -> dict:
    fields = entry_policy.entry_fields(direction, reference_price)
    fields.update({"entry_limit_policy": POLICY_ID, "raw_policy_id": POLICY_ID,
                   "policy_id": POLICY_ID, "exit_profile_id": EXIT_PROFILE_ID,
                   "account_risk_pct": .5, "margin_cap_usd": 0.25,
                   "regime_profiles": PROFILES, "path_end_sec": PATH_END_SEC,
                   "relay_eligible": False, "relay_configured": True,
                   "relay_copy_readiness": "BLOCKED_PARTIAL_CLOSE_UNSUPPORTED"})
    return fields


def exit_action(*, entry: float, direction: str, price: float, atr_abs: float,
                age_sec: float, regime: str, current_stop_distance_atr: float | None = None,
                remaining_fraction: float = 1.0, completed_partials=(),
                peak_price: float | None = None) -> dict | None:
    profile_name = normalize_regime(regime)
    profile = PROFILES[profile_name]
    sign = 1 if str(direction).upper() == "LONG" else -1
    atr = float(atr_abs or 0); entry = float(entry or 0); price = float(price or 0)
    if atr <= 0 or entry <= 0 or price <= 0:
        return None
    stop_k = min(float(current_stop_distance_atr or profile["stop"]), float(profile["stop"]))
    peak = float(peak_price if peak_price is not None else entry)
    peak = max(peak, price) if sign > 0 else min(peak, price)
    stop = entry - sign * atr * stop_k
    # After a first partial, trail the remaining quantity without ever widening.
    if completed_partials:
        trail = peak - sign * atr * float(profile["trail"])
        stop = max(stop, trail) if sign > 0 else min(stop, trail)
    stopped = price <= stop if sign > 0 else price >= stop
    if stopped:
        return {"reason": "REGIME_ATR_STOP", "close_fraction": remaining_fraction,
                "remaining_fraction": 0.0, "stop_price": stop, "peak_price": peak,
                "regime": profile_name, "stop_distance_atr": stop_k}
    done = set(completed_partials or ())
    for target_k, fraction in profile["partials"]:
        if target_k in done:
            continue
        target = entry + sign * atr * target_k
        hit = price >= target if sign > 0 else price <= target
        if hit:
            close = min(float(fraction), float(remaining_fraction))
            return {"reason": f"REGIME_PARTIAL_TP_{target_k:g}_ATR", "close_fraction": close,
                    "remaining_fraction": remaining_fraction - close, "stop_price": stop,
                    "peak_price": peak, "partial_key": target_k, "regime": profile_name,
                    "stop_distance_atr": stop_k}
    final = entry + sign * atr * float(profile["final"])
    if (price >= final if sign > 0 else price <= final):
        return {"reason": "REGIME_FINAL_TP_2_5_ATR", "close_fraction": remaining_fraction,
                "remaining_fraction": 0.0, "stop_price": stop, "peak_price": peak,
                "regime": profile_name, "stop_distance_atr": stop_k}
    if float(age_sec or 0) >= PATH_END_SEC:
        return {"reason": "PATH_END_120M", "close_fraction": remaining_fraction,
                "remaining_fraction": 0.0, "stop_price": stop, "peak_price": peak,
                "regime": profile_name, "stop_distance_atr": stop_k}
    return None


def dashboard_policy() -> dict:
    return {
        "filter_chips": ["Shared AI", "Offset 0.29%", "$2 margin cap", "0.5% account risk",
                         "Continuous regime", "Risk never widens", "120m cap"],
        "entry": {"trigger": "Every shared AI_SCAN APPROVE direction",
                  "entry_path": "OFFSET_029_PATIENT_CHASE", "fill_path": "CONSERVATIVE_PAPER_LIMIT",
                  "execution": "Tile ON creates paper lifecycle; separate armed relay may copy it",
                  "chase_detail": "Same frozen Patient Chase entry and chase as Tiles 1 and 3"},
        "exit": {"profile": "Dynamic regime ATR protection", "ladder": "profile-specific partials + runner",
                 "thesis_stop_margin_pct": "n/a", "hard_stop_margin_pct": "0.75/1/1.25 ATR",
                 "mfe_protect_margin_pct": "profile-specific trail", "thesis_pause_above_margin_pct": "n/a",
                 "fixed_time_exit": "120m"},
        "strategy_detail": ["SIDEWAYS: .75 ATR stop/partials/trail", "ORDINARY: 1 ATR stop/trail",
                            "STRONG: 1.25 ATR stop/trail and 75% runner",
                            "Regime may change during trade; existing stop and risk never widen",
                            "Every transition is timestamped; relay OFF means paper only"],
    }
