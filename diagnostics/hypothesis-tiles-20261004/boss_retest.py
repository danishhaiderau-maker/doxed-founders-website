"""Coordinator re-tests for H11 / CFM / H9 / H10 (research only; REALISTIC_V1, zero fees, harness = exit_study.py).

Every number is per tile on that tile's own signal cohort, entry, capacity and latency:

1. give-back counts: trades whose executable-side MFE reached +10/+20/+25 bp and closed negative, and the $ lost;
2. LATE-armed protections (break-even floor +2..+5 bp armed after +20/+25/+30 bp, ATR trail armed after 1.5/2.0 ATR,
   giveback armed after +20 bp) and the pre-specified composite (late BE +20/+5, ATR trail 1.5 armed at 2.0 ATR,
   time backstop, hard stop);
3. CONDITIONAL early cuts (-10/-12 bp within 3/5 min only while MFE never exceeded +2 bp) with trade-for-trade
   cost/benefit;
4. confirm-then-chase entry for H11/CFM (0.10% limit; 2/3/4 bp move our way before the fill -> taker, 5 bp cap);
5. H9 same-direction concurrent exposure at caps 3/5/10;
6. 40 bp stop hit rate and EV by ATR and realized-vol tercile; ATR-scaled hard stops (2.5/3/4 ATR clamped 25-60 bp);
7. H11 Asia+EU vs all sessions;
8. CI widths of every paired difference (what the sample can and cannot rule out).

Paired differences are per filled signal, same fill, uncapped (variant bp - time-exit bp), 1 h cluster bootstrap.
"Test days" = every UTC day after the first (the nested walk-forward scoring days). Capped results use the
tile's own capacity with pending + open <= cap.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exit_study as es  # noqa: E402
from exit_study import gg, fm  # noqa: E402

NOTIONAL = es.NOTIONAL
GIVEBACK_LEVELS = (10, 20, 25)
CANONICAL_COMPOSITE = "COMPOSITE_LATE_BE20+5_TRAIL1.5@2ATR"
CANONICAL_BE = "LATE_BE_ARM20_FLOOR5"
CANONICAL_CUT = "COND_CUT-12_5M_MFE2"
ENTRY_KIND: dict[tuple[float, str], str] = {}


def boss_exits(T: float) -> dict[str, dict[str, Any]]:
    v: dict[str, dict[str, Any]] = {"BASE": {"time_min": T}}
    for arm in (20, 25, 30):
        for floor in (2, 3, 5):
            v[f"LATE_BE_ARM{arm}_FLOOR{floor}"] = {"time_min": T, "be_arm_bp": arm, "be_floor_bp": floor}
    for k in (1.0, 1.5, 2.0):
        for arm in (1.5, 2.0):
            v[f"LATE_TRAIL_{k}ATR_ARM{arm}ATR"] = {"time_min": T, "trail_k": k, "trail_arm_atr": arm}
    for keep in (0.5, 0.7, 0.8):
        v[f"LATE_GIVEBACK_KEEP{int(keep * 100)}_ARM20"] = {"time_min": T, "gb_keep": keep, "gb_arm_bp": 20}
    v["LATE_LADDER_20>5_30>15_45>30"] = {"time_min": T, "ladder": [(20, 5), (30, 15), (45, 30)]}
    for cut in (10, 12):
        for win in (180, 300):
            v[f"COND_CUT-{cut}_{win // 60}M_MFE2"] = {"time_min": T, "thesis_bp": -cut, "thesis_sec": win,
                                                      "thesis_max_mfe": 2.0}
    for k in (2.5, 3.0, 4.0):
        v[f"ATR_HARD_STOP_{k:g}ATR_25_60"] = {"time_min": T, "hard_atr_k": k}
    v["ATR_STOP_1.5"] = {"time_min": T, "atr_stop_k": 1.5}
    comp = {"be_arm_bp": 20, "be_floor_bp": 5, "trail_k": 1.5, "trail_arm_atr": 2.0}
    v[CANONICAL_COMPOSITE] = {"time_min": T, **comp}
    v["COMPOSITE_LATE_BE20+3_TRAIL1.5@2ATR"] = {"time_min": T, **comp, "be_floor_bp": 3}
    v["COMPOSITE_LATE_BE25+3_TRAIL1.5@1.5ATR"] = {"time_min": T, **comp, "be_arm_bp": 25, "be_floor_bp": 3,
                                                  "trail_arm_atr": 1.5}
    v[CANONICAL_COMPOSITE + "+COND_CUT12_5M"] = {"time_min": T, **comp, "thesis_bp": -12, "thesis_sec": 300,
                                                 "thesis_max_mfe": 2.0}
    v["LATE_BE_ARM20_FLOOR5+COND_CUT12_5M"] = {"time_min": T, "be_arm_bp": 20, "be_floor_bp": 5,
                                               "thesis_bp": -12, "thesis_sec": 300, "thesis_max_mfe": 2.0}
    return v


FAMILIES = {
    "LATE_BE": lambda x: x.startswith("LATE_BE_ARM") and "+" not in x,
    "LATE_TRAIL": lambda x: x.startswith("LATE_TRAIL"),
    "LATE_GIVEBACK": lambda x: x.startswith("LATE_GIVEBACK"),
    "COND_CUT": lambda x: x.startswith("COND_CUT"),
    "ATR_HARD_STOP": lambda x: x.startswith("ATR_HARD_STOP"),
    "COMPOSITE": lambda x: x.startswith("COMPOSITE"),
}

# ------------------------------------------------------------------ confirm-then-chase entry

_orig_evaluate = es.evaluate


def evaluate(sig, eid, espec, exits, latency):
    if espec.get("kind") != "CONFIRM":
        return _orig_evaluate(sig, eid, espec, exits, latency)
    if eid in sig.res:
        return sig.res[eid]
    tape = es._TAPE
    ttl = int(espec["ttl"])
    lat = gg.latency_steps(sig.ts, latency)
    need = ttl + gg.PATH_END_SEC + 5 + lat + fm.TAKER_MAX_WAIT_SEC + 2
    out = None
    if tape.t0 <= sig.start and sig.start + need <= tape.end:
        w = tape.window(sig.start, sig.start + need)
        maker = gg.simulate_fill({"offset_pct": espec["offset"], "chase_id": "no_chase", "ttl_sec": ttl},
                                 sig.direction, sig.price, w, lat)[gg.HEADLINE_WORLD]
        sign = 1.0 if sig.direction == "LONG" else -1.0
        mid = (w["bid"] + w["ask"]) / 2
        confirm_px = sig.price * (1 + sign * espec["confirm_bp"] / 1e4)
        end = min(len(mid), lat + ttl)
        with np.errstate(invalid="ignore"):
            hit = sign * (mid[lat:end] - confirm_px) >= 0
        j = lat + int(np.argmax(hit)) if hit.any() else None
        fill, kind = None, "UNFILLED"
        if maker is not None and (j is None or maker[0] <= j):
            fill, kind = maker, "LIMIT"
        elif j is not None:
            t = gg.realistic_taker_fill(sig.direction, w, j + 1)
            if t is not None and t[0] <= j + 1 + fm.TAKER_MAX_WAIT_SEC \
                    and sign * (t[1] - confirm_px) / confirm_px * 1e4 <= espec["cap_bp"]:
                fill, kind = t, "CHASE_MARKET"
            else:
                kind = "CAP_SKIP"
        ENTRY_KIND[(sig.ts, eid)] = kind
        if fill is None:
            out = (None, ttl, 0.0, 0, None, {})
        else:
            f_idx, f_px, frac, mk = fill
            path = gg.prepare_path(sig.direction, f_px, f_idx, w)
            if path is not None:
                out = (sig.start + f_idx, ttl, frac, mk, None,
                       {x: es.replay(path, xv, sig.atr) for x, xv in exits.items()})
    sig.res[eid] = out
    return out


es.evaluate = evaluate

# ------------------------------------------------------------------ helpers


def r2(x):
    return None if x is None else round(float(x), 2)


def ci_mean(vals, ts):
    if not vals:
        return {"n": 0}
    lo, hi, c = es.cluster_ci(np.asarray(vals, float), np.asarray(ts, float))
    return {"n": len(vals), "mean_bp": r2(np.mean(vals)), "ci95_1h_bp": [lo, hi], "n_eff_clusters_1h": c,
            "ci_width_bp": r2(hi - lo) if lo is not None else None}


def filled(signals, eid, espec, exits, latency):
    rows = []
    for s in signals:
        r = es.evaluate(s, eid, espec, exits, latency)
        if r and r[0] is not None and r[5]:
            rows.append((s, r))
    return rows


def paired(rows, xid, base="BASE", days=None):
    vals, ts = [], []
    better = worse = 0
    for s, r in rows:
        if days is not None and s.day not in days:
            continue
        d = (r[5][xid][0] - r[5][base][0]) * r[2]
        vals.append(d)
        ts.append(s.ts)
        better += d > 1e-9
        worse += d < -1e-9
    out = ci_mean(vals, ts)
    out |= {"better": int(better), "worse": int(worse), "identical": len(vals) - int(better) - int(worse)}
    lo, hi = out.get("ci95_1h_bp", [None, None])
    out["significantly_worse"] = bool(hi is not None and hi < 0)
    out["significantly_better"] = bool(lo is not None and lo > 0)
    return out


def simulate_detail(signals, eid, espec, xid, exits, *, cap, latency, allow=None):
    """es.simulate twin that also returns fill/exit times, side and ATR for exposure and tercile tables."""
    busy, trades, eligible = [], [], 0
    for s in signals:
        if allow is not None and not allow(s):
            continue
        eligible += 1
        busy = [b for b in busy if b > s.ts]
        if len(busy) >= cap:
            continue
        r = es.evaluate(s, eid, espec, exits, latency)
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
                       "reason": reason, "side": s.direction, "maker": maker, "markout": mk,
                       "fill_ts": fill_ts, "exit_ts": fill_ts + age, "atr": s.atr, "vol_pct": s.vol_pct})
    return trades, eligible


def giveback_table(trades):
    out = {}
    for lvl in GIVEBACK_LEVELS:
        hit = [t for t in trades if t["mfe"] >= lvl]
        neg = [t for t in hit if t["bp"] < 0]
        out[f"mfe_ge_{lvl}bp"] = {
            "reached": len(hit),
            "closed_negative": len(neg),
            "closed_negative_pct_of_reached": r2(len(neg) / len(hit) * 100) if hit else None,
            "usd_lost_on_close": round(sum(t["bp"] for t in neg) / 1e4 * NOTIONAL, 3),
            "usd_peak_to_close_given_back": round(sum(t["mfe"] - t["bp"] for t in neg) / 1e4 * NOTIONAL, 3),
            "mean_close_bp_of_those": r2(np.mean([t["bp"] for t in neg])) if neg else None,
        }
    return out


def exposure(trades, side_notional=NOTIONAL):
    ev = []
    for t in trades:
        ev.append((t["fill_ts"], 1, t["side"]))
        ev.append((t["exit_ts"], -1, t["side"]))
    ev.sort(key=lambda e: (e[0], e[1]))
    cnt = {"LONG": 0, "SHORT": 0}
    max_same = 0
    tw, span, last = 0.0, 0.0, None
    at_entry = []
    for ts, d, side in ev:
        if last is not None:
            cur = max(cnt.values())
            if cur > 0:
                tw += cur * (ts - last)
                span += ts - last
        if d > 0:
            at_entry.append(cnt[side] + 1)
        cnt[side] += d
        max_same = max(max_same, max(cnt.values()))
        last = ts
    return {"max_same_direction_open": max_same, "max_same_direction_notional_usd": max_same * side_notional,
            "time_weighted_avg_same_direction_open_while_any": r2(tw / span) if span else None,
            "avg_same_direction_open_at_entry_incl_new": r2(np.mean(at_entry)) if at_entry else None,
            "avg_same_direction_notional_at_entry_usd": r2(np.mean(at_entry) * side_notional) if at_entry else None}


def tercile_table(rows, exits, key):
    vals = [getattr(s, key) for s, _ in rows if getattr(s, key) is not None]
    if len(vals) < 9:
        return None
    q1, q2 = np.percentile(vals, [100 / 3, 200 / 3])
    out = {"cuts": [r2(q1), r2(q2)] if key == "vol_pct" else [round(float(q1), 4), round(float(q2), 4)]}
    for name, lo, hi in (("LOW", -np.inf, q1), ("MID", q1, q2), ("HIGH", q2, np.inf)):
        grp = [(s, r) for s, r in rows if getattr(s, key) is not None and lo <= getattr(s, key) < hi]
        if name == "HIGH":
            grp = [(s, r) for s, r in rows if getattr(s, key) is not None and getattr(s, key) >= q2]
        row = {"n": len(grp)}
        if grp:
            for xid in ("BASE", "ATR_HARD_STOP_2.5ATR_25_60", "ATR_HARD_STOP_3ATR_25_60", "ATR_HARD_STOP_4ATR_25_60"):
                bps = [r[5][xid][0] * r[2] for _, r in grp]
                stops = sum(1 for _, r in grp if r[5][xid][1] == "PHYSICAL_HARD_STOP")
                row[xid] = {"ev_bp": r2(np.mean(bps)), "stop_hit_pct": r2(stops / len(grp) * 100)}
            row["mean_atr_bp"] = r2(np.mean([s.atr * gg.LEVERAGE for s, _ in grp]))
        out[name] = row
    return out


def cut_cost_benefit(rows, xid, base="BASE"):
    cut_n = recovered = saved = 0
    cost = benefit = 0.0
    for s, r in rows:
        o, b = r[5][xid], r[5][base]
        if o[1] != "THESIS_FAST_CUT":
            continue
        cut_n += 1
        if b[0] > o[0]:
            recovered += 1
            cost += (b[0] - o[0]) * r[2]
        else:
            saved += 1
            benefit += (o[0] - b[0]) * r[2]
    return {"cut_trades": cut_n, "would_have_recovered": recovered, "losses_saved": saved,
            "cost_bp_total": round(cost, 1), "benefit_bp_total": round(benefit, 1),
            "net_bp_total": round(benefit - cost, 1),
            "net_usd": round((benefit - cost) / 1e4 * NOTIONAL, 3)}


def family_nested(signals, eid, espec, exits, cap, latency, fam):
    sub = {x: exits[x] for x in exits if x == "BASE" or FAMILIES[fam](x)}
    res = es.nested(signals, {eid: espec}, sub, cap=cap, latency=latency, base_eid=eid, base_xid="BASE",
                    restrict="EXIT_ONLY")
    res.pop("per_design", None)
    return {"folds": res["folds"], "nested_oos": res["nested_oos"], "base_same_days": res["base_same_days"],
            "designs_tried": res["designs_tried"], "deflation": res["deflation"]}


def decide(paired_test, cut_paired_test):
    """Pre-registered coordinator decision rule (see module docstring)."""
    comp, be = paired_test[CANONICAL_COMPOSITE], paired_test[CANONICAL_BE]
    if comp.get("n") and not comp["significantly_worse"]:
        exit_choice = CANONICAL_COMPOSITE
    elif be.get("n") and not be["significantly_worse"]:
        exit_choice = CANONICAL_BE
    else:
        exit_choice = "BASE"
    cut = cut_paired_test
    cut_live = bool(cut.get("n") and cut.get("mean_bp") is not None and cut["mean_bp"] >= 0)
    return {"adopted_exit": exit_choice,
            "conditional_cut": "LIVE" if cut_live else "SHADOW_ONLY",
            "conditional_cut_reason": (f"test-day paired mean {cut.get('mean_bp')} bp >= 0 vs the adopted exit"
                                       if cut_live else
                                       f"test-day paired mean {cut.get('mean_bp')} bp < 0 vs the adopted exit (worse)")}


def run_tile(name, signals, eid, espec, T, cap, latency, *, confirm=False, h9=False, h11=False):
    exits = boss_exits(T)
    t0 = time.time()
    days = sorted({s.day for s in signals})
    test_days = set(days[1:])
    span = max(1e-9, (signals[-1].ts - signals[0].ts) / 86400) if signals else None
    rows = filled(signals, eid, espec, exits, latency)
    res: dict[str, Any] = {"tile": name, "signals": len(signals), "filled_uncapped": len(rows), "days": days,
                           "entry": eid, "time_exit_min": T, "cap": cap, "latency_sec": latency,
                           "exits": exits}
    base_tr, base_el = simulate_detail(signals, eid, espec, "BASE", exits, cap=cap, latency=latency)
    res["base_capped"] = es.stats(base_tr, base_el, span)
    res["item1_giveback_capped"] = giveback_table(base_tr)
    res["item1_giveback_uncapped"] = giveback_table(
        [{"mfe": r[5]["BASE"][3], "bp": r[5]["BASE"][0] * r[2]} for _, r in rows])
    comp_tr, _ = simulate_detail(signals, eid, espec, CANONICAL_COMPOSITE, exits, cap=cap, latency=latency)
    res["item1_giveback_capped_under_canonical_composite"] = giveback_table(comp_tr)
    res["paired_all_days"] = {x: paired(rows, x) for x in exits if x != "BASE"}
    res["paired_test_days"] = {x: paired(rows, x, days=test_days) for x in exits if x != "BASE"}
    res["capped_full_sample"] = {}
    for x in exits:
        tr, el = es.simulate(signals, eid, espec, x, exits, cap=cap, latency=latency)
        st = es.stats(tr, el, span, detail=False)
        st["test_days_fixed_rule"] = es.stats([t for t in tr if t["day"] in test_days],
                                              sum(1 for s in signals if s.day in test_days), detail=False)
        res["capped_full_sample"][x] = st
    res["family_nested"] = {f: family_nested(signals, eid, espec, exits, cap, latency, f) for f in FAMILIES}
    res["item3_cut_cost_benefit_vs_time_exit"] = {x: cut_cost_benefit(rows, x) for x in exits if x.startswith("COND_CUT")}
    res["item3_cut_on_top_of_canonical_composite"] = {
        "paired_all_days": paired(rows, CANONICAL_COMPOSITE + "+COND_CUT12_5M", base=CANONICAL_COMPOSITE),
        "paired_test_days": paired(rows, CANONICAL_COMPOSITE + "+COND_CUT12_5M", base=CANONICAL_COMPOSITE,
                                   days=test_days),
        "cost_benefit": cut_cost_benefit(rows, CANONICAL_COMPOSITE + "+COND_CUT12_5M", base=CANONICAL_COMPOSITE),
    }
    res["item3_cut_on_top_of_late_be"] = {
        "paired_test_days": paired(rows, "LATE_BE_ARM20_FLOOR5+COND_CUT12_5M", base=CANONICAL_BE, days=test_days),
        "cost_benefit": cut_cost_benefit(rows, "LATE_BE_ARM20_FLOOR5+COND_CUT12_5M", base=CANONICAL_BE),
    }
    res["item6_atr_tercile"] = tercile_table(rows, exits, "atr")
    res["item6_realized_vol_tercile"] = tercile_table(rows, exits, "vol_pct")
    dec = decide(res["paired_test_days"],
                 res["item3_cut_on_top_of_canonical_composite"]["paired_test_days"]
                 if res["paired_test_days"][CANONICAL_COMPOSITE].get("n")
                 and not res["paired_test_days"][CANONICAL_COMPOSITE]["significantly_worse"]
                 else res["item3_cut_on_top_of_late_be"]["paired_test_days"])
    res["decision"] = dec
    if h9:
        res["item5_exposure"] = {}
        for c in (3, 5, 10):
            tr, el = simulate_detail(signals, eid, espec, "BASE", exits, cap=c, latency=latency)
            st = es.stats(tr, el, span, detail=False)
            res["item5_exposure"][f"cap_{c}"] = {**exposure(tr), "n": st.get("n"), "ev_bp_per_fill": st.get("ev_bp_per_fill"),
                                                 "ci95_1h_bp": st.get("ci95_1h_bp"), "max_dd_usd": st.get("max_dd_usd"),
                                                 "net_usd": st.get("net_usd"), "trades_per_day": st.get("trades_per_day")}
            tr2, el2 = simulate_detail(signals, eid, espec, dec["adopted_exit"], exits, cap=c, latency=latency)
            st2 = es.stats(tr2, el2, span, detail=False)
            res["item5_exposure"][f"cap_{c}"]["adopted_exit"] = {k: st2.get(k) for k in
                                                                  ("n", "ev_bp_per_fill", "ci95_1h_bp", "max_dd_usd", "net_usd")}
    if h11:
        res["item7_sessions"] = session_test(signals, eid, espec, exits, cap, latency, dec["adopted_exit"], test_days, span)
    print(name, "done", round(time.time() - t0), "s", flush=True)
    return res


def session_test(signals, eid, espec, exits, cap, latency, xid, test_days, span):
    out = {}
    for x in ("BASE", xid):
        for label, allow in (("ALL", None), ("ASIA_EU", lambda s: s.session in ("ASIA", "EU"))):
            tr, el = es.simulate(signals, eid, espec, x, exits, cap=cap, latency=latency, allow=allow)
            st = es.stats(tr, el, span)
            test = [t for t in tr if t["day"] in test_days]
            el_t = sum(1 for s in signals if s.day in test_days and (allow is None or allow(s)))
            st["test_days_fixed_rule"] = es.stats(test, el_t, detail=False)
            st["test_days_bp_per_day"] = r2(sum(t["bp"] for t in test) / max(1, len(test_days)))
            out[f"{x} | {label}"] = st
        tr, _ = es.simulate(signals, eid, espec, x, exits, cap=cap, latency=latency)
        us = [t for t in tr if t["session"] == "US" and t["day"] in test_days]
        out[f"{x} | US_TRADES_TEST_DAYS"] = ci_mean([t["bp"] for t in us], [t["ts"] for t in us])
    gated = es.nested(signals, {eid: espec}, {"BASE": exits["BASE"], xid: exits[xid]}, cap=cap, latency=latency,
                      base_eid=eid, base_xid=xid, restrict="EXIT_ONLY", session_gate=True)
    gated.pop("per_design", None)
    out["nested_session_gate_selected_on_prior_days"] = {"folds": gated["folds"], "nested_oos": gated["nested_oos"],
                                                         "base_same_days": gated["base_same_days"]}
    a = out[f"{xid} | ASIA_EU"]["test_days_fixed_rule"]
    b = out[f"{xid} | ALL"]["test_days_fixed_rule"]
    us = out[f"{xid} | US_TRADES_TEST_DAYS"]
    not_worse = (a.get("ev_bp_per_fill") is not None and b.get("ev_bp_per_fill") is not None
                 and a["ev_bp_per_fill"] >= b["ev_bp_per_fill"])
    out["pre_registered_decision"] = {
        "rule": ("Adopt the Asia+EU filter if its test-day per-fill EV under the adopted exit is not below the "
                 "all-session rule (US trades not accretive)"),
        "asia_eu_test_ev_bp": a.get("ev_bp_per_fill"), "all_test_ev_bp": b.get("ev_bp_per_fill"),
        "us_test_trades": us,
        "adopt_asia_eu": bool(not_worse),
    }
    return out


def entry_comparison(signals, latency, T, cap):
    exits = boss_exits(T)
    entries = {
        "MAKER_0.10_no_chase_TTL1800": {"kind": "FIXED", "offset": 0.10, "chase": "no_chase", "ttl": 1800},
        "TAKER_AT_SIGNAL": {"kind": "TAKER"},
        **{f"CONFIRM_{c}BP_THEN_TAKER_CAP5_0.10_TTL1800": {"kind": "CONFIRM", "confirm_bp": float(c), "cap_bp": 5.0,
                                                           "offset": 0.10, "ttl": 1800} for c in (2, 3, 4)},
    }
    days = sorted({s.day for s in signals})
    test_days = set(days[1:])
    span = max(1e-9, (signals[-1].ts - signals[0].ts) / 86400)
    out = {"signals": len(signals), "days": days, "designs": {}}
    for label, allow in (("ALL", None), ("ASIA_EU", lambda s: s.session in ("ASIA", "EU"))):
        for e, espec in entries.items():
            for x in ("BASE", CANONICAL_COMPOSITE):
                tr, el = es.simulate(signals, e, espec, x, exits, cap=cap, latency=latency, allow=allow)
                st = es.stats(tr, el, span)
                el_t = sum(1 for s in signals if s.day in test_days and (allow is None or allow(s)))
                st["test_days_fixed_rule"] = es.stats([t for t in tr if t["day"] in test_days], el_t, detail=False)
                if espec["kind"] == "CONFIRM":
                    st["entry_outcomes_uncapped"] = dict(Counter(
                        ENTRY_KIND.get((s.ts, e)) for s in signals
                        if (s.ts, e) in ENTRY_KIND and (allow is None or allow(s))))
                    st["fills_by_entry_kind"] = dict(Counter("LIMIT" if t["maker"] else "CHASE_MARKET" for t in tr))
                out["designs"][f"{label} | {e} | {x}"] = st
    nest = es.nested(signals, entries, {"BASE": exits["BASE"]}, cap=cap, latency=latency,
                     base_eid="MAKER_0.10_no_chase_TTL1800", base_xid="BASE", restrict="ENTRY_ONLY")
    nest.pop("per_design", None)
    out["nested_entry_selection_all_sessions"] = {"folds": nest["folds"], "nested_oos": nest["nested_oos"],
                                                  "base_same_days": nest["base_same_days"]}
    return out


def spread_ok(sig, max_bp=3.0) -> bool:
    i = sig.start - es._TAPE.t0
    if not 0 <= i < len(es._TAPE.bid):
        return False
    b, a = es._TAPE.bid[i], es._TAPE.ask[i]
    return bool(b == b and a == a and a > b and (a - b) / ((a + b) / 2) * 1e4 <= max_bp)


def evaluable(signals, horizon):
    return [s for s in signals if es._TAPE.t0 <= s.start and s.start + horizon <= es._TAPE.end]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--tier-a", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ai-latency", type=float, default=6.5)
    ap.add_argument("--xv-latency", type=float, default=8.93)
    ap.add_argument("--tiles", default="H11,CFM,H9,H10,ENTRY")
    a = ap.parse_args()
    mirror = Path(a.mirror)
    es._TAPE = gg.load_tape(mirror, Path(a.tier_a))
    out: dict[str, Any] = {
        "schema": "hypothesis_tiles_coordinator_retest_v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fill_model": fm.FILL_MODEL_VERSION, "fees": "ZERO (BITFINEX_ZERO)", "exit_latency_sec": fm.EXIT_LATENCY_SEC,
        "tape_span_utc": [time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(es._TAPE.t0)),
                          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(es._TAPE.end))],
        "canonical_composite": CANONICAL_COMPOSITE, "canonical_be": CANONICAL_BE, "canonical_cut": CANONICAL_CUT,
        "decision_rule": ("Adopt the canonical composite (late BE +20/+5, ATR trail 1.5 armed 2.0 ATR, time backstop, "
                          "40 bp hard stop) if its test-day paired difference vs the time exit is not significantly "
                          "negative (1 h-cluster 95% CI upper >= 0); else the late BE alone under the same test; else "
                          "keep the time exit. Conditional cut (-12 bp/5 min, MFE never > +2 bp) goes LIVE only if "
                          "its test-day paired mean vs the adopted exit is >= 0, otherwise SHADOW_ONLY."),
        "tiles": {},
    }
    target = Path(a.out)
    tiles = a.tiles.split(",")

    def flush():
        target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

    committed = es.ai_signals(mirror, es._TAPE, "AI_COMMITTED", fade=True)
    if "H11" in tiles:
        sig = [s for s in evaluable(committed, gg.PATH_END_SEC + 60) if spread_ok(s)]
        out["tiles"]["H11"] = run_tile("H11", sig, "TAKER_AT_SIGNAL", {"kind": "TAKER"}, 90, 3, a.ai_latency, h11=True)
        flush()
    if "CFM" in tiles:
        for s in committed:
            s.res.clear()
        sig = evaluable(committed, 1800 + gg.PATH_END_SEC + 60)
        out["tiles"]["CFM"] = run_tile("CFM", sig, "MAKER_0.10_no_chase_TTL1800",
                                       {"kind": "FIXED", "offset": 0.10, "chase": "no_chase", "ttl": 1800},
                                       90, 3, a.ai_latency)
        flush()
    if "ENTRY" in tiles:
        for s in committed:
            s.res.clear()
        sig = [s for s in evaluable(committed, 1800 + gg.PATH_END_SEC + 60) if spread_ok(s)]
        out["item4_confirm_entry_committed_fade"] = entry_comparison(sig, a.ai_latency, 90, 3)
        flush()
        print("ENTRY done", flush=True)
    if "H9" in tiles:
        nt = es.ai_signals(mirror, es._TAPE, "AI_NO_TRADE_SCORE_LED", fade=False)
        sig = evaluable(nt, 3600 + gg.PATH_END_SEC + 60)
        out["tiles"]["H9"] = run_tile("H9", sig, "MAKER_0.15_w234_s25_i180_TTL3600",
                                      {"kind": "FIXED", "offset": 0.15, "chase": "w234_s25_i180", "ttl": 3600},
                                      60, 10, a.ai_latency, h9=True)
        flush()
    if "H10" in tiles:
        sig = evaluable(es.xv_signals(mirror, es._TAPE), gg.PATH_END_SEC + 60)
        out["tiles"]["H10"] = run_tile("H10", sig, "TAKER_AT_SIGNAL", {"kind": "TAKER"}, 60, 3, a.xv_latency)
        flush()
    flush()
    print("wrote", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
