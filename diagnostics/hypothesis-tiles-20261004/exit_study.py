"""HYPOTHESIS-TILES-20261004: joint entry + exit + early-stop study for three entry rules (research only).

Signal rules (REALISTIC_V1 fills from research/genome_grid_study.py, zero fees, exits at the executable side
with 1 s exit latency via fill_model.realistic_exit_margin):

* TILE_A  FOLLOW the score-led side of AI NO_TRADE calls (class AI_NO_TRADE_SCORE_LED).
* TILE_B  FOLLOW every qualifying cross-venue evaluator trigger (XVL lead + XVP premium shadow rows written by
          the production evaluators); taker after the cross-venue latency; per-session gate.
* CFM     H8 committed fade (class AI_COMMITTED, FADE).

Entry variants (AI tiles): fixed offsets, ATR-scaled offsets, a quiet/violent regime switch (volatility
percentile + ADX at decision time), a score-gap shrink, and a taker fallback at TTL end when the spread is small.
Exit variants: time stops, break-even arms, armed MFE giveback, ATR trail, fixed/ATR take-profit, partials,
ladder, early thesis cuts (fixed bp, ATR, MAE velocity), no-progress cuts and combinations; 40 bp hard stop always.

Capacity is simulated per tile (pending + open <= cap). Nested walk-forward by UTC day: the design is selected
on prior days only (objective = net bp per eligible signal) and scored on the next day; pooled test-day trades
are the only out-of-sample number. Everything else is labelled in-sample description.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

AGENT = Path(__file__).resolve().parents[2] / "services" / "btc-conservative-agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(AGENT / "research"))

from research import fill_model as fm  # noqa: E402
from research import genome_grid_study as gg  # noqa: E402

NOTIONAL = gg.MARGIN_USD * gg.LEVERAGE
HARD_STOP = 40.0
CLUSTER = 3600
SEED = 20261004
SESSIONS = ("ASIA", "EU", "US")
GAVE_BACK_MFE_BP = 3.0
FALLBACK_MAX_SPREAD_BP = 2.0
_TAPE = None


def session_of(ts: float) -> str:
    h = time.gmtime(ts).tm_hour
    return "ASIA" if h < 8 else ("EU" if h < 16 else "US")


def day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


# ------------------------------------------------------------------ exit variants

def exit_variants(base_min: float) -> dict[str, dict[str, Any]]:
    """Units: bp of price == margin % at 100x. Every variant keeps the 40 bp hard stop."""
    v: dict[str, dict[str, Any]] = {}
    T = float(base_min)
    tag = f"T{int(T)}"
    v[f"BASE_TIME_{int(T)}M_HARD40"] = {"time_min": T}
    for t in (15, 30, 60, 90, 120):
        if t != T:
            v[f"TIME_{t}M_HARD40"] = {"time_min": float(t)}
    for arm in (3, 5, 8, 12):
        for buf in (1, 2):
            v[f"BE_ARM{arm}BP_FLOOR+{buf}_{tag}"] = {"time_min": T, "be_arm_bp": arm, "be_floor_bp": buf}
    for k in (0.5, 1.0):
        v[f"BE_ARM{k}ATR_FLOOR+1_{tag}"] = {"time_min": T, "be_arm_atr": k, "be_floor_bp": 1}
    for keep in (0.3, 0.5, 0.7):
        for arm in (5, 10):
            v[f"GIVEBACK_KEEP{int(keep*100)}_ARM{arm}BP_{tag}"] = {"time_min": T, "gb_keep": keep, "gb_arm_bp": arm}
    for k, arm in ((1.0, 0.5), (1.0, 1.0), (1.5, 0.5), (1.5, 1.0), (2.0, 0.5), (2.0, 1.0)):
        v[f"ATR_TRAIL_{k}_ARM{arm}_{tag}"] = {"time_min": T, "trail_k": k, "trail_arm_atr": arm}
    for tp in (5, 10, 15, 25):
        v[f"TP_{tp}BP_{tag}"] = {"time_min": T, "tp_bp": tp}
    for k in (1.0, 1.5, 2.5):
        v[f"TP_{k}ATR_{tag}"] = {"time_min": T, "tp_atr": k}
    for lvl in (8, 12):
        v[f"PARTIAL50_AT{lvl}BP_THEN_BE+1_{tag}"] = {"time_min": T, "partials_bp": [(lvl, 0.5)], "be_after_partial": 1}
    v[f"PARTIAL50_AT1ATR_THEN_BE+1_{tag}"] = {"time_min": T, "partials_atr": [(1.0, 0.5)], "be_after_partial": 1}
    v[f"PARTIAL50_AT1ATR_RUNNER_GB50_{tag}"] = {"time_min": T, "partials_atr": [(1.0, 0.5)], "be_after_partial": 1,
                                                "gb_keep": 0.5, "gb_arm_atr": 1.0}
    v[f"LADDER_8>2_15>8_25>15_{tag}"] = {"time_min": T, "ladder": [(8, 2), (15, 8), (25, 15)]}
    # Early stops (Danish: cut a trade that goes straight against us right after entry).
    for cut in (6, 8, 9, 12):
        for win in (120, 300, 600, 1800):
            v[f"CUT-{cut}BP_{win//60}M_{tag}"] = {"time_min": T, "thesis_bp": -cut, "thesis_sec": win}
    v[f"CUT-6BP_1M_MAEVEL_{tag}"] = {"time_min": T, "thesis_bp": -6, "thesis_sec": 60}
    for k in (0.5, 1.0):
        v[f"CUT-{k}ATR_10M_{tag}"] = {"time_min": T, "thesis_atr": k, "thesis_sec": 600}
    for t in (10, 20, 30):
        v[f"NOPROGRESS_{t}M_MFE<2_{tag}"] = {"time_min": T, "noprog_sec": t * 60, "noprog_mfe": 2.0}
    for cut, win in ((15, 900), (25, 900)):
        v[f"CUT-{cut}BP_{win//60}M_{tag}"] = {"time_min": T, "thesis_bp": -cut, "thesis_sec": win}
    v[f"CUT-8BP_5M+BE5+1_{tag}"] = {"time_min": T, "thesis_bp": -8, "thesis_sec": 300, "be_arm_bp": 5, "be_floor_bp": 1}
    v[f"CUT-8BP_5M+GB50ARM10_{tag}"] = {"time_min": T, "thesis_bp": -8, "thesis_sec": 300, "gb_keep": 0.5, "gb_arm_bp": 10}
    v[f"CUT-15BP_15M+BE5+1_{tag}"] = {"time_min": T, "thesis_bp": -15, "thesis_sec": 900, "be_arm_bp": 5, "be_floor_bp": 1}
    v[f"CUT-15BP_15M+GB50ARM10_{tag}"] = {"time_min": T, "thesis_bp": -15, "thesis_sec": 900, "gb_keep": 0.5, "gb_arm_bp": 10}
    v[f"BE5+1+TP2.5ATR_{tag}"] = {"time_min": T, "be_arm_bp": 5, "be_floor_bp": 1, "tp_atr": 2.5}
    v[f"BE8+2+GB50ARM10_{tag}"] = {"time_min": T, "be_arm_bp": 8, "be_floor_bp": 2, "gb_keep": 0.5, "gb_arm_bp": 10}
    # Volatility-scaled protective stops alone.
    for k in (1.0, 1.5, 2.0):
        v[f"ATR_STOP_{k}_{tag}"] = {"time_min": T, "atr_stop_k": k}
    # Composite packages: every protection runs at once, whichever triggers first executes.
    # ATR-scaled: protective stop, 50% at TP1 with stop to break-even, armed ATR trail, early cut, time, hard stop.
    for stop_k, tp1, trail_k, cut_k in ((1.5, 1.0, 1.0, 0.5), (1.5, 1.0, 1.5, None), (2.0, 1.5, 1.0, 0.5),
                                        (1.0, 1.0, 1.0, None), (2.0, 1.0, 2.0, 0.75)):
        name = f"COMPOSITE_ATR_STOP{stop_k}_P50@{tp1}ATR_BE+1_TRAIL{trail_k}" + (f"_CUT{cut_k}ATR10M" if cut_k else "") + f"_{tag}"
        v[name] = {"time_min": T, "atr_stop_k": stop_k, "partials_atr": [(tp1, 0.5)], "be_after_partial": 1,
                   "trail_k": trail_k, "trail_arm_atr": tp1, **({"thesis_atr": cut_k, "thesis_sec": 600} if cut_k else {})}
    v[f"COMPOSITE_ATR_STOP1.5_BE1ATR+1_CHAND1.5_ARM1_{tag}"] = {"time_min": T, "atr_stop_k": 1.5, "be_arm_atr": 1.0,
                                                               "be_floor_bp": 1, "trail_k": 1.5, "trail_arm_atr": 1.0}
    v[f"COMPOSITE_ATR_STOP1.5_LADDER_P50@1ATR_TP2.5ATR_{tag}"] = {"time_min": T, "atr_stop_k": 1.5, "partials_atr": [(1.0, 0.5)],
                                                                 "be_after_partial": 1, "tp_atr": 2.5}
    # Fixed-bp twins of the composites.
    for stop, tp1, keep, cut in ((15, 10, 0.5, 8), (20, 12, 0.5, None), (12, 8, 0.7, 6), (25, 15, 0.5, 9)):
        name = f"COMPOSITE_BP_STOP{stop}_P50@{tp1}BP_BE+1_GB{int(keep*100)}" + (f"_CUT{cut}BP5M" if cut else "") + f"_{tag}"
        v[name] = {"time_min": T, "thesis_bp": -stop, "thesis_sec": 10 ** 9, "partials_bp": [(tp1, 0.5)],
                   "be_after_partial": 1, "gb_keep": keep, "gb_arm_bp": tp1,
                   **({"cut2_bp": -cut, "cut2_sec": 300} if cut else {})}
    return v


def replay(path: dict[str, np.ndarray], var: dict[str, Any], atr_pct: float) -> tuple[float, str, float, float]:
    """REALISTIC_V1 exit replay (twin of gg.fast_replay plus bp targets, armed giveback, BE after partial,
    early cuts). Returns (realized bp, reason, exit age s, MFE bp at exit)."""
    cur, mfe, age = path["cur"], path["mfe"], path["age"]
    n = len(cur)
    atr_m = float(atr_pct) * gg.LEVERAGE
    thr = path["thr_cur"]
    floor = np.full(n, -np.inf)
    any_floor = np.zeros(n, dtype=bool)

    def add(cond, value):
        nonlocal floor, any_floor
        floor = np.maximum(floor, np.where(cond, value, -np.inf))
        any_floor = any_floor | cond

    if var.get("be_arm_bp") is not None:
        add(mfe >= var["be_arm_bp"], float(var["be_floor_bp"]))
    if var.get("be_arm_atr") is not None:
        add(mfe >= var["be_arm_atr"] * atr_m, float(var["be_floor_bp"]))
    if var.get("gb_keep") is not None:
        arm = var["gb_arm_bp"] if var.get("gb_arm_bp") is not None else var["gb_arm_atr"] * atr_m
        add(mfe >= arm, mfe * var["gb_keep"])
    if var.get("trail_k") is not None:
        add(mfe >= var["trail_arm_atr"] * atr_m, mfe - var["trail_k"] * atr_m)
    for trig, val in var.get("ladder") or []:
        add(mfe >= trig, float(val))
    partials = [(lvl, f) for lvl, f in var.get("partials_bp") or []]
    partials += [(k * atr_m, f) for k, f in var.get("partials_atr") or []]
    with np.errstate(invalid="ignore"):
        if partials and var.get("be_after_partial") is not None:
            first_lvl = min(l for l, _ in partials)
            add(np.maximum.accumulate(thr > first_lvl), float(var["be_after_partial"]))
    hard = float(var.get("hard_bp") or HARD_STOP)
    if var.get("hard_atr_k") is not None:
        lo, hi = var.get("hard_clamp_bp", (25.0, 60.0))
        hard = min(hi, max(lo, var["hard_atr_k"] * atr_m))
    conds = [("PHYSICAL_HARD_STOP", cur <= -hard)]
    cut = var.get("thesis_bp")
    if var.get("thesis_atr") is not None:
        cut = -var["thesis_atr"] * atr_m
    if var.get("atr_stop_k") is not None:
        conds.append(("ATR_STOP", cur <= -var["atr_stop_k"] * atr_m))
    if cut is not None:
        never_ran = mfe <= var["thesis_max_mfe"] if var.get("thesis_max_mfe") is not None else True
        conds.append(("THESIS_FAST_CUT", (age <= var["thesis_sec"]) & (cur <= cut) & never_ran))
    if var.get("cut2_bp") is not None:
        conds.append(("THESIS_FAST_CUT", (age <= var["cut2_sec"]) & (cur <= var["cut2_bp"])))
    if var.get("noprog_sec") is not None:
        conds.append(("NO_PROGRESS", (age >= var["noprog_sec"]) & (mfe < var["noprog_mfe"])))
    if any_floor.any():
        conds.append(("PROFIT_PROTECTION_FLOOR", np.maximum.accumulate(any_floor) & (cur <= np.maximum.accumulate(floor))))
    conds.append(("TIME_STOP", age >= var["time_min"] * 60.0))
    tp = var.get("tp_bp")
    if var.get("tp_atr") is not None:
        tp = var["tp_atr"] * atr_m
    if tp is not None:
        with np.errstate(invalid="ignore"):
            conds.append(("ATR_TAKE_PROFIT", thr > tp))
    best, reason = n, "PATH_END"
    for name, c in conds:
        if c.any():
            i = int(np.argmax(c))
            if i < best:
                best, reason = i, name
    exit_idx = best if best < n else n - 1
    exit_m, _ = fm.realistic_exit_margin(cur, age, exit_idx, reason, latency_sec=fm.EXIT_LATENCY_SEC, target_margin=tp)
    remaining, realized = 1.0, 0.0
    for lvl, frac in sorted(partials):
        with np.errstate(invalid="ignore"):
            hit = thr[: exit_idx + 1] > lvl
        if hit.any() and remaining > 0:
            take = min(frac, remaining)
            realized += take * lvl
            remaining -= take
    realized += remaining * float(exit_m)
    return realized, reason, float(age[exit_idx]), float(mfe[exit_idx])


# ------------------------------------------------------------------ entry variants

def entry_variants(base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each entry is a function of decision-time context only (ATR, volatility percentile, ADX, score gap)."""
    off, chase, ttl = base["offset_pct"], base["chase_id"], base["ttl_sec"]
    e: dict[str, dict[str, Any]] = {f"BASE_OFFSET_{off:.2f}_{chase}_TTL{ttl}": {"kind": "FIXED", "offset": off, "chase": chase, "ttl": ttl}}
    e["TAKER_AT_SIGNAL"] = {"kind": "TAKER"}
    for o in (0.05, 0.10, 0.15, 0.25):
        if abs(o - off) > 1e-9:
            e[f"OFFSET_{o:.2f}_{chase}_TTL{ttl}"] = {"kind": "FIXED", "offset": o, "chase": chase, "ttl": ttl}
    for k in (0.5, 1.0, 1.5):
        e[f"OFFSET_{k}xATR_{chase}_TTL{ttl}"] = {"kind": "ATR", "k": k, "chase": chase, "ttl": ttl}
    # Danish's adaptive idea: quiet + directional -> near the market with an aggressive chase; violent -> far.
    e[f"REGIME_QUIET_NEAR_VIOLENT_ATR_TTL{ttl}"] = {"kind": "REGIME", "quiet_offset": 0.03, "quiet_chase": "all_on_s50_i60",
                                                   "k": 1.0, "chase": chase, "ttl": ttl, "vol_pct": 50, "adx": 25}
    e[f"REGIME_GAP_SHRINK_TTL{ttl}"] = {"kind": "GAP", "offset": off, "gap": 20, "shrink": 0.33, "chase": chase, "ttl": ttl}
    e[f"BASE+TAKER_FALLBACK_SPREAD<=2BP_TTL{ttl}"] = {"kind": "FIXED", "offset": off, "chase": chase, "ttl": ttl, "fallback": True}
    e[f"OFFSET_1.0xATR+TAKER_FALLBACK_TTL{ttl}"] = {"kind": "ATR", "k": 1.0, "chase": chase, "ttl": ttl, "fallback": True}
    e[f"REGIME_QUIET_NEAR_VIOLENT_ATR+FALLBACK_TTL{ttl}"] = dict(e[f"REGIME_QUIET_NEAR_VIOLENT_ATR_TTL{ttl}"], fallback=True)
    return e


def concrete_entry(spec: dict[str, Any], sig: "Signal") -> dict[str, Any]:
    if spec["kind"] == "TAKER":
        return {"offset_pct": 0.0, "chase_id": "no_chase", "ttl_sec": 0}
    off, chase = spec.get("offset"), spec["chase"]
    if spec["kind"] == "ATR":
        off = min(0.40, max(0.03, spec["k"] * sig.atr))
    elif spec["kind"] == "REGIME":
        quiet = (sig.vol_pct is not None and sig.vol_pct < spec["vol_pct"]) and (sig.adx is not None and sig.adx < spec["adx"])
        if quiet:
            off, chase = spec["quiet_offset"], spec["quiet_chase"]
        else:
            off = min(0.40, max(0.05, spec["k"] * sig.atr))
    elif spec["kind"] == "GAP":
        if sig.gap is not None and sig.gap >= spec["gap"]:
            off = off * spec["shrink"]
    return {"offset_pct": round(off, 4), "chase_id": chase, "ttl_sec": spec["ttl"]}


# ------------------------------------------------------------------ signals

class Signal:
    __slots__ = ("ts", "price", "direction", "atr", "kind", "start", "session", "day", "adx", "vol_pct", "gap", "res")

    def __init__(self, ts, price, direction, atr, kind="", adx=None, vol_pct=None, gap=None):
        self.ts, self.price, self.direction, self.atr, self.kind = ts, price, direction, atr, kind
        self.adx, self.vol_pct, self.gap = adx, vol_pct, gap
        self.start = int(math.floor(ts)) + 1
        self.session, self.day = session_of(ts), day_of(ts)
        self.res: dict[str, Any] = {}


def evaluate(sig: Signal, eid: str, espec: dict[str, Any], exits: dict[str, dict[str, Any]], latency: float):
    """(fill_ts | None, ttl, frac, maker, markout_bp, {exit_id: outcome}) for one signal x entry, computed once."""
    if eid in sig.res:
        return sig.res[eid]
    tape = _TAPE
    entry = concrete_entry(espec, sig)
    lat = gg.latency_steps(sig.ts, latency)
    ttl = int(entry["ttl_sec"])
    need = ttl + gg.PATH_END_SEC + 5 + lat + fm.TAKER_MAX_WAIT_SEC + 2
    out = None
    if tape.t0 <= sig.start and sig.start + need <= tape.end:
        w = tape.window(sig.start, sig.start + need)
        fill = gg.simulate_fill(entry, sig.direction, sig.price, w, lat)[gg.HEADLINE_WORLD]
        if fill is None and espec.get("fallback") and ttl:
            j = lat + ttl
            if j < len(w["bid"]) and w["ask"][j] == w["ask"][j]:
                mid = (w["bid"][j] + w["ask"][j]) / 2
                if (w["ask"][j] - w["bid"][j]) / mid * 1e4 <= FALLBACK_MAX_SPREAD_BP:
                    fill = gg.realistic_taker_fill(sig.direction, w, j)
        if fill is None:
            out = (None, ttl, 0.0, 0, None, {})
        else:
            f_idx, f_px, frac, maker = fill
            path = gg.prepare_path(sig.direction, f_px, f_idx, w)
            if path is not None:
                mid = (w["bid"] + w["ask"]) / 2
                mk = None
                if f_idx + 60 < len(mid):
                    mk = fm.adverse_selection_bp(float(mid[f_idx + 60]), f_px, sig.direction)
                outs = {x: replay(path, xv, sig.atr) for x, xv in exits.items()}
                out = (sig.start + f_idx, ttl, frac, maker, mk, outs)
    sig.res[eid] = out
    return out


def simulate(signals, eid, espec, xid, exits, *, cap, latency, allow=None):
    """Capacity-limited trades (pending + open <= cap). Returns (trades, eligible signal count)."""
    busy: list[float] = []
    trades, eligible = [], 0
    for s in signals:
        if allow is not None and not allow(s):
            continue
        eligible += 1
        busy = [b for b in busy if b > s.ts]
        if len(busy) >= cap:
            continue
        r = evaluate(s, eid, espec, exits, latency)
        if r is None:
            eligible -= 1
            continue
        fill_ts, ttl, frac, maker, mk, outs = r
        if fill_ts is None:
            busy.append(s.ts + latency + max(ttl, 1))
            continue
        bpv, reason, age, mfe = outs[xid]
        busy.append(fill_ts + age + 1.0)
        trades.append({"ts": s.ts, "day": s.day, "session": s.session, "bp": bpv * frac, "mfe": mfe,
                       "reason": reason, "side": s.direction, "maker": maker, "markout": mk})
    return trades, eligible


# ------------------------------------------------------------------ statistics

def cluster_ci(vals, ts, cluster=CLUSTER):
    if len(vals) < 2:
        return None, None, len(vals)
    _, inv = np.unique(np.floor(np.asarray(ts) / cluster).astype(np.int64), return_inverse=True)
    sums, counts = np.bincount(inv, weights=vals), np.bincount(inv).astype(float)
    c = len(sums)
    draw = np.random.default_rng(SEED).integers(0, c, size=(2000, c))
    means = sums[draw].sum(axis=1) / counts[draw].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return round(float(lo), 2), round(float(hi), 2), c


def stats(trades, eligible=None, span_days=None, detail=True):
    if not trades:
        return {"n": 0, "eligible_signals": eligible}
    v = np.array([t["bp"] for t in trades])
    ts = np.array([t["ts"] for t in trades])
    mfe = np.array([t["mfe"] for t in trades])
    wins, losses = v[v > 0], v[v <= 0]
    usd = v / 1e4 * NOTIONAL
    curve = np.cumsum(usd)
    dd = float(np.min(curve - np.maximum.accumulate(np.r_[0.0, curve])[1:]))
    lo, hi, clusters = cluster_ci(v, ts)
    pos = mfe >= GAVE_BACK_MFE_BP
    gave = pos & (v < 0)
    out = {
        "n": int(len(v)), "eligible_signals": eligible,
        "ev_bp_per_fill": round(float(v.mean()), 2),
        "ev_bp_per_signal": round(float(v.sum() / eligible), 2) if eligible else None,
        "fill_rate_pct": round(len(v) / eligible * 100, 1) if eligible else None,
        "win_rate": round(float((v > 0).mean()) * 100, 1), "net_usd": round(float(usd.sum()), 3),
        "avg_win_bp": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loss_bp": round(float(losses.mean()), 2) if len(losses) else None, "max_dd_usd": round(dd, 3),
        "ci95_1h_bp": [lo, hi], "n_eff_clusters_1h": clusters,
        "gave_back_pct": round(float(gave.mean()) * 100, 1),
        "gave_back_of_positive_pct": round(float(gave.sum() / max(1, pos.sum())) * 100, 1),
        "sharpe_per_trade": round(float(v.mean() / v.std(ddof=1)), 4) if len(v) > 2 and v.std() > 0 else None,
    }
    if span_days:
        out["trades_per_day"] = round(len(v) / span_days, 1)
    if detail:
        lo2, hi2, _ = cluster_ci(v, ts, 7200)
        by_day = defaultdict(list)
        for t in trades:
            by_day[t["day"]].append(t["bp"])
        mk = [t["markout"] for t in trades if t["maker"] and t["markout"] is not None]
        out |= {"ci95_2h_bp": [lo2, hi2],
                "by_day_ev_bp": {d: round(float(np.mean(x)), 2) for d, x in sorted(by_day.items())},
                "by_day_n": {d: len(x) for d, x in sorted(by_day.items())},
                "by_session_ev_bp": {s: [round(float(np.mean([t["bp"] for t in trades if t["session"] == s])), 2),
                                         sum(1 for t in trades if t["session"] == s)]
                                     for s in SESSIONS if any(t["session"] == s for t in trades)},
                "by_side_ev_bp": {s: [round(float(np.mean([t["bp"] for t in trades if t["side"] == s])), 2),
                                      sum(1 for t in trades if t["side"] == s)]
                                  for s in ("LONG", "SHORT") if any(t["side"] == s for t in trades)},
                "maker_fill_share_pct": round(sum(1 for t in trades if t["maker"]) / len(trades) * 100, 1),
                "maker_markout_60s_bp": round(float(np.mean(mk)), 2) if mk else None,
                "exit_reasons": {r: sum(1 for t in trades if t["reason"] == r) for r in sorted({t["reason"] for t in trades})}}
    return out


def deflated_sharpe(sr: float, n_trials: int, sr_var: float, t_eff: float, skew=0.0, kurt=3.0):
    nd = NormalDist()
    if n_trials < 2 or sr_var <= 0 or t_eff < 3:
        return None
    emc = 0.5772156649
    sr0 = math.sqrt(sr_var) * ((1 - emc) * nd.inv_cdf(1 - 1 / n_trials) + emc * nd.inv_cdf(1 - 1 / (n_trials * math.e)))
    den = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    return {"expected_max_sharpe_under_null": round(sr0, 4),
            "deflated_sharpe_prob": round(nd.cdf((sr - sr0) * math.sqrt(t_eff - 1) / den), 4)}


# ------------------------------------------------------------------ nested walk-forward

def nested(signals, entries, exits, *, cap, latency, base_eid, base_xid, min_train=30, session_gate=False,
           min_session=10, restrict=None):
    """Joint selection of (entry, exit[, session gate]) on prior UTC days; objective = bp per eligible signal."""
    days = sorted({s.day for s in signals})
    combos = [(e, x) for e in entries for x in exits]
    if restrict == "EXIT_ONLY":
        combos = [(base_eid, x) for x in exits]
    elif restrict == "ENTRY_ONLY":
        combos = [(e, base_xid) for e in entries]
    full = {}
    for e, x in combos:
        tr, _ = simulate(signals, e, entries[e], x, exits, cap=cap, latency=latency)
        full[(e, x)] = tr
    elig_by_day = defaultdict(int)
    for s in signals:
        elig_by_day[s.day] += 1
    folds, oos, oos_base = [], [], []
    oos_elig = 0
    for i, d in enumerate(days[1:], start=1):
        train_days = set(days[:i])
        best, best_obj, gate = None, -1e18, None
        for key, trades in full.items():
            tr = [t for t in trades if t["day"] in train_days]
            elig = sum(elig_by_day[x] for x in train_days)
            g = None
            if session_gate:
                g = {s for s in SESSIONS if sum(1 for t in tr if t["session"] == s) >= min_session
                     and np.mean([t["bp"] for t in tr if t["session"] == s]) > 0}
                tr = [t for t in tr if t["session"] in g]
            if len(tr) < min_train:
                continue
            obj = float(np.sum([t["bp"] for t in tr])) / max(1, elig)
            if obj > best_obj:
                best, best_obj, gate = key, obj, g
        base_test = [t for t in full.get((base_eid, base_xid), []) if t["day"] == d]
        if best is None:
            folds.append({"test_day": d, "selected": None, "reason": "NO_DESIGN_WITH_MIN_TRAIN_FILLS"})
            continue
        if session_gate:
            test, _ = simulate(signals, best[0], entries[best[0]], best[1], exits, cap=cap, latency=latency,
                               allow=lambda s, g=gate: s.session in g)
            test = [t for t in test if t["day"] == d]
        else:
            test = [t for t in full[best] if t["day"] == d]
        oos += test
        oos_base += base_test
        oos_elig += elig_by_day[d]
        folds.append({"test_day": d, "selected_entry": best[0], "selected_exit": best[1],
                      "train_bp_per_signal": round(best_obj, 3),
                      "session_gate": sorted(gate) if gate is not None else None, "test_n": len(test),
                      "test_ev_bp_per_fill": round(float(np.mean([t["bp"] for t in test])), 2) if test else None,
                      "base_test_n": len(base_test),
                      "base_test_ev_bp_per_fill": round(float(np.mean([t["bp"] for t in base_test])), 2) if base_test else None})
    test_days = set(days[1:])
    sharpes = []
    per = {}
    for key, trades in full.items():
        el_all = sum(elig_by_day.values())
        el_test = sum(elig_by_day[x] for x in test_days)
        st = stats(trades, el_all, detail=False)
        if st.get("sharpe_per_trade") is not None:
            sharpes.append(st["sharpe_per_trade"])
        per[f"{key[0]} | {key[1]}"] = {"all_in_sample": st,
                                       "test_days_fixed_rule": stats([t for t in trades if t["day"] in test_days], el_test, detail=False)}
    nested_stats = stats(oos, oos_elig)
    ds = None
    if nested_stats.get("sharpe_per_trade") is not None and len(sharpes) > 2:
        ds = deflated_sharpe(nested_stats["sharpe_per_trade"], len(full), float(np.var(sharpes)),
                             float(nested_stats["n_eff_clusters_1h"]))
    return {"days": days, "folds": folds, "nested_oos": nested_stats, "base_same_days": stats(oos_base, oos_elig),
            "designs_tried": len(full), "deflation": ds, "per_design": per}


def cut_cost_benefit(signals, base_eid, entries, exits, latency, base_xid):
    """Uncapped, base entry: for every early-cut/no-progress exit, trades it cut that the base exit would have
    recovered (cost) vs losses it saved (benefit)."""
    out = {}
    for xid, xv in exits.items():
        if xv.get("thesis_bp") is None and xv.get("thesis_atr") is None and xv.get("noprog_sec") is None:
            continue
        cut_n = recovered = saved = 0
        cost = benefit = 0.0
        for s in signals:
            r = s.res.get(base_eid) if len(signals) > 5000 else evaluate(s, base_eid, entries[base_eid], exits, latency)
            if not r or r[0] is None:
                continue
            o, b = r[5][xid], r[5][base_xid]
            if o[1] not in ("THESIS_FAST_CUT", "NO_PROGRESS"):
                continue
            cut_n += 1
            if b[0] > o[0]:
                recovered += 1
                cost += b[0] - o[0]
            else:
                saved += 1
                benefit += o[0] - b[0]
        out[xid] = {"cut_trades": cut_n, "would_have_recovered": recovered, "losses_saved": saved,
                    "cost_bp_total": round(cost, 1), "benefit_bp_total": round(benefit, 1),
                    "net_bp_total": round(benefit - cost, 1)}
    return out


def missed_opportunity(signals, entries, exits, latency, base_xid):
    """Per entry: share of signals unfilled and what a taker at the signal would have made on them (base exit)."""
    out = {}
    for eid, espec in entries.items():
        n = unfilled = 0
        missed = []
        for s in signals:
            r = evaluate(s, eid, espec, exits, latency)
            if r is None:
                continue
            n += 1
            if r[0] is None:
                unfilled += 1
                t = evaluate(s, "TAKER_AT_SIGNAL", entries["TAKER_AT_SIGNAL"], exits, latency)
                if t and t[0] is not None:
                    missed.append(t[5][base_xid][0])
        out[eid] = {"signals": n, "unfilled": unfilled, "fill_rate_pct": round((n - unfilled) / max(1, n) * 100, 1),
                    "unfilled_taker_ev_bp": round(float(np.mean(missed)), 2) if missed else None,
                    "missed_bp_total": round(float(np.sum(missed)), 1) if missed else 0.0}
    return out


# ------------------------------------------------------------------ cohorts

def episode_context(mirror: Path) -> tuple[dict, dict]:
    feats, scores = {}, {}
    for r in gg._read_jsonl(mirror / "v3" / "ledgers" / "opportunity.jsonl"):
        ep = str(r.get("episode_id") or "")
        f = r.get("feature_snapshot_at_signal") or {}
        if ep and f and ep not in feats:
            feats[ep] = (gg._num(f.get("adx")), gg._num(f.get("volatility_percentile")))
    for r in gg._read_jsonl(mirror / "v3" / "ledgers" / "decision.jsonl"):
        ep = str(r.get("episode_id") or "")
        ls, ss = gg._num(r.get("long_score")), gg._num(r.get("short_score"))
        if ep and ls is not None and ss is not None:
            scores[ep] = abs(ls - ss)
    return feats, scores


def ai_signals(mirror: Path, tape, klass: str, fade: bool) -> list[Signal]:
    eps, _ = gg.load_episodes(mirror, tape)
    feats, gaps = episode_context(mirror)
    out = []
    for e in eps:
        if e["episode_class"] != klass or e["source"] != "V3_OPPORTUNITY":
            continue
        d = e["direction"]
        if fade:
            d = "SHORT" if d == "LONG" else "LONG"
        adx, vp = feats.get(e["episode_id"], (None, None))
        out.append(Signal(e["signal_ts"], e["signal_price"], d, e["atr14_pct"] or 0.05, klass, adx, vp,
                          gaps.get(e["episode_id"])))
    return out


def xv_signals(mirror: Path, tape) -> list[Signal]:
    rows = []
    for name, kind in (("xvl_shadow_signals.jsonl", "XVL"), ("xvp_shadow_signals.jsonl", "XVP")):
        for p in sorted(mirror.glob(name + "*")):
            if p.name.endswith(".json"):
                continue
            for r in gg._read_jsonl(p):
                if r.get("gate") != "TRIGGER" or not r.get("qualifies") or r.get("side") not in ("LONG", "SHORT"):
                    continue
                ts = gg._num(r.get("evaluated_ts")) or (gg._num(r.get("anchor_bucket_ts")) or 0) + 1.0
                if not ts:
                    continue
                rows.append((ts, r["side"], kind, str(r.get("trigger_id"))))
    rows.sort()
    seen, out = set(), []
    for ts, side, kind, tid in rows:
        if tid in seen:
            continue
        seen.add(tid)
        i = int(ts) - tape.t0
        if not (0 <= i < len(tape.bid)) or tape.bid[i] != tape.bid[i]:
            continue
        px = float((tape.bid[i] + tape.ask[i]) / 2)
        out.append(Signal(ts, px, side, gg.tape_atr14_pct(tape, ts) or 0.05, kind))
    return out


def run_tile(name, signals, base_entry, latency, cap, base_min, *, session_gate=False, adaptive_entry=True):
    exits = exit_variants(base_min)
    base_xid = f"BASE_TIME_{int(base_min)}M_HARD40"
    entries = entry_variants(base_entry) if adaptive_entry else {"TAKER_AT_SIGNAL": {"kind": "TAKER"}}
    base_eid = next(iter(entries)) if adaptive_entry else "TAKER_AT_SIGNAL"
    horizon = max(int(e.get("ttl", 0)) for e in entries.values()) + gg.PATH_END_SEC + 60
    ok = [s for s in signals if _TAPE.t0 <= s.start and s.start + horizon <= _TAPE.end
          and not np.isnan(_TAPE.bid[s.start - _TAPE.t0: s.start - _TAPE.t0 + gg.PATH_END_SEC]).mean() > 1 - gg.MIN_PATH_COVERAGE]
    t0 = time.time()
    res = {"tile": name, "signals": len(signals), "evaluable": len(ok), "latency_sec": latency, "cap": cap,
           "base_entry": base_eid, "base_exit": base_xid, "entries": entries, "exits": exits,
           "span": [day_of(ok[0].ts), day_of(ok[-1].ts)] if ok else None}
    res["joint"] = nested(ok, entries, exits, cap=cap, latency=latency, base_eid=base_eid, base_xid=base_xid,
                          session_gate=session_gate)
    print(name, "joint", round(time.time() - t0), flush=True)
    res["exit_only"] = nested(ok, entries, exits, cap=cap, latency=latency, base_eid=base_eid, base_xid=base_xid,
                              session_gate=session_gate, restrict="EXIT_ONLY")
    if adaptive_entry:
        res["entry_only"] = nested(ok, entries, exits, cap=cap, latency=latency, base_eid=base_eid, base_xid=base_xid,
                                   session_gate=session_gate, restrict="ENTRY_ONLY")
        res["missed_opportunity"] = missed_opportunity(ok, entries, exits, latency, base_xid)
    res["cut_cost_benefit"] = cut_cost_benefit(ok, base_eid, entries, exits, latency, base_xid)
    span = max(1e-9, (ok[-1].ts - ok[0].ts) / 86400) if ok else None
    res["cap_sensitivity_base"] = {}
    for c in (1, 2, 3, 5, 10, 20, 100000):
        tr, el = simulate(ok, base_eid, entries[base_eid], base_xid, exits, cap=c, latency=latency)
        res["cap_sensitivity_base"][f"cap_{c if c < 100000 else 'uncapped'}"] = stats(tr, el, span)
    # Full-sample detail for the base and every design any fold selected.
    keys = {(base_eid, base_xid)} | {(f["selected_entry"], f["selected_exit"]) for blk in ("joint", "exit_only", "entry_only")
                                     for f in (res.get(blk) or {}).get("folds", []) if f.get("selected_entry")}
    res["detail"] = {}
    for e, x in sorted(keys):
        tr, el = simulate(ok, e, entries[e], x, exits, cap=cap, latency=latency)
        res["detail"][f"{e} | {x}"] = stats(tr, el, span)
    for blk in ("joint", "exit_only", "entry_only"):
        if blk in res:
            res[blk]["per_design_top"] = dict(sorted(res[blk].pop("per_design").items(),
                                                     key=lambda kv: -(kv[1]["test_days_fixed_rule"].get("ev_bp_per_signal") or -1e9))[:40])
    return res


def main() -> int:
    global _TAPE
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--tier-a", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ai-latency", type=float, default=6.5)
    ap.add_argument("--xv-latency", type=float, default=8.93)
    ap.add_argument("--tiles", default="A,CFM,B")
    ap.add_argument("--ai-cap", type=int, default=3)
    a = ap.parse_args()
    mirror = Path(a.mirror)
    t0 = time.time()
    _TAPE = gg.load_tape(mirror, Path(a.tier_a))
    print(f"tape {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(_TAPE.t0))} .. "
          f"{time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(_TAPE.end))} ({time.time()-t0:.0f}s)", flush=True)
    out: dict[str, Any] = {"schema": "hypothesis_tiles_entry_exit_study_v1",
                           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "fill_model": fm.FILL_MODEL_VERSION, "exit_latency_sec": fm.EXIT_LATENCY_SEC,
                           "fees": "ZERO (BITFINEX_ZERO)",
                           "gave_back_definition": f"MFE at the executable side >= +{GAVE_BACK_MFE_BP} bp, closed < 0",
                           "tape_span_utc": [time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(_TAPE.t0)),
                                             time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(_TAPE.end))],
                           "tiles": {}}
    tiles = a.tiles.split(",")
    target = Path(a.out)

    def flush():
        target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

    if "A" in tiles:
        sig = ai_signals(mirror, _TAPE, "AI_NO_TRADE_SCORE_LED", fade=False)
        base = {"offset_pct": 0.15, "chase_id": "w234_s25_i180", "ttl_sec": 3600}
        out["tiles"][f"TILE_A_cap{a.ai_cap}"] = run_tile("TILE_A", sig, base, a.ai_latency, a.ai_cap, 60)
        flush()
        print("A done", round(time.time() - t0), flush=True)
    if "CFM" in tiles:
        sig = ai_signals(mirror, _TAPE, "AI_COMMITTED", fade=True)
        base = {"offset_pct": 0.10, "chase_id": "no_chase", "ttl_sec": 1800}
        out["tiles"][f"CFM_cap{a.ai_cap}"] = run_tile("CFM", sig, base, a.ai_latency, a.ai_cap, 90)
        flush()
        print("CFM done", round(time.time() - t0), flush=True)
    if "B" in tiles:
        sig = xv_signals(mirror, _TAPE)
        for lat in (a.xv_latency, 3.0):
            for cap in (1, 3):
                for s in sig:
                    s.res.clear()
                out["tiles"][f"TILE_B_cap{cap}_lat{lat:g}"] = run_tile("TILE_B", sig, None, lat, cap, 60,
                                                                        session_gate=True, adaptive_entry=False)
                flush()
                print("B done", cap, lat, round(time.time() - t0), flush=True)
    flush()
    print("wrote", a.out, f"{time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
