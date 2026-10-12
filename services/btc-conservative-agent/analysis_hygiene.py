"""HM-BATCH3 (ANALYZER-REVIEW-20261012 #1-#5): default analyzer trade hygiene.

* forced closes (paper_pnl_canon.FORCED_EXIT_REASONS: admin closes, deploy flattens) are excluded;
* the 8 Oct 22:19 -> 10 Oct 00:46 AEDT late-exit window is excluded (stale-trigger bookings);
* every row gets ``net_bp`` (price-based, unrounded) so reports can use bp instead of 1-cent USD.
Set ANALYZER_INCLUDE_FORCED=1 / ANALYZER_INCLUDE_LATE_WINDOW=1 to opt back in.
"""
from __future__ import annotations

import os
from typing import Any

from paper_pnl_canon import FORCED_EXIT_REASONS

try:
    from late_window_reprice import DATA as _LATE
except Exception:  # pragma: no cover
    _LATE = {"window_ts": [0, 0], "trades": {}}

LATE_W0, LATE_W1 = (_LATE.get("window_ts") or [0, 0])[:2]
SUMMARY: dict = {}


def _flag(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes")


def apply(trades, *, include_forced: bool | None = None, include_late: bool | None = None):
    import numpy as np
    import pandas as pd
    if trades is None or getattr(trades, "empty", True):
        SUMMARY.update({"rows_in": 0, "rows_out": 0})
        return trades
    include_forced = _flag(os.getenv("ANALYZER_INCLUDE_FORCED")) if include_forced is None else include_forced
    include_late = _flag(os.getenv("ANALYZER_INCLUDE_LATE_WINDOW")) if include_late is None else include_late
    df = trades.copy()
    n0 = len(df)
    reason = df.get("exit_reason", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.upper()
    forced = reason.isin(FORCED_EXIT_REASONS)
    for col in ("forced_close", "deploy_flatten"):
        if col in df.columns:
            forced |= df[col].map(_flag)
    tid = df.get("trade_id", pd.Series("", index=df.index)).fillna("").astype(str)
    late = tid.isin(set((_LATE.get("trades") or {}).keys()))
    for col in ("close_ts", "exit_ts"):
        if col in df.columns:
            ts = pd.to_numeric(df[col], errors="coerce")
            late |= (ts >= LATE_W0) & (ts <= LATE_W1)
            break
    # bp basis (ANALYZER-REVIEW #5): prefer the bot's price bp; else net $ / notional.
    bp = pd.to_numeric(df["net_pnl_bp"], errors="coerce") if "net_pnl_bp" in df.columns else pd.Series(np.nan, index=df.index)
    if "net_pnl_usd" in df.columns:
        usd = pd.to_numeric(df.get("net_pnl_usd_raw", df["net_pnl_usd"]), errors="coerce")
        notional = None
        for col in ("notional_usd", "policy_notional_usd"):
            if col in df.columns:
                notional = pd.to_numeric(df[col], errors="coerce")
                break
        if notional is None and {"margin_usdt", "leverage"} <= set(df.columns):
            notional = pd.to_numeric(df["margin_usdt"], errors="coerce") * pd.to_numeric(df["leverage"], errors="coerce")
        if notional is not None:
            bp = bp.fillna(usd / notional.where(notional > 0) * 1e4)
    df["net_bp"] = bp
    df["late_window"] = late
    df["forced_close_excluded"] = forced
    keep = pd.Series(True, index=df.index)
    if not include_forced:
        keep &= ~forced
    if not include_late:
        keep &= ~late
    out = df[keep]
    SUMMARY.clear()
    SUMMARY.update({"schema": "analyzer_hygiene_v1", "rows_in": n0, "rows_out": int(len(out)),
                    "forced_excluded": 0 if include_forced else int(forced.sum()),
                    "late_window_excluded": 0 if include_late else int((late & ~forced).sum()),
                    "net_bp_known": int(out["net_bp"].notna().sum()),
                    "mean_net_bp": round(float(out["net_bp"].mean()), 4) if out["net_bp"].notna().any() else None,
                    "late_window_aedt": _LATE.get("window_aedt"), "basis": "net_bp (price bp, unrounded)"})
    return out
