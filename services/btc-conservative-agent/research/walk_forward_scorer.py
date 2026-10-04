"""Walk-forward variant scorer over the per-signal research table (PR-D; PAPER / analysis only).

Every variant = side rule (which signals, which side) x entry (taker / maker) x exit (time, stop, break-even,
trailing, early cut; first trigger wins). Each (signal, variant) is simulated on the bot's own 1 s tape with the
repo's REALISTIC_V1 fill model (``research/fill_model.py``) and Bitfinex fee profile (``bitfinex_cost_profile``):

* taker entry   = ``fill_model.taker_fill_rows`` (opposite BBO of the first fresh quote at signal + latency, size walk);
* maker entry   = ``fill_model.maker_fill_rows`` (fills only on trade-through / at-limit volume beyond the queue);
* exits         = executable-side marks (bid for LONG, ask for SHORT); stops / trails / cuts / time exits are
                  marketable and booked with ``fill_model.realistic_exit_margin`` (worse of trigger and
                  trigger + EXIT_LATENCY_SEC);
* fees          = ``fill_model.fee_fields`` maker/taker rates (BITFINEX_ZERO today, but applied explicitly).

Per variant it reports bp per trade, a 1 h-cluster bootstrap 95% CI (and a day-cluster CI), hit rate, fill rate,
per-UTC-day means and stability, and a day-by-day expanding-window walk-forward in which, on each test day, the
variant chosen on the earlier days alone is scored out of sample. Signals are scored independently: there is no
concurrency cap, so overlapping trades are correlated - hence the clustered CIs.

Usage::

    python -m research.walk_forward_scorer --table <dir>/per_signal_research_table.csv.gz --data-dir <runtime dir> \
        --out-dir <dir>
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

try:
    from research import fill_model as fm
    from research import signal_research_table as srt
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from research import fill_model as fm
    from research import signal_research_table as srt

SCORER_SCHEMA = "walk_forward_variant_scores_v1"
TRADE_SCHEMA = "walk_forward_variant_trade_v1"
NOTIONAL_USD = 25.0  # $0.25 margin @100x paper size
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261004
MIN_TRAIN_TRADES = 30


# ------------------------------------------------------------------ variants

@dataclass(frozen=True)
class SideRule:
    name: str
    description: str
    kinds: tuple[str, ...]
    pick: Callable[[Mapping[str, Any]], str | None] = field(compare=False, repr=False)


@dataclass(frozen=True)
class Entry:
    name: str
    liquidity: str                  # TAKER | MAKER
    ttl_sec: int = 0                # maker only
    offset_bp: float = 0.0          # maker only: limit this far better than the decision-time reference (0 = touch)


@dataclass(frozen=True)
class Exit:
    name: str
    time_sec: int
    stop_bp: float | None = None
    be_arm_bp: float | None = None
    be_lock_bp: float | None = None
    trail_arm_bp: float | None = None
    trail_bp: float | None = None
    early_cut_bp: float | None = None
    early_cut_window_sec: int | None = None
    early_cut_max_mfe_bp: float | None = None


def _b(v: Any) -> bool:
    return str(v).lower() in ("true", "1")


def _opp(side: str | None) -> str | None:
    return {"LONG": "SHORT", "SHORT": "LONG"}.get(side or "")


def _side_or_none(v: Any) -> str | None:
    s = str(v or "").upper()
    return s if s in ("LONG", "SHORT") else None


def _random_side(row: Mapping[str, Any]) -> str:
    h = hashlib.sha256(f"random-side:{row['signal_id']}".encode()).digest()
    return "LONG" if h[0] & 1 else "SHORT"


SIDE_RULES: tuple[SideRule, ...] = (
    SideRule("AI_COMMITTED_FADE", "AI explicit LONG/SHORT equal to the score-led side (Danish/committed-fade tiles); "
             "trade the opposite side", ("AI_CALL",),
             lambda r: _opp(_side_or_none(r.get("direction"))) if _b(r.get("ai_explicit_aligned")) else None),
    SideRule("AI_COMMITTED_FOLLOW", "Same calls, trade the AI's side", ("AI_CALL",),
             lambda r: _side_or_none(r.get("direction")) if _b(r.get("ai_explicit_aligned")) else None),
    SideRule("AI_COMMITTED_GAP30_FADE", "ai_committed (gap >= 30) only; trade the opposite side", ("AI_CALL",),
             lambda r: _opp(_side_or_none(r.get("direction"))) if _b(r.get("ai_committed")) else None),
    SideRule("NOTRADE_SCORE_FOLLOW", "AI said NO_TRADE; trade the score-led side (ties refuse) - NO_TRADE follow tile",
             ("AI_CALL",),
             lambda r: _side_or_none(r.get("score_led_side")) if _b(r.get("explicit_abstain")) else None),
    SideRule("ALL_SCORE_LED_FOLLOW", "Every non-tie AI call; trade the score-led side", ("AI_CALL",),
             lambda r: _side_or_none(r.get("score_led_side"))),
    SideRule("AI_RANDOM_SIDE", "Every AI call; deterministic hash-random side (ablation baseline)", ("AI_CALL",),
             _random_side),
    SideRule("XVENUE_FOLLOW", "Cross-venue lead/premium/session-follow trigger; trade the trigger side",
             ("XVENUE_SIGNAL",), lambda r: _side_or_none(r.get("direction"))),
    SideRule("TILE_AS_RECORDED", "Tile entry candidates (filled + expired) on their recorded side, re-simulated",
             ("TILE_ENTRY",), lambda r: _side_or_none(r.get("direction"))),
)

ENTRIES: tuple[Entry, ...] = (
    Entry("TAKER", "TAKER"),
    Entry("MAKER_TOUCH_5M", "MAKER", ttl_sec=300, offset_bp=0.0),
    Entry("MAKER_10BP_30M", "MAKER", ttl_sec=1800, offset_bp=10.0),
)

EXITS: tuple[Exit, ...] = (
    Exit("TIME_15M", 900),
    Exit("TIME_60M", 3600),
    Exit("TIME_90M", 5400),
    Exit("STOP40_TIME90", 5400, stop_bp=40.0),
    Exit("STOP40_BE20TO5_TIME90", 5400, stop_bp=40.0, be_arm_bp=20.0, be_lock_bp=5.0),
    Exit("STOP40_BE_EARLYCUT_TIME90", 5400, stop_bp=40.0, be_arm_bp=20.0, be_lock_bp=5.0,
         early_cut_bp=12.0, early_cut_window_sec=300, early_cut_max_mfe_bp=2.0),
    Exit("STOP40_TRAIL15_10_TIME90", 5400, stop_bp=40.0, trail_arm_bp=15.0, trail_bp=10.0),
    Exit("STOP20_TIME30", 1800, stop_bp=20.0),
)


def variant_id(rule: SideRule, entry: Entry, exit_: Exit) -> str:
    return f"{rule.name}|{entry.name}|{exit_.name}"


# ------------------------------------------------------------------ simulation

def _fee_bp(liquidity: str) -> float:
    f = fm.fee_fields(NOTIONAL_USD, maker=liquidity == "MAKER")
    return float(f["maker_fee_rate"] if liquidity == "MAKER" else f["taker_fee_rate"]) * 1e4


def simulate_entry(tape: srt.Tape, rows: Mapping[int, Mapping[str, Any]], row: Mapping[str, Any], side: str,
                   entry: Entry, latency_sec: float) -> dict[str, Any]:
    """REALISTIC_V1 entry for one signal. Returns ``status`` FILLED / NO_FILL / NO_FRESH_BBO with fill fields."""
    ts = float(row["signal_ts"])
    price = float(row["anchor_mid"]) if row.get("anchor_mid") not in (None, "") else math.nan
    qty = NOTIONAL_USD / price if price and math.isfinite(price) else 0.0
    if not qty:
        return {"status": "NO_ANCHOR"}
    if entry.liquidity == "TAKER":
        return fm.taker_fill_rows(rows, side=side, qty=qty, decision_ts=ts, latency_sec=latency_sec)
    arrival = tape.first_valid_at_or_after(ts + latency_sec)
    if arrival is None:
        return {"status": "NO_FRESH_BBO"}
    sign = 1.0 if side == "LONG" else -1.0
    touch = tape.bid[arrival] if sign > 0 else tape.ask[arrival]
    if entry.offset_bp > 0:
        ref = tape.last[arrival] if math.isfinite(tape.last[arrival]) else price
        raw = ref * (1.0 - sign * entry.offset_bp / 1e4)
        raw = min(raw, touch) if sign > 0 else max(raw, touch)  # never past the touch (not marketable)
        limit = float(fm.round_limit_passive(raw, side))
    else:
        limit = float(touch)
    start = arrival + tape.t0
    end = min(start + entry.ttl_sec, tape.t1 + 1)
    # fast path: no aggressor print at/through the limit and not marketable -> REALISTIC_V1 cannot fill
    a, b = arrival, end - tape.t0
    opp = tape.ask[a] if sign > 0 else tape.bid[a]
    if sign * (limit - opp) < -float(fm.price_tol(limit)):
        vw = tape.sell_vwap[a:b] if sign > 0 else tape.buy_vwap[a:b]
        q = tape.sell_qty[a:b] if sign > 0 else tape.buy_qty[a:b]
        with np.errstate(invalid="ignore"):
            hit = (q > 0) & np.isfinite(vw) & (sign * (limit - vw) >= -float(fm.price_tol(limit)))
        if not hit.any():
            return {"status": "NO_FILL", "limit_price": limit}
    res = fm.maker_fill_rows(rows, side=side, qty=qty, schedule=[{"start_ts": start, "end_ts": end,
                                                                  "limit_price": limit}])
    res["limit_price"] = limit
    if res.get("status") == "PARTIAL":  # a partial maker fill is scored on the filled part
        res["status"] = "FILLED"
    if res.get("status") == "FILLED" and res.get("basis") != "MARKETABLE_AT_PLACEMENT":
        res["fill_ts"] = int(res.get("completed_ts") or res["fill_ts"])  # position live once fully filled
    return res


def simulate_exit(tape: srt.Tape, side: str, fill_ts: int, entry_price: float, exit_: Exit) -> dict[str, Any]:
    """First-trigger-wins exit on the executable-side margin path (bp). Returns gross bp, reason, hold."""
    sign = 1.0 if side == "LONG" else -1.0
    i0 = int(fill_ts) - tape.t0
    i1 = i0 + exit_.time_sec
    if i1 > len(tape.bid_ff) - 1:
        i1_cap = len(tape.bid_ff) - 1
    else:
        i1_cap = i1
    marks = (tape.bid_ff if sign > 0 else tape.ask_ff)[i0:i1_cap + 1]
    cur = sign * (marks - entry_price) / entry_price * 1e4
    cur = np.where(np.isfinite(cur), cur, np.nan)
    # carry the last mark through quote holes (stale marks never trigger new exits by themselves)
    if np.isnan(cur).any():
        idx = np.where(np.isfinite(cur), np.arange(len(cur)), 0)
        cur = cur[np.maximum.accumulate(idx)]
    age = np.arange(len(cur), dtype=float)
    n = len(cur)
    cand: list[tuple[int, str]] = []
    mfe = np.maximum.accumulate(np.maximum(cur, 0.0))
    if exit_.stop_bp is not None:
        hit = np.flatnonzero(cur[1:] <= -exit_.stop_bp)
        if hit.size:
            cand.append((int(hit[0]) + 1, "HARD_STOP"))
    if exit_.be_arm_bp is not None:
        armed = np.flatnonzero(cur >= exit_.be_arm_bp)
        if armed.size:
            j = int(armed[0])
            hit = np.flatnonzero(cur[j + 1:] <= exit_.be_lock_bp)
            if hit.size:
                cand.append((j + 1 + int(hit[0]), "BREAKEVEN_LOCK"))
    if exit_.trail_bp is not None:
        armed = np.flatnonzero(cur >= exit_.trail_arm_bp)
        if armed.size:
            j = int(armed[0])
            peak = np.maximum.accumulate(cur[j:])
            hit = np.flatnonzero(cur[j + 1:] <= peak[1:] - exit_.trail_bp)
            if hit.size:
                cand.append((j + 1 + int(hit[0]), "ATR_TRAIL"))
    if exit_.early_cut_bp is not None:
        w = min(n - 1, int(exit_.early_cut_window_sec or 0))
        seg = cur[1:w + 1]
        hit = np.flatnonzero((seg <= -exit_.early_cut_bp) & (mfe[1:w + 1] <= exit_.early_cut_max_mfe_bp))
        if hit.size:
            cand.append((int(hit[0]) + 1, "EARLY_CUT"))
    order = {"HARD_STOP": 0, "BREAKEVEN_LOCK": 1, "ATR_TRAIL": 2, "EARLY_CUT": 3}
    if cand:
        idx, reason = min(cand, key=lambda c: (c[0], order[c[1]]))
    else:
        if i1 > len(tape.bid_ff) - 1:
            return {"status": "CENSORED_TAPE_END"}
        idx, reason = n - 1, "TIME_EXIT"
    booked, bidx = fm.realistic_exit_margin(cur, age, idx, reason, latency_sec=fm.EXIT_LATENCY_SEC)
    return {"status": "CLOSED", "gross_bp": float(booked), "exit_reason": reason, "hold_sec": int(bidx),
            "mfe_bp": float(mfe[bidx]), "mae_bp": float(min(0.0, np.nanmin(cur[:bidx + 1])))}


def simulate(table: Sequence[Mapping[str, Any]], tape: srt.Tape, *, latency_sec: float,
             side_rules: Sequence[SideRule] = SIDE_RULES, entries: Sequence[Entry] = ENTRIES,
             exits: Sequence[Exit] = EXITS) -> list[dict[str, Any]]:
    """One record per (signal, variant) that selected the signal: fill status and, when closed, net bp."""
    rows = tape.rows()
    entry_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    exit_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for row in table:
        if row.get("tape_status") != "OK":
            continue
        for rule in side_rules:
            if row.get("signal_kind") not in rule.kinds:
                continue
            side = rule.pick(row)
            if side is None:
                continue
            for entry in entries:
                ek = (row["signal_id"], side, entry.name)
                if ek not in entry_cache:
                    entry_cache[ek] = simulate_entry(tape, rows, row, side, entry, latency_sec)
                ent = entry_cache[ek]
                for exit_ in exits:
                    rec = {"schema": TRADE_SCHEMA, "variant_id": variant_id(rule, entry, exit_),
                           "side_rule": rule.name, "entry": entry.name, "exit": exit_.name,
                           "signal_id": row["signal_id"], "signal_kind": row["signal_kind"], "side": side,
                           "signal_ts": float(row["signal_ts"]), "utc_day": row["utc_day"],
                           "session": row.get("session"), "entry_status": ent.get("status")}
                    if ent.get("status") != "FILLED":
                        out.append(rec)
                        continue
                    xk = (row["signal_id"], side, entry.name, exit_.name)
                    if xk not in exit_cache:
                        exit_cache[xk] = simulate_exit(tape, side, int(ent["fill_ts"]), float(ent["fill_price"]),
                                                       exit_)
                    ex = exit_cache[xk]
                    fee = _fee_bp(str(ent.get("liquidity") or entry.liquidity)) + _fee_bp("TAKER")
                    rec.update({"liquidity": ent.get("liquidity"), "fill_basis": ent.get("basis"),
                                "fill_ts": int(ent["fill_ts"]), "fill_price": float(ent["fill_price"]),
                                "fill_delay_sec": round(int(ent["fill_ts"]) - float(row["signal_ts"]), 3),
                                "exit_status": ex.get("status")})
                    if ex.get("status") == "CLOSED":
                        rec.update({"gross_bp": round(ex["gross_bp"], 4), "fee_bp": round(fee, 4),
                                    "net_bp": round(ex["gross_bp"] - fee, 4), "exit_reason": ex["exit_reason"],
                                    "hold_sec": ex["hold_sec"], "mfe_bp": round(ex["mfe_bp"], 4),
                                    "mae_bp": round(ex["mae_bp"], 4)})
                    out.append(rec)
    return out


# ------------------------------------------------------------------ statistics

def cluster_bootstrap_ci(values: Sequence[float], clusters: Sequence[Any], *, resamples: int = BOOTSTRAP_RESAMPLES,
                         seed: int = BOOTSTRAP_SEED) -> tuple[float | None, float | None]:
    """95% percentile CI of the mean, resampling whole clusters (overlapping trades within a cluster correlate)."""
    if len(values) < 2:
        return None, None
    keys = sorted(set(clusters))
    if len(keys) < 2:
        return None, None
    pos = {k: i for i, k in enumerate(keys)}
    sums = np.zeros(len(keys))
    cnts = np.zeros(len(keys))
    for v, c in zip(values, clusters):
        sums[pos[c]] += v
        cnts[pos[c]] += 1
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(keys), size=(resamples, len(keys)))
    means = sums[pick].sum(axis=1) / np.maximum(cnts[pick].sum(axis=1), 1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return round(float(lo), 3), round(float(hi), 3)


def summarize(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    signals = len(trades)
    filled = [t for t in trades if t.get("entry_status") == "FILLED"]
    closed = [t for t in filled if t.get("exit_status") == "CLOSED"]
    out: dict[str, Any] = {"signals": signals, "fills": len(filled), "closed": len(closed),
                           "fill_rate": round(len(filled) / signals, 4) if signals else None}
    if not closed:
        return out | {"mean_bp": None}
    v = np.array([t["net_bp"] for t in closed], dtype=float)
    hours = [int(t["signal_ts"] // 3600) for t in closed]
    days = [t["utc_day"] for t in closed]
    lo, hi = cluster_bootstrap_ci(v, hours)
    dlo, dhi = cluster_bootstrap_ci(v, days) if len(set(days)) >= 3 else (None, None)
    per_day: dict[str, dict[str, Any]] = {}
    for d in sorted(set(days)):
        dv = v[[i for i, x in enumerate(days) if x == d]]
        per_day[d] = {"n": int(len(dv)), "mean_bp": round(float(dv.mean()), 3), "sum_bp": round(float(dv.sum()), 2)}
    dmeans = np.array([x["mean_bp"] for x in per_day.values()])
    pos_sum = sum(x["sum_bp"] for x in per_day.values() if x["sum_bp"] > 0)
    reasons: dict[str, int] = defaultdict(int)
    for t in closed:
        reasons[t["exit_reason"]] += 1
    return out | {
        "mean_bp": round(float(v.mean()), 3), "median_bp": round(float(np.median(v)), 3),
        "std_bp": round(float(v.std(ddof=1)), 3) if len(v) > 1 else None,
        "ci95_1h_cluster": [lo, hi], "ci95_day_cluster": [dlo, dhi],
        "hit_rate": round(float((v > 0).mean()), 4), "sum_bp": round(float(v.sum()), 2),
        "worst_bp": round(float(v.min()), 3), "best_bp": round(float(v.max()), 3),
        "median_hold_sec": int(np.median([t["hold_sec"] for t in closed])),
        "maker_share": round(sum(1 for t in closed if t.get("liquidity") == "MAKER") / len(closed), 4),
        "exit_reasons": dict(sorted(reasons.items())),
        "per_day": per_day, "days": len(per_day), "days_positive": int((dmeans > 0).sum()),
        "daily_mean_std_bp": round(float(dmeans.std(ddof=1)), 3) if len(dmeans) > 1 else None,
        "worst_day_mean_bp": round(float(dmeans.min()), 3),
        "max_day_share_of_profit": (round(max(x["sum_bp"] for x in per_day.values()) / pos_sum, 4)
                                    if pos_sum > 0 else None),
    }


def walk_forward(by_variant: Mapping[str, Sequence[Mapping[str, Any]]], days: Sequence[str], *,
                 groups: Mapping[str, Sequence[str]], min_train: int = MIN_TRAIN_TRADES) -> dict[str, Any]:
    """Expanding-window day walk-forward. For each group of candidate variants and each test day d (from the
    second day on), pick the variant with the best mean net bp over days < d (>= ``min_train`` closed trades) and
    score its trades on day d only. Returns the chosen variants and the pooled out-of-sample summary."""
    closed_by_v_day: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for vid, trades in by_variant.items():
        dd: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for t in trades:
            if t.get("exit_status") == "CLOSED":
                dd[t["utc_day"]].append(t)
        closed_by_v_day[vid] = dd
    result: dict[str, Any] = {}
    for gname, members in groups.items():
        steps, oos = [], []
        for k, test_day in enumerate(days):
            if k == 0:
                continue
            train = days[:k]
            best, best_mean, best_n = None, -math.inf, 0
            for vid in members:
                tv = [t["net_bp"] for d in train for t in closed_by_v_day.get(vid, {}).get(d, [])]
                if len(tv) >= min_train and float(np.mean(tv)) > best_mean:
                    best, best_mean, best_n = vid, float(np.mean(tv)), len(tv)
            if best is None:
                steps.append({"test_day": test_day, "chosen": None, "reason": "NO_VARIANT_WITH_MIN_TRAIN_TRADES"})
                continue
            test = closed_by_v_day[best].get(test_day, [])
            oos.extend(test)
            steps.append({"test_day": test_day, "chosen": best, "train_mean_bp": round(best_mean, 3),
                          "train_n": best_n, "test_n": len(test),
                          "test_mean_bp": round(float(np.mean([t["net_bp"] for t in test])), 3) if test else None})
        s = summarize([t | {"entry_status": "FILLED"} for t in oos]) if oos else {"closed": 0}
        result[gname] = {"steps": steps, "oos": {k: s.get(k) for k in ("closed", "mean_bp", "median_bp",
                                                                       "ci95_1h_cluster", "hit_rate", "per_day")}}
    return result


def score(trades: Sequence[Mapping[str, Any]], *, min_train: int = MIN_TRAIN_TRADES) -> dict[str, Any]:
    by_variant: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for t in trades:
        by_variant[t["variant_id"]].append(t)
    variants = {vid: summarize(ts) | {"side_rule": ts[0]["side_rule"], "entry": ts[0]["entry"], "exit": ts[0]["exit"]}
                for vid, ts in sorted(by_variant.items())}
    days = sorted({t["utc_day"] for t in trades if t.get("exit_status") == "CLOSED"})
    groups: dict[str, list[str]] = defaultdict(list)
    for vid, v in variants.items():
        groups[f"rule:{v['side_rule']}"].append(vid)
        if v["side_rule"] not in ("AI_RANDOM_SIDE",):
            groups["ALL_NON_RANDOM_VARIANTS"].append(vid)
    wf = walk_forward(by_variant, days, groups=dict(sorted(groups.items())), min_train=min_train)
    return {"variants": variants, "days": days, "walk_forward": wf}


# ------------------------------------------------------------------ report

def _fmt(x: Any, nd: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:+.{nd}f}"
    return str(x)


def markdown_report(res: Mapping[str, Any], top: int = 25) -> str:
    lines = [f"# Walk-forward variant scores ({res['generated_utc']})", "",
             f"Fill model {res['fill_model']['fill_model']} ({res['fill_model']['fill_model_fingerprint']}), fees "
             f"{res['fill_model']['fee_profile_id']}, decision latency {res['latency']['latency_sec']} s "
             f"({res['latency']['source']}). UTC days: {', '.join(res['days'])}. Signals scored independently "
             "(no concurrency cap); CIs are 1 h-cluster bootstrap.", "",
             "## Walk-forward (variant chosen on earlier days only, scored on the next day)", "",
             "| Group | OOS trades | OOS mean bp | 95% CI (1h) | Hit | Chosen per test day |",
             "|---|---:|---:|---|---:|---|"]
    for g, w in res["walk_forward"].items():
        o = w["oos"]
        ci = o.get("ci95_1h_cluster") or [None, None]
        chosen = "; ".join(f"{s['test_day'][5:]}: {s['chosen'] or 'none'}"
                           + (f" ({_fmt(s.get('test_mean_bp'))})" if s.get("chosen") else "") for s in w["steps"])
        hit = f"{o['hit_rate']:.3f}" if o.get("hit_rate") is not None else "-"
        lines.append(f"| {g} | {o.get('closed') or 0} | {_fmt(o.get('mean_bp'))} | {_fmt(ci[0])} .. {_fmt(ci[1])} | "
                     f"{hit} | {chosen} |")
    ranked = sorted(((vid, v) for vid, v in res["variants"].items() if v.get("mean_bp") is not None
                     and (v.get("closed") or 0) >= MIN_TRAIN_TRADES), key=lambda kv: -kv[1]["mean_bp"])
    lines += ["", f"## In-sample variants, top {top} by mean (>= {MIN_TRAIN_TRADES} trades; NOT out-of-sample)", "",
              "| Variant | Signals | Fill rate | Trades | Mean bp | 95% CI (1h) | Hit | Days +/total | Worst day |",
              "|---|---:|---:|---:|---:|---|---:|---|---:|"]
    for vid, v in ranked[:top]:
        ci = v["ci95_1h_cluster"]
        lines.append(f"| {vid} | {v['signals']} | {v['fill_rate']:.2f} | {v['closed']} | {_fmt(v['mean_bp'])} | "
                     f"{_fmt(ci[0])} .. {_fmt(ci[1])} | {v['hit_rate']:.3f} | {v['days_positive']}/{v['days']} | "
                     f"{_fmt(v['worst_day_mean_bp'])} |")
    lines += ["", f"Variants scored: {len(res['variants'])}; with >= {MIN_TRAIN_TRADES} trades: {len(ranked)}. "
              "In-sample rankings over this many variants are subject to multiple testing; judge only the "
              "walk-forward rows and pre-registered variants."]
    return "\n".join(lines) + "\n"


def run(table_path: Path, tape_dir: Path, out_dir: Path, *, latency: Mapping[str, Any] | None = None,
        kinds: Iterable[str] | None = None, since_utc: str | None = None) -> dict[str, Any]:
    table = srt.read_table(table_path)
    if kinds:
        ks = set(kinds)
        table = [r for r in table if r.get("signal_kind") in ks]
    if since_utc:
        cut = srt._iso_ts(since_utc) or 0.0
        table = [r for r in table if float(r["signal_ts"]) >= cut]
    tape = srt.load_tape(tape_dir)
    lat = dict(latency or fm.measure_decision_latency(Path(tape_dir) / "v3" / "ledgers"))
    trades = simulate(table, tape, latency_sec=float(lat["latency_sec"]))
    res = score(trades) | {"schema": SCORER_SCHEMA, "generated_utc": datetime.now(timezone.utc).isoformat(),
                           "table": str(table_path), "table_rows": len(table), "latency": lat,
                           "fill_model": fm.fill_model_declaration(), "notional_usd": NOTIONAL_USD,
                           "variant_grid": {"side_rules": [{"name": r.name, "description": r.description,
                                                            "kinds": list(r.kinds)} for r in SIDE_RULES],
                                            "entries": [asdict(e) for e in ENTRIES],
                                            "exits": [asdict(x) for x in EXITS]},
                           "min_train_trades": MIN_TRAIN_TRADES, "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                           "paper_only": True}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "walk_forward_scores.json").write_text(json.dumps(res, indent=1, default=str))
    (out_dir / "walk_forward_scores.md").write_text(markdown_report(res))
    with gzip.open(out_dir / "walk_forward_trades.jsonl.gz", "wt", encoding="utf-8") as fh:
        for t in trades:
            fh.write(json.dumps(t, separators=(",", ":")) + "\n")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--table", required=True, type=Path)
    ap.add_argument("--data-dir", required=True, type=Path, help="Directory with the 1 s tape (and v3/ledgers)")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--latency-sec", type=float, help="Override the measured decision->order latency")
    ap.add_argument("--kinds", nargs="*", help="Only these signal kinds (AI_CALL XVENUE_SIGNAL TILE_ENTRY)")
    ap.add_argument("--since-utc")
    args = ap.parse_args(argv)
    lat = {"source": "CLI_OVERRIDE", "latency_sec": args.latency_sec} if args.latency_sec is not None else None
    res = run(args.table, args.data_dir, args.out_dir, latency=lat, kinds=args.kinds, since_utc=args.since_utc)
    print(markdown_report(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
