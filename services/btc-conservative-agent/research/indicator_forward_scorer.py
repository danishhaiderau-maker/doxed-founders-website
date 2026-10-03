"""Indicator Edge forward scorer (laptop analyzer; never imported by the Fly runtime).

Scores every pre-registered indicator feature forward on bars closed after the freeze, against the Bitfinex
1 s tape: mid return at +3/+15/+60/+120 min from ``decision_ts + latency`` (2 s headline, 9 s robustness),
net of a 2 bp round trip, executable bid/ask crossing alongside, MFE/MAE, hit rate, rank IC, top/bottom-20%
spread, regime splits, daily sign stability, |rho| > 0.7 correlation clusters, a 1 h-cluster bootstrap with
Benjamini-Hochberg FDR over every feature x window trial, and the HINT / PROMISING / NOISE labels.

Outcome paths follow the shadow-exit recorder's convention (executable side, minute horizons, the strategy_lab
dense tape with its hole censor) but are anchored at every bar's decision time instead of at a trade.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import indicator_edge_spec as spec  # noqa: E402
from indicator_engine import rotation_paths  # noqa: E402
from research import indicator_edge_prereg as prereg  # noqa: E402
from strategy_lab import tape as tape_mod  # noqa: E402

SCHEMA = "indicator_edge_report_v1"
DAILY_SCHEMA = "indicator_edge_daily_v1"
DEFAULT_DATA = r"C:\DoxxedCrypto\fly-mirror-segments\tree"
DEFAULT_OUT = r"C:\DoxxedCrypto\analyzer-exports\indicator-edge"
REPORT_FILE = "indicator_edge_report.json"
DAILY_FILE = "indicator_edge_daily.jsonl"
DAY_SEC = 86400
RULES = spec.SCORING_RULES
WINDOWS = tuple(int(w) for w in RULES["windows_min"])
LATENCIES = tuple(int(x) for x in RULES["latencies_sec"])
PRIMARY_LAT = int(RULES["primary_latency_sec"])
COST_BP = float(RULES["round_trip_cost_bp"])
LABEL_RANK = {"PROMISING": 2, "HINT": 1, "NOISE": 0}
MIN_IC_ROWS = 30
MIN_CORR_ROWS = 50
MIN_REGIME_SIGNALS = 5
MIN_RANK_SIGNALS = 20
TOP_N = 5


def _r(value: Any, digits: int = 3) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, digits) if math.isfinite(v) else None


def _utc(ts: float | None) -> str | None:
    return None if ts is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


# ---------------------------------------------------------------- inputs

def load_bar_rows(data_dir: str) -> list[dict[str, Any]]:
    """All indicator bar rows (rotations oldest first), first occurrence per ``bar_ts`` wins."""
    seen, rows = set(), []
    for path in rotation_paths(os.path.join(data_dir, spec.BAR_FILE)):
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("schema") != spec.BAR_SCHEMA:
                    continue
                key = row.get("bar_ts")
                if not isinstance(key, (int, float)) or key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    rows.sort(key=lambda r: r["bar_ts"])
    return rows


def decision_ts(row: Mapping[str, Any]) -> float:
    close = float(row["bar_close_ts"])
    emitted = row.get("ts")
    base = close + float(RULES["decision_lag_sec"])
    return max(base, float(emitted)) if isinstance(emitted, (int, float)) else base


def eligible_rows(rows: Sequence[Mapping[str, Any]], freeze: Mapping[str, Any] | None) -> tuple[list, dict]:
    excluded = {"sha_mismatch": 0, "before_freeze": 0, "unhealthy": 0, "late": 0}
    if not freeze:
        return [], excluded | {"not_preregistered": len(rows)}
    sha, frozen_at = freeze["feature_set_sha"], float(freeze["frozen_at"])
    out = []
    for row in rows:
        if row.get("feature_set_sha") != sha:
            excluded["sha_mismatch"] += 1
        elif float(row.get("bar_close_ts") or 0) <= frozen_at:
            excluded["before_freeze"] += 1
        elif not (row.get("health") or {}).get("ok"):
            excluded["unhealthy"] += 1
        elif row.get("late"):
            excluded["late"] += 1
        else:
            out.append(row)
    return out, excluded


def feature_arrays(rows: Sequence[Mapping[str, Any]], fids: Sequence[str]) -> dict[str, np.ndarray]:
    """``side``: score where status AVAILABLE else NaN; ``value``: oriented pct-50 (raw when no pct)."""
    n, k = len(rows), len(fids)
    side = np.full((n, k), np.nan)
    value = np.full((n, k), np.nan)
    pct = np.full((n, k), np.nan)
    orient = np.array([spec.orientation(f) for f in fids], dtype=float)
    for i, row in enumerate(rows):
        f = row.get("f") or {}
        for j, fid in enumerate(fids):
            cell = f.get(fid)
            if not isinstance(cell, list) or len(cell) < 4 or cell[3] != spec.STATUS_AVAILABLE:
                continue
            raw, score, p = cell[0], cell[1], cell[2]
            if isinstance(score, (int, float)):
                side[i, j] = float(np.sign(score))
            if isinstance(p, (int, float)):
                pct[i, j] = float(p)
                value[i, j] = orient[j] * (float(p) - 50.0)
            elif isinstance(raw, (int, float)) and math.isfinite(raw):
                value[i, j] = orient[j] * float(raw)
    return {"side": side, "value": value, "pct": pct, "orient": orient}


# ---------------------------------------------------------------- outcomes

def outcomes(tape: Any, dts: np.ndarray) -> dict[str, Any]:
    """Forward mid returns (bp) per latency x window, executable long/short and MFE/MAE at the primary latency."""
    n, nw = len(dts), len(WINDOWS)
    out: dict[str, Any] = {"mid": {}, "exec_long": np.full((n, nw), np.nan), "exec_short": np.full((n, nw), np.nan),
                           "up": np.full((n, nw), np.nan), "dn": np.full((n, nw), np.nan)}
    if tape is None or not n:
        for lat in LATENCIES:
            out["mid"][lat] = np.full((n, nw), np.nan)
        return out
    for lat in LATENCIES:
        mid_ret = np.full((n, nw), np.nan)
        e = tape.index(dts + lat)
        e_ok = (e >= 0) & (e < tape.n)
        ec = np.clip(e, 0, tape.n - 1)
        m0 = tape.mid[ec]
        for j, w in enumerate(WINDOWS):
            x = e + w * 60
            ok = e_ok & (x < tape.n) & (tape.next_bad[ec] > x) & np.isfinite(m0)
            xc = np.clip(x, 0, tape.n - 1)
            m1 = tape.mid[xc]
            ok &= np.isfinite(m1)
            mid_ret[ok, j] = (m1[ok] / m0[ok] - 1.0) * 1e4
            if lat == PRIMARY_LAT:
                a0, b0, a1, b1 = tape.ask[ec], tape.bid[ec], tape.ask[xc], tape.bid[xc]
                out["exec_long"][ok, j] = (b1[ok] / a0[ok] - 1.0) * 1e4
                out["exec_short"][ok, j] = (1.0 - a1[ok] / b0[ok]) * 1e4
        out["mid"][lat] = mid_ret
        if lat == PRIMARY_LAT:
            for i in np.flatnonzero(e_ok & np.isfinite(m0)):
                for j, w in enumerate(WINDOWS):
                    if not np.isfinite(mid_ret[i, j]):
                        continue
                    seg = tape.mid[e[i]: e[i] + w * 60 + 1]
                    out["up"][i, j] = (np.nanmax(seg) / m0[i] - 1.0) * 1e4
                    out["dn"][i, j] = (np.nanmin(seg) / m0[i] - 1.0) * 1e4
    return out


# ---------------------------------------------------------------- statistics

def rankdata(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    ranks = np.empty(len(x), dtype=float)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        ranks[order[i: j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 3:
        return None
    ra, rb = rankdata(a[m]), rankdata(b[m])
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def cluster_bootstrap(values: np.ndarray, ts: np.ndarray, *, cluster_sec: float = RULES["cluster_sec"],
                      resamples: int = RULES["bootstrap_resamples"], seed: int = RULES["bootstrap_seed"]) -> dict:
    """One-sided P(mean <= 0) and a 95% CI from resampling whole 1 h clusters with replacement.

    Too few clusters make the resampled means near-degenerate (tiny p from 3 lucky signals), so p is 1 there."""
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return {"p_value": 1.0, "ci95_bp": None, "clusters": int(len(values))}
    keys = np.floor(np.asarray(ts, dtype=float) / cluster_sec)
    _, inv = np.unique(keys, return_inverse=True)
    sums = np.bincount(inv, weights=values)
    cnt = np.bincount(inv).astype(float)
    k = len(sums)
    if k < max(2, int(RULES["min_bootstrap_clusters"])):
        return {"p_value": 1.0, "ci95_bp": None, "clusters": k}
    idx = np.random.default_rng(seed).integers(0, k, size=(resamples, k))
    means = sums[idx].sum(axis=1) / cnt[idx].sum(axis=1)
    p = (float(np.sum(means <= 0.0)) + 1.0) / (resamples + 1.0)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"p_value": round(p, 6), "ci95_bp": [_r(lo), _r(hi)], "clusters": k}


def bh_adjust(pvals: Sequence[float]) -> list[float]:
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    if not m:
        return []
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.clip(q, 0.0, 1.0)
    return [round(float(v), 6) for v in out]


def _mean(x: np.ndarray) -> float | None:
    return _r(np.mean(x)) if len(x) else None


def trial_stats(side: np.ndarray, value: np.ndarray, pct: np.ndarray, orient: float, j: int, oc: Mapping,
                dts: np.ndarray, days: np.ndarray, regimes: Mapping[str, np.ndarray]) -> dict[str, Any]:
    mid = oc["mid"][PRIMARY_LAT][:, j]
    mid9 = oc["mid"][max(LATENCIES)][:, j]
    sig = np.isfinite(side) & (side != 0) & np.isfinite(mid)
    s, r = side[sig], mid[sig]
    gross = s * r
    net = gross - COST_BP
    m9 = np.isfinite(mid9[sig])
    net9 = s[m9] * mid9[sig][m9] - COST_BP
    exl, exs = oc["exec_long"][sig, j], oc["exec_short"][sig, j]
    execb = np.where(s > 0, exl, exs)
    up, dn = oc["up"][sig, j], oc["dn"][sig, j]
    mfe = np.where(s > 0, up, -dn)
    mae = np.where(s > 0, dn, -up)
    sig_days = days[sig]
    daily = []
    for d in sorted(set(sig_days.tolist())):
        g = gross[sig_days == d]
        if len(g) >= RULES["min_signals_per_day"]:
            daily.append({"day": d, "n": int(len(g)), "mean_gross_bp": _r(g.mean())})
    regime_out: dict[str, dict] = {}
    for split, labels in regimes.items():
        cells = {}
        lab = labels[sig]
        for cell in sorted({str(x) for x in lab.tolist() if x is not None}):
            mk = lab == cell
            if mk.sum():
                cells[cell] = {"n": int(mk.sum()), "mean_net_bp": _r(net[mk].mean()),
                               "hit_rate": _r((gross[mk] > 0).mean())}
        regime_out[split] = cells
    boot = cluster_bootstrap(net, dts[sig])
    obs = np.isfinite(value) & np.isfinite(mid)
    ic = spearman(value[obs], mid[obs]) if obs.sum() >= MIN_IC_ROWS else None
    spread = None
    pm = np.isfinite(pct) & np.isfinite(mid)
    if pm.sum() >= MIN_IC_ROWS:
        hi, lo = mid[pm & (pct >= 80)], mid[pm & (pct <= 20)]
        if len(hi) and len(lo):
            spread = _r(orient * (hi.mean() - lo.mean()))
    return {"window_min": WINDOWS[j], "signals": int(sig.sum()), "observed_rows": int(np.isfinite(mid).sum()),
            "hit_rate": _r((gross > 0).mean()) if len(gross) else None,
            "mean_gross_bp": _mean(gross), "mean_net_bp": _mean(net), "mean_net_bp_9s": _mean(net9),
            "mean_exec_bp": _r(np.nanmean(execb)) if np.isfinite(execb).any() else None,
            "mfe_bp": _r(np.nanmean(mfe)) if np.isfinite(mfe).any() else None,
            "mae_bp": _r(np.nanmean(mae)) if np.isfinite(mae).any() else None,
            "rank_ic": _r(ic, 4), "rank_ic_rows": int(obs.sum()), "top_bottom_spread_bp": spread,
            "daily": daily, "regimes": regime_out, "bootstrap": boot, "p_value": boot["p_value"]}


def _quality(t: Mapping[str, Any]) -> tuple:
    """Rank key: label, FDR q, enough signals to mean anything, then net bp."""
    net = t.get("mean_net_bp")
    return (LABEL_RANK[t["label"]], -(t.get("q_value") or 1.0), t["signals"] >= MIN_RANK_SIGNALS,
            net if net is not None else -1e9)


def label_trial(t: Mapping[str, Any]) -> tuple[str, list[str]]:
    daily = t["daily"]
    reasons = []
    net, net9 = t.get("mean_net_bp"), t.get("mean_net_bp_9s")
    days_pos = [d["mean_gross_bp"] is not None and d["mean_gross_bp"] > 0 for d in daily]
    sessions_pos = sum(1 for c in (t["regimes"].get("session") or {}).values()
                       if c["n"] >= MIN_REGIME_SIGNALS and (c["mean_net_bp"] or 0) > 0)
    if (len(daily) >= RULES["min_days_promising"] and (t.get("q_value") or 1.0) <= RULES["fdr_q"]
            and sum(days_pos[-7:]) >= RULES["promising_min_sign_days_of_7"]
            and sessions_pos >= RULES["promising_min_sessions"] and (net or 0) > 0 and (net9 or 0) > 0):
        return "PROMISING", ["7+ days, FDR-significant, sign held >=5/7, >=2 sessions, survives 9 s"]
    if len(daily) >= RULES["min_days_hint"] and all(days_pos) and (net or 0) > 0:
        reasons.append(f"{len(daily)} days, sign held every day, net {net} bp > 0")
        return "HINT", reasons
    if len(daily) < RULES["min_days_hint"]:
        reasons.append(f"only {len(daily)} scored day(s)")
    elif not all(days_pos):
        reasons.append(f"sign held {sum(days_pos)}/{len(daily)} days")
    if net is not None and net <= 0:
        reasons.append(f"net {net} bp <= 0 after {COST_BP:g} bp cost")
    return "NOISE", reasons or ["no signals"]


def correlation_clusters(value: np.ndarray, fids: Sequence[str], quality: Mapping[str, tuple]) -> dict[str, Any]:
    """Greedy |rho| > threshold clusters; best-quality feature first, so each representative is its cluster's best."""
    thr = float(RULES["correlation_cluster_abs_rho"])
    ranks = {}
    for j, fid in enumerate(fids):
        col = value[:, j]
        m = np.isfinite(col)
        if m.sum() >= MIN_CORR_ROWS and np.nanstd(col) > 0:
            r = np.full(len(col), np.nan)
            r[m] = rankdata(col[m])
            ranks[fid] = r
    order = sorted(fids, key=lambda f: quality.get(f, (0,)), reverse=True)
    reps: list[str] = []
    member_of: dict[str, str] = {}
    rho_to_rep: dict[str, float] = {}
    for fid in order:
        home = None
        if fid in ranks:
            for rep in reps:
                if rep not in ranks:
                    continue
                a, b = ranks[fid], ranks[rep]
                m = np.isfinite(a) & np.isfinite(b)
                if m.sum() < MIN_CORR_ROWS or a[m].std() == 0 or b[m].std() == 0:
                    continue
                rho = float(np.corrcoef(a[m], b[m])[0, 1])
                if abs(rho) > thr:
                    home, rho_to_rep[fid] = rep, round(rho, 3)
                    break
        if home is None:
            reps.append(fid)
            member_of[fid] = fid
        else:
            member_of[fid] = home
    clusters = defaultdict(list)
    for fid, rep in member_of.items():
        clusters[rep].append(fid)
    return {"threshold_abs_rho": thr, "representatives": reps,
            "clusters": [{"representative": rep, "members": clusters[rep],
                          "rho_to_representative": {m: rho_to_rep.get(m) for m in clusters[rep] if m != rep}}
                         for rep in reps if len(clusters[rep]) > 1],
            "member_of": member_of, "independent_count": len(reps)}


# ---------------------------------------------------------------- inventory and summary

def latest_healthy(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for row in reversed(rows):
        if (row.get("health") or {}).get("ok"):
            return row
    return rows[-1] if rows else None


def status_inventory(row: Mapping[str, Any] | None) -> dict[str, Any]:
    f = (row or {}).get("f") or {}
    by_ind = defaultdict(dict)
    for fid in spec.feature_ids():
        cell = f.get(fid)
        by_ind[spec.indicator_of(fid)["id"]][fid] = cell[3] if isinstance(cell, list) and len(cell) >= 4 else "U"
    out, counts = [], {"AVAILABLE": 0, "WARMING_UP": 0, "UNAVAILABLE": 0}
    for ind in spec.INDICATORS:
        sts = set(by_ind[ind["id"]].values())
        status = ("UNAVAILABLE" if spec.STATUS_UNAVAILABLE in sts else
                  "WARMING_UP" if spec.STATUS_WARMING_UP in sts else "AVAILABLE")
        counts[status] += 1
        out.append({"num": ind["num"], "id": ind["id"], "family": ind["family"], "status": status,
                    "features": {k: spec.STATUS_NAMES.get(v, v) for k, v in by_ind[ind["id"]].items()}})
    return {"as_of_bar_ts": (row or {}).get("bar_ts"), "as_of_utc": _utc((row or {}).get("bar_close_ts")),
            "counts": counts, "indicators": out}


def _best_regime(t: Mapping[str, Any]) -> str | None:
    best = None
    for split, cells in (t.get("regimes") or {}).items():
        for cell, c in cells.items():
            if c["n"] >= MIN_REGIME_SIGNALS * 2 and c["mean_net_bp"] is not None:
                if best is None or c["mean_net_bp"] > best[2]:
                    best = (split, cell, c["mean_net_bp"])
    if best is None:
        return None
    names = {"vol_tercile": "volatility", "trend_state": "trend", "session": "session"}
    return f"{names.get(best[0], best[0])} {best[1]} ({best[2]:+.1f} bp)"


def daily_paragraph(report: Mapping[str, Any], changes: Mapping[str, list]) -> str:
    status = report["status"]
    day = report.get("generated_at", "")[:10]
    if status == "NOT_PREREGISTERED":
        return (f"Indicator Edge {day}: not scoring yet - the feature set has not been pre-registered, so no bar "
                "counts. Nothing here is a trading signal.")
    if status in ("NO_BARS", "NO_ELIGIBLE_BARS"):
        return (f"Indicator Edge {day}: no scorable bars yet (the Fly indicator engine has not shipped bars closed "
                "after the freeze). Labels start after the first full UTC day.")
    days, bars = report["scored_days"], report["inputs"]["eligible_rows"]
    lc = report["label_counts"]
    top = report.get("top5") or []
    parts = [f"Indicator Edge {day} (day {days} of forward scoring, {bars} bars): "
             f"{lc.get('PROMISING', 0)} PROMISING, {lc.get('HINT', 0)} HINT, {lc.get('NOISE', 0)} NOISE "
             f"out of {report['scored_features']} features ({report['clusters']['independent_count']} independent)."]
    if top:
        bits = []
        for i, t in enumerate(top, 1):
            w = t["best"]
            where = f", best in {t['best_regime']}" if t.get("best_regime") else ""
            hit = f"{w['hit_rate'] * 100:.0f}%" if w.get("hit_rate") is not None else "n/a"
            net = f"{w['mean_net_bp']:+.1f}" if w.get("mean_net_bp") is not None else "n/a"
            bits.append(f"{i}) {t['feature']} {t['label']}: right {hit} at +{w['window_min']} min, "
                        f"{net} bp after costs{where}")
        parts.append("Top 5: " + "; ".join(bits) + ".")
    for lab in ("PROMISING", "HINT", "NOISE"):
        if changes.get(lab):
            parts.append(f"Newly {lab}: {', '.join(changes[lab][:6])}{' ...' if len(changes[lab]) > 6 else ''}.")
    if not lc.get("PROMISING"):
        parts.append("Nothing is PROMISING yet; that needs 7 scored days, FDR significance and a sign that holds.")
    parts.append("Observation only: no tile, order or relay is affected.")
    return " ".join(parts)


# ---------------------------------------------------------------- report

def score(data_dir: str, out_dir: str, *, prereg_root: Path = prereg.DEFAULT_ROOT, now: float | None = None,
          tape: Any = "load", rows: Sequence[Mapping[str, Any]] | None = None,
          context: dict | None = None) -> dict[str, Any]:
    """Score every frozen feature x window. ``context`` (when given) receives the arrays the combination grid reuses."""
    now = float(now if now is not None else time.time())
    t_start = time.time()
    chain_rows, chain = prereg.load_chain(Path(prereg_root) / prereg.FROZEN_FILE)
    freeze = prereg.feature_set_freeze(chain_rows) if chain["chain_ok"] else None
    rows = list(rows) if rows is not None else load_bar_rows(data_dir)
    elig, excluded = eligible_rows(rows, freeze)
    fids = spec.scored_feature_ids()
    report: dict[str, Any] = {
        "schema": SCHEMA, "generated_at": _utc(now), "generated_at_ts": now,
        "feature_set_version": spec.FEATURE_SET_VERSION, "feature_set_sha": spec.feature_set_sha(),
        "prereg": {"chain": {k: chain[k] for k in ("file", "lines", "chain_ok", "head_sha")},
                   "prereg_id": freeze and freeze["prereg_id"], "line_sha": freeze and freeze["_line_sha"],
                   "frozen_at": freeze and freeze["frozen_at"], "frozen_at_utc": freeze and freeze["frozen_at_utc"]},
        "rules": {k: RULES[k] for k in ("id", "round_trip_cost_bp", "latencies_sec", "windows_min", "fdr_q",
                                        "decision_lag_sec", "cluster_sec", "bootstrap_resamples")},
        "trial_count": spec.trial_count(), "scored_features": len(fids),
        "inputs": {"data_dir": data_dir, "bar_rows": len(rows), "eligible_rows": len(elig), "excluded": excluded,
                   "first_bar_utc": _utc(rows[0]["bar_close_ts"]) if rows else None,
                   "last_bar_utc": _utc(rows[-1]["bar_close_ts"]) if rows else None},
        "status_inventory": status_inventory(latest_healthy(rows)),
        "label_counts": {"PROMISING": 0, "HINT": 0, "NOISE": 0}, "scored_days": 0,
        "features": [], "clusters": {"independent_count": 0, "representatives": [], "clusters": []}, "top5": [],
    }
    tampered = any(r.get("kind") == prereg.KIND_FEATURE_SET and r.get("feature_set_sha") == spec.feature_set_sha()
                   and not prereg.freeze_intact(r) for r in chain_rows)
    if not chain["chain_ok"]:
        report["status"] = "PREREG_CHAIN_BROKEN"
    elif tampered:
        report["status"] = "PREREG_TAMPERED"
    elif not freeze:
        report["status"] = "NOT_PREREGISTERED"
    elif not rows:
        report["status"] = "NO_BARS"
    elif not elig:
        report["status"] = "NO_ELIGIBLE_BARS"
    else:
        report["status"] = "OK"
        dts = np.array([decision_ts(r) for r in elig])
        if isinstance(tape, str):
            span = max(WINDOWS) * 60 + max(LATENCIES) + 120
            hist = tape_mod.default_history_sources() if tape_mod.laptop_defaults_enabled() else None
            tape = tape_mod.load_bitfinex_tape(data_dir, float(dts.min()) - 60, float(dts.max()) + span,
                                               cache_dir=os.path.join(out_dir, "tape-cache"), history=hist)
        oc = outcomes(tape, dts)
        report["inputs"]["tape"] = None if tape is None else {"start_utc": _utc(tape.t0), "end_utc": _utc(tape.t1),
                                                             "seconds": int(tape.n)}
        report["inputs"]["outcome_rows_observed_15m"] = int(np.isfinite(oc["mid"][PRIMARY_LAT][:, 1]).sum())
        arr = feature_arrays(elig, fids)
        days = np.array([_day(t) for t in dts], dtype=object)
        regimes = {k: np.array([(r.get("regime") or {}).get(k) for r in elig], dtype=object)
                   for k in spec.REGIME_SPLITS}
        trials = []
        for fj, fid in enumerate(fids):
            for j in range(len(WINDOWS)):
                trials.append((fid, j, trial_stats(arr["side"][:, fj], arr["value"][:, fj], arr["pct"][:, fj],
                                                   arr["orient"][fj], j, oc, dts, days, regimes)))
        qs = bh_adjust([t[2]["p_value"] for t in trials])
        per_feature: dict[str, dict] = {}
        for (fid, j, t), q in zip(trials, qs):
            t["q_value"] = q
            t["label"], t["label_reasons"] = label_trial(t)
            per_feature.setdefault(fid, {})[str(WINDOWS[j])] = t
        scored_days = sorted({d for d in days.tolist()})
        report["scored_days"] = len(scored_days)
        quality = {}
        feats = []
        for fid in fids:
            wins = per_feature[fid]
            best_w = max(wins.values(), key=_quality)
            ind = spec.indicator_of(fid)
            quality[fid] = _quality(best_w)
            feats.append({"feature": fid, "indicator": ind["id"], "num": ind["num"], "family": ind["family"],
                          "role": ind["role"], "variant": fid.rsplit(":", 1)[-1], "label": best_w["label"],
                          "best_window_min": best_w["window_min"], "best_regime": _best_regime(best_w),
                          "signals_per_day": _r(best_w["signals"] / max(1, len(scored_days)), 1),
                          "best": {k: v for k, v in best_w.items() if k not in ("daily", "regimes", "bootstrap")}
                          | {"ci95_bp": best_w["bootstrap"]["ci95_bp"]},
                          "windows": wins})
        clusters = correlation_clusters(arr["value"], fids, quality)
        for f in feats:
            f["cluster_rep"] = clusters["member_of"].get(f["feature"], f["feature"])
            f["independent"] = f["cluster_rep"] == f["feature"]
            report["label_counts"][f["label"]] += 1
        feats.sort(key=lambda f: quality[f["feature"]], reverse=True)
        report["features"] = feats
        report["clusters"] = {k: v for k, v in clusters.items() if k != "member_of"}
        report["top5"] = [{k: f[k] for k in ("feature", "label", "family", "best_regime", "best", "independent")}
                          for f in feats if f["independent"]][:TOP_N]
        if context is not None:
            context.update(rows=elig, fids=fids, arrays=arr, outcomes=oc, dts=dts, days=days, regimes=regimes,
                           freeze=freeze, features={f["feature"]: f for f in feats})
    report["compute_sec"] = round(time.time() - t_start, 2)
    return report


def label_changes(prev: Mapping[str, str], report: Mapping[str, Any]) -> dict[str, list]:
    out: dict[str, list] = {"PROMISING": [], "HINT": [], "NOISE": []}
    for f in report.get("features") or []:
        old = prev.get(f["feature"])
        if old is not None and old != f["label"]:
            out[f["label"]].append(f["feature"])
        elif old is None and f["label"] != "NOISE":
            out[f["label"]].append(f["feature"])
    return out


def last_daily(out_dir: str) -> dict | None:
    path = Path(out_dir) / DAILY_FILE
    if not path.exists():
        return None
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            last = json.loads(line)
        except ValueError:
            continue
    return last


def write_outputs(report: dict[str, Any], out_dir: str) -> dict[str, Any]:
    """Atomic report write; one append-only daily summary per UTC day (first run of the day after midnight)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prev = last_daily(out_dir)
    changes = label_changes((prev or {}).get("labels") or {}, report)
    paragraph = daily_paragraph(report, changes)
    today = report["generated_at"][:10]
    report["daily_summary"] = {"date": today, "paragraph": paragraph, "changes": changes}
    appended = False
    if not prev or prev.get("date") != today:
        entry = {"schema": DAILY_SCHEMA, "date": today, "generated_at": report["generated_at"],
                 "status": report["status"], "scored_days": report["scored_days"], "paragraph": paragraph,
                 "label_counts": report["label_counts"], "changes": changes,
                 "top5": [t["feature"] for t in report["top5"]],
                 "labels": {f["feature"]: f["label"] for f in report["features"]}}
        with open(out / DAILY_FILE, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        appended = True
    elif prev:
        report["daily_summary"] = {"date": prev["date"], "paragraph": prev["paragraph"], "changes": prev["changes"],
                                   "intraday_paragraph": paragraph}
    tmp = out / (REPORT_FILE + ".tmp")
    tmp.write_text(json.dumps(report, default=str, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, out / REPORT_FILE)
    return {"report": str(out / REPORT_FILE), "daily_appended": appended}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Indicator Edge forward scorer (laptop, read-only inputs)")
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--prereg-root", default=str(prereg.DEFAULT_ROOT))
    args = ap.parse_args(argv)
    for p in (args.data_dir, args.out_dir, args.prereg_root):
        if "\\onedrive\\" in str(p).lower():
            raise SystemExit(f"refusing a OneDrive path: {p}")
    report = score(args.data_dir, args.out_dir, prereg_root=Path(args.prereg_root))
    res = write_outputs(report, args.out_dir)
    print(json.dumps({"status": report["status"], "eligible_rows": report["inputs"]["eligible_rows"],
                      "scored_days": report["scored_days"], "label_counts": report["label_counts"],
                      "compute_sec": report["compute_sec"]} | res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
