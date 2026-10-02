"""Direction sources for strategy-lab hypotheses.

* AI-call sources (``LLM_SCORE``, ``INV_LLM``) rebuilt from the shared AI call
  journal exactly as ``sim2.py`` did (``ai_tranche_log.csv`` AI_DECISION rows).
* Cross-venue lead (XVL) from ``nt_cv.py``: lead_w = mean(leader-venue mid
  return over the last w s) - Bitfinex mid return over the same w s; a trigger
  at second t means "take Bitfinex taker in sign(lead) at t + latency".

Every feature uses data at or before the decision second only.
"""
from __future__ import annotations

import os
import warnings
from typing import Optional

import numpy as np
import pandas as pd

from strategy_lab.tape import Tape

AI_TRANCHE_FILE = "ai_tranche_log.csv"
AI_INPUT_FILE = "ai_input_log.jsonl"


def load_ai_calls(data_dir: str, start_ts: Optional[float] = None) -> pd.DataFrame:
    path = os.path.join(data_dir, AI_TRANCHE_FILE)
    cols = ["ts", "call_id", "long", "short", "gap", "raw"]
    if not os.path.isfile(path):
        return pd.DataFrame(columns=cols)
    usecols = lambda c: c in {"ts", "event", "long_score", "short_score", "shared_ai_call_id", "trade_id",
                              "ai_direction_raw"}
    t = pd.read_csv(path, encoding="latin1", low_memory=False, usecols=usecols)
    if "event" in t.columns:
        t = t[t["event"].astype(str) == "AI_DECISION"]
    out = pd.DataFrame({
        "ts": pd.to_datetime(t.get("ts"), utc=True, errors="coerce").map(lambda x: x.timestamp() if pd.notna(x) else np.nan),
        "call_id": t.get("shared_ai_call_id", pd.Series(index=t.index, dtype=object)).fillna(
            t.get("trade_id", pd.Series(index=t.index, dtype=object))).astype(str),
        "long": pd.to_numeric(t.get("long_score"), errors="coerce"),
        "short": pd.to_numeric(t.get("short_score"), errors="coerce"),
        "raw": t.get("ai_direction_raw", pd.Series(index=t.index, dtype=object)).astype(str).str.upper(),
    })
    out = out[np.isfinite(out["ts"]) & np.isfinite(out["long"]) & np.isfinite(out["short"])]
    if start_ts is not None:
        out = out[out["ts"] >= float(start_ts)]
    out = out.drop_duplicates("call_id").sort_values("ts").reset_index(drop=True)
    out["gap"] = out["long"] - out["short"]
    return out[cols]


def attach_funding(calls: pd.DataFrame, data_dir: str) -> pd.DataFrame:
    """Add ``funding_bp_8h`` from the AI input context of each call (0 when absent)."""
    import json

    rates = {}
    path = os.path.join(data_dir, AI_INPUT_FILE)
    wanted = set(calls["call_id"].astype(str)) if len(calls) else set()
    if wanted and os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"funding"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                cid = str(row.get("trade_id") or "")
                if cid not in wanted:
                    continue
                fu = (row.get("context") or {}).get("funding") or {}
                try:
                    rates[cid] = float(fu.get("rate")) * 1e4
                except (TypeError, ValueError):
                    pass
    out = calls.copy()
    out["funding_bp_8h"] = out["call_id"].astype(str).map(rates).fillna(0.0).astype(float)
    return out


def ai_direction(calls: pd.DataFrame, source: str) -> np.ndarray:
    llm = np.sign(calls["gap"].to_numpy(float)).astype(int)
    if source == "LLM_SCORE":
        return llm
    if source == "INV_LLM":            # the deployed ftf rule: inverted score-led side, ties refused
        return -llm
    raise ValueError(f"unknown AI direction source {source}")


def _ret_bp(x: np.ndarray, w: int) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    if 0 < w < len(x):
        with np.errstate(invalid="ignore", divide="ignore"):
            out[w:] = (x[w:] / x[:-w] - 1.0) * 1e4
    return out


def xvl_lead(tape: Tape, venues: dict, window: int, mode: str = "both_mean") -> np.ndarray:
    """Per-second lead (bp) of the leader venues over Bitfinex."""
    rf = _ret_bp(tape.mid, window)
    leaders = [pd.Series(v).ffill(limit=5).to_numpy() for k, v in sorted(venues.items())
               if k in ("binance", "bybit")]
    if not leaders:
        return np.full(tape.n, np.nan)
    rets = np.vstack([_ret_bp(v, window) for v in leaders])
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        if mode == "both_mean":
            lead = np.nanmean(rets, axis=0) - rf if rets.shape[0] > 1 else rets[0] - rf
        elif mode == "own_momentum":          # control: Bitfinex's own move, no other venue
            lead = rf
        elif mode == "venue_momentum":        # control: leader move ignoring Bitfinex catch-up
            lead = np.nanmean(rets, axis=0)
        else:
            raise ValueError(f"unknown XVL mode {mode}")
    return lead


def xvl_signals(tape: Tape, venues: dict, window: int, threshold_bp: float, mode: str = "both_mean",
                lead: Optional[np.ndarray] = None) -> pd.DataFrame:
    lead = xvl_lead(tape, venues, window, mode) if lead is None else lead
    ok = tape.present & np.isfinite(lead) & (np.abs(lead) >= threshold_bp)
    k = np.flatnonzero(ok)
    return pd.DataFrame({"ts": (tape.t0 + k).astype(float), "side": np.sign(lead[k]).astype(int),
                         "lead_bp": lead[k]})


def rv15_at(tape: Tape, ts: np.ndarray) -> np.ndarray:
    """Realised vol of the last 15 closed 1 m mid returns (bp) before each ts."""
    ts = np.asarray(ts, float)
    m_end = np.arange(((tape.t0 // 60) + 1) * 60 - 1, tape.t1 + 1, 60)
    closes = tape.mid[m_end - tape.t0]
    lr = np.diff(np.log(closes)) * 1e4
    sq = np.concatenate([[0.0], np.cumsum(np.nan_to_num(lr) ** 2)])
    m = np.searchsorted(m_end, ts - 1, side="right")       # closed minutes before the decision
    out = np.full(len(ts), np.nan)
    ok = m >= 16
    hi = m[ok] - 1
    out[ok] = np.sqrt(sq[hi] - sq[hi - 15])
    return out
