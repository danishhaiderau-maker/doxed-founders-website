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

import paper_fill

SCHEMA = "disloc_paper_v1"
TRADES_FILE = "disloc_trades.jsonl"
LONG, SHORT = "LONG", "SHORT"
# Strategist DEEP-SEARCH-TILES-20261011: reported net charges the measured 2.8 bp taker round trip.
TAKER_COST_BP = 2.8
EXIT_TOTAL_MAX_SEC = 30.0      # HM 14:00: whole exit (all partial pieces) bounded from the FIRST trigger
EXIT_NO_BOOK_FORCE_SEC = 10.0  # HM 13:27: unpriceable exit retries bounded, then FORCED_NO_BOOK
IOC_CAP_BP = 5.0   # live DISLOC IOC limit = signal ask + 5 bp (LONG) / signal bid - 5 bp (SHORT)
DELAY_BUCKETS = (("le_2s", 0.0, 2.0), ("2_to_5s", 2.0, 5.0))
SIDE_TAGS = {LONG: "LONG_CONFIRMED_BY_DEEP_SEARCH", SHORT: "SHORT_SHADOW_ONLY_NOT_TRADED"}


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
        self.long_d5_max = _num(d.get("long_prem_d5_max_bp"))
        self.short_prem_min = float(d.get("short_prem_min_bp", 1e9))
        self.short_d5_min = float(d.get("short_prem_d5_min_bp", 1e9))
        self.d5_lag_sec = int(d["prem_d5_lag_sec"])
        self.ttl_sec = float(d["order_ttl_sec"])
        self.hold_sec = float(exit_policy["max_duration_sec"])
        self.stop_bp = float(exit_policy["hard_stop_bps"])
        self.max_venue_age = float(entry.get("max_venue_age_sec", 2.0))
        self.max_bbo_age = float(entry.get("max_bbo_age_sec", 2.0))
        self.ffill_sec = int(entry.get("max_fill_forward_sec", 5))
        self.long_only = bool(d.get("long_only", False))
        # Tile 14 DISLOC-FOI (Strategist C3): derivatives conditions, (lo, hi] bounds; None = open.
        foi = d.get("foi") or {}
        self.fund_lo = _num(foi.get("fund_lo_bp"))
        self.fund_hi = _num(foi.get("fund_hi_bp"))
        self.oi_d60_max = _num(foi.get("oi_d60_max_bp"))
        self.oi_lag_min = int(foi.get("oi_lag_min", 60)) if foi else 0
        self.fund_venues = tuple(foi.get("fund_venues") or ())
        self.oi_venues = tuple(foi.get("oi_venues") or ())
        self.one_signal_per_minute = bool(d.get("one_signal_per_minute", False))
        self.uses_derivatives = bool(foi)
        # Strategist final spec 21:06: market (taker) entry at the real ask after the measured delay,
        # no gap-closed exit, short side shadow-logged only.
        self.entry_mode = str(d.get("entry_mode") or "MAKER_AT_TOUCH")
        self.gap_closed_exit = bool(d.get("gap_closed_exit", True))
        self.short_shadow_only = bool(d.get("short_shadow_only", False))

    def side(self, prem, d5, fund=None, oi_d60=None) -> Optional[str]:
        if prem is None:
            return None
        if self.uses_derivatives:
            if fund is None or oi_d60 is None:
                return None
            if self.fund_lo is not None and not fund > self.fund_lo:
                return None
            if self.fund_hi is not None and not fund <= self.fund_hi:
                return None
            if self.oi_d60_max is not None and not oi_d60 <= self.oi_d60_max:
                return None
        if self.long_d5_max is not None and d5 is None:
            return None
        if prem <= self.long_prem_max and (self.long_d5_max is None or d5 <= self.long_d5_max):
            return LONG
        if self.long_only or d5 is None:
            return None
        if prem > self.short_prem_min and d5 > self.short_d5_min:
            return SHORT
        return None

    def shadow_short(self, prem, d5) -> bool:
        return (self.short_shadow_only and prem is not None and d5 is not None
                and prem > self.short_prem_min and d5 > self.short_d5_min)

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


FOI_TRADES_FILE = "disloc_foi_trades.jsonl"


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
    score_cap = _num(pre.get("score_max_signal_to_fill_s"))
    all_closed = closed
    if score_cap is not None:
        closed = [r for r in closed if float(r.get("signal_to_fill_s") or 0) <= score_cap]
        n = len(closed)
        m = mean("net_bp_taker_2p8", closed)
        ci = cluster_ci(closed)
        eq, peak, dd = 0.0, 0.0, 0.0
        for r in sorted(closed, key=lambda x: float(x.get("exit_ts") or 0)):
            eq += float(r.get("net_bp_taker_2p8") or 0)
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
    first = sorted(all_closed, key=lambda x: float(x.get("fill_ts") or 0))[:60]
    first_p50 = _pct([float(r.get("signal_to_fill_s") or 0) for r in first], .5)
    kill_n, kill_mean = int(pre.get("kill_at_trades", 150)), float(pre.get("kill_mean_below_bp", 0.5))
    prom_n, prom_mean = int(pre.get("promote_min_trades", 250)), float(pre.get("promote_mean_min_bp", 2.0))
    early_n, early_mean = pre.get("early_kill_at_trades"), pre.get("early_kill_mean_at_or_below_bp")
    uncap = _num(pre.get("uncapturable_p50_delay_above_s"))
    if uncap is not None and len(first) >= 60 and first_p50 is not None and first_p50 > uncap:
        verdict = "RETIRE_UNCAPTURABLE"
    elif (early_n is not None and n >= int(early_n) and m is not None and m <= float(early_mean)
          and n < kill_n):
        verdict = "KILL_EARLY"
    elif n >= prom_n and m is not None and m >= prom_mean and ci and ci[0] > 0:
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
        "closed_trades": len(all_closed), "scored_trades": n,
        "scoring": (f"only fills with signal->fill <= {score_cap:g} s are scored" if score_cap is not None
                    else "all closed trades scored"),
        "first60_signal_to_fill_p50_s": first_p50, "cancelled": len(cancelled), "cancel_reasons": reasons,
        "abandoned": sum(1 for r in orders if r.get("event") == "DISLOC_ABANDONED"),
        "fill_rate": round(len(all_closed) / len(orders), 4) if orders else None,
        "cancel_rate": round(len(cancelled) / len(orders), 4) if orders else None,
        "exit_reasons": exits,
        "delay_sec": {"signal_to_order": delays("signal_to_order_s"),
                      "order_to_fill": delays("order_to_fill_s"),
                      "signal_to_fill": delays("signal_to_fill_s")},
        "net_basis": f"fill-mid -> exit-mid minus {TAKER_COST_BP} bp taker round trip",
        "mean_net_bp_taker_2p8": m, "mean_bp_vs_touch": mean("pnl_bp_vs_touch", closed),
        "mean_bp_vs_mid": mean("pnl_bp_vs_mid", closed),
        "mean_gap_at_entry_bp": mean("prem_at_fill_bp", closed), "mean_gap_at_exit_bp": mean("prem_at_exit_bp", closed),
        "ci90_1h_cluster_net": ci, "max_drawdown_bp": round(dd, 3), "cum_bp_vs_touch": round(eq, 3),
        "by_side": by_side, "by_fill_delay": by_delay,
        "pre_registered": {"kill": (f"at {kill_n} trades if mean net (2.8 bp taker cost) < +{kill_mean:g} bp"
                                    + (f"; at {early_n} if mean <= {early_mean:g} bp" if early_n else "")
                                    + (f"; retire UNCAPTURABLE if first-60 p50 signal->fill > {uncap:g} s"
                                       if uncap is not None else "")),
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
                 clock: Callable[[], float] = time.time, lane: str = "", variant: str = "base",
                 id_prefix: str = "dsl") -> None:
        self.rule = rule
        self.lane = lane or LANE_REF[0]
        self.variant = variant
        self.ID_PREFIX = id_prefix
        self.policy_id, self.policy_signature = policy_id, policy_signature
        self.clock = clock
        self.enabled_fn: Callable[[], bool] = lambda: False
        # LIVE-FIX-1512: gates NEW entries only (deploy maintenance / operator pause).
        # Exits, shadows and evaluation never read it, so a pause cannot freeze open positions.
        self.entries_allowed_fn: Callable[[], Optional[str]] = lambda: None
        # Step B (HM 13:16): fills come from the 10-level book at signal_ts + measured delay.
        self.delay_fn: Callable[[str, str], tuple] = lambda lane, leg: (None, "default_until_n10")
        self.paused_skips: deque = deque(maxlen=200)
        # G14 (HM 12:20): persist every PAUSED skip so the count survives a deploy restart.
        self.paused_append_fn: Callable[[dict], Any] = lambda row: None
        self.trades_fn: Callable[[], list] = lambda: []
        self.touch_fn: Callable[[], tuple] = lambda: (None, None, None)
        # LIVE-FIX-1510: {"source": "BOOK"|"TICKER", "ask_age_ms": int} for the touch just read.
        self.touch_meta_fn: Callable[[], dict] = lambda: {}
        self.append_fn: Callable[[dict], Any] = lambda row: None
        self.oi_seed_fn: Callable[[int], dict] = lambda since_minute: {}
        self.regime_fn: Optional[Callable[[], Any]] = None
        self._prem: dict = {}
        self._last_seen: dict = {}
        self._last_bucket: Optional[int] = None
        self._prev_side: Optional[str] = None
        self.order: Optional[dict] = None
        self.position: Optional[dict] = None
        self.last_eval: dict = {}
        self.counters = {"ticks": 0, "triggers": 0, "orders": 0, "fills": 0, "cancels": 0, "closes": 0,
                         "skipped_tile_off": 0, "skipped_busy": 0, "skipped_stale": 0, "skipped_no_touch": 0, "skipped_paused": 0,
                         "errors": 0}
        self.last_signal: Optional[dict] = None
        self._seq = 0
        # HM-BATCH2 (Strategist): 30-min-hold shadow on every DISLOC signal, entered at the signal touch.
        self._shadows: list = []
        self.counters["shadow_30m_opened"] = 0
        self.counters["shadow_30m_closed"] = 0

    def bind_runtime(self, *, enabled_fn, trades_fn, touch_fn, append_fn, oi_seed_fn=None,
                     regime_fn=None, touch_meta_fn=None, entries_allowed_fn=None,
                     delay_fn=None, paused_append_fn=None) -> None:
        self.enabled_fn, self.trades_fn, self.touch_fn, self.append_fn = enabled_fn, trades_fn, touch_fn, append_fn
        if delay_fn is not None:
            self.delay_fn = delay_fn
        if touch_meta_fn is not None:
            self.touch_meta_fn = touch_meta_fn
        if entries_allowed_fn is not None:
            self.entries_allowed_fn = entries_allowed_fn
        if paused_append_fn is not None:
            self.paused_append_fn = paused_append_fn
        if regime_fn is not None:
            self.regime_fn = regime_fn
        if oi_seed_fn is not None:
            self.oi_seed_fn = oi_seed_fn

    def make_tape_oi_seed(self, path: str, tail_bytes: int = 1 << 20):
        """Return {minute: sum OI over rule.oi_venues} from the tail of cross_venue_tape_1m.jsonl."""
        venues = self.rule.oi_venues

        def _seed(since_minute: int) -> dict:
            import json, os
            out: dict = {}
            if not venues or not os.path.exists(path):
                return out
            with open(path, "rb") as fh:
                fh.seek(max(0, os.path.getsize(path) - tail_bytes))
                lines = fh.read().splitlines()[1:]
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                m = int(float(row.get("minute_ts") or 0) // 60)
                der = row.get("derivatives") or {}
                ois = [_num((der.get(v) or {}).get("open_interest")) for v in venues]
                if m >= since_minute and all(o is not None and o > 0 for o in ois):
                    out[m] = sum(ois)
            return out
        return _seed

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
        if self.rule.uses_derivatives:
            out.update(self._derivs(now, live))
        out.update(prem_bp=None if prem is None else round(prem, 4),
                   prem_d5_bp=None if d5 is None else round(d5, 4),
                   bfx_mid=bfx, leader_mid=leader)
        return out

    def _derivs(self, now, live) -> dict:
        """fund = mean venue funding_rate * 1e4; oi_d60 = (sum OI / sum OI 60 min ago - 1) * 1e4 (build.py)."""
        der = ((live or {}).get("derivatives") or {}) if isinstance(live, Mapping) else {}
        funds = [_num((der.get(v) or {}).get("funding_rate")) for v in self.rule.fund_venues]
        funds = [f for f in funds if f is not None]
        fund = sum(funds) / len(funds) * 1e4 if funds else None
        ois = [_num((der.get(v) or {}).get("open_interest")) for v in self.rule.oi_venues]
        oi = sum(ois) if ois and all(o is not None and o > 0 for o in ois) else None
        minute = int(now // 60)
        hist = self.__dict__.setdefault("_oi_hist", {})
        if not self.__dict__.get("_oi_seeded"):
            # FOI-ONCE fix (12 Oct): the 60-min OI history lived only in memory, so every
            # restart/deploy blanked oi_d60 for >= 60 min and the tile almost never armed.
            # Warm-start it from the cross-venue minute tape already on disk.
            self._oi_seeded = True
            try:
                for m, v in self.oi_seed_fn(minute - self.rule.oi_lag_min - 5).items():
                    hist.setdefault(int(m), float(v))
            except Exception:
                self.counters["errors"] += 1
        if oi is not None:
            hist[minute] = oi
        for k in [k for k in hist if k < minute - self.rule.oi_lag_min - 5]:
            del hist[k]
        target = minute - self.rule.oi_lag_min
        # Exact-minute lookup missed whenever that one minute had no OI print; take the
        # nearest stored minute within +/-2 (build.py tolerance on the 1m tape).
        then = next((hist[k] for k in (target, target - 1, target + 1, target - 2, target + 2) if k in hist), None)
        oi_d60 = None if oi is None or then is None else (oi / then - 1.0) * 1e4
        return {"fund_bp": None if fund is None else round(fund, 5),
                "oi_d60_bp": None if oi_d60 is None else round(oi_d60, 4),
                "oi_history_minutes": len(hist)}

    # ---- lifecycle ------------------------------------------------------
    def _emit(self, row: dict) -> None:
        row = {"schema": SCHEMA, "research_lane": self.lane, "variant": self.variant, "policy_id": self.policy_id,
               "policy_signature": self.policy_signature, **row}
        try:
            self.append_fn(row)
        except Exception:
            self.counters["errors"] += 1

    def _touch(self):
        bid, ask, ts = self.touch_fn()
        return _f(bid), _f(ask), _num(ts)

    def _touch_meta(self) -> dict:
        try:
            m = self.touch_meta_fn() or {}
            return {"source": m.get("source"), "ask_age_ms": m.get("ask_age_ms")}
        except Exception:  # noqa: BLE001 - observation only
            return {"source": None, "ask_age_ms": None}

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

    def _arm_exit(self, now, reason, feat, bid, ask) -> None:
        """Exit trigger at ``now`` (signal-time data only); priced later at now + exit delay."""
        d, src = self.delay_fn(self.lane, "exit")
        d = max(paper_fill.MIN_DELAY_S, d or paper_fill.DEFAULT_DELAY_S)
        self.position["exit_pending"] = {"reason": reason, "trigger_ts": now, "first_trigger_ts": now, "due_ts": now + d,
                                         "delay_s": d, "delay_source": src or "default_until_n10",
                                         "trigger_bid": bid, "trigger_ask": ask,
                                         "prem_at_exit_bp": feat.get("prem_bp"),
                                         "prem_d5_at_exit_bp": feat.get("prem_d5_bp")}

    def _close(self, now, reason, feat, bid, ask) -> None:
        """Book-priced exit at trigger_ts + exit delay (no cap). A PARTIAL exit closes only the filled qty
        and re-arms the rest; an exit unpriceable for EXIT_NO_BOOK_FORCE_SEC is force-closed unpriced."""
        p = self.position
        ex = p["exit_pending"]
        res = self._price_exit(p, ex)
        if res.get("price") is None or not res.get("filled_qty"):
            ex["attempts"] = ex.get("attempts", 0) + 1
            ex["last_status"] = res.get("status")
            if now - ex["due_ts"] >= EXIT_NO_BOOK_FORCE_SEC or \
                    now - ex.get("first_trigger_ts", ex["trigger_ts"]) >= EXIT_TOTAL_MAX_SEC:
                self._finish_close(now, ex, forced=True)
            return
        piece = {"price": res["price"], "qty": float(res["filled_qty"]), "fill_ts": res["fill_ts"],
                 "book_s": res.get("book_s"), "status": res.get("status"),
                 "signal_book": res.get("depth_snapshot_signal"), "fill_book": res.get("depth_snapshot_fill"),
                 "delay_s": res.get("delay_s"), "delay_source": res.get("delay_source"),
                 "trigger_ts": ex["trigger_ts"]}
        p.setdefault("exit_pieces", []).append(piece)
        p["open_qty"] = round(float(p["open_qty"]) - piece["qty"], 10)
        if p["open_qty"] > 1e-10 and now - ex.get("first_trigger_ts", ex["trigger_ts"]) >= EXIT_TOTAL_MAX_SEC:
            self._finish_close(now, ex, forced=True)
            return
        if p["open_qty"] > 1e-10:
            self.counters["partial_exits"] = self.counters.get("partial_exits", 0) + 1
            self._emit({"event": "DISLOC_PARTIAL_EXIT", "trade_id": p["trade_id"], "side": p["side"],
                        "exit_reason": ex["reason"], **{f"exit_{k}": v for k, v in piece.items()},
                        "remaining_qty": p["open_qty"]})
            # the remainder is a new exit order: re-triggered now, priced at now + exit delay
            d = ex["delay_s"]
            p["exit_pending"] = {**ex, "trigger_ts": now, "due_ts": now + d, "attempts": 0}
            return
        self._finish_close(now, ex, forced=False)

    def _finish_close(self, now, ex, *, forced: bool) -> None:
        p = self.position
        side = p["side"]
        sgn = 1.0 if side == LONG else -1.0
        pieces = p.get("exit_pieces") or []
        closed_qty = sum(x["qty"] for x in pieces)
        exit_px = (sum(x["price"] * x["qty"] for x in pieces) / closed_qty) if closed_qty > 0 else None
        last = pieces[-1] if pieces else {}
        tb, ta = ex.get("trigger_bid"), ex.get("trigger_ask")
        mid_exit = (tb + ta) / 2.0 if tb and ta else None
        entry = p["fill_price"]
        held = float(p["qty"])
        row = {**{k: v for k, v in p.items() if k not in ("exit_pending", "exit_pieces", "open_qty")},
               "event": "DISLOC_CLOSED", "exit_ts": last.get("fill_ts", now),
               "exit_trigger_ts": ex["trigger_ts"], "exit_reason": ex["reason"],
               "exit_status": "FORCED_NO_BOOK" if forced else ("FILLED" if len(pieces) == 1 else "FILLED_IN_PARTS"),
               "exit_price": exit_px, "exit_filled_qty": round(closed_qty, 10), "exit_pieces": len(pieces),
               "exit_unpriced_qty": round(held - closed_qty, 10) if forced else 0.0,
               "exit_bid": tb, "exit_ask": ta, "exit_mid": mid_exit,
               "exit_signal_book": last.get("signal_book"), "exit_fill_book": last.get("fill_book"),
               "exit_book_s": last.get("book_s"), "exit_delay_s": last.get("delay_s", ex.get("delay_s")),
               "exit_delay_source": last.get("delay_source", ex.get("delay_source")),
               "prem_at_exit_bp": ex.get("prem_at_exit_bp"), "prem_d5_at_exit_bp": ex.get("prem_d5_at_exit_bp"),
               "hold_s": round(last.get("fill_ts", now) - p["fill_ts"], 3),
               "pnl_bp_vs_touch": round(sgn * (exit_px / entry - 1.0) * 1e4, 4) if exit_px and not forced else None,
               "pnl_bp_vs_mid": round(sgn * (mid_exit / p["signal_mid"] - 1.0) * 1e4, 4) if mid_exit else None,
               "pnl_bp_fill_mid_to_exit_mid": round(sgn * (mid_exit / p["fill_mid"] - 1.0) * 1e4, 4)
               if mid_exit and p.get("fill_mid") else None,
               "cost_model": "10-level book walk in and out at signal/trigger + measured delay, Bitfinex fee 0"}
        if forced:
            # no real exit price: never fresh data, RED in tile-integrity
            row["fresh_data_excluded"] = "FORCED_NO_BOOK"
            self.counters["forced_no_book"] = self.counters.get("forced_no_book", 0) + 1
        row["net_bp_taker_2p8"] = round(row["pnl_bp_vs_touch"] - TAKER_COST_BP, 4) \
            if row["pnl_bp_vs_touch"] is not None else None
        row.pop("stop_px", None)
        self.position = None
        self.counters["closes"] += 1
        self._emit(row)

    # ---- fill sites (CI: last statement is `return paper_fill.paper_fill(...)`, book = book10l_at)
    @staticmethod
    def _price_entry(o):
        return paper_fill.paper_fill(o["side"], o["qty"], o["signal_ts"], o["delay_s"], paper_fill.book10l_at,
                                     leg="entry", delay_source=o["delay_source"], cap_px=o["ioc_cap_px"])

    @staticmethod
    def _price_exit(p, ex):
        return paper_fill.paper_fill(p["side"], p["open_qty"], ex["trigger_ts"], ex["delay_s"], paper_fill.book10l_at,
                                     leg="exit", delay_source=ex["delay_source"])

    def step(self, *, now, live, bfx_quotes, bfx_bbo_ts):
        """XVL evaluator protocol: returns (evaluation, None, []); DISLOC never hands a trigger to the pipeline."""
        self.counters["ticks"] += 1
        feat = self._features(now, live, bfx_quotes, bfx_bbo_ts)
        side = None if feat["stale_reasons"] else self.rule.side(
            feat.get("prem_bp"), feat.get("prem_d5_bp"), feat.get("fund_bp"), feat.get("oi_d60_bp"))
        rising = side is not None and side != self._prev_side
        self._prev_side = side
        if rising and self.rule.one_signal_per_minute:
            minute = int(now // 60)
            if getattr(self, "_last_signal_minute", None) == minute:
                rising = False
            else:
                self._last_signal_minute = minute
        feat["side"] = side
        self.last_eval = {k: feat.get(k) for k in ("anchor_bucket_ts", "prem_bp", "prem_d5_bp", "fund_bp",
                                                   "oi_d60_bp", "oi_history_minutes", "side", "stale_reasons")}
        enabled = bool(self.enabled_fn())
        bid, ask, bbo_ts = self._touch()
        self._shadow_step(now, side if rising else None, bid, ask, enabled)
        trades = list(self.trades_fn() or ()) if (self.order or self.position or rising) else []
        # 0. Step B: a taker entry is priced once its fill time (signal_ts + delay) has passed
        if self.order and "fill_due_ts" in self.order and now >= self.order["fill_due_ts"]:
            self._taker_fill(feat)
        # 1. resting order: fill (trade-through up to now, within TTL) else cancel
        if self.order and "limit_price" in self.order:
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
        # 2. open position: stop / gap closed / 60 s time exit (first trigger wins). The trigger uses
        # only data at ``now``; the exit is priced from the book at trigger + exit delay (same delay rule).
        if self.position and self.position.get("exit_pending"):
            if now >= self.position["exit_pending"]["due_ts"]:
                self._close(now, None, feat, bid, ask)
        elif self.position and bid and ask:
            p = self.position
            stopped = (bid <= p["stop_px"]) if p["side"] == LONG else (ask >= p["stop_px"])
            if stopped:
                self._arm_exit(now, "STOP", feat, bid, ask)
            elif self.rule.gap_closed_exit and self.rule.gap_closed(p["side"], feat.get("prem_bp")):
                self._arm_exit(now, "GAP_CLOSED", feat, bid, ask)
            elif now - p["fill_ts"] >= self.rule.hold_sec:
                self._arm_exit(now, "TIME_60S", feat, bid, ask)
        # 3. new signal
        if rising:
            self.counters["triggers"] += 1
            self.last_signal = {"ts": now, "side": side, "prem_bp": feat.get("prem_bp"),
                                "prem_d5_bp": feat.get("prem_d5_bp"), "enabled": enabled}
            if not enabled:
                self.counters["skipped_tile_off"] += 1
            elif self.order or self.position:
                self.counters["skipped_busy"] += 1
            elif (blocked := self._entry_block_reason()):
                self.counters["skipped_paused"] += 1
                self.paused_skips.append({"correlation_id": f"{self.ID_PREFIX}-paused-{int(now)}",
                                          "signal_ts": now, "side": side, "reason": "PAUSED",
                                          "pause_owner": blocked, "signal_bid": bid, "signal_ask": ask,
                                          "prem_at_signal_bp": feat.get("prem_bp")})
                try:
                    self.paused_append_fn({**self.paused_skips[-1], "lane": self.lane, "variant": self.variant})
                except Exception:  # noqa: BLE001 - observation only
                    pass
            elif not (bid and ask) or bbo_ts is None or now - bbo_ts > self.rule.max_bbo_age:
                self.counters["skipped_no_touch"] += 1
            else:
                self._open(now, side, feat, bid, ask, bbo_ts)
        elif feat["stale_reasons"]:
            self.counters["skipped_stale"] += 1
        # 4. short side: shadow log only (never traded)
        sh = not feat["stale_reasons"] and self.rule.shadow_short(feat.get("prem_bp"), feat.get("prem_d5_bp"))
        if sh and not getattr(self, "_prev_shadow_short", False):
            minute = int(now // 60)
            if getattr(self, "_shadow_minute", None) != minute and enabled:
                self._shadow_minute = minute
                self.counters["shadow_short_signals"] = self.counters.get("shadow_short_signals", 0) + 1
                self._emit({"event": "DISLOC_SHADOW_SHORT_SIGNAL", "signal_ts": float(feat["anchor_bucket_ts"]) + 1.0,
                            "evaluated_ts": now, "side": SHORT, "side_tag": "SHORT_SHADOW_ONLY_NOT_TRADED",
                            "signal_bid": bid, "signal_ask": ask, "prem_at_signal_bp": feat.get("prem_bp"),
                            "prem_d5_at_signal_bp": feat.get("prem_d5_bp")})
        self._prev_shadow_short = bool(sh)
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
            "signal_touch_source": (_m := self._touch_meta())["source"],
            "signal_ask_age_ms": _m["ask_age_ms"],
            "limit_price": bid if side == LONG else ask, "order_type": "POST_ONLY_LIMIT_AT_TOUCH",
            "prem_at_signal_bp": feat.get("prem_bp"), "prem_d5_at_signal_bp": feat.get("prem_d5_bp"),
            "fund_at_signal_bp": feat.get("fund_bp"), "oi_d60_at_signal_bp": feat.get("oi_d60_bp"),
            "leader_mid": feat.get("leader_mid"), "bfx_mid_1s": feat.get("bfx_mid"),
        }
        if self.regime_fn is not None:  # DATA-GAPS-20261012 (f)
            try:
                self.order["regime_at_signal"] = self.regime_fn()
            except Exception:  # noqa: BLE001 - observation only
                self.order["regime_at_signal"] = None
        self.counters["orders"] += 1
        if self.rule.entry_mode == "TAKER_MARKET":
            d, src = self.delay_fn(self.lane, "entry")
            d = max(paper_fill.MIN_DELAY_S, d or paper_fill.DEFAULT_DELAY_S)
            mid = (bid + ask) / 2.0
            self.order.update({
                "order_type": "IOC_TAKER_BOOK10L_AT_SIGNAL_PLUS_DELAY",
                "qty": round(DEFAULT_MARGIN_USD * DEFAULT_LEVERAGE / mid, 8),
                "ioc_cap_px": ask * (1 + IOC_CAP_BP / 1e4) if side == LONG else bid * (1 - IOC_CAP_BP / 1e4),
                "ioc_cap_bp": IOC_CAP_BP, "delay_s": d, "delay_source": src or "default_until_n10",
                "fill_due_ts": signal_ts + d})
            self.order.pop("limit_price", None)
            self._emit(dict(self.order))
            return
        self._emit(dict(self.order))

    def _taker_fill(self, feat) -> None:
        """Step B: IOC taker entry priced by paper_fill from the 10-level book at signal_ts + delay.

        Only levels inside the live IOC cap (signal ask + 5 bp / signal bid - 5 bp) can fill; if the
        delayed book is entirely past the cap the order is NO_FILL and the uncapped would-be fill is
        written to the shadow ledger.
        """
        o = self.order
        cap, side = o["ioc_cap_px"], o["side"]
        sig_ts = o["signal_ts"]
        res = self._price_entry(o)
        stamp = {"signal_book": res.get("depth_snapshot_signal"), "fill_book": res.get("depth_snapshot_fill"),
                 "depth_snapshot_signal": res.get("depth_snapshot_signal"),
                 "depth_snapshot_fill": res.get("depth_snapshot_fill"),
                 "delay_s": res.get("delay_s"), "delay_source": res.get("delay_source"),
                 "fill_model": res.get("fill_model"), "fill_model_version": res.get("fill_model_version"),
                 "fill_status": res.get("status"), "book_s": res.get("book_s"),
                 "book_age_ms": res.get("book_age_ms"), "ask_age_ms": res.get("ask_age_ms")}
        if res.get("price") is None:
            if res.get("status") == "NO_FILL_IOC_CAP":
                fb = res.get("depth_snapshot_fill") or {}
                lv = paper_fill.taker_side_levels(side, "entry", fb)
                upx, ufilled = paper_fill._walk(lv, o["qty"])
                self.order = {**o, **stamp}
                self._cancel(res["fill_ts"], "NO_FILL_IOC_CAP", feat)
                self.counters["no_fill_ioc_cap"] = self.counters.get("no_fill_ioc_cap", 0) + 1
                self._emit({"event": "DISLOC_SHADOW_NO_FILL", "shadow_only": True, "trade_id": o["trade_id"],
                            "side": side, "signal_ts": sig_ts, "signal_ask": o["signal_ask"],
                            "signal_bid": o["signal_bid"], "ioc_cap_px": cap, "ioc_cap_bp": IOC_CAP_BP,
                            "uncapped_fill_price": upx, "uncapped_filled_qty": round(ufilled, 8), **stamp})
            else:
                self.order = {**o, **stamp}
                self._cancel(res["fill_ts"], "NO_BOOK_AT_FILL", feat)
            return
        px = res["price"]
        fill_ts = res["fill_ts"]
        fb = res.get("depth_snapshot_fill") or {}
        fbid = float(fb["b"][0][0]) if fb.get("b") else None
        fask = float(fb["a"][0][0]) if fb.get("a") else None
        self.order = None
        self.counters["fills"] += 1
        stop = px * (1 - self.rule.stop_bp / 1e4) if side == LONG else px * (1 + self.rule.stop_bp / 1e4)
        self.position = {**o, **stamp, "event": "DISLOC_FILLED", "limit_price": px, "fill_price": px,
                         "requested_qty": o["qty"], "qty": float(res.get("filled_qty")),
                         "open_qty": float(res.get("filled_qty")),
                         "filled_qty": res.get("filled_qty"), "fill_ts": fill_ts,
                         "order_to_fill_s": round(fill_ts - o["order_ts"], 3),
                         "signal_to_fill_s": round(fill_ts - sig_ts, 3),
                         "fill_bid": fbid, "fill_ask": fask,
                         "fill_mid": (fbid + fask) / 2.0 if fbid and fask else None,
                         "slippage_vs_signal_touch_bp": round(
                             ((px / (o["signal_ask"] if side == LONG else o["signal_bid"])) - 1.0) * 1e4
                             * (1 if side == LONG else -1), 4),
                         "prem_at_fill_bp": feat.get("prem_bp"), "stop_px": stop}
        self._emit({k: v for k, v in self.position.items() if k != "stop_px"})

    def _shadow_step(self, now, rising_side, bid, ask, enabled) -> None:
        """Open a 30 min shadow on each rising signal; close due shadows at the side-correct touch."""
        try:
            if bid and ask and self._shadows:
                keep = []
                for sh in self._shadows:
                    if now - sh["signal_ts"] < SHADOW_HOLD_SEC:
                        keep.append(sh)
                        continue
                    exit_px = bid if sh["side"] == LONG else ask
                    sgn = 1.0 if sh["side"] == LONG else -1.0
                    self._emit({"event": "DISLOC_SHADOW_30M", "variant": self.variant, "lane": self.lane,
                                "side": sh["side"], "signal_ts": sh["signal_ts"], "entry_price": sh["entry_price"],
                                "exit_ts": now, "exit_price": exit_px, "exit_bid": bid, "exit_ask": ask,
                                "hold_s": round(now - sh["signal_ts"], 3),
                                "pnl_bp_vs_touch": round(sgn * (exit_px / sh["entry_price"] - 1.0) * 1e4, 4),
                                "shadow_only": True, "cost_model": "taker in/out at side-correct touch, fee 0"})
                    self.counters["shadow_30m_closed"] += 1
                self._shadows = keep
            if rising_side and enabled and bid and ask and len(self._shadows) < 200:
                self._shadows.append({"side": rising_side, "signal_ts": now,
                                      "entry_price": ask if rising_side == LONG else bid})
                self.counters["shadow_30m_opened"] += 1
        except Exception:
            self.counters["errors"] += 1

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

    def _entry_block_reason(self) -> Optional[str]:
        try:
            return self.entries_allowed_fn() or None
        except Exception:  # noqa: BLE001 - never block entries on a gate error
            return None

    def paused_skip_summary(self) -> dict:
        return {"lane": self.lane, "variant": self.variant, "skipped_paused": self.counters["skipped_paused"],
                "recent": list(self.paused_skips)[-50:]}

    def snapshot(self) -> dict:
        return {"schema": SCHEMA, "variant": self.variant, "rule": self.rule.identity(), "counters": dict(self.counters),
                "last_eval": dict(self.last_eval), "last_signal": self.last_signal,
                "open_order": dict(self.order) if self.order else None,
                "open_position": {k: v for k, v in self.position.items()} if self.position else None}


LANE_REF = ["FAMILY_DISLOC_BFX_DISLOCATION_1M"]
PAUSED_SKIPS_FILE = "disloc_paused_skips.jsonl"


def paused_skip_history(path: str = PAUSED_SKIPS_FILE, *, boot_ts: float | None = None, lane: str | None = None) -> dict:
    """Durable PAUSED-skip ledger (survives restarts): totals, since boot, by pause owner, last 50."""
    import json as _json
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = _json.loads(line)
                except ValueError:
                    continue
                if lane is None or r.get("lane") == lane:
                    rows.append(r)
    except FileNotFoundError:
        pass
    by_owner: dict = {}
    for r in rows:
        by_owner[r.get("pause_owner")] = by_owner.get(r.get("pause_owner"), 0) + 1
    since = [r for r in rows if boot_ts and float(r.get("signal_ts") or 0) >= boot_ts]
    return {"file": path, "total": len(rows), "since_boot": len(since), "by_pause_owner": by_owner,
            "recent": rows[-50:]}


# --------------------------------------------------------------------------- tile stats folding
# DISLOC trades live in their own paper ledger (disloc_trades*.jsonl), not in the
# bot's ``trades`` list, so the tile cards, /api/monitor/tiles/totals and the
# monitor lane stats used to read 0 for Tiles 9/10.  These helpers project the
# ledger onto the same shapes those paths already consume.

LANE_FILES = {
    "FAMILY_DISLOC_BFX_DISLOCATION_1M": TRADES_FILE,
    "FAMILY_DISLOC_FOI_LONG_1M": FOI_TRADES_FILE,
}
DEFAULT_MARGIN_USD = 0.25
DEFAULT_LEVERAGE = 100.0


def delay_bucket(signal_to_fill_s) -> Optional[str]:
    v = _num(signal_to_fill_s)
    if v is None:
        return None
    for name, lo, hi in DELAY_BUCKETS:
        if lo <= v <= hi:
            return name
    return "gt_5s"


def closed_rows(rows: Iterable[Mapping[str, Any]]) -> list:
    return sorted((dict(r) for r in rows if isinstance(r, Mapping) and r.get("schema") == SCHEMA
                   and r.get("event") == "DISLOC_CLOSED"),
                  key=lambda r: float(r.get("exit_ts") or 0.0))


# Danish/boss 12 Oct 10:06: DISLOC cards show the real touch P&L (LONG: ask in,
# bid out; SHORT: bid in, ask out) with no fee deduction - Bitfinex charges this
# account no fees. net_bp_taker_2p8 stays on every row for the research verdict.
CARD_BP_KEY = "pnl_bp_vs_touch"
# Strategist 12 Oct: DISLOC is scored only on fills within 2 s of the signal (later fills are
# uncapturable live); they stay visible as ``unscored_late_fills``.
SCORE_MAX_SIGNAL_TO_FILL_S = 2.0
SHADOW_HOLD_SEC = 30 * 60


def is_scored(row: Mapping[str, Any]) -> bool:
    v = _num(row.get("signal_to_fill_s"))
    return v is not None and v <= SCORE_MAX_SIGNAL_TO_FILL_S


def card_bp(row: Mapping[str, Any]) -> Optional[float]:
    return _num(row.get(CARD_BP_KEY))


def row_net_usd(row: Mapping[str, Any], margin_usd: float = DEFAULT_MARGIN_USD,
                leverage: float = DEFAULT_LEVERAGE) -> Optional[float]:
    bp = card_bp(row)
    if bp is None:
        return None
    # HM 13:27: size on what actually traded (partial entry and partial/forced exit)
    frac = 1.0
    req = _num(row.get("requested_qty"))
    held = _num(row.get("exit_filled_qty")) if row.get("exit_filled_qty") is not None else _num(row.get("qty"))
    if req and held is not None:
        frac = max(0.0, min(1.0, held / req))
    return bp * float(margin_usd) * float(leverage) * frac / 1e4


def tile_trade_rows(rows: Iterable[Mapping[str, Any]], lane: str, *, epoch_id=None,
                    margin_usd: float = DEFAULT_MARGIN_USD, leverage: float = DEFAULT_LEVERAGE,
                    short_name: Optional[str] = None, be_band_bp: float = 0.0) -> list:
    """DISLOC_CLOSED ledger rows as ``tile_trade_row_v1``-shaped CLOSED rows (paper, never forced)."""
    out = []
    notional = float(margin_usd) * float(leverage)
    for r in closed_rows(rows):
        bp = card_bp(r)
        net = row_net_usd(r, margin_usd, leverage)
        if bp is None:
            cls = None
        elif bp > be_band_bp:
            cls = "W"
        elif bp < -be_band_bp:
            cls = "L"
        else:
            cls = "BE"
        fill_ts, exit_ts = _num(r.get("fill_ts")), _num(r.get("exit_ts"))
        out.append({
            "schema": "tile_trade_row_v1", "trade_id": str(r.get("trade_id") or ""), "lane": lane,
            "tile_short": short_name, "status": "CLOSED", "data_epoch_id": epoch_id or r.get("data_epoch_id"),
            "policy_signature": r.get("policy_signature"), "side": r.get("side"),
            "notional_usd": notional, "leverage": leverage,
            "trigger": {"signal_ts": _num(r.get("signal_ts")), "entry_path": "DISLOC_PAPER",
                        "entry_type": r.get("order_type"),
                        **({"regime_at_signal": r.get("regime_at_signal"),
                            "regime_at_signal_source": "indicator_engine_regime_tag_at_order"}
                           if r.get("regime_at_signal") else {})},
            "order": {"submitted_ts": _num(r.get("order_ts")), "terminal": "FILLED"},
            "fill": {"fill_price": _num(r.get("fill_price")), "fill_ts": fill_ts,
                     "fill_ts_basis": "DISLOC_PAPER_TAKER_AT_TOUCH"},
            "exit": {"close_ts": exit_ts, "exit_price": _num(r.get("exit_price")),
                     "exit_bid": _num(r.get("exit_bid")), "exit_ask": _num(r.get("exit_ask")),
                     "exit_reason": r.get("exit_reason"), "close_origin": "STRATEGY",
                     "forced_close": False, "deploy_flatten": False, "legs": []},
            "timing": {"signal_to_order_s": _num(r.get("signal_to_order_s")),
                       "signal_to_fill_s": _num(r.get("signal_to_fill_s")),
                       "order_to_fill_s": _num(r.get("order_to_fill_s")), "hold_s": _num(r.get("hold_s")),
                       # DATA-GAPS-20261012 (d): same keys as tile_trade_timing_v1.
                       "signal_to_fill_sec": _num(r.get("signal_to_fill_s")),
                       "signal_to_fill_ms": (int(round(_num(r.get("signal_to_fill_s")) * 1000))
                                             if _num(r.get("signal_to_fill_s")) is not None else None)},
            "path": {"mfe_bp": None, "basis": None},
            "pnl": {"bp": bp, "bp_basis": CARD_BP_KEY, "gross_usd": None, "trading_fees_usd": None,
                    "funding_usd": None, "net_usd": round(net, 6) if net is not None else None,
                    "net_usd_basis": "DISLOC_NET_BP_x_NOTIONAL", "class": cls, "be_band_bp": be_band_bp,
                    "net_usd_cents_booked": round(net, 2) if net is not None else None},
            "disloc": {"side_tag": r.get("side_tag") or SIDE_TAGS.get(str(r.get("side"))),
                       "delay_bucket": delay_bucket(r.get("signal_to_fill_s")), "variant": r.get("variant")},
            "sort_ts": fill_ts or exit_ts or 0.0,
        })
    return out


def lane_metrics(rows: Iterable[Mapping[str, Any]], *, margin_usd: float = DEFAULT_MARGIN_USD,
                 leverage: float = DEFAULT_LEVERAGE) -> dict:
    """Card metrics (same keys as the lane ledger fold in bot.py) + side / delay-bucket breakdown."""
    all_closed = closed_rows(rows)
    # HM 14:00: FORCED_NO_BOOK closes have no real exit price: counted, never scored
    forced = [r for r in all_closed if r.get("exit_status") == "FORCED_NO_BOOK"]
    all_closed = [r for r in all_closed if r.get("exit_status") != "FORCED_NO_BOOK"]
    closed = [r for r in all_closed if is_scored(r)]
    unscored = len(all_closed) - len(closed)
    wins = losses = 0
    net_total = 0.0
    by_side: dict = {}
    by_delay: dict = {}
    for r in all_closed:
        bp = card_bp(r) or 0.0
        net = row_net_usd(r, margin_usd, leverage) or 0.0
        scored = is_scored(r)
        if scored:
            net_total += net
            wins += bp > 0
            losses += bp < 0
        groups = [(delay_bucket(r.get("signal_to_fill_s")) or "unknown", by_delay)]
        if scored:
            groups.append((str(r.get("side") or "?"), by_side))
        for key, bucket in groups:
            b = bucket.setdefault(key, {"closes": 0, "wins": 0, "losses": 0, "net_usd": 0.0, "sum_bp": 0.0})
            b["closes"] += 1
            b["wins"] += bp > 0
            b["losses"] += bp < 0
            b["net_usd"] += net
            b["sum_bp"] += bp
    for bucket in (by_side, by_delay):
        for key, b in bucket.items():
            if key in SIDE_TAGS:
                b["tag"] = SIDE_TAGS[key]
            b["mean_net_bp"] = round(b.pop("sum_bp") / b["closes"], 4) if b["closes"] else None
            b["net_usd"] = round(b["net_usd"], 4)
    n = len(closed)
    return {
        "closes": n, "wins": wins, "losses": losses, "net_pnl_usd": round(net_total, 4),
        "mean_net_bp": round(sum(card_bp(r) or 0.0 for r in closed) / n, 4) if n else None,
        "by_side": by_side, "by_delay_bucket": by_delay,
        "unscored_late_fills": unscored,
        "n_forced": len(forced),
        "unpriced_qty": round(sum(_num(r.get("exit_unpriced_qty")) or 0.0 for r in forced), 10), "scoring_rule": f"signal_to_fill_s <= {SCORE_MAX_SIGNAL_TO_FILL_S:g}",
        "net_pnl_usd_unrounded": round(net_total, 6),
        "usd_basis": f"{CARD_BP_KEY} (real ask in, real bid out, Bitfinex fee 0) x ${float(margin_usd):g} margin x {float(leverage):g}x / 1e4 (paper)",
    }
