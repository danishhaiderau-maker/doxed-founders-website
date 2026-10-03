"""Pure computation for the Live Indicator Edge engine (no I/O, no bot import).

* :class:`BarBuilder` folds the Bitfinex 1 s tape, the cross-venue minute tape and
  the market-context minute tape into closed 3-minute bars.
* :func:`compute_features` evaluates every Appendix A feature on the last closed
  bar from the bounded bar history (``indicator_edge_spec.HISTORY_BARS``).

Every value is a deterministic function of closed bars only: the same history
gives the same row, so a value printed for bar ``t`` can never change later
(no repainting) and a restarted engine reproduces the continuous run.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Iterable, Mapping, Optional

import cross_venue_tape as cvt
import indicator_edge_spec as spec
from cross_venue_lead import LeadRule, lead_from_returns
from cross_venue_premium import PremiumRule, PremiumTracker
from tape_minute_bars import row_prices

A, W, U = spec.STATUS_AVAILABLE, spec.STATUS_WARMING_UP, spec.STATUS_UNAVAILABLE
BAR_SEC = spec.BAR_SEC
TAPE_OK_FRESH_SEC = 120
TAPE_MIN_FRESH_SEC = 20
XV_OK_UP_SEC = 150
HISTORY_INTEGRITY_BARS = 20
LIQ_VENUES = ("binance", "bybit", "okx")
OI_VENUES = ("binance", "bybit", "okx")
FUNDING_VENUES = ("binance", "bybit", "okx", "bitfinex")
SESSION_ENDS = ((8, "ASIA"), (16, "EU"), (24, "US"))


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bar_start(ts: float) -> int:
    t = int(math.floor(float(ts)))
    return t - t % BAR_SEC


def session_of(ts: float) -> str:
    hour = int(float(ts) // 3600) % 24
    for end, label in SESSION_ENDS:
        if hour < end:
            return label
    return "US"


# ---------------------------------------------------------------------------
# Bar accumulation
# ---------------------------------------------------------------------------
class _Acc:
    __slots__ = ("ts", "seen", "fresh", "o", "o_ts", "h", "l", "c", "c_ts", "mid_o", "mid_o_ts", "mid_c", "mid_c_ts",
                 "bid_c", "ask_c",
                 "mid_h", "mid_l", "vol", "buy", "sell", "trades", "spread_sum", "spread_n", "imb_sum", "imb_n",
                 "xv_minutes", "bn_buy", "bn_sell", "bb_buy", "bb_sell", "xv_up", "imb5_sum", "imb5_n", "imb20_sum",
                 "imb20_n", "prem_dev", "prem_ready", "lead_long", "lead_short", "lead_close", "mc_minutes",
                 "funding", "next_funding_ms", "oi", "liq_long", "liq_short", "liq_ok_minutes")

    def __init__(self, ts: int) -> None:
        self.ts = ts
        self.seen = set()
        self.fresh = 0
        self.o = self.h = self.l = self.c = None
        self.o_ts = self.c_ts = None
        self.mid_o = self.mid_c = self.bid_c = self.ask_c = self.mid_h = self.mid_l = None
        self.mid_o_ts = self.mid_c_ts = None
        self.vol = self.buy = self.sell = 0.0
        self.trades = 0
        self.spread_sum = self.imb_sum = 0.0
        self.spread_n = self.imb_n = 0
        self.xv_minutes = set()
        self.bn_buy = self.bn_sell = self.bb_buy = self.bb_sell = 0.0
        self.xv_up = 0
        self.imb5_sum = self.imb20_sum = 0.0
        self.imb5_n = self.imb20_n = 0
        self.prem_dev = None
        self.prem_ready = False
        self.lead_long = self.lead_short = 0
        self.lead_close = None
        self.mc_minutes = set()
        self.funding = None
        self.next_funding_ms = None
        self.oi = None
        self.liq_long = self.liq_short = 0.0
        self.liq_ok_minutes = 0


class BarBuilder:
    """Folds input rows into 3-minute bars; ``finalize(bar_ts)`` returns a closed bar dict.

    Tape rows may arrive in any order inside a bar (idempotent per second);
    cross-venue minute rows must arrive in time order (the per-second premium
    tracker and lead window are causal state).
    """

    def __init__(self, lead_rule: Optional[LeadRule] = None, premium_rule: Optional[PremiumRule] = None) -> None:
        self._acc: dict = {}
        self.lead_rule = lead_rule or LeadRule()
        self.premium_rule = premium_rule or PremiumRule()
        self.premium = PremiumTracker(self.premium_rule)
        self._mid_ring: deque = deque(maxlen=int(self.lead_rule.lookback_sec) + 2)
        self.last_xv_minute: Optional[int] = None
        self.last_mc_minute: Optional[int] = None
        self.latest_tape_ts: Optional[int] = None
        self.xv_imb_seen = {"imb5": False, "imb20": False}

    def _get(self, ts: int) -> _Acc:
        acc = self._acc.get(ts)
        if acc is None:
            acc = self._acc[ts] = _Acc(ts)
        return acc

    def open_bars(self) -> list:
        return sorted(self._acc)

    def drop_before(self, ts: int) -> None:
        for key in [k for k in self._acc if k < ts]:
            del self._acc[key]

    # ---------------------------------------------------------------- tape
    def add_tape_row(self, row: Mapping[str, Any]) -> bool:
        try:
            sec = int(row.get("bucket_ts"))
        except (TypeError, ValueError):
            return False
        acc = self._get(bar_start(sec))
        if sec in acc.seen:
            return False
        acc.seen.add(sec)
        if self.latest_tape_ts is None or sec > self.latest_tape_ts:
            self.latest_tape_ts = sec
        if row.get("fresh") is not True or row.get("valid_bbo") is not True:
            return True
        bid, ask = _finite(row.get("bid")), _finite(row.get("ask"))
        if not bid or not ask or ask < bid:
            return True
        acc.fresh += 1
        mid = (bid + ask) / 2.0
        acc.spread_sum += (ask - bid) / mid * 1e4
        acc.spread_n += 1
        bq, aq = _finite(row.get("bid_qty")), _finite(row.get("ask_qty"))
        if bq is not None and aq is not None and abs(bq) + abs(aq) > 0:
            acc.imb_sum += (abs(bq) - abs(aq)) / (abs(bq) + abs(aq))
            acc.imb_n += 1
        if acc.mid_c_ts is None or sec >= acc.mid_c_ts:
            acc.mid_c_ts, acc.mid_c, acc.bid_c, acc.ask_c = sec, mid, bid, ask
        if acc.mid_o_ts is None or sec < acc.mid_o_ts:
            acc.mid_o_ts, acc.mid_o = sec, mid
        acc.mid_h = mid if acc.mid_h is None else max(acc.mid_h, mid)
        acc.mid_l = mid if acc.mid_l is None else min(acc.mid_l, mid)
        prices = row_prices(row)
        th, tl = _finite(row.get("trade_high")), _finite(row.get("trade_low"))
        if th:
            prices.append(th)
        if tl:
            prices.append(tl)
        buy, sell = _finite(row.get("buy_qty")) or 0.0, _finite(row.get("sell_qty")) or 0.0
        acc.buy += abs(buy)
        acc.sell += abs(sell)
        acc.vol += abs(buy) + abs(sell)
        acc.trades += int(row.get("trade_count") or 0)
        last = _finite(row.get("last"))
        if prices:
            if acc.o_ts is None or sec < acc.o_ts:
                acc.o_ts, acc.o = sec, (last or prices[0])
            if acc.c_ts is None or sec >= acc.c_ts:
                acc.c_ts, acc.c = sec, (last or prices[-1])
            acc.h = max(prices) if acc.h is None else max(acc.h, max(prices))
            acc.l = min(prices) if acc.l is None else min(acc.l, min(prices))
        return True

    # ---------------------------------------------------------- cross venue
    def add_cross_venue_row(self, row: Mapping[str, Any]) -> bool:
        if not isinstance(row, Mapping) or row.get("schema") != cvt.SCHEMA:
            return False
        minute = int(row.get("minute_ts") or 0)
        if self.last_xv_minute is not None and minute <= self.last_xv_minute:
            return False
        self.last_xv_minute = minute
        acc = self._get(bar_start(minute))
        acc.xv_minutes.add(minute)
        decoded = cvt.decode_minute(row)
        venues = row.get("venues") or {}
        bfx = decoded.get("bfx") or {}
        for venue, key_buy, key_sell in (("binance", "bn_buy", "bn_sell"), ("bybit", "bb_buy", "bb_sell")):
            for cell in (decoded.get(venue) or {}).values():
                if cell.get("up") is False:
                    continue
                setattr(acc, key_buy, getattr(acc, key_buy) + (cell.get("buy") or 0.0))
                setattr(acc, key_sell, getattr(acc, key_sell) + (cell.get("sell") or 0.0))
        bn_up = (venues.get("binance") or {}).get("up")
        bb_up = (venues.get("bybit") or {}).get("up")
        if isinstance(bn_up, str) and isinstance(bb_up, str):
            acc.xv_up += sum(1 for a, b in zip(bn_up, bb_up) if a == "1" and b == "1")
        bn_row = venues.get("binance") or {}
        for key, sum_attr, n_attr in (("imb5", "imb5_sum", "imb5_n"), ("imb20", "imb20_sum", "imb20_n")):
            vals = bn_row.get(key)
            if isinstance(vals, list):
                for v in vals:
                    if v is None:
                        continue
                    self.xv_imb_seen[key] = True
                    setattr(acc, sum_attr, getattr(acc, sum_attr) + float(v) * cvt.IMBALANCE_UNIT)
                    setattr(acc, n_attr, getattr(acc, n_attr) + 1)
        w = int(self.lead_rule.lookback_sec)
        for i in range(60):
            sec = minute + i
            mids = {v: (decoded.get(v) or {}).get(sec, {}).get("mid") for v in self.premium_rule.venues}
            facts = self.premium.observe(sec, mids, bfx.get(sec))
            if facts.get("premium_dev_bp") is not None:
                acc.prem_dev = facts["premium_dev_bp"]
                acc.prem_ready = True
            lead_mids = {v: (decoded.get(v) or {}).get(sec, {}).get("mid") for v in self.lead_rule.venues}
            self._mid_ring.append((sec, lead_mids, bfx.get(sec)))
            if len(self._mid_ring) <= w or self._mid_ring[-1 - w][0] != sec - w:
                continue
            _, p_mids, p_bfx = self._mid_ring[-1 - w]
            rets = {v: (None if not lead_mids.get(v) or not p_mids.get(v) else (lead_mids[v] / p_mids[v] - 1.0) * 1e4)
                    for v in self.lead_rule.venues}
            bfx_ret = None if not bfx.get(sec) or not p_bfx else (bfx[sec] / p_bfx - 1.0) * 1e4
            lead = lead_from_returns(rets, bfx_ret, self.lead_rule.venues)
            if lead is None:
                continue
            if sec == bar_start(sec) + BAR_SEC - 1:
                acc.lead_close = round(lead, 4)
            if abs(lead) >= self.lead_rule.lead_threshold_bps:
                if lead > 0:
                    acc.lead_long += 1
                else:
                    acc.lead_short += 1
        return True

    # ------------------------------------------------------- market context
    def add_market_context_row(self, row: Mapping[str, Any]) -> bool:
        if not isinstance(row, Mapping) or row.get("schema") != "market_context_1m_v1":
            return False
        minute = int(row.get("minute_ts") or 0)
        if self.last_mc_minute is not None and minute <= self.last_mc_minute:
            return False
        self.last_mc_minute = minute
        acc = self._get(bar_start(minute))
        acc.mc_minutes.add(minute)
        deriv = row.get("derivatives") or {}
        rates, nexts = [], []
        for venue in FUNDING_VENUES:
            d = deriv.get(venue) or {}
            if d.get("status") not in ("OK", "PARTIAL"):
                continue
            rate = _finite(d.get("predicted_funding_rate"))
            if rate is None:
                rate = _finite(d.get("funding_rate"))
            if rate is not None:
                rates.append(rate)
            nxt = _finite(d.get("next_funding_ms"))
            if nxt is not None and venue == "binance":
                nexts.append(nxt)
        if rates:
            acc.funding = sum(rates) / len(rates)
        if nexts:
            acc.next_funding_ms = min(nexts)
        ois = [_finite((deriv.get(v) or {}).get("oi_btc")) for v in OI_VENUES
               if (deriv.get(v) or {}).get("status") == "OK"]
        if len(ois) == len(OI_VENUES) and all(o is not None for o in ois):
            acc.oi = sum(ois)
        liqs = row.get("liquidations") or {}
        ok = True
        for venue in LIQ_VENUES:
            cell = liqs.get(venue) or {}
            if cell.get("status") not in ("OK", "PARTIAL"):
                ok = False
                continue
            acc.liq_long += _finite(cell.get("long_usd")) or 0.0
            acc.liq_short += _finite(cell.get("short_usd")) or 0.0
        if ok:
            acc.liq_ok_minutes += 1
        return True

    # ------------------------------------------------------------- finalize
    def finalize(self, ts: int, prev_bar: Optional[Mapping[str, Any]] = None) -> dict:
        acc = self._acc.pop(int(ts), None) or _Acc(int(ts))
        o, h, l, c = acc.o, acc.h, acc.l, acc.c
        if c is None and acc.mid_c is not None:
            o, h, l, c = acc.mid_o or acc.mid_c, acc.mid_h, acc.mid_l, acc.mid_c
        filled = False
        if c is None or acc.fresh < TAPE_MIN_FRESH_SEC:
            filled = True
            prev_c = _finite((prev_bar or {}).get("c"))
            if c is None:
                o = h = l = c = prev_c
        tape = "OK" if acc.fresh >= TAPE_OK_FRESH_SEC else ("PARTIAL" if acc.fresh >= TAPE_MIN_FRESH_SEC else "MISSING")
        xv_state = ("OK" if len(acc.xv_minutes) == 3 and acc.xv_up >= XV_OK_UP_SEC
                    else "PARTIAL" if acc.xv_minutes else "MISSING")
        mc_state = "OK" if len(acc.mc_minutes) == 3 else ("PARTIAL" if acc.mc_minutes else "MISSING")
        return {
            "ts": int(ts), "close_ts": int(ts) + BAR_SEC,
            "o": o, "h": h, "l": l, "c": c,
            "mid_c": acc.mid_c, "bid_c": acc.bid_c, "ask_c": acc.ask_c,
            "vol": round(acc.vol, 8), "buy": round(acc.buy, 8), "sell": round(acc.sell, 8), "trades": acc.trades,
            "fresh_sec": acc.fresh, "seen_sec": len(acc.seen), "filled": filled,
            "spread_bp": None if not acc.spread_n else round(acc.spread_sum / acc.spread_n, 4),
            "tob_imb": None if not acc.imb_n else round(acc.imb_sum / acc.imb_n, 5),
            "tape": tape,
            "xv": {"state": xv_state, "minutes": len(acc.xv_minutes), "up_sec": acc.xv_up,
                   "bn_net": round(acc.bn_buy - acc.bn_sell, 6) if acc.xv_minutes else None,
                   "bb_net": round(acc.bb_buy - acc.bb_sell, 6) if acc.xv_minutes else None,
                   "imb5": None if not acc.imb5_n else round(acc.imb5_sum / acc.imb5_n, 5),
                   "imb20": None if not acc.imb20_n else round(acc.imb20_sum / acc.imb20_n, 5),
                   "prem_dev": acc.prem_dev, "prem_ready": acc.prem_ready,
                   "lead_long": acc.lead_long, "lead_short": acc.lead_short, "lead_close": acc.lead_close},
            "mc": {"state": mc_state, "minutes": len(acc.mc_minutes), "funding": acc.funding,
                   "next_funding_ms": acc.next_funding_ms, "oi": acc.oi,
                   "liq_long": round(acc.liq_long, 2), "liq_short": round(acc.liq_short, 2),
                   "liq_ok": acc.liq_ok_minutes == 3},
        }


# ---------------------------------------------------------------------------
# Series helpers (None-prefix aware; closed bars only)
# ---------------------------------------------------------------------------
def ema(x: list, n: int) -> list:
    """SMA-seeded EMA; None until ``n`` consecutive non-None values exist."""
    out = [None] * len(x)
    alpha = 2.0 / (n + 1.0)
    run, acc, prev = 0, 0.0, None
    for i, v in enumerate(x):
        if v is None:
            if prev is None:
                run, acc = 0, 0.0
            else:
                out[i] = None
            continue
        if prev is None:
            run += 1
            acc += v
            if run == n:
                prev = acc / n
                out[i] = prev
            continue
        prev = prev + alpha * (v - prev)
        out[i] = prev
    return out


def wilder(x: list, n: int) -> list:
    """Wilder smoothing (RMA): SMA seed then prev + (v - prev) / n."""
    out = [None] * len(x)
    run, acc, prev = 0, 0.0, None
    for i, v in enumerate(x):
        if v is None:
            continue
        if prev is None:
            run += 1
            acc += v
            if run == n:
                prev = acc / n
                out[i] = prev
            continue
        prev = prev + (v - prev) / n
        out[i] = prev
    return out


def sma(x: list, n: int) -> list:
    out = [None] * len(x)
    acc, q = 0.0, deque()
    for i, v in enumerate(x):
        if v is None:
            acc, q = 0.0, deque()
            continue
        q.append(v)
        acc += v
        if len(q) > n:
            acc -= q.popleft()
        if len(q) == n:
            out[i] = acc / n
    return out


def wma(x: list, n: int) -> list:
    out = [None] * len(x)
    denom = n * (n + 1) / 2.0
    for i in range(n - 1, len(x)):
        window = x[i - n + 1:i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(w * v for w, v in zip(range(1, n + 1), window)) / denom
    return out


def rolling_std(x: list, n: int) -> list:
    out = [None] * len(x)
    for i in range(n - 1, len(x)):
        window = x[i - n + 1:i + 1]
        if any(v is None for v in window):
            continue
        m = sum(window) / n
        out[i] = math.sqrt(sum((v - m) ** 2 for v in window) / n)
    return out


def linreg(y: list, n: int, i: int) -> Optional[tuple]:
    """(slope, t_stat) of y[i-n+1..i] on 0..n-1; None when incomplete."""
    if i - n + 1 < 0:
        return None
    window = y[i - n + 1:i + 1]
    if any(v is None for v in window):
        return None
    xm = (n - 1) / 2.0
    ym = sum(window) / n
    sxx = sum((k - xm) ** 2 for k in range(n))
    sxy = sum((k - xm) * (v - ym) for k, v in enumerate(window))
    slope = sxy / sxx
    if n <= 2:
        return slope, None
    sse = sum((v - (ym + slope * (k - xm))) ** 2 for k, v in enumerate(window))
    se = math.sqrt(sse / (n - 2)) / math.sqrt(sxx) if sse > 0 else 0.0
    t = (slope / se) if se > 0 else (math.inf if slope > 0 else -math.inf if slope < 0 else 0.0)
    return slope, t


def pct_rank(series: list, i: int, lookback: int = spec.PCT_LOOKBACK_BARS,
             min_history: int = spec.MIN_PCT_HISTORY_BARS) -> Optional[float]:
    """Percentile (0-100) of series[i] among the last ``lookback`` non-None values (inclusive)."""
    if i < 0 or i >= len(series) or series[i] is None:
        return None
    x = series[i]
    vals = [v for v in series[max(0, i - lookback + 1):i + 1] if v is not None]
    if len(vals) < min_history:
        return None
    below = sum(1 for v in vals if v < x)
    equal = sum(1 for v in vals if v == x)
    return round(100.0 * (below + 0.5 * equal) / len(vals), 2)


def _sign(x: Optional[float]) -> int:
    if x is None:
        return 0
    return 1 if x > 0 else -1 if x < 0 else 0


def _r(x: Optional[float], nd: int = 6) -> Optional[float]:
    if x is None or not math.isfinite(x):
        return None
    return round(x, nd)


class Series:
    """Column view over the bar history."""

    def __init__(self, bars: list) -> None:
        self.bars = bars
        self.n = len(bars)
        self.ts = [b["ts"] for b in bars]
        self.o = [_finite(b.get("o")) for b in bars]
        self.h = [_finite(b.get("h")) for b in bars]
        self.l = [_finite(b.get("l")) for b in bars]
        self.c = [_finite(b.get("c")) for b in bars]
        self.v = [_finite(b.get("vol")) or 0.0 for b in bars]
        self.buy = [_finite(b.get("buy")) or 0.0 for b in bars]
        self.sell = [_finite(b.get("sell")) or 0.0 for b in bars]
        self.tp = [None if None in (h, l, c) else (h + l + c) / 3.0 for h, l, c in zip(self.h, self.l, self.c)]
        self.med = [None if None in (h, l) else (h + l) / 2.0 for h, l in zip(self.h, self.l)]
        self._cache: dict = {}

    def cached(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def ema(self, n: int) -> list:
        return self.cached(("ema", n), lambda: ema(self.c, n))

    def tr(self) -> list:
        def build():
            out = [None] * self.n
            for i in range(self.n):
                if None in (self.h[i], self.l[i]):
                    continue
                if i == 0 or self.c[i - 1] is None:
                    out[i] = self.h[i] - self.l[i]
                else:
                    pc = self.c[i - 1]
                    out[i] = max(self.h[i] - self.l[i], abs(self.h[i] - pc), abs(self.l[i] - pc))
            return out
        return self.cached("tr", build)

    def atr(self, n: int) -> list:
        return self.cached(("atr", n), lambda: wilder(self.tr(), n))


def _feat(raw, score, pct, status) -> list:
    return [_r(raw), None if score is None else int(score), _r(pct, 2), status]


# ---------------------------------------------------------------------------
# Indicators.  Each returns {feature_id: [raw, score, pct, status]} for bar i = n-1.
# ---------------------------------------------------------------------------
def _ema_trend(s: Series, ind_id: str, n: int, slope_bars: int) -> dict:
    e, i = s.ema(n), s.n - 1
    fid = f"{ind_id}@F:STATE"
    if i < 0 or e[i] is None or i - slope_bars < 0 or e[i - slope_bars] is None:
        return {fid: _feat(None, None, None, W)}
    c = s.c[i]
    dist = [None if e[k] is None or s.c[k] is None else (s.c[k] / e[k] - 1.0) * 1e4 for k in range(s.n)]
    slope = e[i] - e[i - slope_bars]
    score = 1 if c > e[i] and slope > 0 else -1 if c < e[i] and slope < 0 else 0
    return {fid: _feat(dist[i], score, pct_rank(dist, i), A)}


def ind_ema(s: Series) -> dict:
    out = {}
    out.update(_ema_trend(s, "EMA_200", 200, 10))
    out.update(_ema_trend(s, "EMA_50", 50, 10))
    out.update(_ema_trend(s, "EMA_288", 288, 10))
    e9, e21, i = s.ema(9), s.ema(21), s.n - 1
    if i < 0 or e9[i] is None or e21[i] is None:
        out["EMA_CROSS_9_21@F:STATE"] = _feat(None, None, None, W)
    else:
        diff = [None if a is None or b is None else (a / b - 1.0) * 1e4 for a, b in zip(e9, e21)]
        out["EMA_CROSS_9_21@F:STATE"] = _feat(diff[i], _sign(diff[i]), pct_rank(diff, i), A)
    return out


def _hh(x: list, i: int, n: int) -> Optional[float]:
    w = x[i - n + 1:i + 1] if i - n + 1 >= 0 else None
    return None if not w or any(v is None for v in w) else max(w)


def _ll(x: list, i: int, n: int) -> Optional[float]:
    w = x[i - n + 1:i + 1] if i - n + 1 >= 0 else None
    return None if not w or any(v is None for v in w) else min(w)


def ind_ichimoku(s: Series) -> dict:
    fid, i, d = "ICHIMOKU_FAST@F:STATE", s.n - 1, 22
    j = i - d
    if j - 43 < 0:
        return {fid: _feat(None, None, None, W)}
    tenkan = (_hh(s.h, i, 7) + _ll(s.l, i, 7)) / 2.0
    kijun = (_hh(s.h, i, 22) + _ll(s.l, i, 22)) / 2.0
    span_a = ((_hh(s.h, j, 7) + _ll(s.l, j, 7)) / 2.0 + (_hh(s.h, j, 22) + _ll(s.l, j, 22)) / 2.0) / 2.0
    span_b = (_hh(s.h, j, 44) + _ll(s.l, j, 44)) / 2.0
    top, bot, c = max(span_a, span_b), min(span_a, span_b), s.c[i]
    raw = (c - top) / c * 1e4 if c > top else (c - bot) / c * 1e4 if c < bot else 0.0
    score = 1 if c > top and tenkan > kijun else -1 if c < bot and tenkan < kijun else 0
    return {fid: _feat(raw, score, None, A)}


def ind_supertrend(s: Series, n: int = 10, mult: float = 2.0) -> dict:
    fid = "SUPERTREND@F:STATE"
    atr = s.atr(n)
    up, fub, flb, line = None, None, None, None
    count = 0
    for i in range(s.n):
        if atr[i] is None or s.med[i] is None:
            continue
        bub, blb = s.med[i] + mult * atr[i], s.med[i] - mult * atr[i]
        pc = s.c[i - 1] if i > 0 else None
        if fub is None:
            fub, flb, up = bub, blb, s.c[i] >= s.med[i]
        else:
            fub = bub if (bub < fub or (pc is not None and pc > fub)) else fub
            flb = blb if (blb > flb or (pc is not None and pc < flb)) else flb
            if up and s.c[i] < flb:
                up = False
            elif not up and s.c[i] > fub:
                up = True
        line = flb if up else fub
        count += 1
    if count < n or line is None:
        return {fid: _feat(None, None, None, W)}
    c = s.c[-1]
    return {fid: _feat((c / line - 1.0) * 1e4, 1 if up else -1, None, A)}


def ind_psar(s: Series, step: float = 0.02, max_af: float = 0.2) -> dict:
    fid = "PSAR@F:STATE"
    if s.n < 12 or any(v is None for v in s.h[-12:]):
        return {fid: _feat(None, None, None, W)}
    start = 0
    for k in range(s.n - 1, -1, -1):
        if s.h[k] is None or s.l[k] is None:
            start = k + 1
            break
    if s.n - start < 12:
        return {fid: _feat(None, None, None, W)}
    up = s.c[start + 1] >= s.c[start]
    sar = s.l[start] if up else s.h[start]
    ep = s.h[start + 1] if up else s.l[start + 1]
    af = step
    for i in range(start + 2, s.n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, s.l[i - 1], s.l[i - 2])
            if s.l[i] < sar:
                up, sar, ep, af = False, ep, s.l[i], step
            elif s.h[i] > ep:
                ep, af = s.h[i], min(max_af, af + step)
        else:
            sar = max(sar, s.h[i - 1], s.h[i - 2])
            if s.h[i] > sar:
                up, sar, ep, af = True, ep, s.h[i], step
            elif s.l[i] < ep:
                ep, af = s.l[i], min(max_af, af + step)
    c = s.c[-1]
    return {fid: _feat((c - sar) / c * 1e4, 1 if sar < c else -1, None, A)}


def ind_hma(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 14), ("S", 50)):
        fid = f"HMA_SLOPE@{hz}:STATE"
        half, full = wma(s.c, n // 2), wma(s.c, n)
        diff = [None if a is None or b is None else 2 * a - b for a, b in zip(half, full)]
        hma = wma(diff, int(math.sqrt(n)))
        i = s.n - 1
        if i - 3 < 0 or hma[i] is None or hma[i - 3] is None:
            out[fid] = _feat(None, None, None, W)
            continue
        slope = [None if k < 3 or hma[k] is None or hma[k - 3] is None else (hma[k] / hma[k - 3] - 1.0) * 1e4
                 for k in range(s.n)]
        out[fid] = _feat(slope[i], _sign(slope[i]), pct_rank(slope, i), A)
    return out


def ind_linreg(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 14), ("S", 50)):
        fid = f"LINREG_SLOPE@{hz}:STATE"
        fit = linreg(s.c, n, s.n - 1)
        if fit is None:
            out[fid] = _feat(None, None, None, W)
            continue
        slope, t = fit
        c = s.c[-1]
        score = _sign(slope) if t is not None and abs(t) > 2.0 else 0
        out[fid] = _feat(slope / c * 1e4, score, None, A)
    return out


def ind_tema(s: Series) -> dict:
    fid = "TEMA@F:STATE"

    def tema(n):
        e1 = ema(s.c, n)
        e2 = ema(e1, n)
        e3 = ema(e2, n)
        return [None if None in (a, b, c) else 3 * a - 3 * b + c for a, b, c in zip(e1, e2, e3)]
    t9, t21, i = tema(9), tema(21), s.n - 1
    if i < 0 or t9[i] is None or t21[i] is None:
        return {fid: _feat(None, None, None, W)}
    diff = [None if a is None or b is None else (a / b - 1.0) * 1e4 for a, b in zip(t9, t21)]
    return {fid: _feat(diff[i], _sign(diff[i]), pct_rank(diff, i), A)}


def rsi_series(c: list, n: int) -> list:
    gains = [None] + [None if c[k] is None or c[k - 1] is None else max(0.0, c[k] - c[k - 1]) for k in range(1, len(c))]
    losses = [None] + [None if c[k] is None or c[k - 1] is None else max(0.0, c[k - 1] - c[k]) for k in range(1, len(c))]
    ag, al = wilder(gains, n), wilder(losses, n)
    out = [None] * len(c)
    for k in range(len(c)):
        if ag[k] is None or al[k] is None:
            continue
        out[k] = 100.0 if al[k] == 0 and ag[k] > 0 else 50.0 if al[k] == 0 else 100.0 - 100.0 / (1.0 + ag[k] / al[k])
    return out


def _wf(out: dict, fid_base: str, raw, pct, trend, rev, available: bool) -> None:
    status = A if available else W
    if trend is not None or not available:
        out[f"{fid_base}:TREND"] = _feat(raw, trend if available else None, pct, status)
    if rev is not None or not available:
        out[f"{fid_base}:REVERSION"] = _feat(raw, rev if available else None, pct, status)


def ind_rsi(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 9), ("S", 21)):
        r, i = rsi_series(s.c, n), s.n - 1
        ok = i >= 0 and r[i] is not None
        x = r[i] if ok else None
        trend = (1 if x > 55 else -1 if x < 45 else 0) if ok else None
        rev = (1 if x < 25 else -1 if x > 75 else 0) if ok else None
        _wf(out, f"RSI@{hz}", x, pct_rank(r, i) if ok else None, trend, rev, ok)
    return out


def ind_stoch(s: Series) -> dict:
    out = {}
    for hz, (k, sm, d) in (("F", (5, 3, 3)), ("S", (21, 3, 3))):
        raw_k = [None] * s.n
        for i in range(k - 1, s.n):
            hh, ll = _hh(s.h, i, k), _ll(s.l, i, k)
            if hh is None or ll is None or s.c[i] is None:
                continue
            raw_k[i] = 50.0 if hh == ll else 100.0 * (s.c[i] - ll) / (hh - ll)
        kk = sma(raw_k, sm)
        dd = sma(kk, d)
        i = s.n - 1
        ok = i >= 1 and None not in (kk[i], dd[i], kk[i - 1], dd[i - 1])
        if not ok:
            _wf(out, f"STOCH@{hz}", None, None, None, None, False)
            continue
        up = kk[i - 1] <= dd[i - 1] and kk[i] > dd[i]
        down = kk[i - 1] >= dd[i - 1] and kk[i] < dd[i]
        trend = 1 if up and kk[i] > 50 else -1 if down and kk[i] < 50 else 0
        rev = 1 if up and kk[i] < 20 else -1 if down and kk[i] > 80 else 0
        _wf(out, f"STOCH@{hz}", kk[i], pct_rank(kk, i), trend, rev, True)
    return out


def ind_macd(s: Series) -> dict:
    out = {}
    for hz, (f, sl, sg) in (("F", (6, 13, 4)), ("S", (12, 26, 9))):
        ef, es = ema(s.c, f), ema(s.c, sl)
        m = [None if a is None or b is None else a - b for a, b in zip(ef, es)]
        sig = ema(m, sg)
        hist = [None if a is None or b is None else (a - b) for a, b in zip(m, sig)]
        hist_bp = [None if h is None or c is None else h / c * 1e4 for h, c in zip(hist, s.c)]
        i = s.n - 1
        ok = i >= 1 and hist[i] is not None and hist[i - 1] is not None
        if not ok:
            _wf(out, f"MACD_SCALP@{hz}", None, None, None, None, False)
            continue
        rising, falling = hist[i] > hist[i - 1], hist[i] < hist[i - 1]
        trend = 1 if hist[i] > 0 and rising else -1 if hist[i] < 0 and falling else 0
        p_prev = pct_rank(hist_bp, i - 1)
        pct = pct_rank(hist_bp, i)
        if p_prev is None:
            out[f"MACD_SCALP@{hz}:TREND"] = _feat(hist_bp[i], trend, pct, A)
            out[f"MACD_SCALP@{hz}:REVERSION"] = _feat(hist_bp[i], None, pct, W)
            continue
        rev = -1 if p_prev >= 90 and falling else 1 if p_prev <= 10 and rising else 0
        _wf(out, f"MACD_SCALP@{hz}", hist_bp[i], pct, trend, rev, True)
    return out


def ind_cci(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 14), ("S", 50)):
        ma = sma(s.tp, n)
        cci = [None] * s.n
        for i in range(n - 1, s.n):
            if ma[i] is None:
                continue
            w = s.tp[i - n + 1:i + 1]
            md = sum(abs(v - ma[i]) for v in w) / n
            cci[i] = 0.0 if md == 0 else (s.tp[i] - ma[i]) / (0.015 * md)
        i = s.n - 1
        ok = i >= 1 and cci[i] is not None and cci[i - 1] is not None
        if not ok:
            _wf(out, f"CCI@{hz}", None, None, None, None, False)
            continue
        x, p = cci[i], cci[i - 1]
        trend = 1 if x > 100 else -1 if x < -100 else 0
        rev = 1 if p < -100 <= x else -1 if p > 100 >= x else 0
        _wf(out, f"CCI@{hz}", x, pct_rank(cci, i), trend, rev, True)
    return out


def ind_willr(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 10), ("S", 50)):
        wr = [None] * s.n
        for i in range(n - 1, s.n):
            hh, ll = _hh(s.h, i, n), _ll(s.l, i, n)
            if hh is None or ll is None:
                continue
            wr[i] = -50.0 if hh == ll else -100.0 * (hh - s.c[i]) / (hh - ll)
        i = s.n - 1
        ok = i >= 1 and wr[i] is not None and wr[i - 1] is not None
        if not ok:
            _wf(out, f"WILLR@{hz}", None, None, None, None, False)
            continue
        x, p = wr[i], wr[i - 1]
        trend = 1 if x > -20 else -1 if x < -80 else 0
        rev = 1 if p < -80 <= x else -1 if p > -20 >= x else 0
        _wf(out, f"WILLR@{hz}", x, pct_rank(wr, i), trend, rev, True)
    return out


def _local_min(x: list, j: int) -> bool:
    return 0 < j < len(x) - 1 and None not in (x[j - 1], x[j], x[j + 1]) and x[j] < x[j - 1] and x[j] < x[j + 1]


def _local_max(x: list, j: int) -> bool:
    return 0 < j < len(x) - 1 and None not in (x[j - 1], x[j], x[j + 1]) and x[j] > x[j - 1] and x[j] > x[j + 1]


def ind_ao(s: Series, lookback: int = 30) -> dict:
    out = {}
    f, sl = sma(s.med, 3), sma(s.med, 15)
    ao = [None if a is None or b is None else a - b for a, b in zip(f, sl)]
    i = s.n - 1
    ok = i >= lookback and None not in ao[i - lookback:i + 1]
    if not ok:
        _wf(out, "AO@F", None, None, None, None, False)
        return out
    rising, falling = ao[i] > ao[i - 1], ao[i] < ao[i - 1]
    trend = 1 if ao[i] > 0 and rising else -1 if ao[i] < 0 and falling else 0
    rev = 0
    if _local_min(ao, i - 1) and ao[i - 1] < 0:
        for j in range(i - 3, i - lookback, -1):
            if ao[j] >= 0:
                break
            if _local_min(ao, j):
                if ao[j] < ao[i - 1] and max(ao[j:i]) < 0:
                    rev = 1
                break
    elif _local_max(ao, i - 1) and ao[i - 1] > 0:
        for j in range(i - 3, i - lookback, -1):
            if ao[j] <= 0:
                break
            if _local_max(ao, j):
                if ao[j] > ao[i - 1] and min(ao[j:i]) > 0:
                    rev = -1
                break
    c = s.c[i]
    ao_bp = [None if a is None or cc is None else a / cc * 1e4 for a, cc in zip(ao, s.c)]
    _wf(out, "AO@F", ao[i] / c * 1e4, pct_rank(ao_bp, i), trend, rev, True)
    return out


def ind_kst(s: Series) -> dict:
    fid = "KST@F:TREND"
    parts = []
    for r in (3, 6, 9, 12):
        roc = [None if k < r or s.c[k] is None or s.c[k - r] is None else (s.c[k] / s.c[k - r] - 1.0) * 100.0
               for k in range(s.n)]
        parts.append(sma(roc, 3))
    kst = [None if None in vals else vals[0] + 2 * vals[1] + 3 * vals[2] + 4 * vals[3] for vals in zip(*parts)]
    sig = sma(kst, 3)
    i = s.n - 1
    if i < 0 or kst[i] is None or sig[i] is None:
        return {fid: _feat(None, None, None, W)}
    diff = [None if a is None or b is None else a - b for a, b in zip(kst, sig)]
    return {fid: _feat(diff[i], 1 if diff[i] > 0 else -1, pct_rank(diff, i), A)}


def ind_cmo(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 9), ("S", 50)):
        cmo = [None] * s.n
        for i in range(n, s.n):
            w = s.c[i - n:i + 1]
            if any(v is None for v in w):
                continue
            su = sum(max(0.0, w[k] - w[k - 1]) for k in range(1, len(w)))
            sd = sum(max(0.0, w[k - 1] - w[k]) for k in range(1, len(w)))
            cmo[i] = 0.0 if su + sd == 0 else 100.0 * (su - sd) / (su + sd)
        i = s.n - 1
        ok = i >= 0 and cmo[i] is not None
        x = cmo[i] if ok else None
        trend = (1 if x > 30 else -1 if x < -30 else 0) if ok else None
        rev = (1 if x < -50 else -1 if x > 50 else 0) if ok else None
        _wf(out, f"CMO@{hz}", x, pct_rank(cmo, i) if ok else None, trend, rev, ok)
    return out


def ind_roc(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 6), ("S", 20)):
        roc = [None if k < n or s.c[k] is None or s.c[k - n] is None else (s.c[k] / s.c[k - n] - 1.0) * 1e4
               for k in range(s.n)]
        absr = [None if v is None else abs(v) for v in roc]
        i = s.n - 1
        p_abs = pct_rank(absr, i)
        if i < 0 or roc[i] is None or p_abs is None:
            _wf(out, f"ROC@{hz}", None if i < 0 else roc[i], None, None, None, False)
            continue
        trend = _sign(roc[i]) if p_abs > 60 else 0
        rev = -_sign(roc[i]) if p_abs > 95 else 0
        _wf(out, f"ROC@{hz}", roc[i], pct_rank(roc, i), trend, rev, True)
    return out


def ind_dpo(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 12), ("S", 50)):
        ma = sma(s.c, n)
        shift = n // 2 + 1
        dpo = [None if k - shift < 0 or ma[k - shift] is None or s.c[k] is None else (s.c[k] - ma[k - shift]) / s.c[k] * 1e4
               for k in range(s.n)]
        i = s.n - 1
        ok = i >= 1 and dpo[i] is not None and dpo[i - 1] is not None
        if not ok:
            _wf(out, f"DPO@{hz}", None, None, None, None, False)
            continue
        trend = 1 if dpo[i - 1] <= 0 < dpo[i] else -1 if dpo[i - 1] >= 0 > dpo[i] else 0
        pct = pct_rank(dpo, i)
        out[f"DPO@{hz}:TREND"] = _feat(dpo[i], trend, pct, A)
        if pct is None:
            out[f"DPO@{hz}:REVERSION"] = _feat(dpo[i], None, None, W)
        else:
            out[f"DPO@{hz}:REVERSION"] = _feat(dpo[i], 1 if pct <= 10 else -1 if pct >= 90 else 0, pct, A)
    return out


def _session_slice(s: Series) -> tuple:
    i = s.n - 1
    day0 = s.ts[i] - s.ts[i] % 86400
    start = i
    while start - 1 >= 0 and s.ts[start - 1] >= day0:
        start -= 1
    return start, i


def ind_vp_session(s: Series, value_area: float = 0.70, bin_bp: float = 2.0, min_bars: int = 10) -> dict:
    out = {}
    if s.n == 0:
        _wf(out, "VP_SESSION@F", None, None, None, None, False)
        return out
    start, i = _session_slice(s)
    bars = [k for k in range(start, i + 1) if None not in (s.h[k], s.l[k]) and s.v[k] > 0]
    if i - start + 1 < min_bars or not bars or i < 1:
        _wf(out, "VP_SESSION@F", None, None, None, None, False)
        return out
    ref = s.c[start] or s.c[i]
    width = ref * bin_bp / 1e4
    prof: dict = {}
    for k in bars:
        lo, hi = int(math.floor(s.l[k] / width)), int(math.floor(s.h[k] / width))
        nb = hi - lo + 1
        share = s.v[k] / nb
        for b in range(lo, hi + 1):
            prof[b] = prof.get(b, 0.0) + share
    total = sum(prof.values())
    poc = max(sorted(prof), key=lambda b: prof[b])
    lo_b = hi_b = poc
    acc = prof[poc]
    keys = sorted(prof)
    kmin, kmax = keys[0], keys[-1]
    while acc < value_area * total and (lo_b > kmin or hi_b < kmax):
        below = prof.get(lo_b - 1, 0.0) if lo_b > kmin else -1.0
        above = prof.get(hi_b + 1, 0.0) if hi_b < kmax else -1.0
        if above >= below:
            hi_b += 1
            acc += max(0.0, above)
        else:
            lo_b -= 1
            acc += max(0.0, below)
    val, vah, poc_px = lo_b * width, (hi_b + 1) * width, (poc + 0.5) * width
    c, pc = s.c[i], s.c[i - 1]
    trend = 1 if c > vah and pc is not None and pc > vah else -1 if c < val and pc is not None and pc < val else 0
    rev = 1 if s.l[i] <= val < c else -1 if s.h[i] >= vah > c else 0
    _wf(out, "VP_SESSION@F", (c - poc_px) / c * 1e4, None, trend, rev, True)
    return out


def ind_avwap(s: Series, band: float = 2.0, min_bars: int = 10) -> dict:
    out = {}
    if s.n == 0:
        _wf(out, "AVWAP_SESSION@F", None, None, None, None, False)
        return out
    start, i = _session_slice(s)
    ks = [k for k in range(start, i + 1) if s.tp[k] is not None]
    vol = sum(s.v[k] for k in ks)
    if i - start + 1 < min_bars or vol <= 0:
        _wf(out, "AVWAP_SESSION@F", None, None, None, None, False)
        return out
    vwap = sum(s.tp[k] * s.v[k] for k in ks) / vol
    var = sum(s.v[k] * (s.tp[k] - vwap) ** 2 for k in ks) / vol
    sd = math.sqrt(var)
    c = s.c[i]
    trend = _sign(c - vwap)
    rev = 1 if c < vwap - band * sd else -1 if c > vwap + band * sd else 0
    _wf(out, "AVWAP_SESSION@F", (c / vwap - 1.0) * 1e4, None, trend, rev, True)
    return out


def ind_obv(s: Series) -> dict:
    out = {}
    obv, run = [None] * s.n, 0.0
    for k in range(s.n):
        if k > 0 and s.c[k] is not None and s.c[k - 1] is not None:
            run += s.v[k] if s.c[k] > s.c[k - 1] else -s.v[k] if s.c[k] < s.c[k - 1] else 0.0
        obv[k] = run
    for hz, n in (("F", 13), ("S", 50)):
        e, i = ema(obv, n), s.n - 1
        fid = f"OBV_EMA@{hz}:TREND"
        if i < 0 or e[i] is None:
            out[fid] = _feat(None, None, None, W)
            continue
        diff = [None if e[k] is None else obv[k] - e[k] for k in range(s.n)]
        out[fid] = _feat(diff[i], 1 if diff[i] > 0 else -1 if diff[i] < 0 else 0, pct_rank(diff, i), A)
    return out


def _ad_line(s: Series) -> list:
    ad, run = [None] * s.n, 0.0
    for k in range(s.n):
        h, l, c = s.h[k], s.l[k], s.c[k]
        if None not in (h, l, c) and h > l:
            run += ((c - l) - (h - c)) / (h - l) * s.v[k]
        ad[k] = run
    return ad


def ind_ad(s: Series) -> dict:
    out = {}
    ad = s.cached("ad", lambda: _ad_line(s))
    for hz, n in (("F", 14), ("S", 50)):
        fa, fp = linreg(ad, n, s.n - 1), linreg(s.c, n, s.n - 1)
        if fa is None or fp is None:
            out[f"AD_SLOPE@{hz}:TREND"] = _feat(None, None, None, W)
            out[f"AD_SLOPE@{hz}:DIVERGENCE"] = _feat(None, None, None, W)
            continue
        sa, sp = fa[0], fp[0]
        div = 1 if sp < 0 < sa else -1 if sp > 0 > sa else 0
        out[f"AD_SLOPE@{hz}:TREND"] = _feat(sa, _sign(sa), None, A)
        out[f"AD_SLOPE@{hz}:DIVERGENCE"] = _feat(sa, div, None, A)
    return out


def ind_vwma(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 20), ("S", 50)):
        fid = f"VWMA_20@{hz}:TREND"
        i = s.n - 1
        if i - n + 1 < 0 or any(v is None for v in s.c[i - n + 1:i + 1]):
            out[fid] = _feat(None, None, None, W)
            continue
        vs = s.v[i - n + 1:i + 1]
        cs = s.c[i - n + 1:i + 1]
        if sum(vs) <= 0:
            out[fid] = _feat(None, None, None, W)
            continue
        vwma = sum(c * v for c, v in zip(cs, vs)) / sum(vs)
        ma = sum(cs) / n
        c = s.c[i]
        score = 1 if c > vwma and vwma > ma else -1 if c < vwma and vwma < ma else 0
        out[fid] = _feat((c / vwma - 1.0) * 1e4, score, None, A)
    return out


def ind_mfi(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 9), ("S", 50)):
        mfi = [None] * s.n
        for i in range(n, s.n):
            pos = neg = 0.0
            bad = False
            for k in range(i - n + 1, i + 1):
                if s.tp[k] is None or s.tp[k - 1] is None:
                    bad = True
                    break
                flow = s.tp[k] * s.v[k]
                if s.tp[k] > s.tp[k - 1]:
                    pos += flow
                elif s.tp[k] < s.tp[k - 1]:
                    neg += flow
            if not bad:
                mfi[i] = 50.0 if pos + neg == 0 else 100.0 * pos / (pos + neg)
        i = s.n - 1
        ok = i >= 0 and mfi[i] is not None
        x = mfi[i] if ok else None
        trend = (1 if x > 60 else -1 if x < 40 else 0) if ok else None
        rev = (1 if x < 20 else -1 if x > 80 else 0) if ok else None
        _wf(out, f"MFI@{hz}", x, pct_rank(mfi, i) if ok else None, trend, rev, ok)
    return out


def ind_cmf(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 12), ("S", 50)):
        fid = f"CMF@{hz}:TREND"
        i = s.n - 1
        if i - n + 1 < 0:
            out[fid] = _feat(None, None, None, W)
            continue
        num = den = 0.0
        for k in range(i - n + 1, i + 1):
            h, l, c = s.h[k], s.l[k], s.c[k]
            if None in (h, l, c):
                continue
            if h > l:
                num += ((c - l) - (h - c)) / (h - l) * s.v[k]
            den += s.v[k]
        if den <= 0:
            out[fid] = _feat(None, None, None, W)
            continue
        x = num / den
        out[fid] = _feat(x, 1 if x > 0.05 else -1 if x < -0.05 else 0, None, A)
    return out


def ind_klinger(s: Series) -> dict:
    fid = "KLINGER@F:TREND"
    vf = [None] * s.n
    trend_prev, dm_prev, cm = None, None, 0.0
    for k in range(1, s.n):
        if None in (s.h[k], s.l[k], s.c[k], s.h[k - 1], s.l[k - 1], s.c[k - 1]):
            trend_prev, dm_prev, cm = None, None, 0.0
            continue
        t = 1 if (s.h[k] + s.l[k] + s.c[k]) > (s.h[k - 1] + s.l[k - 1] + s.c[k - 1]) else -1
        dm = s.h[k] - s.l[k]
        if trend_prev is None:
            cm = dm
        elif t == trend_prev:
            cm = cm + dm
        else:
            cm = (dm_prev or 0.0) + dm
        vf[k] = 0.0 if cm == 0 else s.v[k] * abs(2.0 * (dm / cm) - 1.0) * t * 100.0
        trend_prev, dm_prev = t, dm
    kvo = [None if a is None or b is None else a - b for a, b in zip(ema(vf, 17), ema(vf, 34))]
    sig = ema(kvo, 9)
    i = s.n - 1
    if i < 0 or kvo[i] is None or sig[i] is None:
        return {fid: _feat(None, None, None, W)}
    diff = [None if a is None or b is None else a - b for a, b in zip(kvo, sig)]
    return {fid: _feat(diff[i], 1 if diff[i] > 0 else -1, pct_rank(diff, i), A)}


def ind_eom(s: Series) -> dict:
    out = {}
    emv = [None] * s.n
    for k in range(1, s.n):
        if None in (s.med[k], s.med[k - 1], s.h[k], s.l[k]):
            continue
        rng = s.h[k] - s.l[k]
        emv[k] = 0.0 if s.v[k] <= 0 or rng <= 0 else (s.med[k] - s.med[k - 1]) * rng / s.v[k]
    for hz, n in (("F", 9), ("S", 50)):
        m, i = sma(emv, n), s.n - 1
        fid = f"EOM@{hz}:TREND"
        if i < 0 or m[i] is None:
            out[fid] = _feat(None, None, None, W)
            continue
        out[fid] = _feat(m[i], _sign(m[i]), pct_rank(m, i), A)
    return out


def ind_net_taker(s: Series) -> dict:
    out = {}
    net = [b - sl for b, sl in zip(s.buy, s.sell)]
    for hz, bars in (("F", 1), ("S", 5)):
        x = net if bars == 1 else [None if k < bars - 1 else sum(net[k - bars + 1:k + 1]) for k in range(s.n)]
        absx = [None if v is None else abs(v) for v in x]
        i = s.n - 1
        p = pct_rank(absx, i)
        fid = f"NET_TAKER_VOL@{hz}:TREND"
        if i < 0 or x[i] is None or p is None:
            out[fid] = _feat(None if i < 0 else x[i], None, None, W)
            continue
        out[fid] = _feat(x[i], _sign(x[i]) if p > 70 else 0, pct_rank(x, i), A)
    return out


def ind_book_imbalance(s: Series, xv_seen: Mapping[str, bool]) -> dict:
    out = {}
    sources = (("BFX", [_finite(b.get("tob_imb")) for b in s.bars], True),
               ("BN5", [_finite((b.get("xv") or {}).get("imb5")) for b in s.bars], bool(xv_seen.get("imb5"))),
               ("BN20", [_finite((b.get("xv") or {}).get("imb20")) for b in s.bars], bool(xv_seen.get("imb20"))))
    for hz, series, collected in sources:
        fid = f"BOOK_IMBALANCE@{hz}:TREND"
        i = s.n - 1
        if i < 0 or not collected or series[i] is None:
            out[fid] = _feat(None, None, None, U)
            continue
        x = series[i]
        out[fid] = _feat(x, 1 if x > 0.3 else -1 if x < -0.3 else 0, pct_rank(series, i), A)
    return out


def ind_cvd(s: Series) -> dict:
    out = {}
    n = 20
    bfx_net = [b - sl for b, sl in zip(s.buy, s.sell)]
    bn_net = [_finite((b.get("xv") or {}).get("bn_net")) for b in s.bars]
    for hz, net in (("BFX", bfx_net), ("BN", bn_net)):
        i = s.n - 1
        window = net[i - n + 1:i + 1] if i - n + 1 >= 0 else []
        if not window or any(v is None for v in window):
            status = U if hz == "BN" and i >= 0 and net[i] is None else W
            out[f"CVD@{hz}:TREND"] = _feat(None, None, None, status)
            out[f"CVD@{hz}:DIVERGENCE"] = _feat(None, None, None, status)
            continue
        cvd, run = [], 0.0
        for v in window:
            run += v
            cvd.append(run)
        sc = linreg(cvd, n, n - 1)[0]
        fp = linreg(s.c, n, i)
        sp = fp[0] if fp else None
        div = (-1 if sp is not None and sp > 0 > sc else 1 if sp is not None and sp < 0 < sc else 0)
        out[f"CVD@{hz}:TREND"] = _feat(sc, _sign(sc), None, A)
        out[f"CVD@{hz}:DIVERGENCE"] = _feat(sc, div, None, A)
    return out


def ind_xvenue_flow(s: Series) -> dict:
    fid = "XVENUE_NET_FLOW@F:TREND"
    x = []
    for b, buy, sell in zip(s.bars, s.buy, s.sell):
        xv = b.get("xv") or {}
        bn, bb = _finite(xv.get("bn_net")), _finite(xv.get("bb_net"))
        x.append(None if bn is None or bb is None or xv.get("state") == "MISSING" else bn + bb - (buy - sell))
    i = s.n - 1
    if i < 0 or x[i] is None:
        return {fid: _feat(None, None, None, U)}
    p = pct_rank([None if v is None else abs(v) for v in x], i)
    if p is None:
        return {fid: _feat(x[i], None, None, W)}
    return {fid: _feat(x[i], _sign(x[i]) if p > 70 else 0, pct_rank(x, i), A)}


def ind_bb(s: Series, regime: dict) -> dict:
    out = {}
    for hz, n in (("F", 20), ("S", 50)):
        ma, sd = sma(s.c, n), rolling_std(s.c, n)
        bw = [None if m is None or d is None or m == 0 else 4.0 * d / m for m, d in zip(ma, sd)]
        i = s.n - 1
        fid = f"BB@{hz}:BREAKOUT"
        if i < 0 or ma[i] is None:
            out[fid] = _feat(None, None, None, W)
            continue
        upper, lower = ma[i] + 2.0 * sd[i], ma[i] - 2.0 * sd[i]
        pb = 0.5 if upper == lower else (s.c[i] - lower) / (upper - lower)
        squeeze = [None] * s.n
        for k in range(max(0, i - 6), i + 1):
            p = pct_rank(bw, k)
            squeeze[k] = None if p is None else p < 20
        if squeeze[i] is None:
            out[fid] = _feat(pb, None, None, W)
            continue
        recent = any(squeeze[k] for k in range(max(0, i - 5), i) if squeeze[k] is not None)
        score = (1 if s.c[i] > upper else -1 if s.c[i] < lower else 0) if recent else 0
        out[fid] = _feat(pb, score, pct_rank(bw, i), A)
        if hz == "F":
            regime["bb_squeeze"] = bool(squeeze[i])
    return out


def ind_atr_regime(s: Series, regime: dict) -> dict:
    fid = "ATR@F:STATE"
    atr, i = s.atr(14), s.n - 1
    atr_pct = [None if a is None or c is None else a / c * 100.0 for a, c in zip(atr, s.c)]
    if i < 0 or atr_pct[i] is None:
        regime.update(vol_tercile="WARMING_UP", atr_pct=None, atr_pct_rank=None)
        return {fid: _feat(None, None, None, W)}
    p = pct_rank(atr_pct, i)
    regime["atr_pct"] = _r(atr_pct[i], 5)
    regime["atr_pct_rank"] = p
    regime["vol_tercile"] = ("WARMING_UP" if p is None else "LOW" if p < 100 / 3.0 else "MID" if p < 200 / 3.0 else "HIGH")
    return {fid: _feat(atr_pct[i], None, p, A if p is not None else W)}


def ind_keltner(s: Series) -> dict:
    out = {}
    mid, atr, i = s.ema(20), s.atr(20), s.n - 1
    if i < 0 or mid[i] is None or atr[i] is None:
        _wf(out, "KELTNER@F", None, None, None, None, False)
        return out
    upper, lower = mid[i] + 1.5 * atr[i], mid[i] - 1.5 * atr[i]
    c = s.c[i]
    trend = 1 if c > upper else -1 if c < lower else 0
    rev = 1 if s.l[i] <= lower < c else -1 if s.h[i] >= upper > c else 0
    pos = 0.0 if atr[i] == 0 else (c - mid[i]) / (1.5 * atr[i])
    _wf(out, "KELTNER@F", pos, None, trend, rev, True)
    return out


def ind_donchian(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 20), ("S", 50)):
        fid = f"DONCHIAN@{hz}:BREAKOUT"
        i = s.n - 1
        if i - n < 0:
            out[fid] = _feat(None, None, None, W)
            continue
        hh, ll = _hh(s.h, i - 1, n), _ll(s.l, i - 1, n)
        if hh is None or ll is None:
            out[fid] = _feat(None, None, None, W)
            continue
        c = s.c[i]
        pos = 0.5 if hh == ll else (c - ll) / (hh - ll)
        out[fid] = _feat(pos, 1 if c > hh else -1 if c < ll else 0, None, A)
    return out


def ind_chaikin_vol(s: Series, regime: dict) -> dict:
    fid = "CHAIKIN_VOL@F:STATE"
    rng = [None if h is None or l is None else h - l for h, l in zip(s.h, s.l)]
    e = ema(rng, 10)
    cv = [None if k < 10 or e[k] is None or e[k - 10] in (None, 0) else (e[k] / e[k - 10] - 1.0) * 100.0
          for k in range(s.n)]
    i = s.n - 1
    if i < 0 or cv[i] is None:
        regime["chaikin_expansion"] = None
        return {fid: _feat(None, None, None, W)}
    p = pct_rank(cv, i)
    regime["chaikin_expansion"] = None if p is None else p > 80
    return {fid: _feat(cv[i], None, p, A if p is not None else W)}


def _logrets(c: list) -> list:
    return [None] + [None if c[k] is None or c[k - 1] is None or c[k - 1] <= 0 or c[k] <= 0
                     else math.log(c[k] / c[k - 1]) for k in range(1, len(c))]


def ind_hv(s: Series) -> dict:
    fid = "HV@F:REVERSION"
    hv = rolling_std(s.cached("logret", lambda: _logrets(s.c)), 10)
    i = s.n - 1
    if i < 3 or hv[i] is None:
        return {fid: _feat(None, None, None, W)}
    p = pct_rank(hv, i)
    if p is None:
        return {fid: _feat(hv[i] * 1e4, None, None, W)}
    score = -_sign(s.c[i] - s.c[i - 3]) if p > 95 else 0
    return {fid: _feat(hv[i] * 1e4, score, p, A)}


def ind_stddev_z(s: Series) -> dict:
    out = {}
    for hz, n in (("F", 20), ("S", 50)):
        ma, sd, i = sma(s.c, n), rolling_std(s.c, n), s.n - 1
        fid = f"STDDEV_Z@{hz}:REVERSION"
        if i < 0 or ma[i] is None or not sd[i]:
            out[fid] = _feat(None, None, None, W)
            continue
        z = [None if m is None or not d or c is None else (c - m) / d for m, d, c in zip(ma, sd, s.c)]
        out[fid] = _feat(z[i], 1 if z[i] < -2 else -1 if z[i] > 2 else 0, pct_rank(z, i), A)
    return out


def ind_fib(s: Series, levels: dict, bars: int = 80) -> dict:
    out = {}
    i = s.n - 1
    if i - bars < 0 or any(v is None for v in s.h[i - bars:i]) or any(v is None for v in s.l[i - bars:i]):
        out["FIB_4H@F:REVERSION"] = _feat(None, None, None, W)
        out["FIB_EXT_4H@F:STATE"] = _feat(None, None, None, W)
        return out
    hs, ls = s.h[i - bars:i], s.l[i - bars:i]
    hi, lo = max(hs), min(ls)
    ih = (i - bars) + max(range(bars), key=lambda k: (hs[k], k))
    il = (i - bars) + max(range(bars), key=lambda k: (-ls[k], k))
    rng = hi - lo
    c = s.c[i]
    if rng <= 0:
        out["FIB_4H@F:REVERSION"] = _feat(0.5, 0, None, A)
        out["FIB_EXT_4H@F:STATE"] = _feat(None, None, None, A)
        return out
    up = ih > il
    score = 0
    if up:
        lvl = hi - 0.618 * rng
        if s.l[i] <= lvl < c:
            score = 1
        ext = {"1.272": lo + 1.272 * rng, "1.618": lo + 1.618 * rng}
    else:
        lvl = lo + 0.618 * rng
        if s.h[i] >= lvl > c:
            score = -1
        ext = {"1.272": hi - 1.272 * rng, "1.618": hi - 1.618 * rng}
    levels["fib_leg"] = "UP" if up else "DOWN"
    levels["fib_0618"] = _r(lvl, 2)
    levels["fib_ext"] = {k: _r(v, 2) for k, v in ext.items()}
    out["FIB_4H@F:REVERSION"] = _feat((c - lo) / rng, score, None, A)
    out["FIB_EXT_4H@F:STATE"] = _feat((ext["1.272"] / c - 1.0) * 1e4, None, None, A)
    return out


def _prev_day(s: Series, min_coverage: float = 0.9) -> Optional[tuple]:
    i = s.n - 1
    day0 = s.ts[i] - s.ts[i] % 86400
    prev0 = day0 - 86400
    ks = [k for k in range(s.n) if prev0 <= s.ts[k] < day0]
    good = [k for k in ks if not s.bars[k].get("filled") and None not in (s.h[k], s.l[k], s.c[k])]
    if len(good) < min_coverage * (86400 // BAR_SEC):
        return None
    return max(s.h[k] for k in good), min(s.l[k] for k in good), s.c[max(good)]


def ind_pivots(s: Series, levels: dict) -> dict:
    out = {}
    pd = _prev_day(s) if s.n else None
    i = s.n - 1
    if pd is None:
        _wf(out, "CPR_PIVOTS@F", None, None, None, None, False)
        out["CAMARILLA@F:REVERSION"] = _feat(None, None, None, W)
        out["CAMARILLA@F:BREAKOUT"] = _feat(None, None, None, W)
        return out
    H, L, C = pd
    c, h, l = s.c[i], s.h[i], s.l[i]
    p = (H + L + C) / 3.0
    bc = (H + L) / 2.0
    tc = 2 * p - bc
    tc, bc = max(tc, bc), min(tc, bc)
    r1, s1 = 2 * p - L, 2 * p - H
    trend = 1 if c > tc else -1 if c < bc else 0
    rev = 1 if l <= s1 < c else -1 if h >= r1 > c else 0
    _wf(out, "CPR_PIVOTS@F", (c - p) / c * 1e4, None, trend, rev, True)
    rng = H - L
    h3, l3, h4, l4 = C + rng * 1.1 / 4, C - rng * 1.1 / 4, C + rng * 1.1 / 2, C - rng * 1.1 / 2
    cam_rev = -1 if h >= h3 > c else 1 if l <= l3 < c else 0
    cam_brk = 1 if c > h4 else -1 if c < l4 else 0
    raw = 0.0 if rng <= 0 else (c - C) / rng
    out["CAMARILLA@F:REVERSION"] = _feat(raw, cam_rev, None, A)
    out["CAMARILLA@F:BREAKOUT"] = _feat(raw, cam_brk, None, A)
    levels.update(cpr_p=_r(p, 2), cpr_tc=_r(tc, 2), cpr_bc=_r(bc, 2), r1=_r(r1, 2), s1=_r(s1, 2),
                  cam_h3=_r(h3, 2), cam_l3=_r(l3, 2), cam_h4=_r(h4, 2), cam_l4=_r(l4, 2))
    return out


def zigzag_swings(s: Series, reversal_pct: float, upto: int) -> list:
    """Confirmed swings [(kind, idx, price, confirmed_at)] using bars 0..upto (causal)."""
    swings = []
    thr = reversal_pct / 100.0
    direction, ext, ext_i = None, None, None
    for j in range(0, upto + 1):
        h, l = s.h[j], s.l[j]
        if h is None or l is None:
            continue
        if direction is None:
            direction, ext, ext_i = "UP", h, j
            continue
        if direction == "UP":
            if h > ext:
                ext, ext_i = h, j
            elif l <= ext * (1.0 - thr):
                swings.append(("H", ext_i, ext, j))
                direction, ext, ext_i = "DOWN", l, j
        else:
            if l < ext:
                ext, ext_i = l, j
            elif h >= ext * (1.0 + thr):
                swings.append(("L", ext_i, ext, j))
                direction, ext, ext_i = "UP", h, j
    return swings


def ind_zigzag(s: Series) -> dict:
    fid = "ZIGZAG_MSB@F:BREAKOUT"
    i = s.n - 1
    if i < 2:
        return {fid: _feat(None, None, None, W)}
    swings = zigzag_swings(s, 0.3, i - 1)
    sh = next((sw for sw in reversed(swings) if sw[0] == "H"), None)
    sl = next((sw for sw in reversed(swings) if sw[0] == "L"), None)
    if sh is None or sl is None:
        return {fid: _feat(None, None, None, W)}
    c, pc = s.c[i], s.c[i - 1]
    score = 1 if pc <= sh[2] < c else -1 if pc >= sl[2] > c else 0
    return {fid: _feat((c - sh[2]) / c * 1e4, score, None, A)}


def ind_pitchfork(s: Series, bars: int = 240) -> dict:
    fid = "PITCHFORK_12H@F:STATE"
    i = s.n - 1
    if i < bars:
        return {fid: _feat(None, None, None, W)}
    swings = [sw for sw in zigzag_swings(s, 0.8, i - 1) if sw[1] >= i - bars]
    if len(swings) < 3:
        return {fid: _feat(None, 0, None, A)}
    p0, p1, p2 = swings[-3], swings[-2], swings[-1]
    mi, mp = (p1[1] + p2[1]) / 2.0, (p1[2] + p2[2]) / 2.0
    if mi == p0[1]:
        return {fid: _feat(None, 0, None, A)}
    slope = (mp - p0[2]) / (mi - p0[1])
    median = p0[2] + slope * (i - p0[1])
    c = s.c[i]
    score = 1 if slope > 0 and c > median else -1 if slope < 0 and c < median else 0
    return {fid: _feat((c / median - 1.0) * 1e4 if median > 0 else None, score, None, A)}


def ind_liq(s: Series) -> dict:
    out = {}
    tot, side = [], []
    for b in s.bars:
        mc = b.get("mc") or {}
        if not mc.get("liq_ok"):
            tot.append(None)
            side.append(0)
            continue
        lo, sh = _finite(mc.get("liq_long")) or 0.0, _finite(mc.get("liq_short")) or 0.0
        tot.append(lo + sh)
        side.append(1 if lo > sh else -1 if sh > lo else 0)
    i = s.n - 1
    if i < 0 or tot[i] is None:
        out["LIQ_BURST@F:REVERSION"] = _feat(None, None, None, U)
        out["LIQ_BURST@F:TREND"] = _feat(None, None, None, U)
        return out
    p = pct_rank(tot, i)
    if p is None:
        out["LIQ_BURST@F:REVERSION"] = _feat(tot[i], None, None, W)
        out["LIQ_BURST@F:TREND"] = _feat(tot[i], None, None, W)
        return out
    burst = p > 95 and tot[i] > 0
    out["LIQ_BURST@F:REVERSION"] = _feat(tot[i], side[i] if burst else 0, p, A)
    out["LIQ_BURST@F:TREND"] = _feat(tot[i], -side[i] if burst else 0, p, A)
    return out


def ind_vol_expected(s: Series, regime: dict) -> dict:
    fid = "VOL_EXPECTED@F:STATE"
    rv = rolling_std(s.cached("logret", lambda: _logrets(s.c)), 20)
    i = s.n - 1
    if i < 0 or rv[i] is None:
        regime["vol_expected_pct"] = None
        return {fid: _feat(None, None, None, W)}
    p = pct_rank(rv, i)
    regime["vol_expected_pct"] = p
    return {fid: _feat(rv[i] * 1e4, None, p, A if p is not None else W)}


def ind_funding(s: Series) -> dict:
    fid = "FUNDING@F:REVERSION"
    f = [_finite((b.get("mc") or {}).get("funding")) for b in s.bars]
    i = s.n - 1
    if i < 0 or f[i] is None:
        return {fid: _feat(None, None, None, U)}
    p = pct_rank(f, i)
    if p is None:
        return {fid: _feat(f[i] * 1e4, None, None, W)}
    return {fid: _feat(f[i] * 1e4, -1 if p > 90 else 1 if p < 10 else 0, p, A)}


def ind_oi(s: Series) -> dict:
    out = {}
    oi = [_finite((b.get("mc") or {}).get("oi")) for b in s.bars]
    for hz, k in (("F", 1), ("S", 20)):
        fid = f"OI_DELTA@{hz}:TREND"
        i = s.n - 1
        if i - k < 0:
            out[fid] = _feat(None, None, None, W)
            continue
        if oi[i] is None or oi[i - k] is None:
            out[fid] = _feat(None, None, None, U)
            continue
        d = oi[i] - oi[i - k]
        pdir = _sign(s.c[i] - s.c[i - k])
        score = (1 if pdir > 0 else -1 if pdir < 0 else 0) if d > 0 else 0
        series = [None if j - k < 0 or oi[j] is None or oi[j - k] is None else oi[j] - oi[j - k] for j in range(s.n)]
        out[fid] = _feat(d, score, pct_rank(series, i), A)
    return out


def ind_basis_premium(s: Series, rule: PremiumRule) -> dict:
    fid = "BASIS_PREMIUM@F:TREND"
    i = s.n - 1
    xv = (s.bars[i].get("xv") or {}) if i >= 0 else {}
    if i < 0 or xv.get("state") in (None, "MISSING"):
        return {fid: _feat(None, None, None, U)}
    if not xv.get("prem_ready") or xv.get("prem_dev") is None:
        return {fid: _feat(None, None, None, W)}
    dev = float(xv["prem_dev"])
    side = rule.side_for(dev)
    devs = [_finite((b.get("xv") or {}).get("prem_dev")) for b in s.bars]
    return {fid: _feat(dev, 1 if side == "LONG" else -1 if side == "SHORT" else 0, pct_rank(devs, i), A)}


def ind_xvenue_lead(s: Series) -> dict:
    fid = "XVENUE_LEAD@F:TREND"
    i = s.n - 1
    xv = (s.bars[i].get("xv") or {}) if i >= 0 else {}
    if i < 0 or xv.get("state") in (None, "MISSING"):
        return {fid: _feat(None, None, None, U)}
    net = int(xv.get("lead_long") or 0) - int(xv.get("lead_short") or 0)
    return {fid: _feat(xv.get("lead_close"), _sign(net), None, A)}


def adx_series(s: Series, n: int = 14) -> list:
    plus_dm, minus_dm = [None], [None]
    for k in range(1, s.n):
        if None in (s.h[k], s.h[k - 1], s.l[k], s.l[k - 1]):
            plus_dm.append(None)
            minus_dm.append(None)
            continue
        up, dn = s.h[k] - s.h[k - 1], s.l[k - 1] - s.l[k]
        plus_dm.append(up if up > dn and up > 0 else 0.0)
        minus_dm.append(dn if dn > up and dn > 0 else 0.0)
    tr = s.tr()
    atr, pdm, mdm = wilder([None] + tr[1:], n), wilder(plus_dm, n), wilder(minus_dm, n)
    dx = [None] * s.n
    for k in range(s.n):
        if None in (atr[k], pdm[k], mdm[k]) or not atr[k]:
            continue
        pdi, mdi = 100.0 * pdm[k] / atr[k], 100.0 * mdm[k] / atr[k]
        dx[k] = 0.0 if pdi + mdi == 0 else 100.0 * abs(pdi - mdi) / (pdi + mdi)
    return wilder(dx, n)


def regime_labels(s: Series, regime: dict, ai: Optional[Mapping[str, Any]]) -> dict:
    i = s.n - 1
    adx = adx_series(s)[i] if s.n else None
    regime["adx"] = _r(adx, 3)
    regime["trend_state"] = ("WARMING_UP" if adx is None else "RANGE" if adx < 20 else "WEAK" if adx < 30 else "TREND")
    close_ts = s.bars[i]["close_ts"] if s.n else None
    regime["session"] = session_of(close_ts) if close_ts is not None else None
    sp = _finite(s.bars[i].get("spread_bp")) if s.n else None
    regime["spread_bp"] = sp
    regime["spread_bucket"] = (None if sp is None else "<1" if sp < 1 else "1-2" if sp < 2 else "2-4" if sp < 4 else ">=4")
    nxt = _finite((s.bars[i].get("mc") or {}).get("next_funding_ms")) if s.n else None
    ttf = None if nxt is None or close_ts is None else (nxt / 1000.0 - close_ts) / 60.0
    regime["time_to_funding_min"] = _r(ttf, 1)
    regime["ttf_bucket"] = (None if ttf is None or ttf < 0 else "<30" if ttf < 30 else "30-120" if ttf < 120
                            else "120-240" if ttf < 240 else ">=240")
    ai = ai or {}
    regime["ai_class"] = ai.get("ai_class")
    regime["ai_side"] = ai.get("ai_side")
    regime["ai_age_sec"] = ai.get("ai_age_sec")
    return regime


def compute_features(bars: list, *, xv_seen: Optional[Mapping[str, bool]] = None,
                     ai: Optional[Mapping[str, Any]] = None,
                     premium_rule: Optional[PremiumRule] = None) -> dict:
    """Feature vector, regime labels and levels for the last bar of ``bars`` (closed bars only)."""
    s = Series(list(bars)[-spec.HISTORY_BARS:])
    regime: dict = {}
    levels: dict = {}
    f: dict = {}
    for fn in (ind_ema, ind_ichimoku, ind_supertrend, ind_psar, ind_hma, ind_linreg, ind_tema, ind_rsi, ind_stoch,
               ind_macd, ind_cci, ind_willr, ind_ao, ind_kst, ind_cmo, ind_roc, ind_dpo, ind_vp_session, ind_avwap,
               ind_obv, ind_ad, ind_vwma, ind_mfi, ind_cmf, ind_klinger, ind_eom, ind_net_taker, ind_cvd,
               ind_xvenue_flow, ind_keltner, ind_donchian, ind_hv, ind_stddev_z, ind_zigzag, ind_pitchfork,
               ind_liq, ind_funding, ind_oi, ind_xvenue_lead):
        f.update(fn(s))
    f.update(ind_book_imbalance(s, xv_seen or {}))
    f.update(ind_bb(s, regime))
    f.update(ind_atr_regime(s, regime))
    f.update(ind_chaikin_vol(s, regime))
    f.update(ind_vol_expected(s, regime))
    f.update(ind_fib(s, levels))
    f.update(ind_pivots(s, levels))
    f.update(ind_basis_premium(s, premium_rule or PremiumRule()))
    regime_labels(s, regime, ai)
    ordered = {}
    for fid in spec.feature_ids():
        ordered[fid] = f.get(fid) or _feat(None, None, None, W)
    return {"f": ordered, "regime": regime, "levels": levels}


def row_health(bars: list) -> dict:
    last = bars[-1]
    recent = bars[-HISTORY_INTEGRITY_BARS:]
    missing = sum(1 for b in recent if b.get("tape") == "MISSING")
    reasons = []
    if last.get("tape") != "OK":
        reasons.append(f"TAPE_{last.get('tape')}")
    if missing:
        reasons.append(f"TAPE_HOLES_LAST_{HISTORY_INTEGRITY_BARS}:{missing}")
    if (last.get("xv") or {}).get("state") != "OK":
        reasons.append(f"XVENUE_{(last.get('xv') or {}).get('state')}")
    if (last.get("mc") or {}).get("state") != "OK":
        reasons.append(f"MARKET_CONTEXT_{(last.get('mc') or {}).get('state')}")
    return {"ok": last.get("tape") == "OK" and missing == 0,
            "tape": last.get("tape"), "xvenue": (last.get("xv") or {}).get("state"),
            "market_context": (last.get("mc") or {}).get("state"),
            "history_bars": len(bars), "tape_holes_recent": missing, "reasons": reasons}


def status_counts(f: Mapping[str, list]) -> dict:
    counts = {"AVAILABLE": 0, "WARMING_UP": 0, "UNAVAILABLE": 0}
    for vals in f.values():
        counts[spec.STATUS_NAMES.get(vals[3], "WARMING_UP")] += 1
    return counts


def latest_ai(snapshots: Iterable[Mapping[str, Any]], close_ts: float) -> dict:
    """``ai_class``/``ai_side`` of the latest decision snapshot at or before ``close_ts``."""
    best = None
    for row in snapshots:
        if not isinstance(row, Mapping) or row.get("row_kind") not in (None, "SNAPSHOT"):
            continue
        ts = _finite(row.get("decision_ts"))
        if ts is None or ts > close_ts:
            continue
        if best is None or ts > best[0]:
            best = (ts, row)
    if best is None:
        return {"ai_class": None, "ai_side": None, "ai_age_sec": None}
    ts, row = best
    ai = row.get("ai") or {}
    raw = str(ai.get("raw_direction") or ai.get("direction") or "").upper()
    ls, ss = _finite(ai.get("long_score")), _finite(ai.get("short_score"))
    score_side = None if ls is None or ss is None or ls == ss else ("LONG" if ls > ss else "SHORT")
    if raw in ("LONG", "SHORT"):
        klass = "COMMITTED_SCORE_CONFLICT" if score_side and score_side != raw else "COMMITTED"
        side = raw
    elif score_side:
        klass, side = "NO_TRADE_SCORE_LED", score_side
    else:
        klass, side = "NO_SIDE", "NONE"
    return {"ai_class": klass, "ai_side": side, "ai_age_sec": round(close_ts - ts, 1)}
