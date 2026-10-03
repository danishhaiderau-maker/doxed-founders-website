"""Capped per-signal paired test for the coordinator decision rule (research only).

For each eligible signal the contribution is its realized bp if the tile (at its own capacity) traded it, else 0;
variant minus time-exit, 1 h cluster bootstrap, test days only. This captures what the uncapped per-fill pairing
cannot: a protection that frees capacity early changes which later signals trade.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boss_retest as br  # noqa: E402
from boss_retest import es, gg  # noqa: E402

KEYS = ("LATE_BE_ARM20_FLOOR5", "LATE_BE_ARM20_FLOOR3", "LATE_BE_ARM25_FLOOR3", "LATE_BE_ARM30_FLOOR5",
        "LATE_TRAIL_1.5ATR_ARM2.0ATR", "LATE_TRAIL_2.0ATR_ARM2.0ATR", "LATE_GIVEBACK_KEEP50_ARM20",
        "COND_CUT-12_5M_MFE2", "ATR_HARD_STOP_3ATR_25_60", "ATR_HARD_STOP_4ATR_25_60",
        br.CANONICAL_COMPOSITE, "LATE_BE_ARM20_FLOOR5+COND_CUT12_5M")


def per_signal(signals, eid, espec, xid, exits, cap, latency, allow=None):
    tr, _ = es.simulate(signals, eid, espec, xid, exits, cap=cap, latency=latency, allow=allow)
    got = {t["ts"]: t["bp"] for t in tr}
    return {s.ts: got.get(s.ts, 0.0) for s in signals if allow is None or allow(s)}


def capped_paired(signals, eid, espec, exits, cap, latency, test_days, allow=None, base="BASE", keys=KEYS):
    b = per_signal(signals, eid, espec, base, exits, cap, latency, allow)
    out = {}
    ts_test = [s.ts for s in signals if s.day in test_days and (allow is None or allow(s))]
    for x in keys:
        v = per_signal(signals, eid, espec, x, exits, cap, latency, allow)
        diffs = [v[t] - b[t] for t in ts_test]
        lo, hi, c = es.cluster_ci(np.asarray(diffs), np.asarray(ts_test))
        out[x] = {"signals": len(diffs), "mean_bp_per_signal": round(float(np.mean(diffs)), 3),
                  "ci95_1h_bp_per_signal": [lo, hi], "significantly_worse": bool(hi is not None and hi < 0),
                  "ci_width": round(hi - lo, 2) if lo is not None else None}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--tier-a", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    mirror = Path(a.mirror)
    es._TAPE = gg.load_tape(mirror, Path(a.tier_a))
    out = {"schema": "hypothesis_tiles_capped_paired_v1", "unit": "bp per eligible signal, test days, variant - time exit"}
    committed = es.ai_signals(mirror, es._TAPE, "AI_COMMITTED", fade=True)
    asia_eu = lambda s: s.session in ("ASIA", "EU")  # noqa: E731
    jobs = [
        ("H11", [s for s in br.evaluable(committed, gg.PATH_END_SEC + 60) if br.spread_ok(s)], "TAKER_AT_SIGNAL",
         {"kind": "TAKER"}, 90, 3, 6.5, None),
        ("H11_ASIA_EU", None, "TAKER_AT_SIGNAL", {"kind": "TAKER"}, 90, 3, 6.5, asia_eu),
        ("CFM", br.evaluable(committed, 1800 + gg.PATH_END_SEC + 60), "MAKER_0.10_no_chase_TTL1800",
         {"kind": "FIXED", "offset": 0.10, "chase": "no_chase", "ttl": 1800}, 90, 3, 6.5, None),
    ]
    prev = None
    for name, sig, eid, espec, T, cap, lat, allow in jobs:
        if sig is None:
            sig = prev
        else:
            for s in committed:
                s.res.clear()
        prev = sig
        exits = br.boss_exits(T)
        days = sorted({s.day for s in sig})
        out[name] = capped_paired(sig, eid, espec, exits, cap, lat, set(days[1:]), allow)
        print(name, flush=True)
    nt = es.ai_signals(mirror, es._TAPE, "AI_NO_TRADE_SCORE_LED", fade=False)
    sig = br.evaluable(nt, 3600 + gg.PATH_END_SEC + 60)
    days = sorted({s.day for s in sig})
    espec = {"kind": "FIXED", "offset": 0.15, "chase": "w234_s25_i180", "ttl": 3600}
    for cap in (5, 10):
        out[f"H9_cap{cap}"] = capped_paired(sig, "MAKER_0.15_w234_s25_i180_TTL3600", espec, br.boss_exits(60),
                                            cap, 6.5, set(days[1:]))
        print("H9", cap, flush=True)
    sig = br.evaluable(es.xv_signals(mirror, es._TAPE), gg.PATH_END_SEC + 60)
    days = sorted({s.day for s in sig})
    out["H10"] = capped_paired(sig, "TAKER_AT_SIGNAL", {"kind": "TAKER"}, br.boss_exits(60), 3, 8.93, set(days[1:]))
    out["H10_days"] = days
    # Danish confirm design, Asia+EU vs all sessions (Tile 1 vs 7b), capped per-signal on the same signals.
    espec = {"kind": "CONFIRM", "confirm_bp": 3.0, "cap_bp": 5.0, "offset": 0.10, "ttl": 1800}
    for s in committed:
        s.res.clear()
    sig = [s for s in br.evaluable(committed, 1800 + gg.PATH_END_SEC + 60) if br.spread_ok(s)]
    days = sorted({s.day for s in sig})
    exits = br.boss_exits(90)
    exits["DANISH_A"] = {"time_min": 90.0, "be_arm_bp": 20, "be_floor_bp": 5, "thesis_bp": -12, "thesis_sec": 300,
                         "thesis_max_mfe": 2.0}
    exits["DANISH_NOES"] = {"time_min": 90.0, "be_arm_bp": 20, "be_floor_bp": 5}
    out["DANISH_CONFIRM_ALL"] = capped_paired(sig, "CONFIRM3", espec, exits, 3, 6.5, set(days[1:]),
                                              keys=("DANISH_A", "DANISH_NOES", br.CANONICAL_COMPOSITE))
    a_all = per_signal(sig, "CONFIRM3", espec, "DANISH_A", exits, 3, 6.5)
    a_ae = per_signal(sig, "CONFIRM3", espec, "DANISH_A", exits, 3, 6.5, allow=asia_eu)
    test = [s.ts for s in sig if s.day in set(days[1:])]
    diffs = [a_ae.get(t, 0.0) - a_all[t] for t in test]
    lo, hi, _ = es.cluster_ci(np.asarray(diffs), np.asarray(test))
    out["DANISH_SESSION_EFFECT_ASIA_EU_MINUS_ALL"] = {"mean_bp_per_signal": round(float(np.mean(diffs)), 3),
                                                      "ci95_1h_bp_per_signal": [lo, hi], "signals": len(diffs)}
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
