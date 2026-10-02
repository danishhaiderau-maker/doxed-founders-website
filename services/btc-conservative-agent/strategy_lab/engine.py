"""Strategy-lab cycle: pre-registered hypotheses, bounded exploration, parity.

``run_strategy_lab`` is called once per analyzer cycle. It returns
``(payload, tables)``: a JSON-safe report and DataFrames for the agent export.
Heavy exploratory families are re-run at most every
``STRATEGY_LAB_HEAVY_INTERVAL_SEC`` (default: every cycle) and otherwise reused
from the previous run with their original timestamp, so the cycle stays well
inside the analyzer freshness bound.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from strategy_lab import SCHEMA, hypotheses as H
from strategy_lab import stats as S
from strategy_lab.signals import (ai_direction, attach_funding, load_ai_calls, rv15_at, xvl_lead,
                                  xvl_signals)
from strategy_lab.simulator import CostModel, live_fill_parity, recorded_fill_atr, simulate, spec_dict
from strategy_lab.tape import (HistorySources, coverage_summary, cross_venue_mids, default_history_sources,
                               load_bitfinex_tape)

LIVE_TEST_MARGIN_USD = 0.25
LIVE_TEST_LEVERAGE = 100.0
USD_PER_BP = LIVE_TEST_MARGIN_USD * LIVE_TEST_LEVERAGE / 1e4
MIN_UNSEEN_TRADES = 30
NULL_DRAWS = 400
RNG_SEED = 20261002
ALPHA = 0.05
HEAVY_CACHE_FILE = "strategy_lab_heavy_cache.json"

_COVERAGE_KEYS = ("epoch_start", "first_available_ts", "first_available", "last_ts", "last", "horizon_hours",
                  "epoch_coverage_share", "in_window_present_share", "unit_sec", "present_units", "epoch_seconds",
                  "sources", "unique_units_by_source")

TRADE_COLUMNS = ["hypothesis_id", "signal_ts", "side", "fill_ts", "fill_px", "exit_ts", "exit_px", "hold_sec",
                 "reason", "quote_bp", "mid_bp", "spread_cost_bp", "fee_bp", "slippage_bp", "funding_bp",
                 "net_bp", "post_registration"]


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None or not np.isfinite(ts):
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value) -> Optional[float]:
    try:
        return pd.Timestamp(value).timestamp()
    except (TypeError, ValueError):
        return None


def _clean(obj):
    """JSON-safe copy (NaN/inf -> None, numpy -> python)."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


# ---------------------------------------------------------------- signals
class _Context:
    def __init__(self, tape, venues, calls, venue_span: Optional[dict] = None):
        self.tape = tape
        self.venues = venues
        self.calls = calls
        self.venue_span = venue_span or {}
        self._lead = {}

    def _in_venue_span(self, sig: pd.DataFrame) -> pd.DataFrame:
        # XVL primaries and their controls are scored on the same cross-venue window,
        # even when the Bitfinex tape (multi-day history) reaches further back.
        lo, hi = self.venue_span.get("start_ts"), self.venue_span.get("end_ts")
        if sig.empty or lo is None or hi is None:
            return sig
        ts = sig["ts"].to_numpy(float)
        return sig[(ts >= float(lo)) & (ts <= float(hi))].reset_index(drop=True)

    def lead(self, window: int, mode: str) -> np.ndarray:
        key = (window, mode)
        if key not in self._lead:
            self._lead[key] = xvl_lead(self.tape, self.venues, window, mode)
        return self._lead[key]

    def signals(self, spec: dict) -> pd.DataFrame:
        if spec["family"] == "XVL":
            if not self.venues:
                return pd.DataFrame(columns=["ts", "side"])
            lead = self.lead(spec["window_sec"], spec["mode"])
            return self._in_venue_span(
                xvl_signals(self.tape, self.venues, spec["window_sec"], spec["threshold_bp"], spec["mode"], lead))
        if spec["family"] == "AI_CALL":
            c = self.calls
            if c is None or c.empty:
                return pd.DataFrame(columns=["ts", "side"])
            side = ai_direction(c, spec["source"])
            out = pd.DataFrame({"ts": c["ts"].to_numpy(float), "side": side, "call_id": c["call_id"].to_numpy(),
                                "funding_bp_8h": c["funding_bp_8h"].to_numpy(float)})
            return out[out["side"] != 0].reset_index(drop=True)
        raise ValueError(f"unknown family {spec['family']}")


def _costs(spec: dict) -> CostModel:
    return CostModel(taker_slippage_bp=float(spec.get("taker_slippage_bp") or 0.0))


def _run_spec(ctx: _Context, spec: dict, capacity_one: bool = True) -> tuple:
    sig = ctx.signals(spec)
    dense = spec["family"] == "XVL"
    sim = simulate(ctx.tape, sig, spec["entry"], spec["exit"], _costs(spec), capacity_one=capacity_one,
                   keep_skipped=not dense)
    return sig, sim


def _closed(sim: pd.DataFrame) -> pd.DataFrame:
    if sim is None or sim.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS[1:-1])
    return sim[sim["filled"].astype(bool) & ~sim["censored"].astype(bool) & sim["net_bp"].notna()]


# ---------------------------------------------------------------- hypotheses
def _verdict(unseen: dict, holm_p: Optional[float]) -> str:
    n = int(unseen.get("n") or 0)
    if n < MIN_UNSEEN_TRADES:
        return "INSUFFICIENT"
    mean = unseen.get("mean_bp") or 0.0
    hi = unseen.get("ci_hi_bp")
    if hi is not None and hi < 0:
        return "NEGATIVE"
    if mean > 0 and holm_p is not None and holm_p < ALPHA:
        return "SUPPORTED"
    if mean > 0 and unseen.get("p_cluster") is not None and unseen["p_cluster"] < 0.2:
        return "HINT"
    return "NOT_SUPPORTED"


def _regime_breakdown(tape, sig: pd.DataFrame, closed: pd.DataFrame) -> dict:
    """rv15 terciles with expanding (strictly past) thresholds over the signal stream."""
    if sig is None or sig.empty or closed.empty:
        return {}
    sig = sig.sort_values("ts")
    if len(sig) > 20000:                        # dense XVL triggers: thin the threshold history
        sig = sig.iloc[:: max(len(sig) // 20000, 1)]
    rv_sig = rv15_at(tape, sig["ts"].to_numpy(float))
    lo = S.expanding_quantile(rv_sig, sig["ts"].to_numpy(float), 1 / 3)
    hi = S.expanding_quantile(rv_sig, sig["ts"].to_numpy(float), 2 / 3)
    pos = np.searchsorted(sig["ts"].to_numpy(float), closed["signal_ts"].to_numpy(float), side="right") - 1
    rv = rv15_at(tape, closed["signal_ts"].to_numpy(float))
    out = {}
    labels = np.full(len(closed), "WARMUP", dtype=object)
    ok = pos >= 0
    lo_t = np.where(ok, lo[np.maximum(pos, 0)], np.nan)
    hi_t = np.where(ok, hi[np.maximum(pos, 0)], np.nan)
    have = np.isfinite(lo_t) & np.isfinite(hi_t) & np.isfinite(rv)
    labels[have & (rv <= lo_t)] = "LOW"
    labels[have & (rv > lo_t) & (rv <= hi_t)] = "MID"
    labels[have & (rv > hi_t)] = "HIGH"
    net = closed["net_bp"].to_numpy(float)
    for lab in ("LOW", "MID", "HIGH", "WARMUP"):
        m = labels == lab
        if m.any():
            out[lab] = {"n": int(m.sum()), "mean_bp": float(net[m].mean())}
    out["basis"] = "rv15 terciles from strictly earlier signals (expanding quantile, min 30)"
    return out


def run_hypotheses(ctx: _Context) -> tuple:
    rows, trades, sims = [], [], {}
    for spec in H.HYPOTHESES:
        reg_ts = _parse_iso(spec["registered_at"])
        sig, sim = _run_spec(ctx, spec)
        closed = _closed(sim)
        full = S.summarize(closed["net_bp"], closed["signal_ts"], usd_per_bp=USD_PER_BP)
        unseen_rows = closed[closed["signal_ts"] >= reg_ts] if reg_ts else closed.iloc[0:0]
        unseen = S.summarize(unseen_rows["net_bp"], unseen_rows["signal_ts"], usd_per_bp=USD_PER_BP)
        sims[spec["id"]] = (sig, closed)
        row = {
            "id": spec["id"], "kind": spec["kind"], "primary": bool(spec.get("primary")),
            "family": spec["family"], "registered_at": spec["registered_at"], "spec_hash": H.spec_hash(spec),
            "control_of": spec.get("control_of"), "expect": spec.get("expect"), "question": spec["question"],
            "source": spec["source"], "entry": spec_dict(spec["entry"]), "exit": spec_dict(spec["exit"]),
            "signals": int(len(sig)),
            "filled": int(sim["filled"].astype(bool).sum()) if len(sim) else 0,
            "censored": int(sim["censored"].astype(bool).sum()) if len(sim) else 0,
            "full": full, "unseen": unseen,
            "dsr": S.deflated_sharpe(closed["net_bp"], n_trials=1),
            "regimes": _regime_breakdown(ctx.tape, sig, closed),
        }
        rows.append(row)
        if len(closed):
            t = closed.copy()
            t["hypothesis_id"] = spec["id"]
            t["post_registration"] = t["signal_ts"] >= reg_ts if reg_ts else False
            trades.append(t[[c for c in TRADE_COLUMNS if c in t.columns]])
    prim = [r for r in rows if r["primary"]]
    adj = S.holm([r["unseen"].get("p_cluster") for r in prim])
    for r, p in zip(prim, adj):
        r["holm_p_unseen"] = p
        r["verdict"] = _verdict(r["unseen"], p)
    for r in rows:
        if not r["primary"]:
            mean = r["full"].get("mean_bp")
            if mean is None or r["full"].get("n", 0) < MIN_UNSEEN_TRADES:
                r["verdict"] = "INSUFFICIENT"
            elif r["expect"] == "<= 0":
                r["verdict"] = "CONTROL_OK" if mean <= 0 else "CONTROL_FAILED"
            else:
                r["verdict"] = "STRESS_OK" if mean > 0 else "STRESS_FAILED"
    tdf = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(columns=TRADE_COLUMNS)
    return rows, tdf, sims


# ---------------------------------------------------------------- exploratory families
def _t_stat(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if len(x) < 5:
        return -np.inf
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / math.sqrt(len(x)))) if sd > 0 else -np.inf


def _family_xvl(ctx: _Context, fam: dict, rng) -> dict:
    frames = {}
    for cfg in fam["configs"]:
        _, sim = _run_spec(ctx, cfg)
        frames[cfg["id"]] = _closed(sim)
    # family-wise null: one set of +/-1 flips per 15 min block shared by every config
    all_ts = np.concatenate([f["signal_ts"].to_numpy(float) for f in frames.values()] or [np.array([])])
    blocks = np.unique((all_ts // 900).astype(np.int64)) if len(all_ts) else np.array([], np.int64)
    null_max = np.full(NULL_DRAWS, -np.inf)
    prepared = {}
    for cid, f in frames.items():
        if len(f) < 5:
            continue
        b = np.searchsorted(blocks, (f["signal_ts"].to_numpy(float) // 900).astype(np.int64))
        cost = (f["spread_cost_bp"] + f["fee_bp"] + f["slippage_bp"] - f["funding_bp"]).to_numpy(float)
        prepared[cid] = (b, f["mid_bp"].to_numpy(float), cost)
    if prepared and len(blocks):
        flips = rng.choice(np.array([-1.0, 1.0]), size=(NULL_DRAWS, len(blocks)))
        for b, mid, cost in prepared.values():
            x = flips[:, b] * mid - cost
            n = x.shape[1]
            sd = x.std(axis=1, ddof=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                t = np.where(sd > 0, x.mean(axis=1) / (sd / math.sqrt(n)), -np.inf)
            null_max = np.maximum(null_max, t)
    return _family_rows(fam, frames, null_max, "block-sign flips per 15 min block, shared across configs; "
                                               "statistic = max per-trade t over the family")


def _family_ai(ctx: _Context, fam: dict, rng) -> dict:
    calls = ctx.calls
    frames = {}
    for cfg in fam["configs"]:
        _, sim = _run_spec(ctx, cfg)
        frames[cfg["id"]] = _closed(sim)
    null_max = np.full(NULL_DRAWS, -np.inf)
    if calls is not None and len(calls) >= 40:
        llm = np.sign(calls["gap"].to_numpy(float)).astype(int)
        shifts = rng.integers(10, len(calls) - 10, NULL_DRAWS)
        outcome = {}
        for cfg in fam["configs"]:
            tc = cfg["exit"].tcap_sec
            if tc in outcome:
                continue
            per_side = {}
            for side in (1, -1):
                sig = pd.DataFrame({"ts": calls["ts"].to_numpy(float), "side": side,
                                    "funding_bp_8h": calls["funding_bp_8h"].to_numpy(float)})
                sim = simulate(ctx.tape, sig, cfg["entry"], cfg["exit"], _costs(cfg), capacity_one=False)
                v = sim["net_bp"].to_numpy(float).copy()
                v[~(sim["filled"].astype(bool).to_numpy() & ~sim["censored"].astype(bool).to_numpy())] = np.nan
                per_side[side] = v
            outcome[tc] = per_side
        for k, s in enumerate(shifts):
            d = np.roll(llm, int(s))
            best = -np.inf
            for cfg in fam["configs"]:
                dd = -d if cfg["source"] == "INV_LLM" else d
                o = outcome[cfg["exit"].tcap_sec]
                x = np.where(dd > 0, o[1], np.where(dd < 0, o[-1], np.nan))
                best = max(best, _t_stat(x))
            null_max[k] = best
        observed_basis = {}
        for cfg in fam["configs"]:
            dd = -llm if cfg["source"] == "INV_LLM" else llm
            o = outcome[cfg["exit"].tcap_sec]
            observed_basis[cfg["id"]] = _t_stat(np.where(dd > 0, o[1], np.where(dd < 0, o[-1], np.nan)))
    else:
        observed_basis = None
    return _family_rows(fam, frames, null_max,
                        "circular shift of the AI direction series (keeps episode stickiness); "
                        "statistic = max overlapping per-call t over the family",
                        observed_override=observed_basis)


def _family_rows(fam: dict, frames: dict, null_max: np.ndarray, null_basis: str,
                 observed_override: Optional[dict] = None) -> dict:
    rows = []
    for cfg in fam["configs"]:
        f = frames.get(cfg["id"])
        s = S.summarize(f["net_bp"], f["signal_ts"], usd_per_bp=USD_PER_BP) if f is not None else {"n": 0}
        t_obs = observed_override.get(cfg["id"]) if observed_override else (
            _t_stat(f["net_bp"].to_numpy(float)) if f is not None else -np.inf)
        rows.append({"family_id": fam["id"], "config_id": cfg["id"], "spec_hash": H.spec_hash(cfg),
                     "n": s.get("n", 0), "mean_bp": s.get("mean_bp"), "ci_lo_bp": s.get("ci_lo_bp"),
                     "ci_hi_bp": s.get("ci_hi_bp"), "p_cluster": s.get("p_cluster"), "sharpe": s.get("sharpe"),
                     "net_usd": s.get("net_usd"), "per_day": s.get("per_day"),
                     "null_t": t_obs if np.isfinite(t_obs) else None,
                     "fwer_p": S.null_pvalue(t_obs, null_max) if np.isfinite(t_obs) else None})
    q = S.benjamini_hochberg([r["p_cluster"] for r in rows])
    for r, v in zip(rows, q):
        r["bh_q"] = v
    srs = np.array([r["sharpe"] for r in rows if r["sharpe"] is not None], float)
    ranked = sorted([r for r in rows if r["mean_bp"] is not None and r["n"] >= MIN_UNSEEN_TRADES],
                    key=lambda r: r["null_t"] if r["null_t"] is not None else -np.inf, reverse=True)
    best = ranked[0] if ranked else None
    dsr = {}
    if best is not None:
        bf = frames[best["config_id"]]["net_bp"]
        dsr = {"noise": S.deflated_sharpe(bf, len(rows)),
               "cross_trial": S.deflated_sharpe(bf, len(rows), float(srs.var(ddof=1)) if len(srs) > 1 else None)}
    max_hold = max(int(c["exit"].tcap_sec) for c in fam["configs"])
    wf = S.walk_forward({k: v.rename(columns={"signal_ts": "ts"}) for k, v in frames.items()},
                        embargo_sec=max_hold, usd_per_bp=USD_PER_BP)
    obs_max = max((r["null_t"] for r in rows if r["null_t"] is not None), default=None)
    finite_null = null_max[np.isfinite(null_max)]
    return {
        "id": fam["id"], "registered_at": fam["registered_at"], "source": fam["source"], "null": fam["null"],
        "null_basis": null_basis, "configs": len(rows), "null_draws": int(finite_null.size),
        "family_p": S.null_pvalue(obs_max, null_max) if obs_max is not None else None,
        "observed_max_t": obs_max,
        "null_max_t_p95": float(np.quantile(finite_null, 0.95)) if finite_null.size else None,
        "best_config": best["config_id"] if best else None, "deflated_sharpe_best": dsr,
        "bh_discoveries_q10": int(sum(1 for r in rows if r["bh_q"] is not None and r["bh_q"] < 0.10
                                      and (r["mean_bp"] or 0) > 0)),
        "walk_forward": wf, "rows": rows,
        "verdict": ("INSUFFICIENT" if best is None else
                    "FAMILY_SIGNAL" if (S.null_pvalue(obs_max, null_max) or 1) < ALPHA
                    and (wf.get("oos") or {}).get("mean_bp", -1) > 0 else "NO_FAMILY_SIGNAL"),
    }


def run_families(ctx: _Context) -> list:
    rng = np.random.default_rng(RNG_SEED)
    out = []
    for fam in H.EXPLORATORY_FAMILIES:
        t0 = time.perf_counter()
        kind = fam["configs"][0]["family"]
        if kind == "XVL" and not ctx.venues:
            res = {"id": fam["id"], "verdict": "NO_CROSS_VENUE_DATA", "rows": []}
        elif kind == "XVL":
            res = _family_xvl(ctx, fam, rng)
        else:
            res = _family_ai(ctx, fam, rng)
        res["elapsed_sec"] = round(time.perf_counter() - t0, 2)
        out.append(res)
    return out


# ---------------------------------------------------------------- correlation
def _lane_trade_series(trades: Optional[pd.DataFrame], lanes) -> dict:
    out = {}
    if trades is None or trades.empty:
        return out
    lane_col = trades.get("research_lane", pd.Series("", index=trades.index)).fillna("").astype(str).str.upper()
    ts = pd.to_datetime(trades.get("close_ts", trades.get("ts")), utc=True, errors="coerce")
    pnl = pd.to_numeric(trades.get("net_pnl_usd"), errors="coerce")
    for lane in lanes:
        m = (lane_col == lane) & ts.notna() & pnl.notna()
        if m.any():
            out[lane] = (ts[m].map(lambda x: x.timestamp()).to_numpy(float), pnl[m].to_numpy(float))
    return out


def correlations(sims: dict, trades: Optional[pd.DataFrame], lanes) -> list:
    rows = []
    lane_series = _lane_trade_series(trades, lanes)
    prim = [h["id"] for h in H.HYPOTHESES if h.get("primary")]
    for hid in prim:
        closed = sims.get(hid, (None, pd.DataFrame()))[1]
        if closed is None or closed.empty:
            continue
        a_ts, a_v = closed["exit_ts"].to_numpy(float), closed["net_bp"].to_numpy(float)
        for lane, (b_ts, b_v) in lane_series.items():
            rows.append({"a": hid, "b": lane, "b_kind": "LIVE_TILE_LEDGER", **S.bucket_corr(a_ts, a_v, b_ts, b_v)})
        for other in prim:
            if other <= hid:
                continue
            oc = sims.get(other, (None, pd.DataFrame()))[1]
            if oc is None or oc.empty:
                continue
            rows.append({"a": hid, "b": other, "b_kind": "HYPOTHESIS",
                         **S.bucket_corr(a_ts, a_v, oc["exit_ts"].to_numpy(float), oc["net_bp"].to_numpy(float))})
    return rows


# ---------------------------------------------------------------- heavy cache
def _heavy_fingerprint(tape, venues_span) -> str:
    return json.dumps({"reg": H.registry_signature(), "t0": int(tape.t0),
                       "venue_start": (venues_span or {}).get("start_ts"),
                       "sources": sorted(k for k, v in (tape.source_rows or {}).items() if v)}, sort_keys=True)


def _load_heavy(cache_dir: Optional[str], fingerprint: str, now: float, interval: float) -> Optional[dict]:
    if not cache_dir or interval <= 0:
        return None
    path = os.path.join(cache_dir, HEAVY_CACHE_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, ValueError):
        return None
    if cached.get("fingerprint") != fingerprint or now - float(cached.get("computed_at_ts") or 0) > interval:
        return None
    return cached


def _save_heavy(cache_dir: Optional[str], fingerprint: str, now: float, families: list) -> None:
    if not cache_dir:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, HEAVY_CACHE_FILE)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(_clean({"fingerprint": fingerprint, "computed_at_ts": now, "families": families}), handle)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


# ---------------------------------------------------------------- multi-day AI calls
AI_CALL_COVERAGE_BUCKET_SEC = 900


def load_ai_calls_union(data_dir: str, start_ts: Optional[float], history: Optional[HistorySources]) -> tuple:
    """AI_DECISION calls from the mirror plus frozen archives, de-duplicated by call id (mirror wins).

    Funding is attached from the mirror's AI input log first, then from each
    archive's log for calls the mirror no longer carries.
    """
    calls = load_ai_calls(data_dir, start_ts=start_ts)
    calls["_src"] = "mirror"
    frames = [calls]
    for folder in (history.archive_dirs if history is not None else ()):
        extra = load_ai_calls(folder, start_ts=start_ts)
        if len(extra):
            extra["_src"] = "archive"
            frames.append(extra)
    rows_by = {"archive": int(sum(len(f) for f in frames if len(f) and f["_src"].iloc[0] == "archive")),
               "tier_a": 0, "mirror": int(len(calls))}
    merged = pd.concat(frames, ignore_index=True) if len(frames) > 1 else calls
    merged = merged.drop_duplicates("call_id", keep="first").sort_values("ts", kind="stable").reset_index(drop=True)
    unique = {"archive": int((merged["_src"] == "archive").sum()), "tier_a": 0,
              "mirror": int((merged["_src"] == "mirror").sum())}
    out = attach_funding(merged.drop(columns=["_src"]), data_dir)
    for folder in (history.archive_dirs if history is not None else ()):
        missing = out["funding_bp_8h"] == 0
        if not missing.any():
            break
        filled = attach_funding(out.loc[missing, ["ts", "call_id", "long", "short", "gap", "raw"]], folder)
        out.loc[missing, "funding_bp_8h"] = filled["funding_bp_8h"].to_numpy(float)
    return out, rows_by, unique


def _ai_call_coverage(calls: pd.DataFrame, epoch_start, now, rows_by, unique) -> dict:
    b = AI_CALL_COVERAGE_BUCKET_SEC
    ts = calls["ts"].to_numpy(float) if len(calls) else np.array([])
    buckets = np.unique((ts // b).astype(np.int64)) if ts.size else np.array([], np.int64)
    return coverage_summary(
        epoch_start=epoch_start, first_ts=float(ts.min()) if ts.size else None,
        last_ts=float(ts.max()) if ts.size else None, present_units=int(buckets.size),
        window_units=int(buckets[-1] - buckets[0] + 1) if buckets.size else 0, unit_sec=b, now=now,
        sources=rows_by, unique_by_source=unique)


# ---------------------------------------------------------------- entry point
def run_strategy_lab(data_dir: str, *, session: Optional[dict] = None, registry: Optional[dict] = None,
                     tile_lanes=(), trades: Optional[pd.DataFrame] = None, cache_dir: Optional[str] = None,
                     now: Optional[float] = None, streams: Optional[list] = None,
                     history: Optional[HistorySources] = None) -> tuple:
    started = time.perf_counter()
    now = float(now if now is not None else time.time())
    session = session or {}
    timing = {}
    epoch_start = session.get("collector_v22_epoch_ts") or None
    if epoch_start is not None:
        epoch_start = float(epoch_start)
    # History is only epoch-pure when the epoch start is known.
    history = (history if history is not None else default_history_sources()) if epoch_start else HistorySources()
    defects = H.validate()
    payload = {
        "schema": SCHEMA, "generated_at": _iso(now), "generated_at_ts": now,
        "epoch_id": session.get("collector_v22_epoch_id") or session.get("epoch_id"), "epoch_start_ts": epoch_start, "epoch_start": _iso(epoch_start),
        "hypothesis_registry_signature": H.registry_signature(), "registry_defects": defects,
        "world": "CONSERVATIVE_BBO", "usd_per_bp": USD_PER_BP,
        "sizing_basis": f"${LIVE_TEST_MARGIN_USD} margin x {LIVE_TEST_LEVERAGE:g}x = "
                        f"${LIVE_TEST_MARGIN_USD * LIVE_TEST_LEVERAGE:g} notional (1 bp = ${USD_PER_BP})",
        "cost_model": {**spec_dict(CostModel()), "profile_id": CostModel().profile_id,
                       "spread": "paid through executable quotes (ask/bid), never mid",
                       "funding": "Bitfinex funding from the AI input context when the hold crosses 00/08/16 UTC"},
        "streams": streams or [],
        "history_sources": history.describe() if history.enabled else {"archive_dirs": [], "tier_a_root": None},
    }
    t = time.perf_counter()
    tape = load_bitfinex_tape(data_dir, start_ts=epoch_start, cache_dir=cache_dir, history=history)
    timing["tape_sec"] = round(time.perf_counter() - t, 2)
    if tape is None:
        payload.update(status="NO_TAPE", timing=timing)
        return _clean(payload), {}
    payload["tape"] = tape.coverage(epoch_start=epoch_start, now=now)
    t = time.perf_counter()
    try:
        cv = cross_venue_mids(data_dir, tape, history=history, epoch_start=epoch_start, now=now)
    except Exception as exc:  # pragma: no cover - shadow tape optional
        cv = {"venues": {}, "span": None, "error": f"{type(exc).__name__}: {exc}"}
    payload["cross_venue"] = {"span": cv.get("span"), "venues": sorted(cv.get("venues") or {}),
                              "error": cv.get("error"), "coverage": cv.get("coverage")}
    calls, call_rows, call_unique = load_ai_calls_union(data_dir, epoch_start, history)
    payload["ai_calls"] = {"n": int(len(calls)), "start": _iso(calls["ts"].min()) if len(calls) else None,
                           "end": _iso(calls["ts"].max()) if len(calls) else None,
                           "with_funding": int((calls["funding_bp_8h"] != 0).sum()) if len(calls) else 0,
                           "coverage": _ai_call_coverage(calls, epoch_start, now, call_rows, call_unique)}
    payload["stream_coverage"] = {
        "market_microstructure_1s.jsonl": {k: payload["tape"].get(k) for k in _COVERAGE_KEYS},
        "cross_venue_tape_1m.jsonl": {k: (cv.get("coverage") or {}).get(k) for k in _COVERAGE_KEYS},
        "ai_tranche_log.csv": {k: payload["ai_calls"]["coverage"].get(k) for k in _COVERAGE_KEYS},
    }
    timing["inputs_sec"] = round(time.perf_counter() - t, 2)
    ctx = _Context(tape, cv.get("venues") or {}, calls, venue_span=cv.get("span"))

    t = time.perf_counter()
    hyp_rows, hyp_trades, sims = run_hypotheses(ctx)
    timing["hypotheses_sec"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    interval = float(os.getenv("STRATEGY_LAB_HEAVY_INTERVAL_SEC", "0") or 0)
    fp = _heavy_fingerprint(tape, cv.get("span"))
    cached = _load_heavy(cache_dir, fp, now, interval)
    if cached:
        families = cached["families"]
        heavy = {"source": "CACHE", "computed_at": _iso(cached["computed_at_ts"])}
    else:
        families = _clean(run_families(ctx))
        _save_heavy(cache_dir, fp, now, families)
        heavy = {"source": "COMPUTED", "computed_at": _iso(now)}
    heavy["interval_sec"] = interval
    timing["families_sec"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    parity = live_fill_parity(tape, trades, registry or {}, tile_lanes, LIVE_TEST_LEVERAGE,
                              recorded_atr=recorded_fill_atr(data_dir))
    corr = correlations(sims, trades, tile_lanes)
    timing["parity_corr_sec"] = round(time.perf_counter() - t, 2)
    timing["total_sec"] = round(time.perf_counter() - started, 2)

    payload.update(
        status="OK" if not defects else "REGISTRY_DEFECT",
        hypotheses=hyp_rows,
        families=[{k: v for k, v in f.items() if k != "rows"} for f in families],
        heavy=heavy,
        sim_parity={k: v for k, v in parity.items() if k != "rows"},
        correlation=corr,
        timing=timing,
        method={
            "primary_correction": "Holm over primary hypotheses on post-registration (unseen) trades",
            "exploratory_correction": "family-wise max-t null + Benjamini-Hochberg q + deflated Sharpe "
                                      "(noise and cross-trial) + anchored walk-forward with embargo = max hold",
            "ci": "CR1 cluster-robust by 1 h block",
            "regimes": "expanding quantiles (no full-sample thresholds)",
            "capacity": "one position per hypothesis/config, sequential",
            "tape_holes": "paths crossing a >60 s tape hole are censored, not filled",
            "history": "tape, cross-venue minutes and AI calls are the union of Tier A partitions, frozen "
                       "mirror archives and the live mirror, de-duplicated (mirror wins) and bounded by the "
                       "epoch start; epoch_coverage_share = present seconds / seconds since epoch start",
            "xvl_window": "XVL hypotheses, controls and the XVL family only take signals inside the cross-venue "
                          "span, so a control never scores a longer window than its primary",
        },
    )
    hyp_table = pd.DataFrame([_flat_hypothesis(r) for r in hyp_rows])
    fam_table = pd.DataFrame([r for f in families for r in (f.get("rows") or [])])
    wf_table = pd.DataFrame([{"family_id": f["id"], **fold} for f in families
                             for fold in ((f.get("walk_forward") or {}).get("folds") or [])])
    tables = {
        "hypotheses": hyp_table,
        "hypothesis_trades": hyp_trades,
        "family_tests": fam_table,
        "walk_forward": wf_table,
        "correlation": pd.DataFrame(corr),
        "sim_parity": pd.DataFrame(parity.get("rows") or []),
    }
    return _clean(payload), tables


def _flat_hypothesis(r: dict) -> dict:
    out = {k: r.get(k) for k in ("id", "kind", "primary", "family", "registered_at", "spec_hash", "control_of",
                                 "expect", "signals", "filled", "censored", "verdict", "holm_p_unseen")}
    for scope in ("full", "unseen"):
        s = r.get(scope) or {}
        for k in ("n", "mean_bp", "ci_lo_bp", "ci_hi_bp", "p_cluster", "win_rate", "net_usd", "profit_factor",
                  "max_dd_usd", "per_day", "sharpe", "first_half_mean_bp", "second_half_mean_bp"):
            out[f"{scope}_{k}"] = s.get(k)
    out["dsr"] = (r.get("dsr") or {}).get("dsr")
    return out
