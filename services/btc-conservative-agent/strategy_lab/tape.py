"""Dense 1 s Bitfinex L1 tape and cross-venue mids for the strategy lab.

Port of the tape section of ``diagnostics/tile2_20261001/sim2.py``:
``market_microstructure_1s.jsonl`` (+ numeric rotations) becomes dense
per-second arrays with forward-filled quotes, a ``present`` mask and a hole
run length so paths crossing a tape hole longer than ``HOLE_CENSOR_SEC`` are
censored instead of being filled with stale quotes.

Closed rotations are immutable, so each one is parsed once and cached as a
small ``.npz`` keyed by (name, size, mtime); only the active file is re-read
every cycle.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

BFX_TAPE_FILE = "market_microstructure_1s.jsonl"
HOLE_CENSOR_SEC = 60
ATR_BAR_SEC = 180
ATR_PERIOD = 14
ATR_MIN_PRESENT = 120


def generations(path: str) -> list:
    """Closed numeric rotations oldest-first, then the active file."""
    rotated = []
    for candidate in glob.glob(glob.escape(path) + ".*"):
        suffix = candidate.rsplit(".", 1)[-1]
        if suffix.isdigit():
            rotated.append((int(suffix), candidate))
    return [p for _, p in sorted(rotated)] + ([path] if os.path.isfile(path) else [])


def _parse_tape_file(path: str) -> dict:
    ts, bid, ask, buy, sell = [], [], [], [], []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("valid_bbo") is not True:
                continue
            b, a = row.get("bid"), row.get("ask")
            if not isinstance(b, (int, float)) or not isinstance(a, (int, float)) or not 0 < b <= a:
                continue
            try:
                ts.append(int(row["bucket_ts"]))
            except (KeyError, TypeError, ValueError):
                continue
            bid.append(float(b))
            ask.append(float(a))
            buy.append(float(row.get("buy_qty") or 0.0))
            sell.append(float(row.get("sell_qty") or 0.0))
    return {
        "ts": np.asarray(ts, dtype=np.int64),
        "bid": np.asarray(bid, dtype=np.float64),
        "ask": np.asarray(ask, dtype=np.float64),
        "buy": np.asarray(buy, dtype=np.float64),
        "sell": np.asarray(sell, dtype=np.float64),
    }


def _cache_key(path: str) -> str:
    st = os.stat(path)
    raw = f"{os.path.basename(path)}|{st.st_size}|{st.st_mtime_ns}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _load_file(path: str, cache_dir: Optional[str], cacheable: bool) -> tuple:
    """Return (arrays, cache_hit)."""
    cache_path = None
    if cacheable and cache_dir:
        cache_path = os.path.join(cache_dir, f"tape_{_cache_key(path)}.npz")
        if os.path.isfile(cache_path):
            try:
                with np.load(cache_path) as z:
                    return {k: z[k] for k in ("ts", "bid", "ask", "buy", "sell")}, True
            except Exception:
                pass
    arrays = _parse_tape_file(path)
    if cache_path:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = cache_path + ".tmp.npz"
            np.savez(tmp, **arrays)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    return arrays, False


@dataclass
class Tape:
    t0: int
    n: int
    bid: np.ndarray
    ask: np.ndarray
    present: np.ndarray
    holerun: np.ndarray
    buy: np.ndarray
    sell: np.ndarray
    files: list = field(default_factory=list)
    cache_hits: int = 0

    def __post_init__(self) -> None:
        self.mid = (self.bid + self.ask) / 2.0
        with np.errstate(invalid="ignore", divide="ignore"):
            self.spread_bp = (self.ask - self.bid) / self.mid * 1e4
        bad = self.holerun > HOLE_CENSOR_SEC
        nxt = np.full(self.n + 1, self.n + 10, dtype=np.int64)
        idx = np.flatnonzero(bad)
        # next_bad[k] = first censored second at or after k
        if idx.size:
            pos = np.searchsorted(idx, np.arange(self.n), side="left")
            valid = pos < idx.size
            nxt[: self.n][valid] = idx[pos[valid]]
        self.next_bad = nxt
        self._atr = None

    @property
    def t1(self) -> int:
        return self.t0 + self.n - 1

    def index(self, ts) -> np.ndarray:
        return np.asarray(np.ceil(np.asarray(ts, dtype=float)), dtype=np.int64) - self.t0

    def coverage(self) -> dict:
        return {
            "start_ts": int(self.t0),
            "end_ts": int(self.t1),
            "seconds": int(self.n),
            "present_share": round(float(self.present.mean()), 4) if self.n else 0.0,
            "longest_hole_sec": int(self.holerun.max()) if self.n else 0,
            "censored_seconds": int((self.holerun > HOLE_CENSOR_SEC).sum()),
            "files": len(self.files),
            "rotation_cache_hits": int(self.cache_hits),
        }

    def atr_abs(self, ts) -> np.ndarray:
        """3 m Wilder ATR14 on mid, usable only after its bar closes (no look-ahead)."""
        if self._atr is None:
            tsec = self.t0 + np.arange(self.n)
            bar = (tsec // ATR_BAR_SEC) * ATR_BAR_SEC
            ub, start = np.unique(bar, return_index=True)
            end = np.append(start[1:], self.n)
            hi = np.maximum.reduceat(self.mid, start)
            lo = np.minimum.reduceat(self.mid, start)
            last = self.mid[end - 1]
            cnt = np.add.reduceat(self.present.astype(np.int64), start)
            keep = cnt >= ATR_MIN_PRESENT
            ub, hi, lo, last = ub[keep], hi[keep], lo[keep], last[keep]
            prev = np.concatenate([[np.nan], last[:-1]])
            tr = np.nanmax(np.vstack([hi - lo, np.abs(hi - prev), np.abs(lo - prev)]), axis=0)
            atr = np.full(tr.shape, np.nan)
            alpha = 1.0 / ATR_PERIOD
            acc = np.nan
            for i, v in enumerate(tr):
                acc = v if not np.isfinite(acc) else acc + alpha * (v - acc)
                if i >= ATR_PERIOD - 1:
                    atr[i] = acc
            self._atr = (ub + ATR_BAR_SEC, atr)
        close, atr = self._atr
        k = np.searchsorted(close, np.asarray(ts, dtype=float), side="right") - 1
        out = np.where(k >= 0, atr[np.maximum(k, 0)], np.nan)
        return out


def build_tape(ts: np.ndarray, bid: np.ndarray, ask: np.ndarray, buy=None, sell=None,
               files=None, cache_hits: int = 0) -> Optional[Tape]:
    ts = np.asarray(ts, dtype=np.int64)
    if ts.size == 0:
        return None
    order = np.argsort(ts, kind="stable")
    ts = ts[order]
    # duplicate buckets: last write wins
    keep = np.append(ts[1:] != ts[:-1], True)
    ts = ts[keep]
    t0, t1 = int(ts[0]), int(ts[-1])
    n = t1 - t0 + 1
    idx = ts - t0

    def dense(values, fill=np.nan):
        out = np.full(n, fill, dtype=np.float64)
        if values is not None:
            out[idx] = np.asarray(values, dtype=np.float64)[order][keep]
        return out

    b, a = dense(bid), dense(ask)
    present = np.isfinite(b)
    # forward fill quotes
    pos = np.where(present, np.arange(n), 0)
    np.maximum.accumulate(pos, out=pos)
    b, a = b[pos], a[pos]
    run = np.zeros(n, dtype=np.int64)
    if n:
        gap = ~present
        # length of the current run of missing seconds
        csum = np.cumsum(gap)
        reset = np.where(present, csum, 0)
        np.maximum.accumulate(reset, out=reset)
        run = csum - reset
    return Tape(t0=t0, n=n, bid=b, ask=a, present=present, holerun=run,
                buy=np.nan_to_num(dense(buy, 0.0)), sell=np.nan_to_num(dense(sell, 0.0)),
                files=list(files or []), cache_hits=cache_hits)


def load_bitfinex_tape(data_dir: str, start_ts: Optional[float] = None, end_ts: Optional[float] = None,
                       cache_dir: Optional[str] = None) -> Optional[Tape]:
    paths = generations(os.path.join(data_dir, BFX_TAPE_FILE))
    parts, hits, used = [], 0, []
    for path in paths:
        cacheable = path.rsplit(".", 1)[-1].isdigit()
        try:
            arrays, hit = _load_file(path, cache_dir, cacheable)
        except OSError:
            continue
        hits += int(hit)
        if arrays["ts"].size == 0:
            continue
        m = np.ones(arrays["ts"].size, dtype=bool)
        if start_ts is not None:
            m &= arrays["ts"] >= int(start_ts)
        if end_ts is not None:
            m &= arrays["ts"] <= int(end_ts)
        if m.any():
            parts.append({k: v[m] for k, v in arrays.items()})
            used.append(os.path.basename(path))
    if not parts:
        return None
    cat = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    return build_tape(cat["ts"], cat["bid"], cat["ask"], cat["buy"], cat["sell"], files=used, cache_hits=hits)


def cross_venue_mids(data_dir: str, tape: Tape, max_days: int = 14) -> dict:
    """Per-second leader-venue mids aligned to ``tape`` (NaN where unobserved).

    Built on the analyzer's existing cross-venue harness
    (``research.lead_lag_report`` loaders + ``cross_venue_tape.decode_minute``)
    so there is one decoder for the shadow tape.
    """
    import cross_venue_tape as cvt
    from research.lead_lag_report import load_cross_venue_rows

    rows = load_cross_venue_rows(data_dir, max_days=max_days)
    out = {}
    for row in rows:
        decoded = cvt.decode_minute(row)
        for venue, cells in decoded.items():
            if venue == "bfx":
                continue
            arr = out.get(venue)
            if arr is None:
                arr = out[venue] = np.full(tape.n, np.nan)
            for sec, cell in (cells or {}).items():
                i = int(sec) - tape.t0
                mid = cell.get("mid") if isinstance(cell, dict) else None
                if 0 <= i < tape.n and mid is not None:
                    arr[i] = float(mid)
    span = None
    if rows:
        span = {"start_ts": int(rows[0]["minute_ts"]), "end_ts": int(rows[-1]["minute_ts"]) + 59,
                "minutes": len(rows)}
    return {"venues": out, "span": span}
