"""DISLOC paper engine: Bitfinex dislocation vs the Binance/Bybit composite (self-simulated, paper only).

Rule (audit/STRATEGIST-AUDIT-20261011.md §4c, wide grid, validated on
validation + holdout; AI-free):

* ``prem``    = (bitfinex_mid / mean(binance_mid, bybit_mid) - 1) * 1e4 at the
  closed anchor second (same definition as the audit's features_1m build).
* ``prem_d5`` = prem(anchor) - prem(anchor - 300 s).
* LONG  Bitfinex (cheap) when prem <= 8.1769 and prem_d5 <= -3.0183  (discovery p10/p10)
* SHORT Bitfinex (rich)  when prem >  13.0163 and prem_d5 >  2.9749  (discovery p90/p90)
  Thresholds are the audit's discovery-window quantiles and are never re-fitted.

Execution (paper, realistic):

* One post-only limit at the touch (LONG at the bid, SHORT at the ask) on the
  rising edge of a trigger, max one order/position open.
* Fill only when the real Bitfinex trade stream trades THROUGH the limit
  (a seller-aggressor print strictly below a LONG bid / buyer-aggressor print
  strictly above a SHORT ask) after the order time. At-limit prints never fill
  (no queue credit), so the fill rate is conservative.
* Cancel when unfilled after ``ttl_sec`` (5 s) -> ``TTL_5S``, or when the gap has
  closed before a fill -> ``GAP_CLOSED_BEFORE_FILL``.
* Exit first-trigger-wins: 40 bp stop on the side-correct touch, gap closed
  (prem back inside the entry threshold) or the 60 s time exit; exits book the
  side-correct touch (taker). P&L is reported vs touch (maker entry at limit,
  taker exit at touch; Bitfinex fee 0) and vs mid (signal mid -> exit mid).

Nothing here places, changes or cancels a real order or reads relay state.
"""
from __future__ import annotations

import math
import statistics
import time
from collections import deque
from typing import Any, Callable, Iterable, Mapping, Optional

SCHEMA = "disloc_paper_v1"
TRADES_FILE = "disloc_trades.jsonl"
LONG, SHORT = "LONG", "SHORT"
# Strategist DEEP-SEARCH-TILES-20261011: reported net charges the measured 2.8 bp taker round trip.
TAKER_COST_BP = 2.8
DELAY_BUCKETS = (("le_2s", 0.0, 2.0), ("2_to_5s", 2.0, 5.0))
SIDE_TAGS = {LONG: "LONG_CONFIRMED_BY_DEEP_SEARCH", SHORT: "SHORT_REFUTED_AT_REAL_LATENCY"}


def _f(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None


def _num(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class DislocRule:
    def __init__(self, entry: Mapping[str, Any], exit_policy: Mapping[str, Any]) -> None:
        d = entry["disloc"]
        self.venues = tuple(entry.get("leader_venues") or ("binance", "bybit"))
        self.long_prem_max = float(d["long_prem_max_bp"])
        self.long_d5_max = float(d["long_prem_d5_max_bp"])
        self.short_prem_min = float(d["short_prem_min_bp"])
        self.short_d5_min = float(d["short_prem_d5_min_bp"])
        self.d5_lag_sec = int(d["prem_d5_lag_sec"])
        self.ttl_sec = float(d["order_ttl_sec"])
        self.hold_sec = float(exit_policy["max_duration_sec"])
        self.stop_bp = float(exit_policy["hard_stop_bps"])
        self.max_venue_age = float(entry.get("max_venue_age_sec", 2.0))
        self.max_bbo_age = float(entry.get("max_bbo_age_sec", 2.0))
        self.ffill_sec = int(entry.get("max_fill_forward_sec", 5))

    def side(self, prem, d5) -> Optional[str]:
        if prem is None or d5 is None:
            return None
        if prem <= self.long_prem_max and d5 <= self.long_d5_max:
            return LONG
        if prem > self.short_prem_min and d5 > self.short_d5_min:
            return SHORT
        return None

    def gap_closed(self, side: str, prem) -> bool:
        if prem is None:
            return False
        return prem > self.long_prem_max if side == LONG else prem <= self.short_prem_min

    def identity(self) -> dict:
        return {k: v for k, v in vars(self).items()}


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 4)


def cluster_ci(rows: list, key: str = "net_bp_taker_2p8", n_boot: int = 2000, seed: int = 7):
    """90% 1h-cluster bootstrap CI of the mean (hour of fill_ts)."""
    clusters: dict = {}
    for r in rows:
        v = _num(r.get(key))
        if v is None:
            continue
        clusters.setdefault(int(float(r.get("fill_ts") or 0) // 3600), []).append(v)
    groups = list(clusters.values())
    if len(groups) < 2:
        return None
    import random
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        pick = [groups[rng.randrange(len(groups))] for _ in groups]
        flat = [x for g in pick for x in g]
        means.append(sum(flat) / len(flat))
    return [_pct(means, 0.05), _pct(means, 0.95)]


def load_rows(path: str = TRADES_FILE) -> list:
    """Read the DISLOC paper ledger (read-only; missing file -> [])."""
    import json
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def summarize(rows: Iterable[Mapping[str, Any]], pre: Mapping[str, Any] | None = None) -> dict:
    """Fill rate, cancel rate, delay percentiles, mean bp and the pre-registered verdict."""
    rows = [r for r in rows if isinstance(r, Mapping) and r.get("schema") == SCHEMA]
    orders = [r for r in rows if r.get("event") in ("DISLOC_CLOSED", "DISLOC_CANCELLED", "DISLOC_ABANDONED")]
    closed = [r for r in orders if r.get("event") == "DISLOC_CLOSED"]
    cancelled = [r for r in orders if r.get("event") == "DISLOC_CANCELLED"]
    reasons: dict = {}
    for r in cancelled:
        reasons[str(r.get("cancel_reason"))] = reasons.get(str(r.get("cancel_reason")), 0) + 1
    exits: dict = {}
    for r in closed:
        exits[str(r.get("exit_reason"))] = exits.get(str(r.get("exit_reason")), 0) + 1

    def delays(key):
        vals = [_num(r.get(key)) for r in orders]
        vals = [v for v in vals if v is not None]
        return {"n": len(vals), "p50": _pct(vals, .5), "p90": _pct(vals, .9), "p99": _pct(vals, .99),
                "max": max(vals) if vals else None}

    def mean(key, src=closed):
        vals = [_num(r.get(key)) for r in src]
        vals = [v for v in vals if v is not None]
        return round(statistics.fmean(vals), 4) if vals else None

    n = len(closed)
    m = mean("net_bp_taker_2p8")
    ci = cluster_ci(closed)
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in sorted(closed, key=lambda x: float(x.get("exit_ts") or 0)):
        eq += float(r.get("net_bp_taker_2p8") or 0)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    pre = dict(pre or {})
    kill_n, kill_mean = int(pre.get("kill_at_trades", 150)), float(pre.get("kill_mean_below_bp", 0.5))
    prom_n, prom_mean = int(pre.get("promote_min_trades", 250)), float(pre.get("promote_mean_min_bp", 2.0))
    if n >= prom_n and m is not None and m >= prom_mean and ci and ci[0] > 0:
        verdict = "PROMOTE_CANDIDATE"
    elif n >= kill_n and m is not None and m < kill_mean:
        verdict = "KILL"
    else:
        verdict = "COLLECTING"
    def block(src, ords):
        return {"orders": len(ords), "closed": len(src),
                "fill_rate": round(len(src) / len(ords), 4) if ords else None,
                "mean_net_bp_taker_2p8": mean("net_bp_taker_2p8", src),
                "mean_bp_vs_touch": mean("pnl_bp_vs_touch", src), "mean_bp_vs_mid": mean("pnl_bp_vs_mid", src),
                "ci90_1h_cluster_net": cluster_ci(src)}

    by_side = {}
    for sd in (LONG, SHORT):
        src = [r for r in closed if r.get("side") == sd]
        b = block(src, [r for r in orders if r.get("side") == sd])
        b["tag"] = SIDE_TAGS[sd]
        b["by_fill_delay"] = {}
        for name, lo, hi in DELAY_BUCKETS:
            sub = [r for r in src if lo < float(r.get("signal_to_fill_s") or 0) <= hi or
                   (lo == 0.0 and float(r.get("signal_to_fill_s") or 0) <= hi)]
            b["by_fill_delay"][name] = block(sub, sub)
        by_side[sd] = b
    by_delay = {}
    for name, lo, hi in DELAY_BUCKETS:
        sub = [r for r in closed if (float(r.get("signal_to_fill_s") or 0) <= hi and
                                     (lo == 0.0 or float(r.get("signal_to_fill_s") or 0) > lo))]
        by_delay[name] = block(sub, sub)
    return {
        "orders": len(orders), "fills": n + sum(1 for r in orders if r.get("event") == "DISLOC_ABANDONED"
                                                and r.get("fill_ts")),
        "closed_trades": n, "cancelled": len(cancelled), "cancel_reasons": reasons,
        "abandoned": sum(1 for r in orders if r.get("event") == "DISLOC_ABANDONED"),
        "fill_rate": round(n / len(orders), 4) if orders else None,
        "cancel_rate": round(len(cancelled) / len(orders), 4) if orders else None,
        "exit_reasons": exits,
        "delay_sec": {"signal_to_order": delays("signal_to_order_s"),
                      "order_to_fill": delays("order_to_fill_s"),
                      "signal_to_fill": delays("signal_to_fill_s")},
        "net_basis": f"fill-mid -> exit-mid minus {TAKER_COST_BP} bp taker round trip",
        "mean_net_bp_taker_2p8": m, "mean_bp_vs_touch": mean("pnl_bp_vs_touch"),
        "mean_bp_vs_mid": mean("pnl_bp_vs_mid"),
        "mean_gap_at_entry_bp": mean("prem_at_fill_bp"), "mean_gap_at_exit_bp": mean("prem_at_exit_bp"),
        "ci90_1h_cluster_net": ci, "max_drawdown_bp": round(dd, 3), "cum_bp_vs_touch": round(eq, 3),
        "by_side": by_side, "by_fill_delay": by_delay,
        "pre_registered": {"kill": f"at {kill_n} trades if mean net (2.8 bp taker cost) < +{kill_mean:g} bp",
                           "promote": f"at >= {prom_n} trades with mean >= +{prom_mean:g} bp and 1h-cluster CI > 0",
                           "verdict": verdict},
    }


class DislocEngine:
    """1 Hz paper engine driven by the cross-venue evaluator tick; never emits a pipeline trigger."""

    ID_PREFIX = "dsl"
    SHADOW_FILE = None          # the XVL shadow writer skips it; DISLOC writes its own ledger
    SIGNAL_KEY = "prem_bp"
    TRIGGER_SCHEMA = SCHEMA
    TRIGGER_FEATURE_KEY = "disloc_trigger"
    TRIGGER_FEATURE_FIELDS = ()

    def __init__(self, rule: DislocRule, *, policy_id: str = "", policy_signature: str = "",
                 clock: Callable[[], float] = time.time) -> None:
        self.rule = rule
        self.policy_id, self.policy_signature = policy_id, policy_signature
        self.clock = clock
        self.enabled_fn: Callable[[], bool] = lambda: False
        self.trades_fn: Callable[[], list] = lambda: []
        self.touch_fn: Callable[[], tuple] = lambda: (None, None, None)
        self.append_fn: Callable[[dict], Any] = lambda row: None
        self._prem: dict = {}
        self._last_seen: dict = {}
        self._last_bucket: Optional[int] = None
        self._prev_side: Optional[str] = None
        self.order: Optional[dict] = None
        self.position: Optional[dict] = None
        self.last_eval: dict = {}
        self.counters = {"ticks": 0, "triggers": 0, "orders": 0, "fills": 0, "cancels": 0, "closes": 0,
                         "skipped_tile_off": 0, "skipped_busy": 0, "skipped_stale": 0, "skipped_no_touch": 0,
                         "errors": 0}
        self.last_signal: Optional[dict] = None
        self._seq = 0

    def bind_runtime(self, *, enabled_fn, trades_fn, touch_fn, append_fn) -> None:
        self.enabled_fn, self.trades_fn, self.touch_fn, self.append_fn = enabled_fn, trades_fn, touch_fn, append_fn

    # ---- features -------------------------------------------------------
    def _ff(self, key, bucket, value):
        if value:
            self._last_seen[key] = (bucket, value)
            return value
        seen = self._last_seen.get(key)
        if seen and 0 <= bucket - seen[0] <= self.rule.ffill_sec:
            return seen[1]
        return None

    def _features(self, now, live, bfx_quotes, bfx_bbo_ts) -> dict:
        import cross_venue_tape as cvt
        anchor = int(math.floor(float(now))) - 1
        out = {"anchor_bucket_ts": anchor, "stale_reasons": []}
        reasons = out["stale_reasons"]
        written = _num((live or {}).get("written_ts")) if isinstance(live, Mapping) else None
        if written is None or now - written > self.rule.max_venue_age:
            reasons.append("COLLECTOR_STALE")
        mids = []
        for venue in self.rule.venues:
            cell = (((live or {}).get("venues") or {}).get(venue) or {}) if isinstance(live, Mapping) else {}
            last = _num(cell.get("last_bbo_ts"))
            if last is None or now - last > self.rule.max_venue_age:
                reasons.append(f"VENUE_STALE:{venue}")
            mid = None
            if isinstance(live, Mapping):
                try:
                    mid = _f(cvt.live_mid_at(live, venue, anchor))
                except Exception:
                    mid = None
            mid = self._ff(venue, anchor, mid)
            if mid:
                mids.append(mid)
        q = (bfx_quotes or {}).get(anchor)
        bfx = None
        if q and _f(q[0]) and _f(q[1]):
            bfx = (float(q[0]) + float(q[1])) / 2.0
        bfx = self._ff("bitfinex", anchor, bfx)
        bbo = _num(bfx_bbo_ts)
        if bbo is None or now - bbo > self.rule.max_bbo_age:
            reasons.append("BFX_BBO_STALE")
        leader = sum(mids) / len(mids) if len(mids) == len(self.rule.venues) else None
        prem = None if not (bfx and leader) else (bfx / leader - 1.0) * 1e4
        if self._last_bucket is None or anchor > self._last_bucket:
            self._prem[anchor] = prem
            self._last_bucket = anchor
            for k in [k for k in self._prem if k < anchor - self.rule.d5_lag_sec - 5]:
                del self._prem[k]
        then = self._prem.get(anchor - self.rule.d5_lag_sec)
        d5 = None if prem is None or then is None else prem - then
        out.update(prem_bp=None if prem is None else round(prem, 4),
                   prem_d5_bp=None if d5 is None else round(d5, 4),
                   bfx_mid=bfx, leader_mid=leader)
        return out

    # ---- lifecycle ------------------------------------------------------
    def _emit(self, row: dict) -> None:
        row = {"schema": SCHEMA, "research_lane": LANE_REF[0], "policy_id": self.policy_id,
               "policy_signature": self.policy_signature, **row}
        try:
            self.append_fn(row)
        except Exception:
            self.counters["errors"] += 1

    def _touch(self):
        bid, ask, ts = self.touch_fn()
        return _f(bid), _f(ask), _num(ts)

    def _fill_check(self, order: dict, trades: list, until: float) -> Optional[dict]:
        limit, side = order["limit_price"], order["side"]
        want = "Sell" if side == LONG else "Buy"
        for t in trades or ():
            try:
                ts, px = float(t.get("received_ts") or 0), float(t.get("p") or 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if ts <= order["order_ts"] or ts > until or str(t.get("S") or "") != want:
                continue
            if (side == LONG and px < limit - 1e-9) or (side == SHORT and px > limit + 1e-9):
                return {"fill_ts": ts, "through_print_px": px, "through_print_qty": _num(t.get("v"))}
        return None

    def _close(self, now, reason, feat, bid, ask) -> None:
        p = self.position
        side = p["side"]
        exit_px = bid if side == LONG else ask
        if reason == "STOP" and p.get("stop_px"):
            exit_px = min(exit_px, p["stop_px"]) if side == LONG else max(exit_px, p["stop_px"])
        sgn = 1.0 if side == LONG else -1.0
        mid_exit = (bid + ask) / 2.0
        row = {**p, "event": "DISLOC_CLOSED", "exit_ts": now, "exit_reason": reason,
               "exit_price": exit_px, "exit_bid": bid, "exit_ask": ask, "exit_mid": mid_exit,
               "prem_at_exit_bp": feat.get("prem_bp"), "prem_d5_at_exit_bp": feat.get("prem_d5_bp"),
               "hold_s": round(now - p["fill_ts"], 3),
               "pnl_bp_vs_touch": round(sgn * (exit_px / p["limit_price"] - 1.0) * 1e4, 4),
               "pnl_bp_vs_mid": round(sgn * (mid_exit / p["signal_mid"] - 1.0) * 1e4, 4),
               "pnl_bp_fill_mid_to_exit_mid": round(sgn * (mid_exit / p["fill_mid"] - 1.0) * 1e4, 4)
               if p.get("fill_mid") else None,
               "cost_model": "maker entry at limit, taker exit at side-correct touch, Bitfinex fee 0"}
        gross = row["pnl_bp_fill_mid_to_exit_mid"]
        if gross is None:
            gross = row["pnl_bp_vs_mid"]
        row["net_bp_taker_2p8"] = round(gross - TAKER_COST_BP, 4)
        row.pop("stop_px", None)
        self.position = None
        self.counters["closes"] += 1
        self._emit(row)

    def step(self, *, now, live, bfx_quotes, bfx_bbo_ts):
        """XVL evaluator protocol: returns (evaluation, None, []); DISLOC never hands a trigger to the pipeline."""
        self.counters["ticks"] += 1
        feat = self._features(now, live, bfx_quotes, bfx_bbo_ts)
        side = None if feat["stale_reasons"] else self.rule.side(feat.get("prem_bp"), feat.get("prem_d5_bp"))
        rising = side is not None and side != self._prev_side
        self._prev_side = side
        feat["side"] = side
        self.last_eval = {k: feat.get(k) for k in ("anchor_bucket_ts", "prem_bp", "prem_d5_bp", "side",
                                                   "stale_reasons")}
        enabled = bool(self.enabled_fn())
        bid, ask, bbo_ts = self._touch()
        trades = list(self.trades_fn() or ()) if (self.order or self.position or rising) else []
        # 1. resting order: fill (trade-through up to now, within TTL) else cancel
        if self.order:
            o = self.order
            fill = self._fill_check(o, trades, min(now, o["order_ts"] + self.rule.ttl_sec))
            if fill:
                self.order = None
                self.counters["fills"] += 1
                stop = o["limit_price"] * (1 - self.rule.stop_bp / 1e4) if o["side"] == LONG \
                    else o["limit_price"] * (1 + self.rule.stop_bp / 1e4)
                self.position = {**o, **fill, "event": "DISLOC_FILLED",
                                 "order_to_fill_s": round(fill["fill_ts"] - o["order_ts"], 3),
                                 "signal_to_fill_s": round(fill["fill_ts"] - o["signal_ts"], 3),
                                 "fill_bid": bid, "fill_ask": ask,
                                 "fill_mid": (bid + ask) / 2.0 if bid and ask else None,
                                 "prem_at_fill_bp": feat.get("prem_bp"), "stop_px": stop}
                self._emit({k: v for k, v in self.position.items() if k != "stop_px"})
            elif now - o["order_ts"] >= self.rule.ttl_sec:
                self._cancel(now, "TTL_5S", feat)
            elif self.rule.gap_closed(o["side"], feat.get("prem_bp")):
                self._cancel(now, "GAP_CLOSED_BEFORE_FILL", feat)
        # 2. open position: stop / gap closed / 60 s time exit (first trigger wins)
        if self.position and bid and ask:
            p = self.position
            stopped = (bid <= p["stop_px"]) if p["side"] == LONG else (ask >= p["stop_px"])
            if stopped:
                self._close(now, "STOP", feat, bid, ask)
            elif self.rule.gap_closed(p["side"], feat.get("prem_bp")):
                self._close(now, "GAP_CLOSED", feat, bid, ask)
            elif now - p["fill_ts"] >= self.rule.hold_sec:
                self._close(now, "TIME_60S", feat, bid, ask)
        # 3. new signal
        if rising:
            self.counters["triggers"] += 1
            self.last_signal = {"ts": now, "side": side, "prem_bp": feat.get("prem_bp"),
                                "prem_d5_bp": feat.get("prem_d5_bp"), "enabled": enabled}
            if not enabled:
                self.counters["skipped_tile_off"] += 1
            elif self.order or self.position:
                self.counters["skipped_busy"] += 1
            elif not (bid and ask) or bbo_ts is None or now - bbo_ts > self.rule.max_bbo_age:
                self.counters["skipped_no_touch"] += 1
            else:
                self._open(now, side, feat, bid, ask, bbo_ts)
        elif feat["stale_reasons"]:
            self.counters["skipped_stale"] += 1
        return dict(self.last_eval, status=("TRIGGER" if rising else "BELOW")), None, []

    def _open(self, now, side, feat, bid, ask, bbo_ts) -> None:
        self._seq += 1
        signal_ts = float(feat["anchor_bucket_ts"]) + 1.0   # the anchor second's data closes here
        order_ts = self.clock()
        tid = f"{self.ID_PREFIX}-{int(now)}-{self._seq}"
        self.order = {
            "trade_id": tid, "side": side, "side_tag": SIDE_TAGS[side], "event": "DISLOC_ORDER",
            "signal_ts": signal_ts, "evaluated_ts": now, "order_ts": order_ts,
            "signal_to_order_s": round(order_ts - signal_ts, 3),
            "signal_bid": bid, "signal_ask": ask, "signal_mid": (bid + ask) / 2.0,
            "signal_spread_bp": round((ask - bid) / ((ask + bid) / 2.0) * 1e4, 4),
            "signal_bbo_age_s": round(now - bbo_ts, 3),
            "limit_price": bid if side == LONG else ask, "order_type": "POST_ONLY_LIMIT_AT_TOUCH",
            "prem_at_signal_bp": feat.get("prem_bp"), "prem_d5_at_signal_bp": feat.get("prem_d5_bp"),
            "leader_mid": feat.get("leader_mid"), "bfx_mid_1s": feat.get("bfx_mid"),
        }
        self.counters["orders"] += 1
        self._emit(dict(self.order))

    def _cancel(self, now, reason, feat) -> None:
        o = self.order
        self.order = None
        self.counters["cancels"] += 1
        self._emit({**o, "event": "DISLOC_CANCELLED", "cancel_ts": now, "cancel_reason": reason,
                    "order_age_s": round(now - o["order_ts"], 3), "prem_at_cancel_bp": feat.get("prem_bp")})

    def abandon(self, reason: str = "SHUTDOWN") -> None:
        for item in (self.order, self.position):
            if item:
                self._emit({**{k: v for k, v in item.items() if k != "stop_px"},
                            "event": "DISLOC_ABANDONED", "abandon_reason": reason})
        self.order = self.position = None

    def snapshot(self) -> dict:
        return {"schema": SCHEMA, "rule": self.rule.identity(), "counters": dict(self.counters),
                "last_eval": dict(self.last_eval), "last_signal": self.last_signal,
                "open_order": dict(self.order) if self.order else None,
                "open_position": {k: v for k, v in self.position.items()} if self.position else None}


LANE_REF = ["FAMILY_DISLOC_BFX_DISLOCATION_1M"]
