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


# ---------------------------------------------------------------------------- all epochs (bp)
def _clustered(vals_ts):
    import math
    n = len(vals_ts)
    if not n:
        return {"n": 0, "mean_bp": None, "ci90_lo": None, "ci90_hi": None, "wr": None}
    vals = [v for v, _ in vals_ts]
    mean = sum(vals) / n
    cl: dict = {}
    for v, t in vals_ts:
        cl.setdefault(int((t or 0) // 3600), []).append(v - mean)
    g = len(cl)
    lo = hi = None
    if n >= 2 and g >= 2:
        se = math.sqrt(sum(sum(c) ** 2 for c in cl.values()) / (n * n) * g / (g - 1))
        lo, hi = round(mean - 1.6449 * se, 4), round(mean + 1.6449 * se, 4)
    return {"n": n, "mean_bp": round(mean, 4), "sum_bp": round(sum(vals), 4), "ci90_lo": lo, "ci90_hi": hi,
            "wr": round(100.0 * sum(1 for v in vals if v > 0) / n, 2)}


def all_epochs_bp_study(data_root: str, trades_file: str = "trades_3factor.csv") -> dict:
    """ANALYZER-REVIEW #4: every epoch (current file unfiltered + research_archive/pre_epoch/*), hygiene
    applied, reported in bp with hour-clustered 90% CIs per tile and per epoch. The DISLOC / DISLOC-FOI
    store fold was removed with Tiles 6/7 (23 Oct 2026, R9); their ledgers are retired history (R5)."""
    import glob
    import os
    import pandas as pd
    frames, sources = [], []
    paths = [os.path.join(data_root, trades_file)] + sorted(
        glob.glob(os.path.join(data_root, "research_archive", "pre_epoch", "*", trades_file)))
    for p in paths:
        if not os.path.isfile(p):
            continue
        try:
            f = pd.read_csv(p, low_memory=False, on_bad_lines="skip")
        except Exception:
            continue
        epoch = os.path.basename(os.path.dirname(p)) if "pre_epoch" in p else "current_file"
        f["_epoch_source"] = epoch
        frames.append(f)
        sources.append({"path": p, "rows": int(len(f)), "epoch_source": epoch})
    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not trades.empty and "trade_id" in trades.columns:
        trades = trades.drop_duplicates(subset=["trade_id"], keep="last")
    if not trades.empty and "net_pnl_usd" in trades.columns:
        trades = trades[pd.to_numeric(trades["net_pnl_usd"], errors="coerce").notna()]
    clean = apply(trades, include_forced=False, include_late=False) if not trades.empty else trades
    summary = dict(SUMMARY)
    rows = []
    excluded_pending = 0
    if not clean.empty:
        lane_col = next((c for c in ("research_lane", "lane", "tile") if c in clean.columns), None)
        ts_col = next((c for c in ("close_ts", "exit_ts", "ts") if c in clean.columns), None)
        ts = pd.to_numeric(clean[ts_col], errors="coerce") if ts_col else pd.Series(0, index=clean.index)
        if ts_col and ts.isna().all():
            ts = pd.to_datetime(clean[ts_col], errors="coerce", utc=True).astype("int64") / 1e9
        import paper_fill as _pf
        records = clean.to_dict("records")
        for rec, lane, epoch, bp, t in zip(records, clean[lane_col] if lane_col else ["?"] * len(clean),
                                           clean["_epoch_source"], clean["net_bp"], ts):
            # fresh data only from lanes priced through paper_fill (research output, not P&L), and only rows whose
            # every entry/exit leg passes the SAME strict rule the monitors use (paper_fill.row_fresh_strict);
            # the lane check alone let FORCED_NO_BOOK / unpriced-leg / pre-epoch rows into the analyzer count.
            if epoch == "current_file" and not (_pf.fresh_data_eligible(str(lane)) and _pf.row_fresh_strict(rec)):
                excluded_pending += 1
                continue
            if pd.notna(bp):
                rows.append((str(lane), str(epoch), float(bp), float(t) if pd.notna(t) else 0.0))
    by_tile: dict = {}
    by_epoch: dict = {}
    for lane, epoch, bp, t in rows:
        by_tile.setdefault(lane, []).append((bp, t))
        by_epoch.setdefault(epoch, []).append((bp, t))
    return {"schema": "analyzer_all_epochs_bp_v1", "basis": "net bp (price, unrounded); fee 0",
            "hygiene": summary, "sources": sources,
            "excluded_pending_paper_fill_migration": excluded_pending,
            "overall": _clustered([(bp, t) for _, _, bp, t in rows]),
            "by_tile": {k: _clustered(v) for k, v in sorted(by_tile.items())},
            "by_epoch": {k: _clustered(v) for k, v in sorted(by_epoch.items())}}
