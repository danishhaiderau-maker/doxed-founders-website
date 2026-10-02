"""Continuously re-run pre-registered strategy/indicator screens with false-positive guards.

The screens in ``edges_registry.json`` are frozen by hash (polarity fixed in
advance, never picked in-sample). Every run evaluates them on a 5-minute grid
over the Bitfinex 1 s tape with a chronological holdout (last 22%), a 1 h
embargo, BH and Holm over the whole family, minimum N / days / hour clusters
and a decay check. Status:

* CANDIDATE - every guard passes (alerted; shadow-only until Danish approves);
* HINT - holdout hit >= 55% with positive after-cost bp (Danish's threshold),
  shown in the digest, never alerted, never promoted;
* WATCH / REJECTED / INSUFFICIENT otherwise.

Grid events are upserted (features + forward outcomes) so evidence keeps
accumulating after the mirror rotates its tape. The regime playbook is
descriptive only (not corrected for multiple testing).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import tape as tp
from .facts import iso

REGISTRY_PATH = Path(__file__).with_name("edges_registry.json")
HSEC = {v: k for k, v in tp.HLABEL.items()}
FEATURES = ("r5m", "r15m", "r60m", "ofi1m", "ofi5m", "l1imb", "tz60")


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    reg = json.loads(path.read_text(encoding="utf-8"))
    for spec in reg["specs"]:
        canon = json.dumps({k: spec[k] for k in sorted(spec)}, sort_keys=True, separators=(",", ":"))
        spec["spec_hash"] = "sha256:" + hashlib.sha256(canon.encode()).hexdigest()[:16]
    canon = json.dumps({"clock": reg["clock"], "guards": reg["guards"], "specs": reg["specs"]}, sort_keys=True)
    reg["registry_hash"] = "sha256:" + hashlib.sha256(canon.encode()).hexdigest()[:16]
    return reg


def grid_events(tape: tp.Tape, step: int, t_from: float | None = None) -> pd.DataFrame:
    start = tape.t0 + 3600 + 1
    if t_from is not None:
        start = max(start, int(np.ceil(t_from / step) * step))
    start = int(np.ceil(start / step) * step)
    ts = np.arange(start, tape.t_end - max(HSEC.values()) - 60, step, dtype=np.int64)
    if not len(ts):
        return pd.DataFrame()
    i = tape.index(ts - 1)  # index() adds +1 s; grid points act at the grid second
    ok = tape.valid_entry(i, lookback=3600)
    ts, i = ts[ok], i[ok]
    if not len(ts):
        return pd.DataFrame()
    r1 = np.stack([tape.back_return(i - k * 60, 60) for k in range(60)], axis=1)
    vol1 = np.nanstd(r1, axis=1)
    r60 = tape.back_return(i, 3600)
    ev = pd.DataFrame({"ts": ts.astype(float), "r5m": tape.back_return(i, 300), "r15m": tape.back_return(i, 900),
                       "r60m": r60, "ofi1m": tape.flow(i, 60), "ofi5m": tape.flow(i, 300), "l1imb": tape.l1_imbalance(i),
                       "tz60": r60 / np.maximum(vol1 * np.sqrt(60), 1e-9), "session": tp.session_of(ts),
                       "rv15_bp": tape.realized_vol(i)})
    for lab, h in HSEC.items():
        ret, cost = tape.forward(i, h)
        ev[f"fwd_{lab}_bp"] = ret
        ev[f"cost_{lab}_bp"] = cost
    return ev


def gate_mask(ev: pd.DataFrame, gate: dict | None, train: np.ndarray) -> np.ndarray:
    """Regime gate for a spec. Tercile cut points come from the training window only (no holdout leakage)."""
    if not gate:
        return np.ones(len(ev), dtype=bool)
    x = ev[gate["feature"]].to_numpy(float)
    if "tercile" in gate:
        ref = x[train & np.isfinite(x)]
        if len(ref) < 30:
            return np.zeros(len(ev), dtype=bool)
        q1, q2 = np.percentile(ref, [100 / 3, 200 / 3])
        return {"LOW": x <= q1, "MID": (x > q1) & (x <= q2), "HIGH": x > q2}[gate["tercile"]] & np.isfinite(x)
    if "abs_min" in gate:
        return np.abs(x) >= gate["abs_min"]
    if "abs_max" in gate:
        return np.abs(x) < gate["abs_max"]
    raise ValueError(f"unknown gate {gate}")


def evaluate(events: pd.DataFrame, reg: dict, released: set[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``released``: research questions whose data is sufficient; gated specs of other questions stay QUEUED_DATA
    and are kept out of the multiple-testing family until then."""
    g = reg["guards"]
    released = released or set()
    ev = events.sort_values("ts").reset_index(drop=True)
    if ev.empty:
        return pd.DataFrame(), pd.DataFrame()
    t_lo, t_hi = float(ev["ts"].min()), float(ev["ts"].max())
    hold_start = t_hi - (t_hi - t_lo) * g["holdout_fraction"]
    train_end = hold_start - g["embargo_sec"]
    hour = (ev["ts"] // 3600).astype(np.int64).to_numpy()
    day = (ev["ts"] // 86400).astype(np.int64).to_numpy()
    ts = ev["ts"].to_numpy()
    rows, queued = [], []
    for spec in reg["specs"]:
        if spec.get("requires") and spec["requires"] not in released:
            queued.extend({"spec_id": spec["id"], "spec_hash": spec["spec_hash"], "feature": spec["feature"],
                           "polarity": spec["polarity"], "threshold": spec["threshold"], "horizon": lab,
                           "gate": json.dumps(spec.get("gate")), "requires": spec["requires"], "status": "QUEUED_DATA",
                           "reasons": f"waiting for {spec['requires']} data sufficiency (see /api/selfaware/data/sufficiency)"}
                          for lab in spec["horizons"])
            continue
        x = ev[spec["feature"]].to_numpy(float)
        side = np.where(np.abs(x) > spec["threshold"], spec["polarity"] * np.sign(x), 0).astype(int)
        side = np.where(gate_mask(ev, spec.get("gate"), ts < train_end), side, 0)
        for lab in spec["horizons"]:
            ret = ev[f"fwd_{lab}_bp"].to_numpy(float)
            net = side * ret - ev[f"cost_{lab}_bp"].to_numpy(float)
            take = (side != 0) & np.isfinite(net)
            tr, ho = take & (ts < train_end), take & (ts >= hold_start)
            hit = lambda m: float(((side[m] * ret[m]) > 0).mean()) if m.any() else np.nan  # noqa: E731
            mean = lambda m: float(net[m].mean()) if m.any() else np.nan  # noqa: E731
            ho_idx = np.where(ho)[0]
            half = ho_idx[: len(ho_idx) // 2], ho_idx[len(ho_idx) // 2:]
            lo, hi = tp.cluster_bootstrap(net[ho], hour[ho]) if ho.sum() >= 10 else (np.nan, np.nan)
            rows.append({
                "spec_id": spec["id"], "spec_hash": spec["spec_hash"], "feature": spec["feature"],
                "polarity": spec["polarity"], "threshold": spec["threshold"], "horizon": lab,
                "gate": json.dumps(spec.get("gate")) if spec.get("gate") else None, "requires": spec.get("requires"),
                "train_n": int(tr.sum()), "train_hit": hit(tr), "train_net_bp": mean(tr),
                "holdout_n": int(ho.sum()), "holdout_hit": hit(ho), "holdout_net_bp": mean(ho),
                "holdout_net_lo": lo, "holdout_net_hi": hi,
                "holdout_days": int(len(np.unique(day[ho]))), "holdout_clusters": int(len(np.unique(hour[ho]))),
                "holdout_p": tp.cluster_t_pvalue(net[ho], hour[ho]),
                "holdout_first_half_bp": float(net[half[0]].mean()) if len(half[0]) else np.nan,
                "holdout_second_half_bp": float(net[half[1]].mean()) if len(half[1]) else np.nan,
                "holdout_start": iso(hold_start), "train_end": iso(train_end),
            })
    if not rows:
        return pd.DataFrame(queued), playbook(ev, reg, hold_start)
    res = pd.DataFrame(rows)
    res["bh_q"] = tp.bh_q(res["holdout_p"].to_numpy())
    res["holm_p"] = tp.holm(res["holdout_p"].to_numpy())
    res["status"], res["reasons"] = zip(*[_status(r, g) for r in res.to_dict("records")])
    if queued:
        res = pd.concat([res, pd.DataFrame(queued)], ignore_index=True)
    return res, playbook(ev, reg, hold_start)


def _status(r: dict, g: dict) -> tuple[str, str]:
    reasons = []
    n, net, hitv = r["holdout_n"], r["holdout_net_bp"], r["holdout_hit"]
    if n < g["min_n_hint"]:
        return "INSUFFICIENT", f"holdout n {n} < {g['min_n_hint']}"
    if n < g["min_n_candidate"]:
        reasons.append(f"n {n} < {g['min_n_candidate']}")
    if r["holdout_days"] < g["min_days"]:
        reasons.append(f"days {r['holdout_days']} < {g['min_days']}")
    if r["holdout_clusters"] < g["min_clusters"]:
        reasons.append(f"hour clusters {r['holdout_clusters']} < {g['min_clusters']}")
    if not (net > 0):
        reasons.append(f"holdout net {net:.2f} bp <= 0")
    if not (r["bh_q"] <= g["alpha"]):
        reasons.append(f"BH q {r['bh_q']:.2f} > {g['alpha']}")
    if not (r["holm_p"] <= g["alpha"]):
        reasons.append(f"Holm p {r['holm_p']:.2f} > {g['alpha']}")
    if g.get("require_train_sign") and not (r["train_net_bp"] > 0):
        reasons.append("train net <= 0 (sign not stable)")
    if not (r["holdout_second_half_bp"] >= 0):
        reasons.append("decay: holdout second half < 0")
    if not reasons:
        return "CANDIDATE", "all guards pass"
    if hitv >= g["hint_min_hit"] and net > g["hint_min_net_bp"]:
        return "HINT", "hit >= 55% and net > 0 but: " + "; ".join(reasons)
    if net > 0:
        return "WATCH", "; ".join(reasons)
    return "REJECTED", "; ".join(reasons)


def playbook(ev: pd.DataFrame, reg: dict, hold_start: float) -> pd.DataFrame:
    ho = ev[ev["ts"] >= hold_start].copy()
    if ho.empty:
        return pd.DataFrame()
    rv = ev["rv15_bp"].to_numpy(float)
    q1, q2 = (np.nanpercentile(rv, [33.3, 66.7]) if np.isfinite(rv).sum() > 30 else (np.nan, np.nan))
    ho["vol"] = np.where(ho["rv15_bp"] <= q1, "LOW", np.where(ho["rv15_bp"] <= q2, "MID", "HIGH"))
    ho["trend"] = np.where(ho["tz60"].abs() >= 1.0, "TRENDING", "CHOP")
    rows = []
    for (sess, vol, trend), cell in ho.groupby(["session", "vol", "trend"]):
        best = None
        for spec in reg["specs"]:
            if spec.get("gate"):
                continue  # the playbook cells are the regime split; gated specs would double-gate
            x = cell[spec["feature"]].to_numpy(float)
            side = np.where(np.abs(x) > spec["threshold"], spec["polarity"] * np.sign(x), 0)
            for lab in spec["horizons"]:
                net = side * cell[f"fwd_{lab}_bp"].to_numpy(float) - cell[f"cost_{lab}_bp"].to_numpy(float)
                m = (side != 0) & np.isfinite(net)
                if m.sum() < 30:
                    continue
                cand = (float(net[m].mean()), spec["id"], lab, int(m.sum()),
                        float(((side[m] * cell[f"fwd_{lab}_bp"].to_numpy(float)[m]) > 0).mean()))
                if best is None or cand[0] > best[0]:
                    best = cand
        rows.append({"session": sess, "vol": vol, "trend": trend, "events": int(len(cell)),
                     "best_spec": best[1] if best else None, "best_horizon": best[2] if best else None,
                     "best_net_bp": best[0] if best else None, "best_n": best[3] if best else None,
                     "best_hit": best[4] if best else None,
                     "action": ("STAND_ASIDE" if not best or best[0] <= 0 else "CONSIDER_" + best[1]),
                     "note": "descriptive holdout only; not multiple-testing corrected"})
    return pd.DataFrame(rows)


def run(store, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    started = time.time()
    reg = load_registry()
    step = int(reg["clock"]["step_sec"])
    last = None
    if store.table_exists("res_edge_events"):
        last = store.read("SELECT max(ts) AS t FROM res_edge_events")[0]["t"]
    t_from = (float(last) + step) if last else None
    tape = tp.load(store, (t_from - 3600 - 600) if t_from else 0)
    new = grid_events(tape, step, t_from) if tape is not None else pd.DataFrame()
    if len(new):
        new["computed_at"] = iso(now)
        store.upsert("edge_events", new, "ts", sources=["raw_tape_1s", "raw_tape_1s_archive"],
                     window=(iso(float(new["ts"].min())), iso(float(new["ts"].max()))),
                     compute_ms=int((time.time() - started) * 1000), note=f"5-min grid, registry {reg['registry_hash']}")
    if not store.table_exists("res_edge_events"):
        return {"status": "NO_DATA"}
    events = store.frame("SELECT * FROM res_edge_events")
    released: set[str] = set()
    if store.table_exists("res_data_sufficiency"):
        released = {r["id"] for r in store.read("SELECT id FROM res_data_sufficiency WHERE screens_released")}
    res, book = evaluate(events, reg, released)
    res["registry_hash"] = reg["registry_hash"]
    window = (iso(float(events["ts"].min())), iso(float(events["ts"].max())))
    store.publish("edges", res, sources=["res_edge_events", "edges_registry.json"], window=window,
                  compute_ms=int((time.time() - started) * 1000),
                  note=f"registry {reg['registry_hash']}; guards {json.dumps(reg['guards'], sort_keys=True)}")
    store.publish("regime_playbook", book if len(book) else pd.DataFrame({"note": ["no holdout events yet"]}),
                  sources=["res_edge_events"], window=window, note="descriptive only")
    counts = res["status"].value_counts().to_dict() if len(res) else {}
    return {"status": "OK", "events": int(len(events)), "new_events": int(len(new)),
            "tests": int((res["status"] != "QUEUED_DATA").sum()) if len(res) else 0, "released": sorted(released),
            "by_status": counts, "registry_hash": reg["registry_hash"], "ms": int((time.time() - started) * 1000)}
