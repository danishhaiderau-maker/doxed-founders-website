"""Cross-venue lead-lag: do Binance/Bybit/OKX BTC perps lead Bitfinex tBTCF0?

Inputs are the shadow ``cross_venue_tape_1m.jsonl`` minute rows and the
Bitfinex ``market_microstructure_1s.jsonl`` BBO tape, aligned on the shared
epoch-second bucket clock (bucket ``s`` holds the quote as of ``s + 1``).

Sections, per leader venue:

* ``xcorr`` - correlation of 1 s log-mid returns, leader at ``t`` vs Bitfinex
  at ``t + k`` for k in -10..30 s (iid 95% band shown for scale only).
* ``response`` - after a leader trigger at ``t`` (a ``leader_move`` of at least
  the threshold over the window, or a ``lead_gap`` where the leader moved that
  much more than Bitfinex did), the signed Bitfinex mid move from ``t`` to
  ``t + k`` for k in 1..30 s, hour-clustered CR1 t statistics.
* ``follow`` - a capacity-one leader-follow rule on Bitfinex: enter at the
  executable quote of bucket ``t + 1`` (ask for LONG, bid for SHORT), exit at
  the opposite side of bucket ``t + 1 + hold``. This is an after-spread markout
  with no exchange fee applied (fees are owned elsewhere); hour-cluster CR1 t,
  cluster bootstrap 95% CI and Benjamini-Hochberg q across all follow tests.
* ``basis`` - leader-minus-Bitfinex basis distribution and whether a basis
  deviation from its 15-minute mean predicts Bitfinex's next 60 s / 300 s.
* ``derivatives`` - funding and open-interest change vs the next 15 minutes.
* ``xvl`` - every registered cross-venue tile (lead XVL and premium XVP):
  capacity-one replay of its rule on these tapes, the live shadow
  trigger/outcome streams (``xvl_shadow_signals.jsonl``,
  ``xvp_shadow_signals.jsonl``) and their anchor-matched parity.

The pre-registered rule is the shadow challenger's: 10 s window, 2 bp. Every
other window/threshold is exploratory and carries the multiple-testing
correction. Nothing here can place, change or cancel an order.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
from typing import Mapping, Optional

import numpy as np

import cross_venue_tape as cvt
from research.ai_challenger_report import benjamini_hochberg, t_two_sided_p

SCHEMA = "lead_lag_report_v1"
REPORT_FILE = "lead_lag_report.json"
BFX_TAPE_FILE = "market_microstructure_1s.jsonl"
MAX_DAYS = 7
XCORR_LAGS = tuple(range(-10, 31))
RESPONSE_LAGS = (1, 2, 3, 5, 10, 15, 20, 30)
TRIGGERS = ((1, 1.5), (5, 2.0), (10, 2.0))
PREREGISTERED = (cvt.LEADER_WINDOW_SEC, cvt.LEADER_MIN_MOVE_BP)
FOLLOW_HOLDS = (5, 10, 30, 60)
ENTRY_DELAY_SEC = 1
BASIS_WINDOW_SEC = 900
BASIS_Z = 2.0
BASIS_HORIZONS = (60, 300)
OI_CHANGE_SEC = 300
DERIV_HORIZON_SEC = 900
MIN_HOURS = 6
MIN_CLUSTERS = 10
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261001
FDR_Q = 0.05
_BUCKET_RE = re.compile(rb'"bucket_ts":\s*(\d+)')


def _r(value, digits: int = 4):
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _generations(path: str) -> list:
    """Active file plus numeric rotations, oldest first."""
    rotated = []
    for candidate in glob.glob(path + ".*"):
        suffix = candidate.rsplit(".", 1)[-1]
        if suffix.isdigit():
            rotated.append((int(suffix), candidate))
    return [p for _, p in sorted(rotated)] + ([path] if os.path.isfile(path) else [])


def load_cross_venue_rows(data_dir: str, max_days: int = MAX_DAYS) -> list:
    rows = []
    for path in _generations(os.path.join(data_dir, cvt.FILE_NAME)):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("schema") == cvt.SCHEMA:
                        rows.append(row)
        except OSError:
            continue
    if not rows:
        return rows
    latest = max(int(r.get("minute_ts") or 0) for r in rows)
    floor = latest - max_days * 86400
    dedup = {}
    for row in rows:
        ts = int(row.get("minute_ts") or 0)
        if ts >= floor:
            dedup[ts] = row
    return [dedup[k] for k in sorted(dedup)]


def _bucket_range(path: str) -> tuple:
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4096))
            tail = handle.read()
    except OSError:
        return None, None
    first = _BUCKET_RE.search(head)
    last = _BUCKET_RE.findall(tail)
    return (int(first.group(1)) if first else None, int(last[-1]) if last else None)


def load_bitfinex_quotes(data_dir: str, start: int, end: int) -> dict:
    """{bucket_ts: (bid, ask)} for fresh, valid buckets in [start, end)."""
    out = {}
    for path in _generations(os.path.join(data_dir, BFX_TAPE_FILE)):
        first, last = _bucket_range(path)
        if first is not None and last is not None and (last < start or first >= end):
            continue
        try:
            with open(path, "rb") as handle:
                for line in handle:
                    match = _BUCKET_RE.search(line)
                    if not match:
                        continue
                    ts = int(match.group(1))
                    if not start <= ts < end:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("fresh") is not True or row.get("valid_bbo") is not True:
                        continue
                    bid, ask = row.get("bid"), row.get("ask")
                    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and 0 < bid <= ask:
                        out[ts] = (float(bid), float(ask))
        except OSError:
            continue
    return out


class Aligned:
    """Dense per-second arrays (NaN = unobserved) over the cross-venue span."""

    def __init__(self, rows: list, bfx_quotes: Optional[Mapping[int, tuple]] = None) -> None:
        self.start = int(rows[0]["minute_ts"])
        self.end = int(rows[-1]["minute_ts"]) + 60
        n = self.end - self.start
        self.n = n
        self.venues = sorted({v for r in rows for v in (r.get("venues") or {})})
        self.mid = {v: np.full(n, np.nan) for v in self.venues}
        self.flow = {v: np.zeros(n) for v in self.venues}
        cv_bfx = np.full(n, np.nan)
        self.deriv_rows = []
        for row in rows:
            decoded = cvt.decode_minute(row)
            for sec, mid in decoded.get("bfx", {}).items():
                cv_bfx[sec - self.start] = mid
            for v in self.venues:
                for sec, cell in (decoded.get(v) or {}).items():
                    i = sec - self.start
                    if cell["mid"] is not None:
                        self.mid[v][i] = cell["mid"]
                    self.flow[v][i] = cell["buy"] - cell["sell"]
            if row.get("derivatives"):
                self.deriv_rows.append((int(row["minute_ts"]) + 59, row["derivatives"]))
        self.bid = np.full(n, np.nan)
        self.ask = np.full(n, np.nan)
        for sec, (bid, ask) in (bfx_quotes or {}).items():
            i = sec - self.start
            if 0 <= i < n:
                self.bid[i], self.ask[i] = bid, ask
        tape_mid = (self.bid + self.ask) / 2.0
        self.bfx_source = ("MICROSTRUCTURE_TAPE" if np.isfinite(tape_mid).any()
                           else "CROSS_VENUE_EMBEDDED_MID")
        self.bfx_mid = np.where(np.isfinite(tape_mid), tape_mid, cv_bfx)
        self.hour = (np.arange(n) + self.start) // 3600


def _log_ret(series: np.ndarray, lag: int) -> np.ndarray:
    out = np.full(series.shape, np.nan)
    if 0 < lag < len(series):
        out[lag:] = np.log(series[lag:] / series[:-lag]) * 1e4
    return out


def _shift(series: np.ndarray, k: int) -> np.ndarray:
    """``out[t] = series[t + k]`` (NaN beyond the edge)."""
    out = np.full(series.shape, np.nan)
    if k == 0:
        return series.copy()
    if k > 0:
        out[:-k] = series[k:]
    else:
        out[-k:] = series[:k]
    return out


def _at(series: np.ndarray, idx: np.ndarray) -> np.ndarray:
    inside = (idx >= 0) & (idx < series.size)
    return np.where(inside, series[np.clip(idx, 0, series.size - 1)], np.nan)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def cluster_stats(values: np.ndarray, clusters: np.ndarray, *, bootstrap: bool = True,
                  seed: int = BOOTSTRAP_SEED) -> dict:
    """Mean with CR1 cluster-robust t (G-1 df) and cluster bootstrap 95% CI."""
    values = np.asarray(values, dtype=float)
    clusters = np.asarray(clusters)
    mask = np.isfinite(values)
    values, clusters = values[mask], clusters[mask]
    n = int(values.size)
    out = {"n": n, "clusters": 0, "mean": None, "se": None, "t": None, "p": None,
           "ci95": [None, None]}
    if n == 0:
        return out
    labels, inverse = np.unique(clusters, return_inverse=True)
    g = int(labels.size)
    out["clusters"] = g
    mean = float(values.mean())
    out["mean"] = _r(mean)
    if g < 2:
        return out
    sums = np.bincount(inverse, weights=values, minlength=g)
    counts = np.bincount(inverse, minlength=g).astype(float)
    score = float(((sums - mean * counts) ** 2).sum())
    se = math.sqrt(g / (g - 1) * score / (n * n))
    out["se"] = _r(se)
    if se > 0:
        t = mean / se
        out["t"] = _r(t, 3)
        out["p"] = _r(t_two_sided_p(t, g - 1), 6)
    if bootstrap:
        rng = np.random.default_rng(seed)
        draws = rng.integers(0, g, size=(BOOTSTRAP_RESAMPLES, g))
        boots = np.sort(sums[draws].sum(axis=1) / counts[draws].sum(axis=1))
        out["ci95"] = [_r(boots[int(0.025 * BOOTSTRAP_RESAMPLES)]),
                       _r(boots[int(0.975 * BOOTSTRAP_RESAMPLES) - 1])]
    return out


def _corr(x: np.ndarray, y: np.ndarray) -> tuple:
    mask = np.isfinite(x) & np.isfinite(y)
    n = int(mask.sum())
    if n < 30:
        return None, n
    a, b = x[mask], y[mask]
    sa, sb = a.std(), b.std()
    if sa == 0 or sb == 0:
        return None, n
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb)), n


def _debounce(idx: np.ndarray, gap: int) -> np.ndarray:
    keep, last = [], -10 ** 9
    for i in idx:
        if i - last >= gap:
            keep.append(i)
            last = i
    return np.array(keep, dtype=int)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def xcorr_section(al: Aligned, venue: str) -> dict:
    r_lead = _log_ret(al.mid[venue], 1)
    r_bfx = _log_ret(al.bfx_mid, 1)
    rows, best = [], None
    for k in XCORR_LAGS:
        c, n = _corr(r_lead, _shift(r_bfx, k))
        rows.append({"lag_s": k, "corr": _r(c), "n": n})
        if c is not None and (best is None or c > best["corr"]):
            best = {"lag_s": k, "corr": _r(c)}
    n0 = next((r["n"] for r in rows if r["lag_s"] == 0), 0)
    leader_first = sum(r["corr"] for r in rows if r["lag_s"] > 0 and r["corr"] is not None)
    bfx_first = sum(r["corr"] for r in rows if r["lag_s"] < 0 and r["corr"] is not None)
    return {
        "rows": rows,
        "peak": best,
        "iid_band_95": _r(1.96 / math.sqrt(n0)) if n0 else None,
        "sum_corr_leader_first": _r(leader_first),
        "sum_corr_bitfinex_first": _r(bfx_first),
        "note": "lag k>0: leader return at t vs Bitfinex return at t+k (leader first).",
    }


def triggers(al: Aligned, venue: str, window: int, threshold: float, kind: str) -> np.ndarray:
    move = _log_ret(al.mid[venue], window)
    if kind == "lead_gap":
        move = move - _log_ret(al.bfx_mid, window)
    signal = np.where(np.isfinite(move) & (np.abs(move) >= threshold), np.sign(move), 0.0)
    keep = _debounce(np.flatnonzero(signal), window)
    out = np.zeros(al.n)
    if keep.size:
        out[keep] = signal[keep]
    return out


def response_section(al: Aligned, venue: str, signal: np.ndarray, window: int) -> dict:
    idx = np.flatnonzero(signal)
    sign = signal[idx]
    hours = al.hour[idx]
    base = al.bfx_mid[idx]
    lead_move = sign * np.log(al.mid[venue][idx] / _at(al.mid[venue], idx - window)) * 1e4
    bfx_pre = sign * np.log(base / _at(al.bfx_mid, idx - window)) * 1e4
    curve = []
    for k in RESPONSE_LAGS:
        stat = cluster_stats(sign * np.log(_at(al.bfx_mid, idx + k) / base) * 1e4, hours,
                             bootstrap=False)
        curve.append({"lag_s": k, **stat})
    return {
        "events": int(idx.size),
        "mean_leader_move_bp": _r(np.nanmean(lead_move)) if np.isfinite(lead_move).any() else None,
        "mean_bitfinex_same_window_bp": _r(np.nanmean(bfx_pre)) if np.isfinite(bfx_pre).any() else None,
        "bitfinex_response": curve,
    }


def follow_section(al: Aligned, signal: np.ndarray, hold: int) -> dict:
    """Capacity-one after-spread markout of the leader-follow rule."""
    nets, grosses, hours, sides = [], [], [], []
    busy_until = -1
    for i in np.flatnonzero(signal):
        if i <= busy_until:
            continue
        entry_i, exit_i = i + ENTRY_DELAY_SEC, i + ENTRY_DELAY_SEC + hold
        if exit_i >= al.n:
            break
        s = signal[i]
        bid0, ask0, bid1, ask1 = al.bid[entry_i], al.ask[entry_i], al.bid[exit_i], al.ask[exit_i]
        if not (np.isfinite(bid0) and np.isfinite(ask0) and np.isfinite(bid1) and np.isfinite(ask1)):
            continue
        entry, exit_ = (ask0, bid1) if s > 0 else (bid0, ask1)
        m0, m1 = (bid0 + ask0) / 2.0, (bid1 + ask1) / 2.0
        nets.append(s * (exit_ - entry) / entry * 1e4)
        grosses.append(s * (m1 - m0) / m0 * 1e4)
        hours.append(al.hour[i])
        sides.append(s)
        busy_until = exit_i
    return {
        "hold_s": hold,
        "trades": len(nets),
        "longs": int(sum(1 for s in sides if s > 0)),
        "hit_rate_net": _r(sum(1 for v in nets if v > 0) / len(nets)) if nets else None,
        "mean_gross_mid_bp": _r(float(np.mean(grosses))) if grosses else None,
        "net_after_spread": cluster_stats(np.array(nets), np.array(hours)),
    }


def basis_section(al: Aligned, venue: str) -> dict:
    basis = (al.mid[venue] / al.bfx_mid - 1.0) * 1e4
    finite = basis[np.isfinite(basis)]
    out = {"seconds": int(finite.size)}
    if finite.size < 600:
        out["status"] = "NOT_ENOUGH_DATA"
        return out
    out.update({
        "mean_bp": _r(finite.mean()), "median_bp": _r(np.median(finite)),
        "p05_bp": _r(np.percentile(finite, 5)), "p95_bp": _r(np.percentile(finite, 95)),
        "std_bp": _r(finite.std()),
    })
    import pandas as pd
    series = pd.Series(basis)
    roll = series.rolling(BASIS_WINDOW_SEC, min_periods=BASIS_WINDOW_SEC // 2)
    z = ((series - roll.mean()) / roll.std()).to_numpy()
    signal = np.where(np.isfinite(z) & (np.abs(z) >= BASIS_Z), np.sign(z), 0.0)
    keep = _debounce(np.flatnonzero(signal), max(BASIS_HORIZONS))
    tests = {}
    for h in BASIS_HORIZONS:
        fwd = signal[keep] * np.log(_at(al.bfx_mid, keep + h) / al.bfx_mid[keep]) * 1e4
        tests[str(h)] = cluster_stats(fwd, al.hour[keep])
    out["deviation_events"] = int(keep.size)
    out["deviation_rule"] = (f"|basis - rolling {BASIS_WINDOW_SEC}s mean| >= {BASIS_Z} rolling std; "
                             "side = sign(deviation) on Bitfinex (leader rich -> Bitfinex up)")
    out["bitfinex_forward_signed_bp"] = tests
    return out


def derivatives_section(al: Aligned) -> dict:
    out = {"minutes": len(al.deriv_rows)}
    if len(al.deriv_rows) < 60:
        out["status"] = "NOT_ENOUGH_DATA"
        return out
    for v in sorted({v for _, d in al.deriv_rows for v in d}):
        oi, rates = {}, []
        for ts, d in al.deriv_rows:
            cell = d.get(v) or {}
            if isinstance(cell.get("open_interest"), (int, float)) and cell["open_interest"] > 0:
                oi[ts] = float(cell["open_interest"])
            if isinstance(cell.get("funding_rate"), (int, float)):
                rates.append(float(cell["funding_rate"]))
        changes, fwd = [], []
        for ts, x in oi.items():
            past = oi.get(ts - OI_CHANGE_SEC)
            i, j = ts - al.start, ts - al.start + DERIV_HORIZON_SEC
            if past and 0 <= i and j < al.n and np.isfinite(al.bfx_mid[i]) and np.isfinite(al.bfx_mid[j]):
                changes.append((x / past - 1.0) * 100.0)
                fwd.append(math.log(al.bfx_mid[j] / al.bfx_mid[i]) * 1e4)
        c, n = _corr(np.array(changes), np.array(fwd)) if changes else (None, 0)
        out[v] = {
            "oi_change_5m_vs_bitfinex_next_15m": {"corr": _r(c), "n": n,
                                                  "note": "overlapping minutes; descriptive only"},
            "funding_rate_last": rates[-1] if rates else None,
            "funding_rate_range": [min(rates), max(rates)] if rates else None,
        }
    return out


# ---------------------------------------------------------------------------
# Cross-venue lead tile (XVL): tape replay, live shadow stream and parity
# ---------------------------------------------------------------------------
def _xvl_rules() -> dict:
    """{lane: (rule, policy_signature)} for registry tiles on the cross-venue clock.

    The rule is a ``LeadRule`` (XVL) or a ``PremiumRule`` (XVP) by the tile's
    direction source; ``_replay_for`` picks the matching tape replay.
    """
    from combo_pathway_config import ACTIVE_TILE_REGISTRY, cross_venue_clock_lanes
    from cross_venue_lead import LeadRule
    from cross_venue_premium import PremiumRule
    out = {}
    for lane in cross_venue_clock_lanes():
        spec = ACTIVE_TILE_REGISTRY[lane]
        premium = spec["entry_policy"].get("direction_source") == "CROSS_VENUE_PREMIUM"
        rule_cls = PremiumRule if premium else LeadRule
        out[lane] = (rule_cls.from_policy(spec["entry_policy"], spec["exit_policy"]),
                     str(spec.get("policy_signature") or ""))
    return out


def _ffill_limited(series: np.ndarray, limit: int) -> np.ndarray:
    """Forward-fill NaNs for at most ``limit`` consecutive seconds (the research dataset rule)."""
    out = series.copy()
    last_val, last_i = np.nan, -10 ** 9
    for i in range(len(out)):
        if np.isfinite(out[i]):
            last_val, last_i = out[i], i
        elif i - last_i <= limit:
            out[i] = last_val
    return out


def xvp_replay_trades(al: Aligned, rule) -> list:
    """Capacity-one replay of the registered XVP premium-deviation rule (same markout as the shadow)."""
    prem = []
    bfx = _ffill_limited(al.bfx_mid, int(rule.max_fill_forward_sec))
    for venue in rule.venues:
        if venue not in al.mid:
            return []
        mid = _ffill_limited(al.mid[venue], int(rule.max_fill_forward_sec))
        with np.errstate(invalid="ignore", divide="ignore"):
            prem.append((mid / bfx - 1.0) * 1e4)
    with np.errstate(invalid="ignore"):
        stack = np.vstack(prem)
        valid = np.isfinite(stack)
        count_v = valid.sum(axis=0)
        premium = np.where(count_v > 0, np.where(valid, stack, 0.0).sum(axis=0) / np.maximum(count_v, 1), np.nan)
    finite = np.isfinite(premium)
    csum = np.concatenate([[0.0], np.cumsum(np.where(finite, premium, 0.0))])
    ccnt = np.concatenate([[0], np.cumsum(finite.astype(int))])
    idx = np.arange(al.n)
    lo = np.maximum(0, idx + 1 - int(rule.mean_window_sec))
    cnt = ccnt[idx + 1] - ccnt[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(cnt >= int(rule.min_mean_samples), (csum[idx + 1] - csum[lo]) / np.maximum(cnt, 1), np.nan)
        dev = premium - mean
        spread = (al.ask - al.bid) / ((al.ask + al.bid) / 2.0) * 1e4
        side = np.where(dev >= rule.long_threshold_bps, 1.0, np.where(dev <= rule.short_threshold_bps, -1.0, 0.0))
        ok = np.isfinite(dev) & (side != 0) & np.isfinite(spread) & (spread <= rule.max_spread_bps)
    trades, busy_until = [], -1
    for i in np.flatnonzero(ok):
        if i <= busy_until:
            continue
        entry_i, exit_i = i + rule.entry_delay_sec, i + rule.entry_delay_sec + rule.hold_sec
        if exit_i >= al.n:
            break
        quotes = (al.bid[entry_i], al.ask[entry_i], al.bid[exit_i], al.ask[exit_i])
        if not all(np.isfinite(q) for q in quotes):
            continue
        s = float(side[i])
        bid0, ask0, bid1, ask1 = quotes
        entry, exit_ = (ask0, bid1) if s > 0 else (bid0, ask1)
        trades.append({"anchor": int(al.start + i), "side": "LONG" if s > 0 else "SHORT",
                       "premium_dev_bp": float(dev[i]), "net_bp": float(s * (exit_ - entry) / entry * 1e4),
                       "hour": int(al.hour[i])})
        busy_until = exit_i
    return trades


def _replay_for(rule):
    return xvp_replay_trades if hasattr(rule, "mean_window_sec") else xvl_replay_trades


def xvl_replay_trades(al: Aligned, rule) -> list:
    """Capacity-one replay of the registered XVL rule on the tapes (same markout as the shadow)."""
    w = int(rule.lookback_sec)
    rets = []
    for venue in rule.venues:
        if venue not in al.mid:
            return []
        rets.append((al.mid[venue] / _shift(al.mid[venue], -w) - 1.0) * 1e4)
    bfx_ret = (al.bfx_mid / _shift(al.bfx_mid, -w) - 1.0) * 1e4
    with np.errstate(invalid="ignore"):
        lead = np.mean(np.vstack(rets), axis=0) - bfx_ret
        spread = (al.ask - al.bid) / ((al.ask + al.bid) / 2.0) * 1e4
        ok = np.isfinite(lead) & (np.abs(lead) >= rule.lead_threshold_bps) \
            & np.isfinite(spread) & (spread <= rule.max_spread_bps)
    trades, busy_until = [], -1
    for i in np.flatnonzero(ok):
        if i <= busy_until:
            continue
        entry_i, exit_i = i + rule.entry_delay_sec, i + rule.entry_delay_sec + rule.hold_sec
        if exit_i >= al.n:
            break
        quotes = (al.bid[entry_i], al.ask[entry_i], al.bid[exit_i], al.ask[exit_i])
        if not all(np.isfinite(q) for q in quotes):
            continue
        s = 1.0 if lead[i] > 0 else -1.0
        bid0, ask0, bid1, ask1 = quotes
        entry, exit_ = (ask0, bid1) if s > 0 else (bid0, ask1)
        trades.append({"anchor": int(al.start + i), "side": "LONG" if s > 0 else "SHORT",
                       "lead_bp": float(lead[i]), "net_bp": float(s * (exit_ - entry) / entry * 1e4),
                       "hour": int(al.hour[i])})
        busy_until = exit_i
    return trades


def load_xvl_shadow_rows(data_dir: str) -> tuple:
    """Trigger and outcome rows from every cross-venue shadow stream (XVL and XVP)."""
    import cross_venue_lead as lead
    import cross_venue_premium as premium
    trigger_schemas = {lead.TRIGGER_SCHEMA, premium.TRIGGER_SCHEMA}
    outcome_schemas = {lead.OUTCOME_SCHEMA, premium.OUTCOME_SCHEMA}
    triggers, outcomes = [], []
    paths = [path for name in (lead.SHADOW_FILE, premium.SHADOW_FILE)
             for path in _generations(os.path.join(data_dir, name))]
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if row.get("schema") in trigger_schemas:
                        triggers.append(row)
                    elif row.get("schema") in outcome_schemas:
                        outcomes.append(row)
        except OSError:
            continue
    return triggers, outcomes


def _xvl_summary(values: list, hours: list) -> dict:
    stat = cluster_stats(np.array(values, dtype=float), np.array(hours))
    return {"trades": len(values),
            "win_rate": _r(sum(1 for v in values if v > 0) / len(values)) if values else None,
            "mean_net_bp": stat.get("mean"), "ci95_1h_clusters": stat.get("ci95"),
            "clusters": stat.get("clusters")}


def xvl_section(al: Optional[Aligned], triggers, outcomes) -> dict:
    out = {"mode": "SHADOW_ALWAYS_PAPER_WHEN_TILE_ON",
           "markout": "Bitfinex taker at anchor+1 (ask/bid) to anchor+1+hold (bid/ask); after spread, no fee"}
    try:
        rules = _xvl_rules()
    except Exception as exc:
        return {**out, "status": "NO_REGISTRY", "error": f"{type(exc).__name__}: {exc}"}
    if not rules:
        return {**out, "status": "NO_CROSS_VENUE_TILES"}
    lanes = {}
    for lane, (rule, signature) in rules.items():
        def _mine(r):
            if r.get("research_lane") not in (None, lane):
                return False
            return r.get("policy_signature") in ("", None, signature)
        lane_trig = [r for r in triggers if _mine(r)]
        lane_out = [r for r in outcomes if _mine(r)]
        gates = {}
        for row in lane_trig:
            gates[str(row.get("gate"))] = gates.get(str(row.get("gate")), 0) + 1
        ok = [r for r in lane_out if r.get("status") == "OK"]
        cap1 = [r for r in ok if r.get("cap1_take")]
        qualifying = [r for r in ok if r.get("qualifies")]
        first = min((int(r["anchor_bucket_ts"]) for r in lane_trig), default=None)
        last = max((int(r["anchor_bucket_ts"]) for r in lane_trig), default=None)
        cell = {
            "rule": rule.identity(),
            "shadow": {
                "triggers_logged": len(lane_trig),
                "by_gate": gates,
                "stale_feed_share": _r(gates.get("STALE_FEED", 0) / len(lane_trig)) if lane_trig else None,
                "outcomes_ok": len(ok),
                "outcomes_missing": sum(1 for r in lane_out if r.get("status") != "OK"),
                "first_anchor": first,
                "last_anchor": last,
                "capacity_one": _xvl_summary([float(r["net_bp_after_spread"]) for r in cap1],
                                             [int(r["anchor_bucket_ts"]) // 3600 for r in cap1]),
                "every_qualifying_second": _xvl_summary(
                    [float(r["net_bp_after_spread"]) for r in qualifying],
                    [int(r["anchor_bucket_ts"]) // 3600 for r in qualifying]),
            },
        }
        if al is not None and al.bfx_source == "MICROSTRUCTURE_TAPE":
            replay = _replay_for(rule)(al, rule)
            cell["replay"] = {"span": [al.start, al.end],
                              **_xvl_summary([t["net_bp"] for t in replay], [t["hour"] for t in replay])}
            shadow_by_anchor = {int(r["anchor_bucket_ts"]): r for r in cap1}
            matched = [(t, shadow_by_anchor[t["anchor"]]) for t in replay if t["anchor"] in shadow_by_anchor]
            in_window = [t for t in replay if first is not None and first <= t["anchor"] <= last]
            gaps = [abs(t["net_bp"] - float(s["net_bp_after_spread"])) for t, s in matched]
            cell["parity"] = {
                "replay_trades_in_shadow_window": len(in_window),
                "matched_on_anchor": len(matched),
                "match_rate": _r(len(matched) / len(in_window)) if in_window else None,
                "side_agreement": _r(sum(1 for t, s in matched if t["side"] == s["side"]) / len(matched))
                if matched else None,
                "mean_abs_net_gap_bp": _r(sum(gaps) / len(gaps)) if gaps else None,
                "limit_bp": 1.0,
                "note": ("replay uses the minute cross-venue tape without per-second feed age; the live "
                         "shadow also refuses stale feeds, so some unmatched replay trades are expected"),
            }
        else:
            cell["replay"] = "UNAVAILABLE_NO_BITFINEX_BBO"
        lanes[lane] = cell
    return {**out, "status": "OK", "lanes": lanes}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _is_prereg(kind: str, window: int, threshold: float) -> bool:
    return kind == "leader_move" and (window, threshold) == PREREGISTERED


def build_lead_lag_report(rows: list, bfx_quotes: Optional[Mapping[int, tuple]] = None,
                          xvl_rows: tuple = ((), ())) -> dict:
    base = {"schema": SCHEMA, "mode": "SHADOW_ONLY_NO_ORDERS",
            "preregistered_rule": {"window_s": PREREGISTERED[0], "min_move_bp": PREREGISTERED[1],
                                   "trigger": "leader_move", "challenger": "leader_10s"}}
    if not rows:
        return {**base, "status": "NO_DATA", "xvl": xvl_section(None, *xvl_rows)}
    al = Aligned(rows, bfx_quotes)
    aligned = np.isfinite(al.bfx_mid)
    hours_aligned = int(np.unique(al.hour[aligned]).size) if aligned.any() else 0
    venues, follow_tests = {}, []
    for venue in al.venues:
        cell = {"aligned_seconds": int((aligned & np.isfinite(al.mid[venue])).sum()),
                "xcorr": xcorr_section(al, venue), "basis": basis_section(al, venue),
                "triggers": {}}
        for kind in ("leader_move", "lead_gap"):
            for window, threshold in TRIGGERS:
                signal = triggers(al, venue, window, threshold, kind)
                key = f"{kind}_{window}s_{threshold:g}bp"
                follows = []
                if al.bfx_source == "MICROSTRUCTURE_TAPE":
                    for hold in FOLLOW_HOLDS:
                        f = follow_section(al, signal, hold)
                        follow_tests.append((venue, key, kind, window, threshold, f))
                        follows.append(f)
                cell["triggers"][key] = {
                    "kind": kind, "window_s": window, "threshold_bp": threshold,
                    "preregistered": _is_prereg(kind, window, threshold),
                    "response": response_section(al, venue, signal, window),
                    "follow": follows or "UNAVAILABLE_NO_BITFINEX_BBO",
                }
        venues[venue] = cell
    qs = benjamini_hochberg([f["net_after_spread"].get("p") for *_, f in follow_tests])
    summary = []
    for (venue, key, kind, window, threshold, f), q in zip(follow_tests, qs):
        stat = f["net_after_spread"]
        stat["q_bh"] = _r(q, 6)
        if (stat.get("clusters") or 0) < MIN_CLUSTERS or hours_aligned < MIN_HOURS:
            verdict = "NOT_ENOUGH_DATA"
        elif q is not None and q <= FDR_Q:
            verdict = "POSITIVE_AFTER_SPREAD" if (stat.get("mean") or 0) > 0 else "NEGATIVE_AFTER_SPREAD"
        else:
            verdict = "NO_DETECTABLE_EDGE"
        stat["verdict"] = verdict
        summary.append({"venue": venue, "trigger": key, "hold_s": f["hold_s"], "trades": f["trades"],
                        "mean_gross_mid_bp": f["mean_gross_mid_bp"],
                        "mean_net_bp": stat.get("mean"), "ci95": stat.get("ci95"), "q_bh": stat["q_bh"],
                        "preregistered": _is_prereg(kind, window, threshold), "verdict": verdict})
    tail = rows[-60:]
    meta = [r.get("meta") or {} for r in tail]
    cpu = [m["cpu_pct"] for m in meta if isinstance(m.get("cpu_pct"), (int, float))]
    return {
        **base,
        "status": "OK" if hours_aligned >= MIN_HOURS else "NOT_ENOUGH_DATA",
        "span": {"start_ts": al.start, "end_ts": al.end, "minutes": len(rows),
                 "hours_aligned_with_bitfinex": hours_aligned, "bitfinex_source": al.bfx_source},
        "collector": {
            "version": (meta[-1] if meta else {}).get("collector_version"),
            "cpu_pct_last_hour_mean": _r(sum(cpu) / len(cpu), 3) if cpu else None,
            "row_bytes_mean_last_hour": _r(
                sum(len(json.dumps(r, separators=(",", ":"))) + 1 for r in tail) / len(tail), 1),
        },
        "method": {
            "clock": "shared epoch-second buckets; bucket s = quote as of s+1",
            "xcorr": "Pearson on 1 s log-mid returns; iid band is for scale only (returns are not iid)",
            "triggers": "debounced: a trigger needs >= window seconds since the previous one",
            "follow": ("capacity one; enter at bucket t+1 ask/bid, exit at bucket t+1+hold bid/ask; "
                       "after spread, no exchange fee applied"),
            "inference": (f"hour-clustered CR1 t with G-1 df; cluster bootstrap {BOOTSTRAP_RESAMPLES} "
                          f"(seed {BOOTSTRAP_SEED}); BH across {len(follow_tests)} follow tests, q<={FDR_Q}"),
            "gates": f"verdicts need >= {MIN_HOURS} aligned hours and >= {MIN_CLUSTERS} hour clusters",
        },
        "follow_summary": summary,
        "venues": venues,
        "derivatives": derivatives_section(al),
        "xvl": xvl_section(al, *xvl_rows),
    }


def build_from_data_dir(data_dir: str, max_days: int = MAX_DAYS) -> dict:
    rows = load_cross_venue_rows(data_dir, max_days=max_days)
    xvl_rows = load_xvl_shadow_rows(data_dir)
    if not rows:
        return build_lead_lag_report([], xvl_rows=xvl_rows)
    start = int(rows[0]["minute_ts"])
    end = int(rows[-1]["minute_ts"]) + 60
    return build_lead_lag_report(rows, load_bitfinex_quotes(data_dir, start, end), xvl_rows=xvl_rows)
