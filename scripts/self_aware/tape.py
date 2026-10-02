"""Bitfinex 1 s tape helpers shared by the AI scorecard and the edges engine.

Same markout method as the 2026-10-02 AI-direction audits: entry at the
decision second + 1 s on the L1 mid, horizons 1/5/15/30/60/120 min, cost =
half-spread in + half-spread out, windows crossing a tape hole > 60 s dropped.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

HORIZONS_SEC = (60, 300, 900, 1800, 3600, 7200)
HLABEL = {60: "1m", 300: "5m", 900: "15m", 1800: "30m", 3600: "60m", 7200: "120m"}
MAX_HOLE_SEC = 60


@dataclass
class Tape:
    t0: int
    mid: np.ndarray
    spr_bp: np.ndarray
    present: np.ndarray
    next_bad: np.ndarray
    bid_qty: np.ndarray
    ask_qty: np.ndarray
    cbuy: np.ndarray
    csell: np.ndarray

    @property
    def n(self) -> int:
        return len(self.mid)

    @property
    def t_end(self) -> int:
        return self.t0 + self.n - 1

    def index(self, ts: np.ndarray) -> np.ndarray:
        """Entry index: decision second + 1 s."""
        return (np.ceil(np.asarray(ts, dtype=float)).astype(np.int64) + 1 - self.t0)

    def valid_entry(self, i: np.ndarray, lookback: int = 0) -> np.ndarray:
        i = np.asarray(i)
        ok = (i - lookback >= 0) & (i < self.n)
        ii = np.clip(i, 0, self.n - 1)
        return ok & self.present[ii]

    def forward(self, i: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray]:
        """(mid return bp, round-trip half-spread cost bp); NaN where the window is incomplete."""
        i = np.asarray(i, dtype=np.int64)
        j = i + h
        ii = np.clip(i, 0, self.n - 1)
        jj = np.clip(j, 0, self.n - 1)
        ok = (i >= 0) & (j < self.n) & self.present[ii] & self.present[jj] & (self.next_bad[ii] > j)
        ret = np.where(ok, (self.mid[jj] / self.mid[ii] - 1.0) * 1e4, np.nan)
        cost = np.where(ok, self.spr_bp[ii] / 2.0 + self.spr_bp[jj] / 2.0, np.nan)
        return ret, cost

    def back_return(self, i: np.ndarray, w: int) -> np.ndarray:
        i = np.asarray(i, dtype=np.int64)
        k = i - w
        ok = (k >= 0) & (i < self.n)
        ii, kk = np.clip(i, 0, self.n - 1), np.clip(k, 0, self.n - 1)
        return np.where(ok, (self.mid[ii] / self.mid[kk] - 1.0) * 1e4, np.nan)

    def flow(self, i: np.ndarray, w: int) -> np.ndarray:
        i = np.asarray(i, dtype=np.int64)
        a, b = np.clip(i + 1 - w, 0, self.n), np.clip(i + 1, 0, self.n)
        buy, sell = self.cbuy[b] - self.cbuy[a], self.csell[b] - self.csell[a]
        return (buy - sell) / np.maximum(buy + sell, 1e-9)

    def l1_imbalance(self, i: np.ndarray) -> np.ndarray:
        ii = np.clip(np.asarray(i, dtype=np.int64), 0, self.n - 1)
        b, a = self.bid_qty[ii], self.ask_qty[ii]
        return (b - a) / np.maximum(b + a, 1e-9)

    def realized_vol(self, i: np.ndarray, w: int = 900, step: int = 10) -> np.ndarray:
        """Std of 10 s log returns over the trailing window, in bp (computed lazily per index)."""
        lm = np.log(self.mid)
        out = np.full(len(np.atleast_1d(i)), np.nan)
        for n, idx in enumerate(np.atleast_1d(i)):
            idx = int(idx)
            if idx - w < 0 or idx >= self.n:
                continue
            seg = lm[idx - w: idx + 1: step]
            if len(seg) > 3:
                out[n] = float(np.std(np.diff(seg)) * 1e4)
        return out


def load(store, t_min: float, t_max: float | None = None) -> Tape | None:
    """Mirror + archive tape over [t_min, t_max], deduplicated by second."""
    parts = []
    for view in ("raw_tape_1s_archive", "raw_tape_1s"):
        if not store.table_exists(view) and not _view_exists(store, view):
            continue
        cond = f"bucket_ts >= {int(t_min)}" + (f" AND bucket_ts <= {int(t_max)}" if t_max else "")
        try:
            parts.append(store.frame(
                f"SELECT bucket_ts, bid, ask, bid_qty, ask_qty, buy_qty, sell_qty FROM {view} "
                f"WHERE valid_bbo AND bid > 0 AND ask > 0 AND {cond}"))
        except Exception:  # noqa: BLE001 - a rotated file is retried next refresh
            continue
    parts = [p for p in parts if len(p)]
    if not parts:
        return None
    import pandas as pd  # noqa: PLC0415

    df = pd.concat(parts).drop_duplicates("bucket_ts", keep="last").sort_values("bucket_ts")
    ts = df["bucket_ts"].to_numpy(np.int64)
    t0, t1 = int(ts[0]), int(ts[-1])
    n = t1 - t0 + 1
    arr = np.full((n, 6), np.nan)
    arr[ts - t0] = df[["bid", "ask", "bid_qty", "ask_qty", "buy_qty", "sell_qty"]].to_numpy(float)
    present = ~np.isnan(arr[:, 0])
    bid = pd.Series(arr[:, 0]).ffill().to_numpy()
    ask = pd.Series(arr[:, 1]).ffill().to_numpy()
    mid = (bid + ask) / 2.0
    spr = (ask - bid) / mid * 1e4
    hole = np.zeros(n, dtype=np.int64)
    run = 0
    for k in range(n):
        run = 0 if present[k] else run + 1
        hole[k] = run
    bad = hole > MAX_HOLE_SEC
    idx = np.where(bad, np.arange(n), n + 10)
    next_bad = np.minimum.accumulate(idx[::-1])[::-1]
    buy, sell = np.nan_to_num(arr[:, 4]), np.nan_to_num(arr[:, 5])
    return Tape(t0=t0, mid=mid, spr_bp=spr, present=present, next_bad=next_bad,
                bid_qty=np.nan_to_num(pd.Series(arr[:, 2]).ffill().to_numpy()),
                ask_qty=np.nan_to_num(pd.Series(arr[:, 3]).ffill().to_numpy()),
                cbuy=np.concatenate([[0.0], np.cumsum(buy)]), csell=np.concatenate([[0.0], np.cumsum(sell)]))


def _view_exists(store, name: str) -> bool:
    try:
        return bool(store.read("SELECT count(*) AS n FROM duckdb_views() WHERE view_name = ?", [name])[0]["n"])
    except Exception:  # noqa: BLE001
        return False


def session_of(ts: np.ndarray) -> np.ndarray:
    h = (np.asarray(ts, dtype=float) % 86400) / 3600.0
    return np.where(h < 8, "ASIA", np.where(h < 16, "EU", "US"))


def cluster_bootstrap(values: np.ndarray, clusters: np.ndarray, *, b: int = 400, seed: int = 20261002) -> tuple[float, float]:
    """95% CI of the mean, resampling hour clusters."""
    m = np.isfinite(values)
    v, c = values[m], clusters[m]
    if len(v) < 5:
        return (float("nan"), float("nan"))
    uniq, inv = np.unique(c, return_inverse=True)
    sums = np.bincount(inv, weights=v)
    cnts = np.bincount(inv).astype(float)
    k = len(uniq)
    if k < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, k, size=(b, k))
    means = sums[picks].sum(1) / np.maximum(cnts[picks].sum(1), 1.0)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (float("nan"), float("nan"))
    p = hits / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return float(centre - half), float(centre + half)


def cluster_t_pvalue(values: np.ndarray, clusters: np.ndarray) -> float:
    """One-sided p-value that the mean is > 0, using hour-cluster means (robust to autocorrelation)."""
    from scipy import stats  # noqa: PLC0415

    m = np.isfinite(values)
    v, c = values[m], clusters[m]
    if len(v) < 5:
        return float("nan")
    uniq, inv = np.unique(c, return_inverse=True)
    means = np.bincount(inv, weights=v) / np.bincount(inv)
    if len(means) < 3 or np.std(means, ddof=1) == 0:
        return float("nan")
    t = means.mean() / (np.std(means, ddof=1) / np.sqrt(len(means)))
    return float(stats.t.sf(t, df=len(means) - 1))


def bh_q(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    out = np.full(len(p), np.nan)
    m = np.isfinite(p)
    pv = p[m]
    if not len(pv):
        return out
    order = np.argsort(pv)
    ranked = pv[order] * len(pv) / np.arange(1, len(pv) + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    res = np.empty(len(pv))
    res[order] = np.minimum(q, 1.0)
    out[m] = res
    return out


def holm(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    out = np.full(len(p), np.nan)
    m = np.isfinite(p)
    pv = p[m]
    if not len(pv):
        return out
    order = np.argsort(pv)
    k = len(pv)
    adj = np.maximum.accumulate((k - np.arange(k)) * pv[order])
    res = np.empty(k)
    res[order] = np.minimum(adj, 1.0)
    out[m] = res
    return out
