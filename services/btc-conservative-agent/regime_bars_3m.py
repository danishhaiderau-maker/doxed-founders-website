"""Causal 3-minute Bitfinex bar engine, regime classifier and CVD triggers (GS-20261004 tiles).

Execution infrastructure, not a tile. One process-wide engine is fed every 1 s
microstructure bucket the bot already writes (``market_microstructure_1s.jsonl``
rows: ``bucket_ts, fresh, valid_bbo, bid, ask, buy_qty, sell_qty``) and hydrated
at boot from that file's tail, so the trailing-24 h ATR percentile is available
without a new runtime data file.

The arithmetic replicates the Grok Strategist reference that the
pre-registrations PREREG-GS-20261004 / -B froze
(scripts/analysis/a02_bars_features.py, dynlib.py):

* 3 m buckets of the 1 s mid; a second's mid counts while the last valid quote
  is at most 60 s old; ``bar_ok`` needs >= 90 valid seconds; empty bars carry
  the previous close.
* ATR14 / ADX14 are Wilder EWMs (alpha 1/14, adjust=False, min_periods 14,
  pandas NaN semantics); ``atr_bp = ATR14 / close * 1e4``.
* ``atr_pct_rank`` = rolling 480-bar (min 160) average-method percentile rank
  of ATR14/close, x100 (the trailing 24 h).
* ``spread_bp`` = the last second's (ask - bid) / bid * 1e4.
* CVD = cumulative (buy - sell) taker volume per bar; 20-bar OLS slopes of CVD
  and of the close; DIVERGENCE +1 when price slope < 0 and CVD slope > 0, -1 on
  the reverse; TREND = sign(CVD slope). Scores are 0 on bars that are not ok.
  An event is a bar whose score is non-zero and differs from the previous bar.
* Regime: VIOLENT if ATR pct >= violent_pct or spread >= violent_spread, then
  TREND if ADX >= trend_adx (when a TREND cell is declared), else QUIET; a
  missing input never classifies (None is skipped).
"""
from __future__ import annotations

import glob
import json
import math
import os
import threading
from collections import deque
from typing import Any, Iterable, Mapping, Optional

BAR_SEC = 180
MIN_VALID_SECONDS = 90
QUOTE_MAX_AGE_SEC = 60
TAPE_STALE_SEC = 3.5
WILDER_N = 14
PCT_WINDOW = 480
PCT_MIN = 160
CVD_LOOKBACK = 20
MAX_BARS = 720
SHOCK_WINDOW_SEC = 60
SHOCK_MIN_SAMPLES = 30
HYDRATE_SECONDS = 26 * 3600
ENGINE_SCHEMA = "regime_bars_3m_v1"

QUIET, TREND, VIOLENT = "QUIET", "TREND", "VIOLENT"
DIVERGENCE, TREND_SCORE = "CVD_DIVERGENCE_20", "CVD_TREND_20"


class _Ewm:
    """pandas ``ewm(alpha, adjust=False, min_periods=n, ignore_na=False).mean()`` one value at a time."""

    __slots__ = ("alpha", "min_periods", "value", "old_wt", "nobs")

    def __init__(self, alpha: float, min_periods: int) -> None:
        self.alpha, self.min_periods = float(alpha), int(min_periods)
        self.value: Optional[float] = None
        self.old_wt = 1.0
        self.nobs = 0

    def update(self, x: Optional[float]) -> Optional[float]:
        obs = x is not None and math.isfinite(x)
        self.nobs += 1 if obs else 0
        if self.value is not None:
            self.old_wt *= 1.0 - self.alpha
            if obs:
                if self.value != x:
                    self.value = (self.old_wt * self.value + self.alpha * x) / (self.old_wt + self.alpha)
                self.old_wt = 1.0
        elif obs:
            self.value = float(x)
        return self.value if self.nobs >= self.min_periods else None


def _slope(values) -> Optional[float]:
    n = len(values)
    if n < CVD_LOOKBACK or any(v is None for v in values):
        return None
    vals = list(values)[-CVD_LOOKBACK:]
    xs = [i - (CVD_LOOKBACK - 1) / 2.0 for i in range(CVD_LOOKBACK)]
    sxx = sum(x * x for x in xs)
    return sum(x * v for x, v in zip(xs, vals)) / sxx


def _pct_rank(window) -> Optional[float]:
    vals = [v for v in window if v is not None]
    if len(vals) < PCT_MIN or window[-1] is None:
        return None
    last = window[-1]
    less = sum(1 for v in vals if v < last)
    equal = sum(1 for v in vals if v == last)
    return (less + (equal + 1) / 2.0) / len(vals) * 100.0


def _sign(v: Optional[float]) -> int:
    if v is None:
        return 0
    return 1 if v > 0 else -1 if v < 0 else 0


def classify_regime(features: Mapping[str, Any] | None, *, violent_pct: float, violent_spread_bp: float,
                    trend_adx: Optional[float]) -> str:
    """dynlib.regime / gslib.is_violent: None inputs never classify."""
    f = features or {}
    vp, sp, adx = f.get("atr_pct_rank"), f.get("spread_bp"), f.get("adx")
    if (vp is not None and vp >= violent_pct) or (sp is not None and sp >= violent_spread_bp):
        return VIOLENT
    if trend_adx is not None and adx is not None and adx >= trend_adx:
        return TREND
    return QUIET


class RegimeBars3m:
    def __init__(self, max_bars: int = MAX_BARS) -> None:
        self._lock = threading.RLock()
        self.bars: deque = deque(maxlen=int(max_bars))
        self._cur: Optional[dict] = None
        self._last_ts: Optional[int] = None
        self._last_quote: Optional[tuple] = None   # (ts, bid, ask)
        # Dense per-second forward-filled mid (no age cap, like Tape.mid_ff)
        # for the 60 s volatility-shock inputs of the exit stacks.
        self._mids: deque = deque(maxlen=SHOCK_WINDOW_SEC + 2)
        self._prev_close: Optional[float] = None
        self._prev_h: Optional[float] = None
        self._prev_l: Optional[float] = None
        self._atr = _Ewm(1.0 / WILDER_N, WILDER_N)
        self._pdm = _Ewm(1.0 / WILDER_N, WILDER_N)
        self._ndm = _Ewm(1.0 / WILDER_N, WILDER_N)
        self._adx = _Ewm(1.0 / WILDER_N, WILDER_N)
        self._atr_ratio: deque = deque(maxlen=PCT_WINDOW)
        self._cvd = 0.0
        self._cvd_hist: deque = deque(maxlen=CVD_LOOKBACK)
        self._close_hist: deque = deque(maxlen=CVD_LOOKBACK)
        self._prev_scores = {DIVERGENCE: 0, TREND_SCORE: 0}
        self.seq = 0
        self.hydrated = False
        self.hydrated_seconds = 0
        self.hydrate_error: Optional[str] = None
        self.rows_seen = 0
        self._pending: deque = deque(maxlen=7200)

    # ------------------------------------------------------------------ feed
    def observe_row(self, row: Mapping[str, Any]) -> bool:
        """Live 1 s bucket. Rows arriving before boot hydration finishes are replayed after it."""
        with self._lock:
            if not self.hydrated:
                self._pending.append(dict(row))
                return False
            return self._observe(row)

    def hydrate(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Replay durable history (sorted by bucket_ts), then the rows buffered while it loaded."""
        count = 0
        with self._lock:
            for row in rows:
                count += 1 if self._observe(row) else 0
            while self._pending:
                self._observe(self._pending.popleft())
            self.hydrated = True
            self.hydrated_seconds = count
        return count

    def _observe(self, row: Mapping[str, Any]) -> bool:
        try:
            ts = int(row.get("bucket_ts"))
        except (TypeError, ValueError, AttributeError):
            return False
        if self._last_ts is not None and ts <= self._last_ts:
            return False
        if self._last_ts is not None and self._last_quote is not None:
            qmid = (self._last_quote[1] + self._last_quote[2]) / 2.0
            for sec in range(max(self._last_ts + 1, ts - SHOCK_WINDOW_SEC - 1), ts):
                self._mids.append((sec, qmid))
        if self._last_ts is not None and ts > self._last_ts + 1 and self._last_quote is not None:
            # Canonical loader is a dense per-second grid: absent seconds carry the forward-filled
            # quote (while it is <= QUOTE_MAX_AGE_SEC old) into every bar they touch.
            qts, qb, qa = self._last_quote
            sec, end = self._last_ts + 1, min(ts - 1, qts + QUOTE_MAX_AGE_SEC)
            while sec <= end:
                self._contribute(sec - sec % BAR_SEC, (qb + qa) / 2.0, (qa - qb) / qb * 1e4, 0.0, 0.0, False)
                sec = sec - sec % BAR_SEC + BAR_SEC
        self._last_ts = ts
        self.rows_seen += 1
        bid, ask = _num(row.get("bid")), _num(row.get("ask"))
        age = _num(row.get("source_age_sec"))
        # research/fill_model._row_fresh + Tape.valid (canonical loader)
        valid = (row.get("fresh", True) is not False and row.get("valid_bbo", True) is not False
                 and (age is None or age <= TAPE_STALE_SEC) and bid is not None
                 and ask is not None and bid > 0 and ask >= bid)
        if valid:
            self._last_quote = (ts, bid, ask)
        if self._last_quote is not None:
            self._mids.append((ts, (self._last_quote[1] + self._last_quote[2]) / 2.0))
        mid = spread = None
        if self._last_quote is not None and ts - self._last_quote[0] <= QUOTE_MAX_AGE_SEC:
            _, qb, qa = self._last_quote
            mid = (qb + qa) / 2.0
            spread = (qa - qb) / qb * 1e4
        self._contribute(ts - ts % BAR_SEC, mid, spread, _num(row.get("buy_qty")) or 0.0,
                         _num(row.get("sell_qty")) or 0.0, valid)
        return True

    def _contribute(self, bucket: int, mid: Optional[float], spread: Optional[float], buy: float, sell: float,
                    valid: bool) -> None:
        if self._cur is not None and bucket != self._cur["bucket"]:
            self._close_until(bucket)
        if self._cur is None:
            self._cur = {"bucket": bucket, "o": None, "h": None, "l": None, "c": None,
                         "buy": 0.0, "sell": 0.0, "nvalid": 0, "spread_bp": None}
        cur = self._cur
        cur["buy"] += buy
        cur["sell"] += sell
        cur["nvalid"] += 1 if valid else 0
        if spread is not None:
            cur["spread_bp"] = spread
        if mid is not None:
            cur["o"] = mid if cur["o"] is None else cur["o"]
            cur["h"] = mid if cur["h"] is None else max(cur["h"], mid)
            cur["l"] = mid if cur["l"] is None else min(cur["l"], mid)
            cur["c"] = mid

    def _close_until(self, next_bucket: int) -> None:
        bucket = self._cur["bucket"]
        self._finish(self._cur)
        bucket += BAR_SEC
        # Reference reindexes the full bar range: empty bars carry the close.
        gap = 0
        while bucket < next_bucket and gap < PCT_WINDOW:
            self._finish({"bucket": bucket, "o": None, "h": None, "l": None, "c": None,
                          "buy": 0.0, "sell": 0.0, "nvalid": 0, "spread_bp": None})
            bucket += BAR_SEC
            gap += 1
        self._cur = None

    def _finish(self, raw: dict) -> None:
        c = raw["c"] if raw["c"] is not None else self._prev_close
        if c is None:
            return
        h = raw["h"] if raw["h"] is not None else c
        l_ = raw["l"] if raw["l"] is not None else c
        bar_ok = raw["nvalid"] >= MIN_VALID_SECONDS
        prev_c = self._prev_close
        tr = h - l_ if prev_c is None else max(h - l_, abs(h - prev_c), abs(l_ - prev_c))
        atr = self._atr.update(tr)
        if self._prev_h is None:
            pdm = ndm = 0.0
        else:
            up, dn = h - self._prev_h, self._prev_l - l_
            pdm = up if (up > dn and up > 0) else 0.0
            ndm = dn if (dn > up and dn > 0) else 0.0
        pdm_w, ndm_w = self._pdm.update(pdm), self._ndm.update(ndm)
        dx = None
        if atr and pdm_w is not None and ndm_w is not None:
            pdi, ndi = 100.0 * pdm_w / atr, 100.0 * ndm_w / atr
            dx = 100.0 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else None
        adx = self._adx.update(dx)
        self._atr_ratio.append(atr / c if atr is not None and c else None)
        pct = _pct_rank(list(self._atr_ratio))
        self._cvd += raw["buy"] - raw["sell"]
        self._cvd_hist.append(self._cvd)
        self._close_hist.append(c)
        cs, ps = _slope(self._cvd_hist), _slope(self._close_hist)
        div = 1 if (ps is not None and cs is not None and ps < 0 and cs > 0) else (
            -1 if (ps is not None and cs is not None and ps > 0 and cs < 0) else 0)
        trend = _sign(cs)
        if not bar_ok:
            div = trend = 0
        events = {}
        for name, score in ((DIVERGENCE, div), (TREND_SCORE, trend)):
            prev = self._prev_scores[name]
            events[name] = score if (score != 0 and score != prev) else 0
            self._prev_scores[name] = score
        self.seq += 1
        self.bars.append({
            "seq": self.seq, "bucket": raw["bucket"], "close_ts": raw["bucket"] + BAR_SEC,
            "available_ts": raw["bucket"] + BAR_SEC + 1, "c": c, "h": h, "l": l_,
            "bar_ok": bar_ok, "nvalid": raw["nvalid"],
            "atr_abs": atr, "atr_bp": (atr / c * 1e4) if atr is not None else None,
            "atr_pct_rank": pct, "adx": adx, "spread_bp": raw["spread_bp"],
            "cvd": self._cvd, "cvd_slope": cs, "price_slope": ps,
            "scores": {DIVERGENCE: div, TREND_SCORE: trend}, "events": events,
        })
        self._prev_close, self._prev_h, self._prev_l = c, h, l_

    # ------------------------------------------------------------------ read
    def latest(self, at_ts: Optional[float] = None) -> Optional[dict]:
        """Last bar available at ``at_ts`` (close + 1 s, no lookahead); the newest bar when None."""
        with self._lock:
            for bar in reversed(self.bars):
                if at_ts is None or bar["available_ts"] <= float(at_ts):
                    return dict(bar)
        return None

    def bars_since(self, after_ts: float, until_ts: Optional[float] = None) -> list:
        """Bars whose availability falls in (after_ts, until_ts], each with its previous bar's scores."""
        out = []
        with self._lock:
            rows = list(self.bars)
        for i, bar in enumerate(rows):
            if bar["available_ts"] <= float(after_ts):
                continue
            if until_ts is not None and bar["available_ts"] > float(until_ts):
                break
            prev = rows[i - 1]["scores"] if i > 0 else {DIVERGENCE: 0, TREND_SCORE: 0}
            out.append({"available_ts": bar["available_ts"], "scores": dict(bar["scores"]), "prev_scores": dict(prev)})
        return out

    def shock_inputs(self) -> dict:
        """dynlib R60 / RET60 at the newest second: 60 s rolling mid range and 60 s mid change, bp of the mid."""
        with self._lock:
            mids = list(self._mids)
        if not mids:
            return {"r60_bp": None, "ret60_bp": None, "ts": None}
        ts, mid = mids[-1]
        window = [m for t, m in mids if ts - SHOCK_WINDOW_SEC < t <= ts]
        r60 = ((max(window) - min(window)) / mid * 1e4) if len(window) >= SHOCK_MIN_SAMPLES and mid else None
        then = next((m for t, m in mids if t == ts - SHOCK_WINDOW_SEC), None)
        ret60 = ((mid - then) / mid * 1e4) if then and mid else None
        return {"r60_bp": r60, "ret60_bp": ret60, "ts": ts}

    def exit_context(self, fill_ts: float, now_ts: float) -> dict:
        """Market inputs of the regime exit stacks for one open position."""
        return {**self.shock_inputs(), "bars": self.bars_since(float(fill_ts), float(now_ts))}

    def snapshot(self) -> dict:
        bar = self.latest()
        return {
            "schema": ENGINE_SCHEMA, "hydrated": self.hydrated, "hydrated_seconds": self.hydrated_seconds,
            "hydrate_error": self.hydrate_error, "bars": len(self.bars), "rows_seen": self.rows_seen,
            "pending_rows": len(self._pending),
            "latest": None if bar is None else {k: bar[k] for k in (
                "close_ts", "bar_ok", "atr_bp", "atr_pct_rank", "adx", "spread_bp", "scores", "events")},
        }


def _num(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def regime_features(bar: Mapping[str, Any] | None) -> dict:
    """Signal-time feature view of one closed bar (None-safe)."""
    bar = bar or {}
    return {k: bar.get(k) for k in ("close_ts", "available_ts", "bar_ok", "atr_bp", "atr_abs", "atr_pct_rank",
                                    "adx", "spread_bp", "c")}


def history_files(current_path: str) -> list:
    """The newest rotated tape file plus the current one, oldest first."""
    rotated = []
    for path in glob.glob(current_path + ".*"):
        suffix = path[len(current_path) + 1:]
        if suffix.isdigit():
            try:
                rotated.append((os.path.getmtime(path), path))
            except OSError:
                continue
    rotated.sort()
    out = [p for _, p in rotated[-2:]]
    if os.path.exists(current_path):
        out.append(current_path)
    return out


_KEYS = ("bucket_ts", "fresh", "valid_bbo", "bid", "ask", "buy_qty", "sell_qty", "source_age_sec")


def read_history(paths: Iterable[str], *, since_ts: float) -> list:
    """Parse only the needed fields of 1 s rows newer than ``since_ts``; sorted, deduplicated."""
    rows = {}
    for path in paths:
        try:
            handle = open(path, "rb")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    ts = int(row.get("bucket_ts"))
                except (ValueError, TypeError, AttributeError, UnicodeDecodeError):
                    continue
                if ts >= since_ts:
                    rows[ts] = {k: row.get(k) for k in _KEYS}
    return [rows[ts] for ts in sorted(rows)]


ENGINE = RegimeBars3m()


def hydrate_engine_from_tape(current_path: str, now_ts: float, engine: RegimeBars3m = ENGINE) -> int:
    try:
        rows = read_history(history_files(current_path), since_ts=float(now_ts) - HYDRATE_SECONDS)
    except Exception as exc:  # never blocks the bot; the engine simply warms up live
        engine.hydrate_error = f"{type(exc).__name__}: {exc}"[:200]
        rows = []
    return engine.hydrate(rows)
