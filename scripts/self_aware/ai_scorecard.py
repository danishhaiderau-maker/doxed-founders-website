"""Per-call AI log with forward outcomes, and a rolling scorecard.

``res_ai_calls`` (incremental, one row per AI call): prompt id/version,
input fingerprint and reference, raw response, parsed decision, configured and
served model, latency, cost (logged cost when present, otherwise an explicit
estimate), and forward mid returns at 1/5/15/30/60/120 min with the
round-trip half-spread cost. Labels are kept after the mirror rotates its
tape.

``res_ai_scorecard`` (replaced each run): hit rate with Wilson CI and
after-cost bp with an hour-cluster bootstrap CI per horizon x window x slice
(overall, served model, prompt, session, volatility tertile) for the AI
side and its comparators (always-long, random, rule-vote, invert, abstain-
respecting, shadow compact prompt).
"""
from __future__ import annotations

import hashlib
import time

import numpy as np
import pandas as pd

from . import tape as tp
from .facts import iso

INPUT_TOKENS_EST = 1100
OUTPUT_TOKENS_EST = 60
PRICE_PER_M_INPUT = 0.27
PRICE_PER_M_OUTPUT = 1.10
WINDOWS = {"24h": 86400, "7d": 7 * 86400, "all": None}
SOURCES = ["raw_ai_tranche", "raw_ai_tranche_archive", "raw_ai_input", "raw_ai_input_archive", "raw_ai_compact",
           "raw_ai_challengers", "raw_tape_1s", "raw_tape_1s_archive"]


def _union(store, views: list[str], select: str) -> pd.DataFrame:
    frames = []
    for v in views:
        try:
            frames.append(store.frame(select.format(v=v)))
        except Exception:  # noqa: BLE001 - archive views may be absent
            continue
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def load_calls(store) -> pd.DataFrame:
    t = _union(store, ["raw_ai_tranche_archive", "raw_ai_tranche"], """
        SELECT ts, trade_id, shared_ai_call_id, research_lane, event, ai_direction_raw, decision, approved,
               long_score, short_score, win_prob, latency_ms, http_status, ai_error, error_type, ai_failure_class,
               deepseek_model, deepseek_served_model, deepseek_system_fingerprint, bot_version, research_schema,
               coalesce(nullif(full_comment, ''), comment) AS raw_response, filename
        FROM {v} WHERE event = 'AI_DECISION'""")
    if t.empty:
        return t
    t["call_id"] = t["shared_ai_call_id"].where(t["shared_ai_call_id"].fillna("") != "", t["trade_id"])
    dt = pd.to_datetime(t["ts"], utc=True, errors="coerce", format="mixed")
    t["ts_epoch"] = (dt - pd.Timestamp(0, tz="UTC")).dt.total_seconds()
    t = t.dropna(subset=["ts_epoch"])
    t = t.dropna(subset=["call_id"]).sort_values("ts_epoch").drop_duplicates("call_id", keep="last")

    i = _union(store, ["raw_ai_input_archive", "raw_ai_input"], """
        SELECT trade_id, context_fingerprint,
               context->>'$.market_context.multi_tf.agreement' AS mtf,
               context->>'$.market_context.ema_alignment.stack_bull' AS stack_bull,
               context->>'$.market_context.ema_alignment.stack_bear' AS stack_bear,
               context->>'$.sr_bias' AS sr_bias,
               context->>'$.trend_health.bull_score' AS th_bull,
               context->>'$.trend_health.bear_score' AS th_bear,
               filename AS input_file
        FROM {v}""")
    if not i.empty:
        i = i.drop_duplicates("trade_id", keep="last")
        t = t.merge(i, left_on="call_id", right_on="trade_id", how="left", suffixes=("", "_in"))
    ch = _union(store, ["raw_ai_challengers"], """
        SELECT shared_ai_call_id AS call_id, any_value(prompt_id) AS prompt_id, any_value(deepseek_model) AS ch_model,
               any_value(sides::VARCHAR) AS sides
        FROM {v} WHERE row_kind = 'CALL' GROUP BY 1""")
    if not ch.empty:
        t = t.merge(ch, on="call_id", how="left")
    cp = _union(store, ["raw_ai_compact"], """
        SELECT shared_ai_call_id AS call_id, any_value(prompt_id) AS compact_prompt_id, any_value(side) AS compact_side,
               any_value(served_model) AS compact_served_model, any_value(latency_ms) AS compact_latency_ms
        FROM {v} GROUP BY 1""")
    if not cp.empty:
        t = t.merge(cp, on="call_id", how="left")
    for col in ("prompt_id", "sides", "compact_side", "compact_prompt_id", "mtf", "stack_bull", "stack_bear", "sr_bias",
                "th_bull", "th_bear", "context_fingerprint", "input_file"):
        if col not in t.columns:
            t[col] = None
    return t.reset_index(drop=True)


def _side(series_long: pd.Series, series_short: pd.Series) -> np.ndarray:
    return np.sign(_num(series_long).fillna(0).to_numpy() - _num(series_short).fillna(0).to_numpy()).astype(int)


def build_records(calls: pd.DataFrame, tape: tp.Tape | None, now: float) -> pd.DataFrame:
    c = calls
    ts = c["ts_epoch"].to_numpy(float)
    side = _side(c["long_score"], c["short_score"])
    raw = c["ai_direction_raw"].fillna("").str.upper()
    abstain = np.where(raw.eq("NO_TRADE"), 0, side)
    mtf = c["mtf"].map({"BULL_ALIGNED": 1, "BEAR_ALIGNED": -1}).fillna(0).to_numpy()
    stack = np.where(c["stack_bull"].astype(str).str.lower().eq("true"), 1,
                     np.where(c["stack_bear"].astype(str).str.lower().eq("true"), -1, 0))
    sr = c["sr_bias"].map({"LONG_PREFERRED": 1, "SHORT_PREFERRED": -1}).fillna(0).to_numpy()
    th = np.sign(_num(c["th_bull"]).fillna(0).to_numpy() - _num(c["th_bear"]).fillna(0).to_numpy())
    rule = np.sign(mtf + stack + sr + th).astype(int)
    compact = c["compact_side"].map({"LONG": 1, "SHORT": -1}).fillna(0).astype(int).to_numpy()
    rnd = np.array([1 if int(hashlib.sha256(str(x).encode()).hexdigest()[:8], 16) % 2 else -1 for x in c["call_id"]])
    served = c["deepseek_served_model"].where(c["deepseek_served_model"].fillna("") != "", c.get("ch_model"))
    rec = pd.DataFrame({
        "call_id": c["call_id"], "ts": ts, "ts_iso": [iso(x) for x in ts], "lane": c["research_lane"],
        "model_configured": c["deepseek_model"], "model_served": served.fillna(c["deepseek_model"]).fillna("unknown"),
        "system_fingerprint": c["deepseek_system_fingerprint"],
        "prompt_id": c["prompt_id"].fillna("unlogged"), "prompt_version": c["bot_version"],
        "prompt_hash": None, "input_fingerprint": c["context_fingerprint"],
        "input_ref": [f"{f}#trade_id={cid}" if isinstance(f, str) else None for f, cid in zip(c["input_file"], c["call_id"])],
        "raw_response": c["raw_response"].fillna("").str.slice(0, 2000),
        "parsed_direction": raw, "decision": c["decision"], "long_score": _num(c["long_score"]),
        "short_score": _num(c["short_score"]), "ai_error": c["ai_error"], "failure_class": c["ai_failure_class"],
        "latency_ms": _num(c["latency_ms"]), "http_status": c["http_status"],
        "cost_usd": np.nan, "cost_source": "ESTIMATE_TOKENS",
        "cost_usd_est": (INPUT_TOKENS_EST * PRICE_PER_M_INPUT + OUTPUT_TOKENS_EST * PRICE_PER_M_OUTPUT) / 1e6,
        "side_score_led": side, "side_abstain": abstain, "side_rule_vote": rule, "side_random": rnd,
        "side_compact": compact, "compact_prompt_id": c["compact_prompt_id"], "challenger_sides": c["sides"],
        "session": tp.session_of(ts),
    })
    if tape is not None:
        i = tape.index(ts)
        rec["rv15_bp"] = tape.realized_vol(i)
        for h in tp.HORIZONS_SEC:
            ret, cost = tape.forward(i, h)
            lab = tp.HLABEL[h]
            rec[f"fwd_{lab}_bp"] = ret
            rec[f"cost_{lab}_bp"] = cost
        # Matured calls are final even when a tape hole left a horizon unlabeled.
        rec["labels_complete"] = ts + max(tp.HORIZONS_SEC) + 60 < tape.t_end
    else:
        rec["rv15_bp"] = np.nan
        for h in tp.HORIZONS_SEC:
            rec[f"fwd_{tp.HLABEL[h]}_bp"] = np.nan
            rec[f"cost_{tp.HLABEL[h]}_bp"] = np.nan
        rec["labels_complete"] = False
    rec["labeled_at"] = iso(now)
    return rec


STRATEGIES = {
    "AI_SCORE_LED": "side_score_led", "AI_ABSTAIN_RESPECTING": "side_abstain", "INVERT_AI": None,
    "ALWAYS_LONG": None, "RANDOM": "side_random", "RULE_VOTE": "side_rule_vote", "SHADOW_COMPACT": "side_compact",
}


def _strategy_sides(df: pd.DataFrame) -> dict[str, np.ndarray]:
    out = {}
    for name, col in STRATEGIES.items():
        if name == "INVERT_AI":
            out[name] = -df["side_score_led"].to_numpy(int)
        elif name == "ALWAYS_LONG":
            out[name] = np.ones(len(df), dtype=int)
        else:
            out[name] = df[col].to_numpy(int)
    if not np.any(out["SHADOW_COMPACT"] != 0):
        out.pop("SHADOW_COMPACT")
    return out


def scorecard(calls: pd.DataFrame, now: float, *, bootstrap: int = 400) -> pd.DataFrame:
    if calls.empty:
        return pd.DataFrame()
    df = calls.copy()
    df["vol_tertile"] = "unknown"
    rv = df["rv15_bp"].to_numpy(float)
    if np.isfinite(rv).sum() >= 30:
        q1, q2 = np.nanpercentile(rv, [33.3, 66.7])
        df["vol_tertile"] = np.where(~np.isfinite(rv), "unknown", np.where(rv <= q1, "LOW", np.where(rv <= q2, "MID", "HIGH")))
    df["hour"] = (df["ts"] // 3600).astype(np.int64)
    rows = []
    for wname, wsec in WINDOWS.items():
        w = df if wsec is None else df[df["ts"] >= now - wsec]
        if w.empty:
            continue
        sides = _strategy_sides(w)
        slices = [("overall", "all", np.ones(len(w), dtype=bool))]
        for dim in ("model_served", "prompt_id", "session", "vol_tertile"):
            for val in sorted(w[dim].fillna("unknown").astype(str).unique()):
                slices.append((dim, val, (w[dim].fillna("unknown").astype(str) == val).to_numpy()))
        clusters = w["hour"].to_numpy()
        for h in tp.HORIZONS_SEC:
            lab = tp.HLABEL[h]
            ret = w[f"fwd_{lab}_bp"].to_numpy(float)
            cost = w[f"cost_{lab}_bp"].to_numpy(float)
            for dim, val, mask in slices:
                base = {}
                for sname, s in sides.items():
                    m = mask & (s != 0) & np.isfinite(ret)
                    n = int(m.sum())
                    if n == 0:
                        continue
                    gross = s[m] * ret[m]
                    net = gross - cost[m]
                    hits = int((gross > 0).sum())
                    lo, hi = tp.wilson(hits, n)
                    nlo, nhi = tp.cluster_bootstrap(net, clusters[m], b=bootstrap) if n >= 10 else (np.nan, np.nan)
                    base[sname] = float(net.mean())
                    rows.append({"window": wname, "horizon": lab, "horizon_sec": h, "slice_dim": dim, "slice_value": val,
                                 "strategy": sname, "n": n, "n_clusters": int(len(np.unique(clusters[m]))),
                                 "hit_rate": hits / n, "hit_lo": lo, "hit_hi": hi, "gross_bp": float(gross.mean()),
                                 "net_bp": float(net.mean()), "net_lo": nlo, "net_hi": nhi})
                for r in rows[-len(base):] if base else []:
                    r["vs_always_long_bp"] = r["net_bp"] - base.get("ALWAYS_LONG", np.nan)
                    r["vs_random_bp"] = r["net_bp"] - base.get("RANDOM", np.nan)
                    r["vs_rule_vote_bp"] = r["net_bp"] - base.get("RULE_VOTE", np.nan)
    return pd.DataFrame(rows)


def run(store, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    started = time.time()
    calls = load_calls(store)
    if calls.empty:
        return {"status": "NO_DATA", "calls": 0}
    existing = set()
    if store.table_exists("res_ai_calls"):
        existing = {r["call_id"] for r in store.read("SELECT call_id FROM res_ai_calls WHERE labels_complete")}
    todo = calls[~calls["call_id"].isin(existing)]
    labeled_new = 0
    if len(todo):
        tmin = float(todo["ts_epoch"].min()) - 1200
        tape = tp.load(store, tmin)
        rec = build_records(todo, tape, now)
        labeled_new = int(rec["labels_complete"].sum())
        store.upsert("ai_calls", rec, "call_id", sources=SOURCES,
                     window=(iso(float(calls["ts_epoch"].min())), iso(float(calls["ts_epoch"].max()))),
                     compute_ms=int((time.time() - started) * 1000),
                     note=f"cost_usd_est assumes {INPUT_TOKENS_EST}+{OUTPUT_TOKENS_EST} tokens at "
                          f"${PRICE_PER_M_INPUT}/${PRICE_PER_M_OUTPUT} per M (provider cost is not logged)")
    allrec = store.frame("SELECT * FROM res_ai_calls")
    card = scorecard(allrec, now)
    store.publish("ai_scorecard", card, sources=["res_ai_calls"],
                  window=(iso(float(allrec["ts"].min())), iso(float(allrec["ts"].max()))),
                  compute_ms=int((time.time() - started) * 1000),
                  note="hit=Wilson 95%; net CI=hour-cluster bootstrap 95%; comparators share the same calls")
    return {"status": "OK", "calls": int(len(allrec)), "processed": int(len(todo)), "labeled_new": labeled_new,
            "scorecard_rows": int(len(card)), "ms": int((time.time() - started) * 1000)}


def headline(store) -> dict:
    """Compact AI verdict for health/digest: AI vs comparators at 5m and 60m (all-time + 24h)."""
    if not store.table_exists("res_ai_scorecard"):
        return {}
    rows = store.read("""SELECT "window", horizon, strategy, n, hit_rate, net_bp, net_lo, net_hi FROM res_ai_scorecard
                         WHERE slice_dim='overall' AND horizon IN ('5m','60m') AND "window" IN ('all','24h')""")
    out: dict = {}
    for r in rows:
        out.setdefault(r["window"], {}).setdefault(r["horizon"], {})[r["strategy"]] = {
            k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k in ("n", "hit_rate", "net_bp", "net_lo", "net_hi")}
    return out


def json_safe(value):
    """Strict-JSON copy: NaN/inf -> None, numpy scalars -> Python, everything else str()."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [json_safe(v) for v in value.tolist()]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if value is None or isinstance(value, str):
        return value
    if value is pd.NaT:
        return None
    return str(value)
