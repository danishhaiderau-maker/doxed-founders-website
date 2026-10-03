"""Danish "Confirmed Fade" backtest (research only; REALISTIC_V1, zero fees, harness = exit_study.py).

Signal: fade explicit committed AI calls (class AI_COMMITTED: raw LONG/SHORT equal to the score-led side; the same
cohort as CFM). Gates: UTC 00:00-16:00 (ASIA+EU) and spread <= 3 bp at the decision second.
Entry CONFIRM: resting limit 0.10% better than the signal price (REALISTIC_V1 maker fill). If, before it fills, the
mid moves 3 bp in the trade direction from the signal price, the limit is cancelled and a taker fill is taken one
second later at the executable side, only if that fill is within 5 bp of the confirmation price (else skipped).
Neither within 30 min -> dropped.
Exits: 90-min time stop; once MFE >= +20 bp the stop moves to +5 bp; variant A adds an early stop in the first
5 min at -12 bp while MFE has never exceeded +2 bp; hard stop -40 bp always.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exit_study as es  # noqa: E402
from exit_study import gg, fm  # noqa: E402

CONFIRM_BP = 3.0
CAP_BP = 5.0
OFFSET_PCT = 0.10
TTL = 1800
MAX_SPREAD_BP = 3.0
ENTRY_KIND: dict[tuple[float, str], str] = {}

EXITS = {
    "DANISH_A_EARLY_STOP": {"time_min": 90.0, "be_arm_bp": 20, "be_floor_bp": 5,
                            "thesis_bp": -12, "thesis_sec": 300, "thesis_max_mfe": 2.0},
    "DANISH_NO_EARLY_STOP": {"time_min": 90.0, "be_arm_bp": 20, "be_floor_bp": 5},
    "BASE_TIME_90M_HARD40": {"time_min": 90.0},
}
ENTRIES = {
    "CONFIRM_0.10_3BP_MKT_CAP5_TTL1800": {"kind": "CONFIRM"},
    "CFM_MAKER_0.10_no_chase_TTL1800": {"kind": "FIXED", "offset": OFFSET_PCT, "chase": "no_chase", "ttl": TTL},
    "TAKER_AT_SIGNAL": {"kind": "TAKER"},
}
_orig_evaluate = es.evaluate


def evaluate(sig, eid, espec, exits, latency):
    if espec.get("kind") != "CONFIRM":
        return _orig_evaluate(sig, eid, espec, exits, latency)
    if eid in sig.res:
        return sig.res[eid]
    tape = es._TAPE
    lat = gg.latency_steps(sig.ts, latency)
    need = TTL + gg.PATH_END_SEC + 5 + lat + fm.TAKER_MAX_WAIT_SEC + 2
    out = None
    if tape.t0 <= sig.start and sig.start + need <= tape.end:
        w = tape.window(sig.start, sig.start + need)
        maker = gg.simulate_fill({"offset_pct": OFFSET_PCT, "chase_id": "no_chase", "ttl_sec": TTL},
                                 sig.direction, sig.price, w, lat)[gg.HEADLINE_WORLD]
        sign = 1.0 if sig.direction == "LONG" else -1.0
        mid = (w["bid"] + w["ask"]) / 2
        confirm_px = sig.price * (1 + sign * CONFIRM_BP / 1e4)
        end = min(len(mid), lat + TTL)
        with np.errstate(invalid="ignore"):
            hit = sign * (mid[lat:end] - confirm_px) >= 0
        j = lat + int(np.argmax(hit)) if hit.any() else None
        fill, kind = None, "UNFILLED"
        if maker is not None and (j is None or maker[0] <= j):
            fill, kind = maker, "LIMIT"
        elif j is not None:
            t = gg.realistic_taker_fill(sig.direction, w, j + 1)
            if t is not None and t[0] <= j + 1 + fm.TAKER_MAX_WAIT_SEC \
                    and sign * (t[1] - confirm_px) / confirm_px * 1e4 <= CAP_BP:
                fill, kind = t, "CHASE_MARKET"
            else:
                kind = "CAP_SKIP"
        ENTRY_KIND[(sig.ts, eid)] = kind
        if fill is None:
            out = (None, TTL, 0.0, 0, None, {})
        else:
            f_idx, f_px, frac, mk = fill
            path = gg.prepare_path(sig.direction, f_px, f_idx, w)
            if path is not None:
                outs = {x: es.replay(path, xv, sig.atr) for x, xv in exits.items()}
                out = (sig.start + f_idx, TTL, frac, mk, None, outs)
    sig.res[eid] = out
    return out


es.evaluate = evaluate


def spread_ok(sig) -> bool:
    i = sig.start - es._TAPE.t0
    if not 0 <= i < len(es._TAPE.bid):
        return False
    b, a = es._TAPE.bid[i], es._TAPE.ask[i]
    return bool(b == b and a == a and a > b and (a - b) / ((a + b) / 2) * 1e4 <= MAX_SPREAD_BP)


def run(signals, cap, latency, label):
    days = sorted({s.day for s in signals})
    span = max(1e-9, (signals[-1].ts - signals[0].ts) / 86400) if signals else None
    res = {"label": label, "signals": len(signals), "days": days, "cap": cap, "designs": {}}
    test_days = set(days[1:])
    for e, espec in ENTRIES.items():
        for x in EXITS:
            tr, el = es.simulate(signals, e, espec, x, EXITS, cap=cap, latency=latency)
            el_test = sum(1 for s in signals if s.day in test_days)
            st = es.stats(tr, el, span)
            st["test_days_fixed_rule"] = es.stats([t for t in tr if t["day"] in test_days], el_test, detail=False)
            if espec["kind"] == "CONFIRM":
                fills = {t["ts"] for t in tr}
                kinds = Counter(ENTRY_KIND.get((s.ts, e)) for s in signals if (s.ts, e) in ENTRY_KIND)
                st["entry_outcomes_uncapped"] = dict(kinds)
                st["fills_by_entry_kind"] = dict(Counter("LIMIT" if t["maker"] else "CHASE_MARKET" for t in tr))
                st["filled_signals"] = len(fills)
            res["designs"][f"{e} | {x}"] = st
    res["nested_joint"] = es.nested(signals, ENTRIES, EXITS, cap=cap, latency=latency,
                                    base_eid="CONFIRM_0.10_3BP_MKT_CAP5_TTL1800", base_xid="DANISH_A_EARLY_STOP")
    res["nested_joint"].pop("per_design", None)
    res["early_stop_cost_benefit"] = es.cut_cost_benefit(signals, "CONFIRM_0.10_3BP_MKT_CAP5_TTL1800", ENTRIES, EXITS,
                                                         latency, "DANISH_NO_EARLY_STOP")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--tier-a", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--latency", type=float, default=6.5)
    ap.add_argument("--cap", type=int, default=3)
    a = ap.parse_args()
    mirror = Path(a.mirror)
    es._TAPE = gg.load_tape(mirror, Path(a.tier_a))
    sig = es.ai_signals(mirror, es._TAPE, "AI_COMMITTED", fade=True)
    horizon = TTL + gg.PATH_END_SEC + 60
    ok = [s for s in sig if es._TAPE.t0 <= s.start and s.start + horizon <= es._TAPE.end and spread_ok(s)]
    out = {"schema": "danish_confirmed_fade_study_v1", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "fill_model": fm.FILL_MODEL_VERSION, "fees": "ZERO (BITFINEX_ZERO)", "latency_sec": a.latency,
           "committed_signals": len(sig), "evaluable_spread_ok": len(ok),
           "tape_span_utc": [time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(es._TAPE.t0)),
                             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(es._TAPE.end))]}
    asia_eu = [s for s in ok if s.session in ("ASIA", "EU")]
    out["ASIA_EU"] = run(asia_eu, a.cap, a.latency, "ASIA+EU (the tile)")
    for s in ok:
        s.res.clear()
    ENTRY_KIND.clear()
    out["ALL_SESSIONS"] = run(ok, a.cap, a.latency, "all sessions (for the US split)")
    Path(a.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
