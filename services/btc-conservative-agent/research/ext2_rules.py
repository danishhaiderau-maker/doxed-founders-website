"""Frozen, swappable rules for the EXT2 screen. Paper only.

These functions are the only place the HAC test, the percentile defined-share
gate, and the liquidation fill are decided. N, q and the BH denominator stay
181442 and 0.05.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from research.ext2_costs import Ext2Stop

COMBOS_SEARCHED = 181_442
BH_Q = 0.05
BH_DENOMINATOR = COMBOS_SEARCHED
BOOTSTRAP_DRAWS = 2000
WHITE_RC_DRAWS = 2000
WHITE_RC_SEED = 20261109
WHITE_RC_IS_HARD_GATE = True
NEFF_LEAD_FLOOR = 30
NEFF_LEAD_FLOOR_HARD = True
PERCENTILE_DEFINED_SHARE = 0.95
PERCENTILE_SPAN_BARS = 30 * 1440
ENTRY_LATENCY_CAP_MS = 60_000
ENTRY_DELAY_MS = 1_000
LIQUIDATION_ADVERSE_BP = 50.0
PRIMARY_P_MIN_ATTAINABLE = 0.0
NEWEY_WEST_LAG_RULE = "floor(4*(T/100)**(2/9))"
P_METHOD = "one_sided_student_t_hac_newey_west"
P_DISTRIBUTION = "student_t_df_T_minus_1"
SIGMA_ESTIMATOR = "trailing_causal_29d_hourly_r24"
C2_STUDY1_DISCLOSURE = (
    "W1 and H1 sealed but previously viewed (Study1) were previously viewed by "
    "EXT-STUDY-20261031 Study1 TRAIN (coarse grammar, N=4,720, no survivors)."
)
MARK_PRICE_APPROXIMATION = "trade_print_proxy_no_historical_mark"


def bh_threshold() -> float:
    return BH_Q / BH_DENOMINATOR


def assert_primary_p_can_reject() -> None:
    if not PRIMARY_P_MIN_ATTAINABLE < bh_threshold():
        raise Ext2Stop("STOP_P_MIN")


def newey_west_lag(observations: int) -> int:
    """Pre-registered lag. Named rule: floor(4*(T/100)**(2/9))."""
    if observations < 2:
        return 0
    return int(math.floor(4.0 * (observations / 100.0) ** (2.0 / 9.0)))


def _betacf(a: float, b: float, x: float) -> float:
    max_iter = 200
    amp = 1e-12
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < amp:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - log_beta) / a
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x)
    return 1.0 - math.exp(b * math.log(1.0 - x) + a * math.log(x) - log_beta) / b * _betacf(b, a, 1.0 - x)


def student_t_sf(statistic: float, degrees: int) -> float:
    """One-sided upper tail of Student-t with `degrees` df. Not the normal tail."""
    if degrees < 1:
        raise Ext2Stop("STOP_T_DF")
    x = degrees / (degrees + statistic * statistic)
    tail = 0.5 * _regularized_beta(x, degrees / 2.0, 0.5)
    if statistic >= 0.0:
        return tail
    return 1.0 - tail


def hac_onesided_t_pvalue(daily_net: np.ndarray) -> float:
    """Primary cell p. H0: mean(D) <= 0. Parametric Student-t, df = T-1."""
    values = np.asarray(daily_net, dtype=np.float64)
    observations = int(values.size)
    if observations < 2 or not np.isfinite(values).all():
        return 1.0
    mean = float(values.mean())
    centred = values - mean
    lag = newey_west_lag(observations)
    gamma0 = float(np.dot(centred, centred) / observations)
    variance = gamma0
    for ell in range(1, lag + 1):
        weight = 1.0 - ell / (lag + 1.0)
        gamma = float(np.dot(centred[ell:], centred[:-ell]) / observations)
        variance += 2.0 * weight * gamma
    variance = max(variance, 0.0)
    standard_error = math.sqrt(variance / observations)
    if standard_error == 0.0:
        return PRIMARY_P_MIN_ATTAINABLE if mean > 0.0 else 1.0
    statistic = mean / standard_error
    return float(student_t_sf(statistic, observations - 1))


def bootstrap_p_value(extremes: int, draws: int) -> float:
    """Reported only. (1+k)/(B+1). Never k/B."""
    if draws < 1 or extremes < 0:
        raise Ext2Stop("STOP_BOOTSTRAP")
    return (1 + extremes) / (draws + 1)


def bootstrap_p_min_attainable(draws: int = BOOTSTRAP_DRAWS) -> float:
    return bootstrap_p_value(0, draws)


def percentile_is_defined(values: np.ndarray) -> bool:
    """True only when the full 30-day span is present and at least 95% is defined."""
    window = np.asarray(values, dtype=np.float64)
    if window.shape != (PERCENTILE_SPAN_BARS,):
        return False
    share = float(np.isfinite(window).mean())
    return share + 1e-15 >= PERCENTILE_DEFINED_SHARE


def defined_share(values: np.ndarray) -> float | None:
    window = np.asarray(values, dtype=np.float64)
    if window.shape != (PERCENTILE_SPAN_BARS,):
        return None
    return float(np.isfinite(window).mean())


def liquidation_fill_price(entry_price: float, side: float, print_price: float) -> float:
    """Worse of the derived liquidation level and the print. Long: the lower price."""
    level = float(entry_price) * (1.0 - float(side) * LIQUIDATION_ADVERSE_BP / 10000.0)
    if float(side) > 0.0:
        return min(level, float(print_price))
    return max(level, float(print_price))


def entry_latency_ms(bar_close_mts: int, print_mts: int) -> int:
    return int(print_mts) - (int(bar_close_mts) + ENTRY_DELAY_MS)


def entry_latency_accepted(bar_close_mts: int, print_mts: int) -> bool:
    delay = entry_latency_ms(bar_close_mts, print_mts)
    return 0 <= delay <= ENTRY_LATENCY_CAP_MS


def latency_distribution(delays_ms: list[int]) -> dict[str, Any]:
    accepted = [delay for delay in delays_ms if 0 <= delay <= ENTRY_LATENCY_CAP_MS]
    rejected = [delay for delay in delays_ms if delay > ENTRY_LATENCY_CAP_MS]
    array = np.asarray(accepted, dtype=np.float64)
    return {
        "cap_ms": ENTRY_LATENCY_CAP_MS,
        "n_accepted": len(accepted),
        "n_rejected_stale": len(rejected),
        "p50_ms": None if array.size == 0 else float(np.quantile(array, 0.50)),
        "p90_ms": None if array.size == 0 else float(np.quantile(array, 0.90)),
        "max_ms": None if array.size == 0 else float(array.max()),
    }


def lead_neff_ok(n_eff: float) -> bool:
    return float(n_eff) >= NEFF_LEAD_FLOOR


def white_rc_passes(p_value: float) -> bool:
    """Gate 7. Hard. Not a secondary confirmation."""
    if not WHITE_RC_IS_HARD_GATE:
        raise Ext2Stop("STOP_WHITE_RC")
    return float(p_value) <= BH_Q


def dry_check_coverage(defined: dict[str, dict[int, np.ndarray]], fold_bars: dict[str, np.ndarray],
                       percentile_windows: dict[str, dict[int, np.ndarray]]) -> dict[str, Any]:
    """Coverage only. This function does not read prices, signals, or P&L."""
    first_defined = {}
    fold_share = {}
    shares = []
    for name, by_lookback in defined.items():
        first_defined[name] = {}
        for lookback, mask in by_lookback.items():
            hits = np.flatnonzero(np.asarray(mask, dtype=bool))
            first_defined[name][str(lookback)] = None if hits.size == 0 else int(hits[0])
    for fold, mask in fold_bars.items():
        values = np.asarray(mask, dtype=bool)
        fold_share[fold] = 0.0 if values.size == 0 else float(values.mean())
    min_by_percentile = {str(level): None for level in (80, 90, 95, 97.5, 99)}
    for by_lookback in percentile_windows.values():
        for window in by_lookback.values():
            share = defined_share(window)
            if share is None:
                continue
            shares.append(share)
    minimum = None if not shares else float(min(shares))
    for key in min_by_percentile:
        min_by_percentile[key] = minimum
    untestable = 0
    for by_lookback in defined.values():
        for mask in by_lookback.values():
            if not bool(np.asarray(mask, dtype=bool).any()):
                untestable += 4 * 5 * 2 * 4 * 54
    return {
        "reads_pnl": False,
        "reads_signals": False,
        "percentile_defined_share": PERCENTILE_DEFINED_SHARE,
        "first_defined_bar": first_defined,
        "fold_eligible_share": fold_share,
        "min_defined_share_by_percentile": min_by_percentile,
        "structurally_untestable_cells": untestable,
        "empty_bar_count": None,
    }


def dual_c2_projection_hours(lattice_seconds: float, quantile_seconds: float, white_rc_seconds: float) -> float:
    """Runtime projection includes rolling quantiles and the White RC panel."""
    parts = (float(lattice_seconds), float(quantile_seconds), float(white_rc_seconds))
    if any(part < 0.0 or not math.isfinite(part) for part in parts):
        raise Ext2Stop("STOP_DUAL_C2")
    return sum(parts) / 3600.0


def projection_needs_new_card(hours: float) -> bool:
    return float(hours) > 2.0


def rule_receipt_fields() -> dict[str, Any]:
    assert_primary_p_can_reject()
    return {
        "fee_bp": 0,
        "funding": "set_aside",
        "p_method": P_METHOD,
        "p_distribution": P_DISTRIBUTION,
        "hac_parametric": True,
        "newey_west_lag_rule": NEWEY_WEST_LAG_RULE,
        "p_min_attainable": PRIMARY_P_MIN_ATTAINABLE,
        "bootstrap_p_formula": "(1+k)/(B+1)",
        "bootstrap_p_min_attainable": bootstrap_p_min_attainable(),
        "bootstrap_role": "reported_only",
        "white_rc_role": "hard_gate_7",
        "bh_q": BH_Q,
        "bh_denominator": BH_DENOMINATOR,
        "percentile_defined_share": PERCENTILE_DEFINED_SHARE,
        "neff_lead_floor": NEFF_LEAD_FLOOR,
        "neff_lead_floor_hard": NEFF_LEAD_FLOOR_HARD,
        "entry_latency_cap_ms": ENTRY_LATENCY_CAP_MS,
        "sigma_estimator": SIGMA_ESTIMATOR,
        "dual_c2_projection_parts": ["exit_lattice", "rolling_quantiles", "white_rc_panel"],
        "gpu_dtype": "float64",
        "c2_study1_disclosure": C2_STUDY1_DISCLOSURE,
        "mark_price_approximation": MARK_PRICE_APPROXIMATION,
        "posted_n": COMBOS_SEARCHED,
    }
