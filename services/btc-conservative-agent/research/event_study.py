"""Event-study harness for pre-registered hypotheses (``preregistered_hypotheses``).

For every registered hypothesis it detects events causally on the shared
epoch-second clock, then measures the signed Bitfinex ``tBTCF0:USTF0`` outcome
after each event against matched controls:

* controls - ``CONTROLS_PER_EVENT`` seconds drawn deterministically from the
  same UTC hour of day (or weekday class) and the same *trailing* rv15
  volatility tercile (``trailing_regime``; no full-sample quantile), at least
  the longest horizon away from every event;
* metrics - ``car_mid_bp`` (signed mid return minus the control mean),
  ``abs_move_bp`` (absolute move minus the control mean) and
  ``taker_after_spread_bp`` (Bitfinex taker entry at the next bucket's
  ask/bid and exit at the opposite side; no exchange fee applied);
* inference - hour-clustered CR1 t and cluster-bootstrap CI per horizon,
  Benjamini-Hochberg q across the primary tests of every scored lockbox.

Lockbox discipline: discovery events (before registration) are reported as
exploratory; lockbox events are only *counted* until the lockbox closes and
are scored once afterwards against the frozen kill rule. Nothing here can
place, change or cancel an order.
"""
from __future__ import annotations

import hashlib
import math
import os
from datetime import datetime, timezone
from typing import Mapping, Optional

import numpy as np

import cross_venue_tape as cvt
import market_context_tape as mct
import market_session_calendar as msc
import trailing_regime as tr
from research import preregistered_hypotheses as ph
from research.ai_challenger_report import benjamini_hochberg
from research.data_health_report import load_liquidations, load_market_context_rows
from research.lead_lag_report import cluster_stats, load_bitfinex_quotes, load_cross_venue_rows

SCHEMA = "event_study_report_v1"
REPORT_FILE = "event_study_report.json"
MAX_DAYS = 10
SEED = 20261002


def _r(value, digits: int = 4):
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def _iso_ts(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


class Clock:
    """Dense per-second arrays (NaN = unobserved) over [start, end)."""

    def __init__(self, start: int, end: int) -> None:
        self.start, self.end = int(start), int(end)
        self.n = max(0, self.end - self.start)
        self.bid = np.full(self.n, np.nan)
        self.ask = np.full(self.n, np.nan)
        self.venue_mid: dict = {}
        self.coinbase_premium = np.full(self.n, np.nan)
        self.deriv_minutes: dict = {}

    def idx(self, ts: float) -> int:
        return int(ts) - self.start

    @property
    def mid(self) -> np.ndarray:
        return (self.bid + self.ask) / 2.0

    def fill_quotes(self, quotes: Mapping[int, tuple]) -> None:
        for sec, (bid, ask) in quotes.items():
            i = sec - self.start
            if 0 <= i < self.n:
                self.bid[i], self.ask[i] = bid, ask

    def fill_cross_venue(self, rows: list) -> None:
        for row in rows:
            decoded = cvt.decode_minute(row)
            for venue, cells in decoded.items():
                if venue == "bfx":
                    continue
                arr = self.venue_mid.setdefault(venue, np.full(self.n, np.nan))
                for sec, cell in cells.items():
                    i = sec - self.start
                    if 0 <= i < self.n and cell.get("mid") is not None:
                        arr[i] = cell["mid"]

    def fill_market_context(self, rows: list) -> None:
        for row in rows:
            decoded = mct.decode_minute(row)
            for sec, bp in (decoded.get("premium") or {}).get("coinbase_vs_bfx", {}).items():
                i = sec - self.start
                if 0 <= i < self.n and bp is not None:
                    self.coinbase_premium[i] = bp
            self.deriv_minutes[int(row["minute_ts"])] = row.get("derivatives") or {}


# ---------------------------------------------------------------------------
# Causal regime and control pools
# ---------------------------------------------------------------------------
def minute_terciles(clock: Clock) -> dict:
    """{minute_ts: tercile label} from trailing rv15 percentile ranks (WARMUP until history)."""
    out = {}
    if clock.n < 120:
        return out
    mid = clock.mid
    first_min = clock.start - clock.start % 60 + 60
    closes = []
    tp = tr.TrailingPercentile()
    for m in range(first_min, clock.end, 60):
        i = m - 1 - clock.start
        closes.append(mid[i] if 0 <= i < clock.n and np.isfinite(mid[i]) else None)
        window = closes[-(16):]
        rets = [math.log(b / a) if a and b else None for a, b in zip(window, window[1:])]
        rv = tr.rv_bps(rets) if sum(1 for r in rets if r is not None) >= 13 else None
        obs = tp.observe(rv)
        rank = obs["rank_pct"]
        out[m] = "WARMUP" if rank is None else ("LOW" if rank < 100 / 3 else "MID" if rank < 200 / 3 else "HIGH")
    return out


def _match_key(ts: int, terciles: dict, match: tuple) -> tuple:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    first = dt.hour if match[0] == "hour_of_day_utc" else ("WEEKEND" if dt.weekday() >= 5 else "WEEKDAY")
    return first, terciles.get(ts - ts % 60, "WARMUP")


def control_indices(clock: Clock, event_idx: list, terciles: dict, *, max_h: int, match: tuple,
                    per_event: int, seed_key: str, valid: np.ndarray) -> list:
    """Deterministic matched controls per event (index lists)."""
    if clock.n == 0:
        return [[] for _ in event_idx]
    blocked = np.zeros(clock.n, dtype=bool)
    for i in event_idx:
        blocked[max(0, i - max_h): min(clock.n, i + max_h + 1)] = True
    pools: dict = {}
    for offset in range(0, clock.n - max_h - 1, 60):
        if blocked[offset] or not valid[offset]:
            continue
        pools.setdefault(_match_key(clock.start + offset, terciles, match), []).append(offset)
    out = []
    for i in event_idx:
        key = _match_key(clock.start + i, terciles, match)
        pool = pools.get(key) or []
        if not pool:
            out.append([])
            continue
        digest = hashlib.sha256(f"{seed_key}:{clock.start + i}".encode("utf-8")).hexdigest()
        rng = np.random.default_rng(int(digest[:12], 16) ^ SEED)
        pick = rng.choice(len(pool), size=min(per_event, len(pool)), replace=False)
        sec_in_min = (clock.start + i) % 60
        out.append([min(clock.n - 1, pool[j] + sec_in_min) for j in sorted(pick)])
    return out


# ---------------------------------------------------------------------------
# Event rules (causal: only data at or before the event second)
# ---------------------------------------------------------------------------
def _debounce(events: list, gap: int) -> list:
    keep, last = [], -10 ** 12
    for i, s in sorted(events):
        if i - last >= gap:
            keep.append((i, s))
            last = i
    return keep


def events_xvl(clock: Clock, rule: dict) -> list:
    w = int(rule["lookback_sec"])
    venues = [clock.venue_mid.get(v) for v in rule["venues"]]
    if clock.n <= w or any(v is None for v in venues):
        return []
    mid = clock.mid

    def ret(a):
        out = np.full(clock.n, np.nan)
        out[w:] = (a[w:] / a[:-w] - 1.0) * 1e4
        return out

    lead = np.mean([ret(v) for v in venues], axis=0) - ret(mid)
    spread = (clock.ask - clock.bid) / mid * 1e4
    hit = np.isfinite(lead) & (np.abs(lead) >= float(rule["lead_threshold_bps"])) & \
        np.isfinite(spread) & (spread <= float(rule["max_spread_bps"]))
    return _debounce([(int(i), 1 if lead[i] > 0 else -1) for i in np.flatnonzero(hit)], int(rule["debounce_sec"]))


def events_liquidation_burst(clock: Clock, rule: dict, liqs: list) -> list:
    window, floor = int(rule["window_sec"]), float(rule["min_notional_usd"])
    out = []
    for side, sign in (("LONG_LIQUIDATED", -1), ("SHORT_LIQUIDATED", 1)):
        evs = [(float(e["ts"]), float(e.get("notional_usd") or 0.0)) for e in liqs
               if e.get("liq_side") == side and e.get("venue") in rule["venues"]]
        lo, total = 0, 0.0
        for ts, usd in evs:
            total += usd
            while evs[lo][0] < ts - window:
                total -= evs[lo][1]
                lo += 1
            if total >= floor:
                i = clock.idx(ts)
                if 0 <= i < clock.n:
                    out.append((i, sign))
    return _debounce(out, int(rule["debounce_sec"]))


def events_funding_window(clock: Clock, rule: dict) -> list:
    out = []
    before = int(rule["minutes_before_settlement"]) * 60
    day0 = clock.start - clock.start % 86400
    for day in range(day0, clock.end + 86400, 86400):
        for hour in (0, 8, 16):
            t = day + hour * 3600 - before
            i = clock.idx(t)
            if not 0 <= i < clock.n:
                continue
            minute = t - t % 60
            rates = []
            for m in (minute, minute - 60, minute - 120):
                d = clock.deriv_minutes.get(m)
                if d:
                    for v in rule["funding_venues"]:
                        cell = d.get(v) or {}
                        if cell.get("status") == "OK" and cell.get("predicted_funding_rate") is not None:
                            rates.append(float(cell["predicted_funding_rate"]))
                    if rates:
                        break
            if not rates:
                continue
            f = sum(rates) / len(rates)
            if abs(f) >= float(rule["min_abs_predicted_funding"]):
                out.append((i, -1 if f > 0 else 1))
    return out


def events_us_open(clock: Clock, rule: dict) -> list:
    out = []
    mid = clock.mid
    day0 = clock.start - clock.start % 86400
    for day in range(day0, clock.end + 86400, 86400):
        probe = day + 15 * 3600
        if datetime.fromtimestamp(probe, timezone.utc).weekday() >= 5:
            continue
        t = day + (13 if msc.us_dst(probe) else 14) * 3600 + 1800
        i = clock.idx(t)
        j = i - 21600
        if not 0 <= i < clock.n:
            continue
        prior = None
        if 0 <= j < clock.n and np.isfinite(mid[i]) and np.isfinite(mid[j]):
            prior = mid[i] / mid[j] - 1.0
        out.append((i, -1 if (prior or 0) > 0 else 1))
    return out


def events_coinbase_premium(clock: Clock, rule: dict) -> list:
    p = clock.coinbase_premium
    a, lag, need = int(rule["avg_sec"]), int(rule["change_lag_sec"]), int(rule["min_valid_sec"])
    out = []
    if clock.n <= lag + a or not np.isfinite(p).any():
        return out
    valid = np.isfinite(p).astype(float)
    vals = np.where(np.isfinite(p), p, 0.0)
    csum = np.concatenate([[0.0], np.cumsum(vals)])
    ccnt = np.concatenate([[0.0], np.cumsum(valid)])
    for i in range(lag + a, clock.n):
        n_now = ccnt[i + 1] - ccnt[i + 1 - a]
        n_prev = ccnt[i + 1 - lag] - ccnt[i + 1 - lag - a]
        if n_now < need or n_prev < need:
            continue
        change = (csum[i + 1] - csum[i + 1 - a]) / n_now - (csum[i + 1 - lag] - csum[i + 1 - lag - a]) / n_prev
        if abs(change) >= float(rule["min_change_bp"]):
            out.append((i, 1 if change > 0 else -1))
    return _debounce(out, int(rule["debounce_sec"]))


def detect_events(spec: dict, clock: Clock, liqs: list) -> list:
    rule = spec["event_rule"]
    kind = rule["kind"]
    if kind == "xvl_lead":
        return events_xvl(clock, rule)
    if kind == "liquidation_burst":
        return events_liquidation_burst(clock, rule, liqs)
    if kind == "funding_window":
        return events_funding_window(clock, rule)
    if kind == "us_cash_open":
        return events_us_open(clock, rule)
    if kind == "coinbase_premium_jump":
        return events_coinbase_premium(clock, rule)
    raise ValueError(f"unknown event rule {kind}")


# ---------------------------------------------------------------------------
# Outcomes and scoring
# ---------------------------------------------------------------------------
def outcome(clock: Clock, i: int, sign: int, h: int, metric: str, delay: int) -> float:
    e, x = i + delay, i + delay + h
    if e < 0 or x >= clock.n:
        return float("nan")
    mid = clock.mid
    if metric == "taker_after_spread_bp":
        if sign > 0:
            return (clock.bid[x] / clock.ask[e] - 1.0) * 1e4
        return (1.0 - clock.ask[x] / clock.bid[e]) * 1e4
    move = (mid[x] / mid[e] - 1.0) * 1e4
    return abs(move) if metric == "abs_move_bp" else move * sign


def score_events(spec: dict, clock: Clock, events: list, terciles: dict) -> dict:
    metric = spec["metric"]
    delay = int(spec.get("entry_delay_sec", 1))
    horizons = [int(h) for h in spec["horizons_sec"]]
    max_h = max(horizons) + delay
    match = tuple(spec.get("control_match_override") or ph.CONTROL_MATCH)
    valid = np.isfinite(clock.mid)
    idx = [i for i, _ in events]
    controls = control_indices(clock, idx, terciles, max_h=max_h, match=match,
                               per_event=ph.CONTROLS_PER_EVENT, seed_key=spec["id"], valid=valid)
    clusters = np.array([(clock.start + i) // 3600 for i in idx])
    curve = []
    for h in horizons:
        raw = np.array([outcome(clock, i, s, h, metric, delay) for i, s in events], dtype=float)
        ctrl = np.array([
            np.nanmean([outcome(clock, c, s, h, metric, delay) for c in cs]) if cs else np.nan
            for (i, s), cs in zip(events, controls)
        ], dtype=float) if events else np.array([])
        abnormal = raw - ctrl if metric != "taker_after_spread_bp" else raw
        curve.append({
            "horizon_sec": h,
            "raw": cluster_stats(raw, clusters) if events else cluster_stats(np.array([]), np.array([])),
            "control_mean": _r(np.nanmean(ctrl)) if events and np.isfinite(ctrl).any() else None,
            "abnormal": cluster_stats(abnormal, clusters) if events else cluster_stats(np.array([]), np.array([])),
        })
    primary = next(c for c in curve if c["horizon_sec"] == int(spec["primary_horizon_sec"]))
    return {"n_events": len(events), "events_with_controls": sum(1 for c in controls if c),
            "metric": metric, "abnormal_definition": (
                "raw (after-spread markout; controls shown for scale)" if metric == "taker_after_spread_bp"
                else "event minus matched-control mean"),
            "curve": curve, "primary": primary}


def verdict(spec: dict, primary: dict) -> str:
    stats = primary["abnormal"]
    n = stats.get("n") or 0
    if n < int(spec["min_lockbox_events"]):
        return "INSUFFICIENT_LOCKBOX_EVENTS"
    t, mean = stats.get("t"), stats.get("mean")
    kill = spec["kill_rule"]
    if t is None or mean is None:
        return "KILLED"
    if "kill_if_abs_t_lt" in kill:
        if abs(t) < kill["kill_if_abs_t_lt"]:
            return "KILLED"
        return "CONFIRMED_CONTINUATION" if t > 0 else "CONFIRMED_REVERSAL"
    if "kill_if_mean_le_bp" in kill and mean <= kill["kill_if_mean_le_bp"]:
        return "KILLED"
    if "kill_if_t_lt" in kill and t < kill["kill_if_t_lt"]:
        return "KILLED"
    return "CONFIRMED"


def run_hypothesis(spec: dict, clock: Clock, liqs: list, terciles: dict, now: float) -> dict:
    reg = _iso_ts(ph.REGISTERED_UTC)
    lock_end = reg + float(spec["lockbox_days"]) * 86400
    events = detect_events(spec, clock, liqs)
    disc = [(i, s) for i, s in events if clock.start + i < reg]
    lock = [(i, s) for i, s in events if reg <= clock.start + i < lock_end]
    span_days = max(1e-9, (min(now, clock.end) - max(reg, clock.start)) / 86400.0)
    rate = len(lock) / span_days if clock.end > reg else None
    lock_open = now < lock_end
    out = {
        "id": spec["id"],
        "spec_hash": ph.spec_hash(spec),
        "mechanism": spec["mechanism"],
        "metric": spec["metric"],
        "primary_horizon_sec": spec["primary_horizon_sec"],
        "min_lockbox_events": spec["min_lockbox_events"],
        "kill_rule": spec["kill_rule"],
        "lockbox": {
            "start_utc": ph.REGISTERED_UTC,
            "end_utc": datetime.fromtimestamp(lock_end, timezone.utc).isoformat(),
            "open": lock_open,
            "events_counted": len(lock),
            "events_per_day": _r(rate, 2),
            "days_to_min_sample": None if not rate else _r(max(0.0, (spec["min_lockbox_events"] - len(lock)) / rate), 1),
        },
        "discovery": {"label": "EXPLORATORY_NOT_CONFIRMATORY", **score_events(spec, clock, disc, terciles)}
        if disc else {"label": "EXPLORATORY_NOT_CONFIRMATORY", "n_events": 0},
    }
    if lock_open:
        out["status"] = "LOCKBOX_ACCRUING"
        out["lockbox"]["scored"] = False
    else:
        scored = score_events(spec, clock, lock, terciles)
        out["lockbox"].update({"scored": True, **scored})
        out["status"] = verdict(spec, scored["primary"])
    return out


def build_event_study(clock: Clock, liqs: list, now: float, data_status: Optional[dict] = None) -> dict:
    terciles = minute_terciles(clock)
    results = [run_hypothesis(spec, clock, liqs, terciles, now) for spec in ph.HYPOTHESES]
    scored = [r for r in results if r["lockbox"].get("scored")]
    qs = benjamini_hochberg([r["lockbox"]["primary"]["abnormal"].get("p") for r in scored]) if scored else []
    for r, q in zip(scored, qs):
        r["lockbox"]["primary_bh_q"] = q
    return {
        "schema": SCHEMA,
        "registry": {k: v for k, v in ph.registry().items() if k != "hypotheses"},
        "generated_ts": round(now, 3),
        "span": {"start_ts": clock.start, "end_ts": clock.end, "seconds": clock.n,
                 "bitfinex_mid_coverage_pct": _r(100.0 * np.isfinite(clock.mid).mean(), 2) if clock.n else None},
        "regime_labels": {k: int(v) for k, v in zip(*np.unique(list(terciles.values()) or ["NONE"], return_counts=True))},
        "data_status": data_status or {},
        "hypotheses": results,
    }


def build_from_data_dir(data_dir: str, now: Optional[float] = None, max_days: int = MAX_DAYS) -> dict:
    import time
    now = time.time() if now is None else float(now)
    cv_rows = load_cross_venue_rows(data_dir, max_days=max_days)
    mc_rows = load_market_context_rows(data_dir, max_days=max_days)
    spans = [(int(r[0]["minute_ts"]), int(r[-1]["minute_ts"]) + 60) for r in (cv_rows, mc_rows) if r]
    if not spans:
        return {"schema": SCHEMA, "status": "NO_DATA", "generated_ts": round(now, 3),
                "registry": ph.registry(), "hypotheses": []}
    start, end = min(s for s, _ in spans), max(e for _, e in spans)
    start = max(start, end - max_days * 86400)
    clock = Clock(start, end)
    clock.fill_quotes(load_bitfinex_quotes(data_dir, start, end))
    clock.fill_cross_venue(cv_rows)
    clock.fill_market_context(mc_rows)
    liqs = load_liquidations(data_dir, start=start)
    status = {"cross_venue_minutes": len(cv_rows), "market_context_minutes": len(mc_rows),
              "liquidation_events": len(liqs)}
    return build_event_study(clock, liqs, now, status)
