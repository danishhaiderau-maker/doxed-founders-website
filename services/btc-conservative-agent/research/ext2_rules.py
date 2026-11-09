"""Frozen, swappable rules for the EXT2 screen. Paper only.

These functions are the only place the HAC test, the percentile defined-share
gate, and the liquidation fill are decided. N, q and the BH denominator stay
181442 and 0.05.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import NormalDist
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
T_MIN_TEST_DAYS = 20
SD_FLOOR_REL = 1e-12
UNTESTED_P = 1.0
NEWEY_WEST_LAG_RULE = "floor(4*(T/100)**(2/9))"
P_METHOD = "one_sided_student_t_hac_newey_west"
P_DISTRIBUTION = "student_t_df_T_minus_1"
SIGMA_ESTIMATOR = "trailing_causal_29d_hourly_r24"
# Gate 6 (RK D1, 10 Nov): the shipped HAC-t (Student-t T-1) over-rejects in small T
# (HM null sim: ~47x nominal at the BH region at T=41, lag 3). Until a pre-registered
# null-sim acceptance passes AND card v4 has a board PASS, gate 6 is NOMINAL-LEVEL and
# gate 7 (White RC) is the binding control. Method is a config key; default unchanged.
GATE6_DISCLOSURE = "gate 6 nominal-level; gate 7 White RC binding"
GATE6_METHOD_HAC_T = "hac_t_student_T1"
GATE6_METHOD = GATE6_METHOD_HAC_T
GATE6_METHODS: tuple[str, ...] = (GATE6_METHOD_HAC_T,)
GATE6_METHODS_ACCEPTED_FOR_LAUNCH: tuple[str, ...] = (GATE6_METHOD_HAC_T,)
GATE6_NOMINAL_ONLY = {GATE6_METHOD_HAC_T: True}
C2_STUDY1_DISCLOSURE = (
    "W1 and H1 sealed but previously viewed (Study1) were previously viewed by "
    "EXT-STUDY-20261031 Study1 TRAIN (coarse grammar, N=4,720, no survivors). "
    "They are not touched by RUN-A/A2, Step-1, Phase-B or the spent 2026-03..10 hold-out. "
    "The genuinely untouched forward window is post-2026-10-29 public data plus forward "
    "paper (separate card)."
)
LABEL = "LIMITED SCOPE / NOT A VERDICT / SCREEN-LEVEL / EXT2-EXTERNAL / EXPLORATORY / INVALID-for-fills / R24 NOT TESTED"
R3_LEAD_LABEL = "INVALID-for-fills research lead (R3), never an edge"
LIQUIDATION_DISCLOSURE = (
    "Liquidation is detected per trade print (mark-price approximation: trade print proxy, "
    "no historical mark). The fill is the worse of the derived 100x level and the print (RK). "
    "Because detection requires a print at or beyond the level, the worse-of always equals "
    "the crossing print in this screen (HM N6)."
)
DSR_MIN = 0.95
WHITE_RC_P_MAX = 0.05
MIN_OOF_TRADES = 60
MIN_FOLDS_POS = 3
MAX_DAY_SHARE = 0.40
MAX_WEEK_SHARE = 0.50
MIN_MEDIAN_HOLD_S = 300.0
MAX_LEADS = 10
SWITCH_MIN_INNER_TRADES = 20
SWITCH_MIN_LEG_OOF_TRADES = 20
DSR_ANNUALISATION = math.sqrt(365.0)
EULER_GAMMA = 0.5772156649015329
MARK_PRICE_APPROXIMATION = "trade_print_proxy_no_historical_mark"


def bh_threshold() -> float:
    return BH_Q / BH_DENOMINATOR


def bh_critical_t(observations: int, threshold: float | None = None) -> float:
    """Smallest t with one-sided Student-t (df = T-1) tail <= BH threshold. Computed."""
    if observations < 2:
        return math.inf
    target = bh_threshold() if threshold is None else float(threshold)
    lo, hi = 0.0, 1.0
    while student_t_sf(hi, observations - 1) > target:
        hi *= 2.0
        if hi > 1e12:
            return math.inf
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if student_t_sf(mid, observations - 1) > target:
            lo = mid
        else:
            hi = mid
    return hi


def p_min_attainable_table(t_values: Sequence[int] = tuple(range(T_MIN_TEST_DAYS, 44))) -> dict[str, Any]:
    """Boss ruling: computed from the t-tail at each T, not asserted. For every
    pre-registered T, the critical t is finite and the tail at it is <= q/N."""
    rows = {}
    for observations in t_values:
        crit = bh_critical_t(int(observations))
        tail = student_t_sf(crit, int(observations) - 1) if math.isfinite(crit) else 1.0
        rows[str(int(observations))] = {"critical_t": crit, "tail_at_critical_t": tail,
                                        "reachable": bool(math.isfinite(crit) and tail <= bh_threshold())}
    return {"bh_threshold": bh_threshold(), "t_min_test_days": T_MIN_TEST_DAYS, "by_T": rows}


def assert_primary_p_can_reject() -> dict[str, Any]:
    table = p_min_attainable_table()
    if not all(row["reachable"] for row in table["by_T"].values()):
        raise Ext2Stop("STOP_P_MIN")
    return table


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


def hac_statistic(daily_net: np.ndarray) -> dict[str, Any]:
    """HAC t ingredients. Untested (p = 1) when T < T_MIN_TEST_DAYS, any value is
    non-finite, the sample sd is zero (or below the relative floor), or the HAC
    variance is not positive. Never p = 0 from a degenerate series (Boss ruling)."""
    values = np.asarray(daily_net, dtype=np.float64)
    observations = int(values.size)
    result = {"T": observations, "lag": newey_west_lag(observations), "df": observations - 1,
              "t": None, "testable": False, "reason": None}
    if observations < T_MIN_TEST_DAYS:
        result["reason"] = "T_BELOW_MIN"
        return result
    if not np.isfinite(values).all():
        result["reason"] = "NON_FINITE"
        return result
    mean = float(values.mean())
    sd = float(values.std(ddof=0))
    if sd == 0.0 or sd <= SD_FLOOR_REL * max(1.0, abs(mean)):
        result["reason"] = "ZERO_VARIANCE"
        return result
    centred = values - mean
    lag = result["lag"]
    variance = float(np.dot(centred, centred) / observations)
    for ell in range(1, lag + 1):
        weight = 1.0 - ell / (lag + 1.0)
        gamma = float(np.dot(centred[ell:], centred[:-ell]) / observations)
        variance += 2.0 * weight * gamma
    if not variance > 0.0:
        result["reason"] = "HAC_VARIANCE_NONPOSITIVE"
        return result
    result["t"] = mean / math.sqrt(variance / observations)
    result["testable"] = True
    return result


def gate6_method_check(method: str, *, real: bool = False) -> str:
    """Config key for the gate-6 p method. Unknown = STOP. A method not accepted for
    launch (null-sim acceptance + card v4 board PASS) is refused in a real run."""
    if method not in GATE6_METHODS:
        raise Ext2Stop("STOP_GATE6_METHOD")
    if real and method not in GATE6_METHODS_ACCEPTED_FOR_LAUNCH:
        raise Ext2Stop("STOP_GATE6_METHOD_NOT_ACCEPTED")
    return method


def primary_p_from_stat(stat: Mapping[str, Any], method: str | None = None) -> float:
    """The one place the primary p is formed. Default: Student-t upper tail, df = T-1
    (nominal-level, see GATE6_DISCLOSURE); untested -> UNTESTED_P (1.0)."""
    chosen = GATE6_METHOD if method is None else method
    gate6_method_check(chosen)
    if not stat["testable"]:
        return UNTESTED_P
    return float(student_t_sf(stat["t"], stat["T"] - 1))


def hac_onesided_t_pvalue(daily_net: np.ndarray) -> float:
    """Primary cell p. H0: mean(D) <= 0. Parametric Student-t, df = T-1."""
    return primary_p_from_stat(hac_statistic(daily_net))


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
    return float(p_value) <= WHITE_RC_P_MAX


def white_rc_gate(p_values: np.ndarray) -> np.ndarray:
    """Gate 7 over the universe (same rule as white_rc_passes)."""
    if not WHITE_RC_IS_HARD_GATE:
        raise Ext2Stop("STOP_WHITE_RC")
    return np.asarray(p_values, dtype=np.float64) <= WHITE_RC_P_MAX


def dry_check_coverage(eligible: dict[str, dict[int, np.ndarray]], fold_masks: dict[str, np.ndarray],
                       window_share: dict[str, dict[int, np.ndarray]] | None = None,
                       members: Sequence[tuple[str, ...]] | None = None) -> dict[str, Any]:
    """Coverage only (no prices, signals or P&L).

    eligible[feature][L]: per-bar bool, feature defined AND its trailing
    percentile defined (95% rule). fold_masks[fold]: per-bar bool, the fold's
    scored test bars. window_share[feature][L]: per-bar finite share of the
    trailing window (p-independent: one number, not five copies; HM N4).
    members: singles and pairs; a pair is eligible on a bar only if both are.
    Untestable cells are counted per (member, L) with no eligible scored bar
    in any fold: 5 p x 2 M x 4 G x 54 exits = 2,160 each (no x4 overcount).
    """
    names = list(eligible)
    if members is None:
        members = [(name,) for name in names] + [
            (names[i], names[j]) for i in range(len(names)) for j in range(i + 1, len(names))
        ]
    per_member: dict[str, Any] = {}
    untestable = 0
    cells_per_member_l = 5 * 2 * 4 * 54
    for member in members:
        key = "+".join(member)
        per_member[key] = {}
        for lookback in eligible[member[0]]:
            mask = np.ones_like(np.asarray(eligible[member[0]][lookback], dtype=bool))
            for name in member:
                mask &= np.asarray(eligible[name][lookback], dtype=bool)
            hits = np.flatnonzero(mask)
            by_fold = {}
            any_scored = False
            for fold, fmask in fold_masks.items():
                fm = np.asarray(fmask, dtype=bool)
                total = int(fm.sum())
                ok = int((mask & fm).sum())
                by_fold[fold] = {"eligible_bars": ok, "scored_bars": total,
                                 "eligible_share": 0.0 if total == 0 else ok / total}
                any_scored |= ok > 0
            entry = {"first_eligible_bar": None if hits.size == 0 else int(hits[0]), "by_fold": by_fold,
                     "structurally_untestable": not any_scored}
            if len(member) == 1 and window_share is not None:
                share = np.asarray(window_share[member[0]][lookback], dtype=np.float64)
                scored = np.zeros(share.shape[0], dtype=bool)
                for fm in fold_masks.values():
                    scored |= np.asarray(fm, dtype=bool)
                vals = share[scored & np.isfinite(share)]
                entry["min_window_defined_share_on_scored_bars"] = None if vals.size == 0 else float(vals.min())
            per_member[key][str(lookback)] = entry
            if not any_scored:
                untestable += cells_per_member_l
    return {
        "reads_pnl": False,
        "reads_signals": False,
        "percentile_defined_share": PERCENTILE_DEFINED_SHARE,
        "percentile_share_is_p_independent": True,
        "per_member": per_member,
        "structurally_untestable_cells": untestable,
        "cells_per_member_lookback": cells_per_member_l,
    }


def bh_reject(p_values: np.ndarray, q: float = BH_Q, denominator: int = BH_DENOMINATOR) -> np.ndarray:
    """Benjamini-Hochberg step-up over the full posted N (untested cells carry p=1)."""
    p = np.asarray(p_values, dtype=np.float64)
    if p.shape[0] != denominator:
        raise Ext2Stop("STOP_BH_DENOMINATOR")
    p = np.where(np.isfinite(p), p, 1.0)
    order = np.argsort(p, kind="mergesort")
    ranked = p[order]
    limits = q * np.arange(1, denominator + 1) / denominator
    passing = np.flatnonzero(ranked <= limits)
    reject = np.zeros(denominator, dtype=bool)
    if passing.size:
        cut = ranked[passing[-1]]
        reject = p <= cut
    return reject


def stationary_bootstrap_indices(length: int, draws: int, mean_block: float,
                                 rng: np.random.Generator) -> np.ndarray:
    """Politis-Romano stationary bootstrap indices (draws x length). With a mean
    block of 1 observation every position restarts, i.e. an iid day bootstrap."""
    if length < 1 or draws < 1 or mean_block < 1.0:
        raise Ext2Stop("STOP_BOOTSTRAP")
    restart_p = 1.0 / float(mean_block)
    idx = np.empty((draws, length), dtype=np.int64)
    idx[:, 0] = rng.integers(0, length, size=draws)
    for pos in range(1, length):
        restart = rng.random(draws) < restart_p
        fresh = rng.integers(0, length, size=draws)
        idx[:, pos] = np.where(restart, fresh, (idx[:, pos - 1] + 1) % length)
    return idx


def bootstrap_mean_pvalue(daily: np.ndarray, seed: int, draws: int = BOOTSTRAP_DRAWS,
                          mean_block: float = 1.0) -> float:
    """Reported only (RK): one-sided p that mean <= 0, centred stationary bootstrap,
    (1+k)/(B+1)."""
    values = np.asarray(daily, dtype=np.float64)
    if values.size < 2:
        return UNTESTED_P
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    idx = stationary_bootstrap_indices(values.size, draws, mean_block, rng)
    observed = float(values.mean())
    centred = values - observed
    boot = centred[idx].mean(axis=1)
    extremes = int(np.sum(boot >= observed))
    return bootstrap_p_value(extremes, draws)


def white_rc_pvalues(panel: np.ndarray, tested: np.ndarray, *, draws: int = WHITE_RC_DRAWS,
                     seed: int = WHITE_RC_SEED, mean_block: float = 1.0,
                     chunk: int = 20_000, backend: Any = None) -> dict[str, Any]:
    """White (2000) reality check over the full universe (A17 gate 7).

    panel: cells x days daily net (0 on no-trade days). Untested cells are
    excluded from the max. Null: each cell's resampled mean is centred on its
    own sample mean. Per-cell single-step p_j = (1 + #{b: max_k m*_kb >= m_j}) / (B+1);
    for the best cell this is White's RC p.
    """
    data = np.asarray(panel, dtype=np.float64)
    cells, days = data.shape
    mask = np.asarray(tested, dtype=bool)
    out = np.ones(cells, dtype=np.float64)
    if days == 0 or not np.any(mask):
        return {"p": out, "rc_p_best": 1.0, "best_cell": None, "draws": draws, "seed": seed}
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    idx = stationary_bootstrap_indices(days, draws, mean_block, rng)
    counts = np.zeros((draws, days), dtype=np.float64)
    np.add.at(counts, (np.repeat(np.arange(draws), days), idx.reshape(-1)), 1.0)
    weights = counts / days
    tested_idx = np.flatnonzero(mask)
    observed = data.mean(axis=1)
    vmax = np.full(draws, -np.inf)
    for start in range(0, tested_idx.size, chunk):
        sel = tested_idx[start:start + chunk]
        block = data[sel]
        if backend is not None:
            boot = backend.matmul(weights, block.T)
        else:
            boot = weights @ block.T
        boot = boot - observed[sel][None, :]
        vmax = np.maximum(vmax, boot.max(axis=1))
    sorted_v = np.sort(vmax)
    obs_t = observed[tested_idx]
    k = draws - np.searchsorted(sorted_v, obs_t, side="left")
    out[tested_idx] = (1.0 + k) / (draws + 1.0)
    best = int(tested_idx[int(np.argmax(obs_t))])
    return {"p": out, "rc_p_best": float(out[best]), "best_cell": best, "draws": draws, "seed": seed,
            "statistic": "max_j mean(D*_j - mean D_j) vs mean D_j", "p_formula": "(1+k)/(B+1)"}


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def sharpe_stats(daily: np.ndarray) -> dict[str, float] | None:
    values = np.asarray(daily, dtype=np.float64)
    if values.size < 3:
        return None
    sd = float(values.std(ddof=1))
    if not sd > 0.0:
        return None
    mean = float(values.mean())
    centred = values - mean
    m2 = float(np.mean(centred ** 2))
    skew = float(np.mean(centred ** 3) / m2 ** 1.5) if m2 > 0 else 0.0
    kurt = float(np.mean(centred ** 4) / m2 ** 2) if m2 > 0 else 3.0
    return {"sr": mean / sd, "skew": skew, "kurt": kurt, "T": int(values.size)}


def expected_max_sharpe(sr_variance: float, trials: int = COMBOS_SEARCHED) -> float:
    """Bailey-Lopez de Prado SR0 for N independent trials (False Strategy Theorem)."""
    if not sr_variance > 0.0:
        return 0.0
    inv = NormalDist().inv_cdf
    a = inv(1.0 - 1.0 / trials)
    b = inv(1.0 - 1.0 / (trials * math.e))
    return math.sqrt(sr_variance) * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b)


def deflated_sharpe(daily: np.ndarray, sr0: float) -> float:
    """DSR = PSR(SR0) with per-period (daily) SR; annualisation (sqrt 365) is reported
    alongside and cancels in the ratio."""
    stats = sharpe_stats(daily)
    if stats is None:
        return float("nan")
    sr = stats["sr"]
    denom = 1.0 - stats["skew"] * sr + (stats["kurt"] - 1.0) / 4.0 * sr * sr
    if not denom > 0.0:
        return float("nan")
    return _norm_cdf((sr - sr0) * math.sqrt(stats["T"] - 1.0) / math.sqrt(denom))


def cluster_bootstrap(nets: np.ndarray, clusters: np.ndarray, seed: int,
                      draws: int = BOOTSTRAP_DRAWS) -> dict[str, Any]:
    """1-hour cluster bootstrap of the mean per-trade net. n_eff = number of
    distinct 1h clusters (conservative: <= Kish effective n)."""
    values = np.asarray(nets, dtype=np.float64)
    labels = np.asarray(clusters, dtype=np.int64)
    if values.size == 0:
        return {"n_eff": 0, "ci_low": None, "ci_high": None}
    uniq, inverse = np.unique(labels, return_inverse=True)
    g = uniq.size
    sums = np.bincount(inverse, weights=values, minlength=g)
    counts = np.bincount(inverse, minlength=g).astype(np.float64)
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    pick = rng.integers(0, g, size=(draws, g))
    means = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return {"n_eff": int(g), "ci_low": float(low), "ci_high": float(high)}


def concentration(day_net: np.ndarray, day_week: np.ndarray) -> dict[str, float]:
    """max_d |net_d| / sum_d |net_d| and the ISO-week analogue. Sum 0 -> fail (1.0)."""
    nets = np.asarray(day_net, dtype=np.float64)
    weeks = np.asarray(day_week, dtype=np.int64)
    total = float(np.abs(nets).sum())
    if total == 0.0:
        return {"max_day_share": 1.0, "max_week_share": 1.0}
    _, inverse = np.unique(weeks, return_inverse=True)
    week_net = np.bincount(inverse, weights=nets)
    week_total = float(np.abs(week_net).sum())
    return {
        "max_day_share": float(np.abs(nets).max() / total),
        "max_week_share": 1.0 if week_total == 0.0 else float(np.abs(week_net).max() / week_total),
    }


T2000_FP64_RATE_FACTOR = 1.0 / 32.0


def dual_c2_projection_hours(lattice_seconds: float, quantile_seconds: float, white_rc_seconds: float,
                             chain_seconds: float = 0.0, *, lattice_measured_fp64_on_device: bool = True) -> float:
    """Runtime projection: lattice + rolling quantiles + White RC panel + cell chains.

    HM N11: the T2000 runs FP64 at about 1/32 of its FP32 rate. A lattice time
    measured in float64 on the device already includes that; any lattice time
    NOT measured in float64 on the device is scaled by 32 (conservative)."""
    parts = (float(lattice_seconds), float(quantile_seconds), float(white_rc_seconds), float(chain_seconds))
    if any(part < 0.0 or not math.isfinite(part) for part in parts):
        raise Ext2Stop("STOP_DUAL_C2")
    lattice = parts[0] if lattice_measured_fp64_on_device else parts[0] / T2000_FP64_RATE_FACTOR
    return (lattice + parts[1] + parts[2] + parts[3]) / 3600.0


def projection_needs_new_card(hours: float) -> bool:
    return float(hours) > 2.0


def rule_receipt_fields() -> dict[str, Any]:
    """Frozen rule values. Measured values (devices, dtype, projection, p tables)
    are added by the runner from what actually ran; nothing here is a stand-in."""
    table = assert_primary_p_can_reject()
    return {
        "fee_bp": 0,
        "funding": "set_aside",
        "p_method": P_METHOD,
        "p_distribution": P_DISTRIBUTION,
        "gate6_default_method": GATE6_METHOD,
        "gate6_methods_available": list(GATE6_METHODS),
        "gate6_methods_accepted_for_launch": list(GATE6_METHODS_ACCEPTED_FOR_LAUNCH),
        "gate6_disclosure": GATE6_DISCLOSURE,
        "gate6_nominal_only": GATE6_NOMINAL_ONLY.get(GATE6_METHOD, True),
        "hac_parametric": True,
        "newey_west_lag_rule": NEWEY_WEST_LAG_RULE,
        "t_min_test_days": T_MIN_TEST_DAYS,
        "untested_p": UNTESTED_P,
        "zero_variance_rule": "sd == 0 (or <= 1e-12 * max(1, |mean|)) -> untested, p = 1",
        "p_min_attainable_computed": table,
        "bootstrap_p_formula": "(1+k)/(B+1)",
        "bootstrap_p_min_attainable": bootstrap_p_min_attainable(),
        "bootstrap_role": "reported_only",
        "white_rc_role": "hard_gate_7",
        "bh_q": BH_Q,
        "bh_denominator": BH_DENOMINATOR,
        "percentile_defined_share": PERCENTILE_DEFINED_SHARE,
        "neff_lead_floor": NEFF_LEAD_FLOOR,
        "neff_lead_floor_hard": NEFF_LEAD_FLOOR_HARD,
        "neff_definition": "distinct 1h entry clusters among out-of-fold trades",
        "entry_latency_cap_ms": ENTRY_LATENCY_CAP_MS,
        "sigma_estimator": SIGMA_ESTIMATOR,
        "dual_c2_projection_parts": ["exit_lattice", "rolling_quantiles", "white_rc_panel", "cell_chains"],
        "c2_study1_disclosure": C2_STUDY1_DISCLOSURE,
        "mark_price_approximation": MARK_PRICE_APPROXIMATION,
        "liquidation_disclosure": LIQUIDATION_DISCLOSURE,
        "posted_n": COMBOS_SEARCHED,
        "label": LABEL,
    }
