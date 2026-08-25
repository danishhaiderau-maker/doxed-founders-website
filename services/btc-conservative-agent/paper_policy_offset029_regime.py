"""Causal regime-adaptive protection treatment for Patient Chase Tile Four."""
from __future__ import annotations

import paper_policy_offset029 as entry_policy

LANE = "OFFSET_029_ATR_REGIME"
POLICY_ID = "OFFSET_0.29_CHASE_w234_s25_i60|REGIME_ATR_PROTECTION_V1"
EXIT_PROFILE_ID = "REGIME_ATR_PROTECTION_V1"
PATH_END_SEC = 7200
ACCOUNT_RISK_PCT = 0.5
MARGIN_CAP_USD = 0.25
PROFILES = {
    "SIDEWAYS": {"stop": 0.75, "partials": ((0.75, 0.25), (1.25, 0.25)), "trail": 0.75, "final": 2.5},
    "ORDINARY_TREND": {"stop": 1.0, "partials": ((1.0, 0.25), (1.5, 0.25)), "trail": 1.0, "final": 2.5},
    "STRONG_ALIGNED_TREND": {"stop": 1.25, "partials": ((1.25, 0.25),), "trail": 1.25, "final": 2.5},
}


def normalize_regime(value: str) -> str:
    value = str(value or "").upper().replace(" ", "_")
    value = {"RANGE": "SIDEWAYS", "CHOPPY": "SIDEWAYS", "TREND": "ORDINARY_TREND",
             "BULL": "ORDINARY_TREND", "BEAR": "ORDINARY_TREND",
             "STRONG_TREND": "STRONG_ALIGNED_TREND"}.get(value, value)
    return value if value in PROFILES else "ORDINARY_TREND"


def classify_regime(*, direction: str, market_regime: str = "",
                    trend_state: str = "", base_state: str = "", adx: float = 0.0) -> str:
    direction = str(direction or "").upper(); market = str(market_regime or "").upper()
    trend = str(trend_state or "").upper(); base = str(base_state or "").upper()
    if market in {"RANGE", "SIDEWAYS", "CHOPPY"} or "COMPRESSION" in market or trend == "MIXED":
        return "SIDEWAYS"
    aligned = (direction == "LONG" and base == "BULL") or (direction == "SHORT" and base == "BEAR")
    return "STRONG_ALIGNED_TREND" if aligned and not trend.endswith("_WEAKENING") and float(adx or 0) >= 30 else "ORDINARY_TREND"


def transition(*, previous_regime: str, observed_regime: str,
               current_stop_distance_atr: float) -> dict:
    old = normalize_regime(previous_regime); new = normalize_regime(observed_regime)
    requested = float(PROFILES[new]["stop"])
    applied = min(float(current_stop_distance_atr), requested)
    return {"from": old, "to": new, "requested_stop_atr": requested,
            "applied_stop_atr": applied, "risk_widened": False, "changed": old != new}


def entry_fields(direction: str, reference_price: float) -> dict:
    fields = entry_policy.entry_fields(direction, reference_price)
    fields.update({"entry_limit_policy": POLICY_ID, "raw_policy_id": POLICY_ID,
                   "policy_id": POLICY_ID, "exit_profile_id": EXIT_PROFILE_ID,
                   "account_risk_pct": ACCOUNT_RISK_PCT, "margin_cap_usd": MARGIN_CAP_USD,
                   "regime_profiles": PROFILES, "path_end_sec": PATH_END_SEC,
                   "paper_only": True, "relay_eligible": False,
                   "relay_copy_readiness": "BLOCKED_PARTIAL_CLOSE_UNPROVEN"})
    return fields


def exit_action(*, entry: float, direction: str, price: float, atr_abs: float,
                age_sec: float, regime: str, current_stop_distance_atr: float | None = None,
                remaining_fraction: float = 1.0, completed_partials=(),
                peak_price: float | None = None) -> dict | None:
    profile_name = normalize_regime(regime); profile = PROFILES[profile_name]
    sign = 1 if str(direction).upper() == "LONG" else -1
    entry = float(entry or 0); price = float(price or 0); atr = float(atr_abs or 0)
    if atr <= 0 or entry <= 0 or price <= 0:
        return None
    stop_k = min(float(current_stop_distance_atr or profile["stop"]), float(profile["stop"]))
    peak = float(peak_price if peak_price is not None else entry)
    peak = max(peak, price) if sign > 0 else min(peak, price)
    stop = entry - sign * atr * stop_k
    if completed_partials:
        trail = peak - sign * atr * float(profile["trail"])
        stop = max(stop, trail) if sign > 0 else min(stop, trail)
    if price <= stop if sign > 0 else price >= stop:
        return {"reason": "REGIME_ATR_STOP", "close_fraction": remaining_fraction,
                "remaining_fraction": 0.0, "stop_price": stop, "peak_price": peak,
                "regime": profile_name, "stop_distance_atr": stop_k}
    done = set(completed_partials or ())
    for target_k, fraction in profile["partials"]:
        if target_k in done:
            continue
        target = entry + sign * atr * target_k
        if price >= target if sign > 0 else price <= target:
            close = min(float(fraction), float(remaining_fraction))
            return {"reason": f"REGIME_PARTIAL_TP_{target_k:g}_ATR", "close_fraction": close,
                    "remaining_fraction": remaining_fraction - close, "stop_price": stop,
                    "peak_price": peak, "partial_key": target_k, "regime": profile_name,
                    "stop_distance_atr": stop_k}
    final = entry + sign * atr * float(profile["final"])
    if price >= final if sign > 0 else price <= final:
        return {"reason": "REGIME_FINAL_TP_2_5_ATR", "close_fraction": remaining_fraction,
                "remaining_fraction": 0.0, "stop_price": stop, "peak_price": peak,
                "regime": profile_name, "stop_distance_atr": stop_k}
    if float(age_sec or 0) >= PATH_END_SEC:
        return {"reason": "PATH_END_120M", "close_fraction": remaining_fraction,
                "remaining_fraction": 0.0, "stop_price": stop, "peak_price": peak,
                "regime": profile_name, "stop_distance_atr": stop_k}
    return None


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
    return {"policy_snapshot_schema": "exit_policy_v1", "policy_source": "btc-conservative-agent",
            "policy_version": POLICY_ID, "raw_policy_id": POLICY_ID,
            "analyzer_sync_id": analyzer_sync_id, "exit_profile_id": EXIT_PROFILE_ID,
            "atr_source": "FILL_TIME_3M_ATR14", "regime_profiles": PROFILES,
            "path_end_sec": PATH_END_SEC, "scenario_c_exit_profile": False,
            "partial_reduction_required": True, "risk_may_widen": False}


def chase_due(**kwargs):
    return entry_policy.chase_due(**kwargs)


def marketable_quote_at_limit(**kwargs):
    return entry_policy.marketable_quote_at_limit(**kwargs)


CHASE_STEP = entry_policy.CHASE_STEP


def dashboard_policy() -> dict:
    return {
        "filter_chips": ["PAPER ONLY", "Offset 0.29%", "$0.25 margin cap", "0.5% account risk",
                         "Causal regime", "Risk never widens", "120m cap"],
        "entry": {"trigger": "Every shared AI_SCAN APPROVE direction",
                  "entry_path": "OFFSET_029_PATIENT_CHASE", "fill_path": "CONSERVATIVE_PAPER_LIMIT",
                  "execution": "Independent paper lifecycle; live copy remains fail-closed",
                  "chase_detail": "Same Patient Chase entry and chase as Tiles 1 and 3"},
        "exit": {"profile": "Dynamic regime ATR protection", "ladder": "profile partials + runner",
                 "thesis_stop_margin_pct": "n/a", "hard_stop_margin_pct": "0.75/1/1.25 ATR",
                 "mfe_protect_margin_pct": "profile-specific trail", "thesis_pause_above_margin_pct": "n/a",
                 "fixed_time_exit": "120m"},
        "strategy_detail": ["SIDEWAYS: 0.75 ATR stop/partials/trail", "ORDINARY: 1 ATR stop/trail",
                            "STRONG: 1.25 ATR stop/trail and 75% runner",
                            "Regime changes are causal; stop distance and risk never widen",
                            "Partial reductions are paper-only until relay reconciliation is proven"],
    }
