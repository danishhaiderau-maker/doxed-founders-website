"""Honest statistics for the strategy lab.

Ports the validation layers of the 2026-10-01/02 research (``analyze3.py``,
``nt_cv2.py``) as reusable functions:

* cluster-robust (CR1) confidence intervals by time block;
* deflated Sharpe ratio (Bailey & Lopez de Prado) with both the cross-trial
  and the estimator-noise null variance;
* Holm (family-wise) and Benjamini-Hochberg (FDR) adjustments;
* circular time-shift and block-sign nulls that preserve the stickiness /
  timing of a direction series;
* anchored walk-forward with an embargo;
* expanding-window quantiles, so regime thresholds never use future data
  (the full-sample-quantile look-ahead flagged by DATA-SUFFICIENCY model A).
"""
from __future__ import annotations

import math
from typing import Callable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

EULER_GAMMA = 0.5772156649015329

try:  # scipy is present on the analyzer host; keep a normal fallback for tests
    from scipy import stats as _sps
except Exception:  # pragma: no cover
    _sps = None


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    if _sps is not None:
        return float(_sps.norm.ppf(p))
    # Acklam approximation
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00,
         4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    q = min(max(p, 1e-12), 1 - 1e-12)
    if q < 0.02425:
        r = math.sqrt(-2 * math.log(q))
        return (((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) / \
            ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1)
    if q > 1 - 0.02425:
        return -_norm_ppf(1 - q)
    r = q - 0.5
    s = r * r
    return (((((a[0] * s + a[1]) * s + a[2]) * s + a[3]) * s + a[4]) * s + a[5]) * r / \
        (((((b[0] * s + b[1]) * s + b[2]) * s + b[3]) * s + b[4]) * s + 1)


def t_two_sided_p(t: float, df: int) -> Optional[float]:
    if t is None or not np.isfinite(t) or df < 1:
        return None
    if _sps is not None:
        return float(2 * _sps.t.sf(abs(t), df))
    return float(2 * (1 - _norm_cdf(abs(t))))


def _t_quantile(p: float, df: int) -> float:
    if _sps is not None:
        return float(_sps.t.ppf(p, max(df, 1)))
    return _norm_ppf(p)


def cluster_ci(values, clusters, level: float = 0.95) -> dict:
    """Mean with a CR1 cluster-robust CI (clusters = time blocks)."""
    v = np.asarray(values, float)
    g = np.asarray(clusters)
    ok = np.isfinite(v)
    v, g = v[ok], g[ok]
    n = len(v)
    if n < 3:
        return {"n": n, "mean": float(v.mean()) if n else None, "lo": None, "hi": None, "clusters": 0,
                "t": None, "p": None}
    mu = float(v.mean())
    sums = pd.Series(v - mu).groupby(g).sum()
    G = len(sums)
    se = math.sqrt(float((sums ** 2).sum()) * G / max(G - 1, 1)) / n if G > 1 else float("nan")
    tq = _t_quantile(0.5 + level / 2, G - 1)
    t = mu / se if se and np.isfinite(se) and se > 0 else None
    return {"n": n, "mean": mu, "lo": mu - tq * se if np.isfinite(se) else None,
            "hi": mu + tq * se if np.isfinite(se) else None, "clusters": int(G), "t": t,
            "p": t_two_sided_p(t, G - 1) if t is not None else None}


def sharpe(values) -> Optional[float]:
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) < 3:
        return None
    sd = v.std(ddof=1)
    return float(v.mean() / sd) if sd > 0 else None


def expected_max_sharpe(n_trials: int, sr_variance: float) -> Optional[float]:
    """E[max SR] of ``n_trials`` independent null strategies (Bailey & LdP)."""
    if n_trials is None or n_trials < 2 or sr_variance is None or not np.isfinite(sr_variance) or sr_variance <= 0:
        return 0.0 if n_trials == 1 else None
    n = float(n_trials)
    return math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * _norm_ppf(1 - 1 / n)
                                     + EULER_GAMMA * _norm_ppf(1 - 1 / (n * math.e)))


def deflated_sharpe(values, n_trials: int, sr_variance: Optional[float] = None) -> dict:
    """Deflated Sharpe ratio of per-trade returns.

    ``sr_variance=None`` uses the estimator-noise variance 1/T, which the
    NEXT-TILE study showed is the honest null when the cross-trial variance is
    inflated by deterministic spread drag; pass the cross-trial variance to get
    the textbook number. Both are reported by the engine.
    """
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    T = len(v)
    sr = sharpe(v)
    if sr is None or T < 5:
        return {"sr": sr, "sr0": None, "dsr": None, "T": T, "n_trials": n_trials}
    var = sr_variance if sr_variance is not None else 1.0 / T
    sr0 = expected_max_sharpe(n_trials, var) or 0.0
    if _sps is not None:
        sk = float(_sps.skew(v))
        ku = float(_sps.kurtosis(v, fisher=False))
    else:  # pragma: no cover
        z = (v - v.mean()) / v.std(ddof=0)
        sk, ku = float((z ** 3).mean()), float((z ** 4).mean())
    den = math.sqrt(max(1 - sk * sr + (ku - 1) / 4 * sr * sr, 1e-9))
    return {"sr": sr, "sr0": sr0, "dsr": _norm_cdf((sr - sr0) * math.sqrt(T - 1) / den), "T": T,
            "n_trials": int(n_trials), "sr_variance": var,
            "sr_variance_basis": "ESTIMATOR_NOISE_1_OVER_T" if sr_variance is None else "CROSS_TRIAL"}


def holm(pvalues: Sequence[Optional[float]]) -> list:
    """Holm step-down adjusted p-values (strong family-wise error control)."""
    idx = [i for i, p in enumerate(pvalues) if p is not None and np.isfinite(p)]
    out: list = [None] * len(pvalues)
    m = len(idx)
    running = 0.0
    for rank, i in enumerate(sorted(idx, key=lambda k: pvalues[k])):
        adj = min(1.0, (m - rank) * float(pvalues[i]))
        running = max(running, adj)
        out[i] = running
    return out


def benjamini_hochberg(pvalues: Sequence[Optional[float]]) -> list:
    idx = [i for i, p in enumerate(pvalues) if p is not None and np.isfinite(p)]
    out: list = [None] * len(pvalues)
    m = len(idx)
    prev = 1.0
    for rank, i in reversed(list(enumerate(sorted(idx, key=lambda k: pvalues[k]), start=1))):
        prev = min(prev, float(pvalues[i]) * m / rank)
        out[i] = prev
    return out


def summarize(net_bp, ts, *, usd_per_bp: float, cluster_sec: int = 3600,
              split_ts: Optional[float] = None) -> dict:
    """Per-trade summary with cluster CI, PF, drawdown and a chronological split."""
    v = np.asarray(net_bp, float)
    t = np.asarray(ts, float)
    ok = np.isfinite(v) & np.isfinite(t)
    v, t = v[ok], t[ok]
    n = len(v)
    if n == 0:
        return {"n": 0}
    order = np.argsort(t, kind="stable")
    v, t = v[order], t[order]
    wins, losses = v[v > 0], v[v <= 0]
    cum = np.cumsum(v) * usd_per_bp
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], cum])) - np.concatenate([[0.0], cum])).max())
    span_days = max((t[-1] - t[0]) / 86400.0, 1e-9)
    ci = cluster_ci(v, (t // cluster_sec).astype(np.int64))
    split = float(np.median(t)) if split_ts is None else float(split_ts)
    first, second = v[t < split], v[t >= split]
    return {
        "n": int(n), "mean_bp": float(v.mean()), "sd_bp": float(v.std(ddof=1)) if n > 1 else None,
        "win_rate": float((v > 0).mean()), "net_usd": float(v.sum() * usd_per_bp),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
        "max_dd_usd": dd, "per_day": float(n / span_days) if n > 1 else None,
        "ci_lo_bp": ci["lo"], "ci_hi_bp": ci["hi"], "ci_clusters": ci["clusters"], "ci_cluster_sec": cluster_sec,
        "t_cluster": ci["t"], "p_cluster": ci["p"], "sharpe": sharpe(v),
        "first_half_n": int(len(first)), "first_half_mean_bp": float(first.mean()) if len(first) else None,
        "second_half_n": int(len(second)), "second_half_mean_bp": float(second.mean()) if len(second) else None,
        "split_ts": split, "start_ts": float(t[0]), "end_ts": float(t[-1]),
    }


def circular_shift_null(direction: np.ndarray, stat_fn: Callable[[np.ndarray], float], *, n_shifts: int,
                        rng: np.random.Generator, min_shift: int = 10) -> np.ndarray:
    """Null distribution of ``stat_fn`` over circularly shifted direction series.

    Shifting keeps each series' autocorrelation (direction episodes) but breaks
    its alignment with future prices, which an iid side shuffle would not.
    """
    d = np.asarray(direction)
    n = len(d)
    if n < 2 * min_shift + 2:
        return np.array([])
    shifts = rng.integers(min_shift, n - min_shift, n_shifts)
    return np.array([stat_fn(np.roll(d, int(s))) for s in shifts], float)


def block_sign_null(ts: np.ndarray, stat_fn: Callable[[np.ndarray], float], *, block_sec: int, n_draws: int,
                    rng: np.random.Generator) -> np.ndarray:
    """Null over random +/-1 side flips per time block (keeps timing and volatility)."""
    blocks = (np.asarray(ts, float) // block_sec).astype(np.int64)
    ub, inv = np.unique(blocks, return_inverse=True)
    out = np.empty(n_draws)
    for k in range(n_draws):
        flips = rng.choice(np.array([-1.0, 1.0]), len(ub))
        out[k] = stat_fn(flips[inv])
    return out


def null_pvalue(observed: Optional[float], null: np.ndarray) -> Optional[float]:
    null = np.asarray(null, float)
    null = null[np.isfinite(null)]
    if observed is None or not np.isfinite(observed) or null.size == 0:
        return None
    return float((1 + (null >= observed).sum()) / (1 + null.size))


def expanding_quantile(values, ts, q: float, min_history: int = 30) -> np.ndarray:
    """Threshold at each row from strictly earlier rows only (no look-ahead)."""
    v = np.asarray(values, float)
    order = np.argsort(np.asarray(ts, float), kind="stable")
    thr = pd.Series(v[order]).expanding(min_periods=min_history).quantile(q).shift(1).to_numpy()
    out = np.full(len(v), np.nan)
    out[order] = thr
    return out


def walk_forward(trades_by_config: Mapping[str, pd.DataFrame], *, n_folds: int = 4, embargo_sec: float = 3600,
                 min_train: int = 20, select_by: str = "t", usd_per_bp: float = 0.0025) -> dict:
    """Anchored walk-forward: pick the best config on data before each fold
    (minus an embargo), score it on the fold, pool the out-of-sample trades.

    ``trades_by_config`` maps config id -> DataFrame with ``ts`` and ``net_bp``.
    """
    frames = {k: v[["ts", "net_bp"]].dropna() for k, v in trades_by_config.items() if v is not None and len(v)}
    if not frames:
        return {"status": "NO_DATA", "folds": []}
    all_ts = np.sort(np.concatenate([f["ts"].to_numpy(float) for f in frames.values()]))
    edges = np.quantile(all_ts, np.linspace(0, 1, n_folds + 2))
    folds, pooled = [], []
    for k in range(1, n_folds + 1):
        lo, hi = edges[k], edges[k + 1]
        best, best_id = -np.inf, None
        for cid, f in frames.items():
            tr = f[f["ts"] < lo - embargo_sec]["net_bp"].to_numpy(float)
            if len(tr) < min_train:
                continue
            sd = tr.std(ddof=1)
            score = tr.mean() / (sd / math.sqrt(len(tr))) if select_by == "t" and sd > 0 else tr.mean()
            if score > best:
                best, best_id = score, cid
        if best_id is None:
            folds.append({"fold": k, "status": "INSUFFICIENT_TRAIN", "test_start_ts": float(lo)})
            continue
        f = frames[best_id]
        test = f[(f["ts"] >= lo) & ((f["ts"] < hi) if k < n_folds else (f["ts"] <= hi))]
        pooled.append(test)
        folds.append({"fold": k, "pick": best_id, "train_score": float(best), "test_start_ts": float(lo),
                      "test_end_ts": float(hi), "test_n": int(len(test)),
                      "test_mean_bp": float(test["net_bp"].mean()) if len(test) else None})
    if not pooled:
        return {"status": "INSUFFICIENT_TRAIN", "folds": folds}
    P = pd.concat(pooled)
    s = summarize(P["net_bp"], P["ts"], usd_per_bp=usd_per_bp)
    return {"status": "OK", "folds": folds, "oos": s, "embargo_sec": embargo_sec, "select_by": select_by,
            "n_configs": len(frames)}


def bucket_corr(a_ts, a_v, b_ts, b_v, *, bucket_sec: int = 3600) -> dict:
    """Correlation of bucketed PnL over the overlapping window."""
    a_ts, b_ts = np.asarray(a_ts, float), np.asarray(b_ts, float)
    if len(a_ts) < 2 or len(b_ts) < 2:
        return {"rho": None, "buckets": 0, "n_a": int(len(a_ts)), "n_b": int(len(b_ts))}
    lo, hi = max(a_ts.min(), b_ts.min()), min(a_ts.max(), b_ts.max())
    if hi <= lo:
        return {"rho": None, "buckets": 0, "n_a": int(len(a_ts)), "n_b": int(len(b_ts))}
    nb = int((hi - lo) // bucket_sec) + 1

    def series(t, v):
        m = (t >= lo) & (t <= hi)
        s = np.zeros(nb)
        np.add.at(s, ((t[m] - lo) // bucket_sec).astype(int), np.asarray(v, float)[m])
        return s, int(m.sum())

    sa, na = series(a_ts, a_v)
    sb, nb_ = series(b_ts, b_v)
    rho = float(np.corrcoef(sa, sb)[0, 1]) if sa.std() > 0 and sb.std() > 0 else None
    return {"rho": rho, "buckets": nb, "n_a": na, "n_b": nb_}
