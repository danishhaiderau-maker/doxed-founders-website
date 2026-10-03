"""Shadow-exit recorder and per-trade / per-signal path dataset (observation only).

One record per paper trade and per signal path (``shadow_exit_paths.jsonl``):

* MFE/MAE at +1/2/5/10/30/60 min after the fill, time-of-peak and time-of-trough;
* a compact minute-by-minute executable path (bid for LONG, ask for SHORT) in bp
  from the entry, bounded to ``MAX_PATH_MINUTES``;
* entry context (ATR 3m, realized-vol percentile, ADX, spread, top-of-book depth
  and imbalance, UTC session, regime, signal-to-fill latency);
* counterfactual entries for unfilled limits (taker at signal under REALISTIC_V1
  rules, and the optimistic at-limit touch, labelled as a comparison shadow);
* every configured shadow exit replayed on the same path and scored side by side
  (net bp, exit time, reason), plus the first-trigger-wins composites;
* a reference to the hold window in the market-context feeds (funding, open
  interest, liquidations), joined by the analyzer with :func:`hold_market_context`.

The Fly runtime and the laptop analyzer/backfill call the same pure functions, so
both produce the identical schema. Nothing here can create, change, cancel or gate
an order: the runtime only hands finished replay snapshots to a bounded queue that
a daemon worker drains off the trading hot path.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import threading
import time
from collections import deque
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA = "shadow_exit_path_v1"
FILE_NAME = "shadow_exit_paths.jsonl"
STATUS_SCHEMA = "shadow_exit_recorder_status_v1"
SUMMARY_SCHEMA = "shadow_exit_public_summary_v1"
RECORDER_VERSION = "shadow_exit_recorder_v1_20261004"

HORIZONS_MIN = (1, 2, 5, 10, 30, 60)
MAX_PATH_MINUTES = 240
COUNTERFACTUAL_PATH_MINUTES = 120
MAX_INPUT_TICKS = 20_000
# Worker-thread pacing: release the GIL for COOPERATIVE_PAUSE_SEC after every
# COOPERATIVE_SLICE_SEC of computation so the trading threads never wait on it.
COOPERATIVE_SLICE_SEC = 0.002
COOPERATIVE_PAUSE_SEC = 0.001
_COOP = threading.local()
DEFAULT_HORIZON_SEC = 7200
# A path ending this close to the horizon is a time exit, not a censored path.
CENSOR_TOLERANCE_SEC = 180.0
PATH_UNIT_BP = 0.1
# REALISTIC_V1 (research/fill_model.py): stops, floors and time exits are
# marketable at the executable-side quote one observed tick after the trigger,
# booked at the worse of the trigger mark and the post-latency mark.
EXIT_LATENCY_SEC = 1.0
TAKER_LATENCY_SEC = 1.0
MEANINGFUL_PROFIT_BP = 20.0
SESSION_ENDS = ((8, "ASIA"), (16, "EU"), (24, "US"))

SOURCE_RUNTIME = "RUNTIME_REPLAY"
SOURCE_BACKFILL_REPLAY = "BACKFILL_SIGNAL_REPLAY"
SOURCE_BACKFILL_TAPE = "BACKFILL_TAPE_1S"
SOURCES = (SOURCE_RUNTIME, SOURCE_BACKFILL_REPLAY, SOURCE_BACKFILL_TAPE)

KIND_LATE_BREAKEVEN = "LATE_BREAKEVEN"
KIND_LATE_ATR_TRAIL = "LATE_ATR_TRAIL"
KIND_CONDITIONAL_EARLY_CUT = "CONDITIONAL_EARLY_CUT"
KIND_GIVEBACK = "GIVEBACK"
KIND_LADDER = "LADDER"
KIND_ATR_HARD_STOP = "ATR_HARD_STOP"
KIND_HOLD = "HOLD"
KIND_COMPOSITE = "COMPOSITE"
SUPPORTED_KINDS = frozenset({
    KIND_LATE_BREAKEVEN, KIND_LATE_ATR_TRAIL, KIND_CONDITIONAL_EARLY_CUT, KIND_GIVEBACK,
    KIND_LADDER, KIND_ATR_HARD_STOP, KIND_HOLD, KIND_COMPOSITE,
})
ACTUAL_EXIT_ID = "actual"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def direction_sign(direction: Any) -> int:
    text = str(direction or "").upper()
    return 1 if text == "LONG" else -1 if text == "SHORT" else 0


def session_of(ts: float | None) -> str | None:
    ts = _finite(ts)
    if ts is None:
        return None
    hour = time.gmtime(ts).tm_hour
    return next(label for end, label in SESSION_ENDS if hour < end)


def group_key(record: Mapping[str, Any]) -> str:
    """Tile lane for paper trades; ``SIGNAL:<lane>`` for shadow/counterfactual paths."""
    if record.get("tile"):
        return str(record["tile"])
    return f"SIGNAL:{record.get('lane') or 'UNKNOWN'}"


def is_giveback(exit_row: Mapping[str, Any], meaningful_bp: float = MEANINGFUL_PROFIT_BP) -> bool:
    """A trade that was in meaningful profit before this exit but closed negative."""
    net, mfe = _finite(exit_row.get("net_bp")), _finite(exit_row.get("mfe_before_exit_bp"))
    return net is not None and mfe is not None and mfe >= meaningful_bp and net < 0


def shadow_exit_set_id(shadow_set: Sequence[Mapping[str, Any]]) -> str:
    blob = json.dumps(list(shadow_set or ()), sort_keys=True, separators=(",", ":"), default=list)
    return "sxs:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def validate_shadow_exit_set(shadow_set: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Defects of a shadow-exit list; empty tuple means usable."""
    defects: list[str] = []
    ids: set[str] = set()
    for spec in shadow_set or ():
        if not isinstance(spec, Mapping):
            defects.append("SPEC_NOT_MAPPING")
            continue
        sid, kind = str(spec.get("id") or ""), spec.get("kind")
        if not sid or sid == ACTUAL_EXIT_ID:
            defects.append(f"INVALID_ID:{sid}")
        if sid in ids:
            defects.append(f"DUPLICATE_ID:{sid}")
        ids.add(sid)
        if kind not in SUPPORTED_KINDS:
            defects.append(f"{sid}:UNSUPPORTED_KIND:{kind}")
    for spec in shadow_set or ():
        if isinstance(spec, Mapping) and spec.get("kind") == KIND_COMPOSITE:
            members = tuple(spec.get("members") or ())
            if not members:
                defects.append(f"{spec.get('id')}:EMPTY_COMPOSITE")
            for member in members:
                target = next((s for s in shadow_set if isinstance(s, Mapping) and s.get("id") == member), None)
                if target is None:
                    defects.append(f"{spec.get('id')}:UNKNOWN_MEMBER:{member}")
                elif target.get("kind") == KIND_COMPOSITE:
                    defects.append(f"{spec.get('id')}:NESTED_COMPOSITE:{member}")
    return tuple(defects)


# --------------------------------------------------------------------------
# Path normalisation
# --------------------------------------------------------------------------
def _exec_exit_px(sign: int, bid: Any, ask: Any, last: Any) -> tuple[float | None, bool]:
    """Executable exit-side price; falls back to the last trade (flagged)."""
    side = _finite(bid if sign > 0 else ask)
    if side and side > 0:
        return side, False
    last = _finite(last)
    return (last, True) if last and last > 0 else (None, True)


def _exec_entry_px(sign: int, bid: Any, ask: Any, last: Any) -> tuple[float | None, bool]:
    side = _finite(ask if sign > 0 else bid)
    if side and side > 0:
        return side, False
    last = _finite(last)
    return (last, True) if last and last > 0 else (None, True)


def _cooperate() -> None:
    deadline = getattr(_COOP, "deadline", None)
    if deadline is not None and time.perf_counter() >= deadline:
        time.sleep(COOPERATIVE_PAUSE_SEC)
        _COOP.deadline = time.perf_counter() + COOPERATIVE_SLICE_SEC


def ticks_from_replay(replay: Mapping[str, Any]) -> list[tuple[float, float | None, float | None, float | None]]:
    """``signal_replay_v4`` ticks -> sorted ``(abs_ts, bid, ask, last)``, at most one per second."""
    out: list[tuple[float, float | None, float | None, float | None]] = []
    last_sec = None
    for i, tick in enumerate(list(replay.get("ticks") or ())[-MAX_INPUT_TICKS:]):
        if not i & 255:
            _cooperate()
        if not isinstance(tick, dict):
            continue
        ts = _finite(tick.get("observed_ts"))
        if ts is None:
            continue
        sec = int(ts)
        bid = _finite(tick.get("best_bid")) or _finite(tick.get("depth_best_bid"))
        ask = _finite(tick.get("best_ask")) or _finite(tick.get("depth_best_ask"))
        row = (ts, bid, ask, _finite(tick.get("price")))
        if sec == last_sec and out:
            out[-1] = row
        else:
            out.append(row)
        last_sec = sec
    out.sort(key=lambda r: r[0])
    return out


def _pnl_series(ticks, sign: int, entry: float, start_ts: float, end_ts: float | None):
    """``[(t_since_start, pnl_bp)]`` on the executable exit side, plus fallback count."""
    series: list[tuple[float, float]] = []
    fallback = 0
    for i, (ts, bid, ask, last) in enumerate(ticks):
        if not i & 511:
            _cooperate()
        if ts < start_ts:
            continue
        if end_ts is not None and ts > end_ts:
            break
        px, fell_back = _exec_exit_px(sign, bid, ask, last)
        if px is None:
            continue
        fallback += 1 if fell_back else 0
        series.append((ts - start_ts, sign * (px - entry) / entry * 1e4))
    return series, fallback


def horizon_extremes(series: Sequence[tuple[float, float]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for minutes in HORIZONS_MIN:
        limit = minutes * 60.0
        window = [p for t, p in series if t <= limit]
        observed = bool(series) and series[-1][0] >= limit
        out[str(minutes)] = {
            "mfe_bp": round(max(window), 2) if window else None,
            "mae_bp": round(min(window), 2) if window else None,
            "observed": observed,
        }
    return out


def _peak_trough(series: Sequence[tuple[float, float]], end_t: float | None = None) -> dict[str, Any]:
    window = [(t, p) for t, p in series if end_t is None or t <= end_t]
    if not window:
        return {"mfe_bp": None, "mae_bp": None, "peak_t_sec": None, "trough_t_sec": None}
    peak = max(window, key=lambda r: r[1])
    trough = min(window, key=lambda r: r[1])
    return {"mfe_bp": round(peak[1], 2), "mae_bp": round(trough[1], 2),
            "peak_t_sec": round(peak[0], 1), "trough_t_sec": round(trough[0], 1)}


def minute_path(series: Sequence[tuple[float, float]], max_minutes: int = MAX_PATH_MINUTES) -> dict[str, Any]:
    """Compact per-minute close/high/low of the executable pnl, ints in ``PATH_UNIT_BP``."""
    if not series:
        return {"unit_bp": PATH_UNIT_BP, "step_sec": 60, "minutes": 0, "truncated": False,
                "close": [], "high": [], "low": []}
    total = int(series[-1][0] // 60) + 1
    minutes = min(total, int(max_minutes))
    close: list[int | None] = [None] * minutes
    high: list[int | None] = [None] * minutes
    low: list[int | None] = [None] * minutes
    for t, pnl in series:
        idx = int(t // 60)
        if idx >= minutes:
            break
        q = int(round(pnl / PATH_UNIT_BP))
        close[idx] = q
        high[idx] = q if high[idx] is None else max(high[idx], q)
        low[idx] = q if low[idx] is None else min(low[idx], q)
    return {"unit_bp": PATH_UNIT_BP, "step_sec": 60, "minutes": minutes, "truncated": total > minutes,
            "close": close, "high": high, "low": low}


# --------------------------------------------------------------------------
# Shadow exits
# --------------------------------------------------------------------------
class _Rule:
    """Incremental first-trigger evaluator for one shadow-exit spec."""

    def __init__(self, spec: Mapping[str, Any], atr_bp: float | None) -> None:
        self.spec, self.kind, self.atr_bp = spec, spec.get("kind"), atr_bp
        self.mfe = -math.inf
        self.armed = False
        self.unavailable = None
        if self.kind in (KIND_LATE_ATR_TRAIL, KIND_ATR_HARD_STOP) and not (atr_bp and atr_bp > 0):
            self.unavailable = "NO_ATR"

    def step(self, t: float, pnl: float) -> str | None:
        if self.unavailable:
            return None
        self.mfe = max(self.mfe, pnl)
        spec, kind = self.spec, self.kind
        hard_stop = spec.get("hard_stop_bp")
        if hard_stop is not None and pnl <= -float(hard_stop):
            return "HARD_STOP"
        backstop = spec.get("backstop_sec")
        if backstop is not None and t >= float(backstop):
            return "TIME_BACKSTOP"
        if kind == KIND_LATE_BREAKEVEN:
            self.armed = self.armed or self.mfe >= float(spec["arm_bp"])
            return "LATE_BREAKEVEN" if self.armed and pnl <= float(spec.get("floor_bp", 0.0)) else None
        if kind == KIND_LATE_ATR_TRAIL:
            atr = float(self.atr_bp)
            self.armed = self.armed or self.mfe >= float(spec["arm_atr"]) * atr
            if not self.armed:
                return None
            level = max(float(spec.get("floor_bp", 0.0)), self.mfe - float(spec["trail_atr"]) * atr)
            return "LATE_ATR_TRAIL" if pnl <= level else None
        if kind == KIND_CONDITIONAL_EARLY_CUT:
            if t > float(spec["within_sec"]) or self.mfe > float(spec["max_mfe_bp"]):
                return None
            return "THESIS_WRONG_EARLY_CUT" if pnl <= float(spec["cut_bp"]) else None
        if kind == KIND_GIVEBACK:
            self.armed = self.armed or self.mfe >= float(spec["arm_bp"])
            if not self.armed:
                return None
            level = max(float(spec.get("floor_bp", 0.0)), self.mfe * (1.0 - float(spec["giveback_frac"])))
            return "GIVEBACK" if pnl <= level else None
        if kind == KIND_LADDER:
            lock = None
            for trigger, locked in spec.get("rungs_bp") or ():
                if self.mfe >= float(trigger):
                    lock = float(locked)
            return "LADDER_LOCK" if lock is not None and pnl <= lock else None
        if kind == KIND_ATR_HARD_STOP:
            stop = float(spec["stop_atr"]) * float(self.atr_bp)
            if spec.get("clamp_bp"):
                lo, hi = (float(x) for x in spec["clamp_bp"])
                stop = min(max(stop, lo), hi)
            return "ATR_HARD_STOP" if pnl <= -stop else None
        return None


def _book_exit(series, idx: int, fee_bp: float) -> tuple[float, float]:
    """REALISTIC_V1 marketable exit: worse of trigger mark and first mark >= trigger + latency."""
    t_trig, p_trig = series[idx]
    t_fill, p_fill = t_trig, p_trig
    for j in range(idx + 1, len(series)):
        if series[j][0] >= t_trig + EXIT_LATENCY_SEC:
            t_fill, p_fill = series[j]
            break
    return t_fill, min(p_trig, p_fill) - fee_bp


def _prefix_max(series) -> list[float]:
    out, best = [], -math.inf
    for _t, pnl in series:
        best = max(best, pnl)
        out.append(best)
    return out


def evaluate_shadow_exits(series: Sequence[tuple[float, float]], shadow_set: Sequence[Mapping[str, Any]], *,
                          atr_bp: float | None, horizon_sec: float, fee_bp: float = 0.0) -> list[dict[str, Any]]:
    """Score every spec on one pnl path; composites are first-trigger-wins over their members."""
    window = [row for row in series if row[0] <= horizon_sec]
    if not window:
        return [{"id": s.get("id"), "kind": s.get("kind"), "net_bp": None, "exit_t_sec": None,
                 "reason": "NO_PATH", "triggered": False} for s in shadow_set]
    censored = window[-1][0] < horizon_sec - CENSOR_TOLERANCE_SEC
    rules = {s.get("id"): _Rule(s, atr_bp) for s in shadow_set if s.get("kind") != KIND_COMPOSITE}
    first: dict[str, tuple[int, str]] = {}
    for idx, (t, pnl) in enumerate(window):
        if not idx & 63:
            _cooperate()
        for sid, rule in rules.items():
            if sid in first:
                continue
            reason = rule.step(t, pnl)
            if reason:
                first[sid] = (idx, reason)
        if len(first) == len(rules):
            break
    end_t, end_p = window[-1]
    peak = _prefix_max(window)
    time_exit = {"net_bp": round(end_p - fee_bp, 2), "exit_t_sec": round(end_t, 1),
                 "mfe_before_exit_bp": round(peak[-1], 2),
                 "reason": "PATH_END_CENSORED" if censored else "HORIZON_TIME_EXIT", "triggered": False}
    out: list[dict[str, Any]] = []
    for spec in shadow_set:
        sid, kind = spec.get("id"), spec.get("kind")
        if kind == KIND_COMPOSITE:
            members = [m for m in spec.get("members") or () if m in rules]
            hits = [(first[m][0], members.index(m), m) for m in members if m in first]
            unavailable = [m for m in members if rules[m].unavailable]
            if hits:
                idx, _order, member = min(hits)
                t_fill, net = _book_exit(window, idx, fee_bp)
                row = {"net_bp": round(net, 2), "exit_t_sec": round(t_fill, 1),
                       "mfe_before_exit_bp": round(peak[idx], 2),
                       "reason": f"{first[member][1]}:{member}", "triggered": True, "trigger_member": member}
            else:
                row = dict(time_exit)
            if unavailable:
                row["members_unavailable"] = unavailable
        else:
            rule = rules[sid]
            if rule.unavailable:
                row = {"net_bp": None, "exit_t_sec": None, "mfe_before_exit_bp": None,
                       "reason": rule.unavailable, "triggered": False}
            elif sid in first:
                idx, reason = first[sid]
                t_fill, net = _book_exit(window, idx, fee_bp)
                row = {"net_bp": round(net, 2), "exit_t_sec": round(t_fill, 1),
                       "mfe_before_exit_bp": round(peak[idx], 2), "reason": reason, "triggered": True}
            else:
                row = dict(time_exit)
        out.append({"id": sid, "kind": kind, **{k: spec[k] for k in ("label", "role") if k in spec}, **row})
    return out


# --------------------------------------------------------------------------
# Context and counterfactuals
# --------------------------------------------------------------------------
def entry_context(*, signal_ts: float | None, fill_ts: float | None, atr_pct: Any = None, adx: Any = None,
                  rv_pct_rank: Any = None, rv_label: Any = None, regime: Any = None,
                  bid: Any = None, ask: Any = None, bid_qty: Any = None, ask_qty: Any = None) -> dict[str, Any]:
    bid, ask = _finite(bid), _finite(ask)
    bq, aq = _finite(bid_qty), _finite(ask_qty)
    mid = (bid + ask) / 2.0 if bid and ask and ask >= bid else None
    spread_bp = round((ask - bid) / mid * 1e4, 3) if mid else None
    imbalance = round((bq - aq) / (bq + aq), 4) if bq is not None and aq is not None and (bq + aq) > 0 else None
    latency = None
    if _finite(signal_ts) is not None and _finite(fill_ts) is not None:
        latency = round(float(fill_ts) - float(signal_ts), 3)
    ctx = {
        "atr3m_pct": _finite(atr_pct),
        "rv_pct_rank": _finite(rv_pct_rank),
        "rv_label": rv_label if rv_label is None else str(rv_label),
        "adx": _finite(adx),
        "spread_bp": spread_bp,
        "top_bid_qty": bq,
        "top_ask_qty": aq,
        "depth_imbalance": imbalance,
        "session": session_of(fill_ts if _finite(fill_ts) is not None else signal_ts),
        "regime": regime if regime is None or isinstance(regime, (str, int, float)) else str(regime),
        "signal_to_fill_latency_sec": latency,
    }
    ctx["missing"] = sorted(k for k, v in ctx.items() if v is None)
    return ctx


def _first_quote_at(ticks, ts: float):
    keys = [row[0] for row in ticks]
    k = bisect.bisect_left(keys, ts)
    return ticks[k] if k < len(ticks) else None


def counterfactual_entries(ticks, *, sign: int, signal_ts: float, limit_price: Any,
                           horizon_sec: float) -> dict[str, Any]:
    """Taker at signal (REALISTIC_V1 latency, opposite side) and at-limit touch (comparison shadow)."""
    out: dict[str, Any] = {}
    quote = _first_quote_at(ticks, signal_ts + TAKER_LATENCY_SEC)
    if quote is not None:
        entry, fell_back = _exec_entry_px(sign, quote[1], quote[2], quote[3])
        if entry:
            series, _fb = _pnl_series(ticks, sign, entry, quote[0], quote[0] + horizon_sec)
            out["taker_at_signal"] = {
                "fill_model": "REALISTIC_V1_TAKER", "entry_price": entry, "entry_ts": round(quote[0], 3),
                "entry_side_fallback": fell_back, "horizons": horizon_extremes(series),
                "extremes": _peak_trough(series),
                "path": minute_path(series, COUNTERFACTUAL_PATH_MINUTES),
            }
    limit = _finite(limit_price)
    if limit and limit > 0:
        touched = None
        for ts, bid, ask, last in ticks:
            if ts < signal_ts:
                continue
            touch_px = _finite(ask if sign > 0 else bid) or _finite(last)
            if touch_px and ((sign > 0 and touch_px <= limit) or (sign < 0 and touch_px >= limit)):
                touched = ts
                break
        cell: dict[str, Any] = {"fill_model": "OPTIMISTIC_TOUCH_V1", "role": "COMPARISON_SHADOW_NOT_HEADLINE",
                                "limit_price": limit, "touched": touched is not None,
                                "touch_ts": round(touched, 3) if touched is not None else None}
        if touched is not None:
            series, _fb = _pnl_series(ticks, sign, limit, touched, touched + horizon_sec)
            cell.update({"horizons": horizon_extremes(series), "extremes": _peak_trough(series),
                         "path": minute_path(series, COUNTERFACTUAL_PATH_MINUTES)})
        out["limit_touch"] = cell
    return out


def market_context_ref(start_ts: float | None, end_ts: float | None) -> dict[str, Any]:
    return {
        "join": "DEFERRED_ANALYZER_JOIN",
        "source_file": "market_context_1m.jsonl",
        "source_schema": "market_context_1m_v1",
        "liquidations_file": "liquidations.jsonl",
        "window_start_ts": None if start_ts is None else round(float(start_ts), 3),
        "window_end_ts": None if end_ts is None else round(float(end_ts), 3),
    }


def hold_market_context(minute_rows: Iterable[Mapping[str, Any]], start_ts: float, end_ts: float, *,
                        burst_usd: float = 1_000_000.0) -> dict[str, Any]:
    """Funding, open-interest change and liquidation bursts over a hold from ``market_context_1m`` rows."""
    rows = sorted((r for r in minute_rows if isinstance(r, Mapping)
                   and start_ts - 60 <= (_finite(r.get("minute_ts")) or -1) <= end_ts),
                  key=lambda r: r["minute_ts"])
    out: dict[str, Any] = {"join": "JOINED" if rows else "NO_MARKET_CONTEXT_ROWS", "minutes": len(rows)}
    if not rows:
        return out
    funding = {}
    oi_change = {}
    for venue in ("bitfinex", "binance", "bybit", "okx"):
        cells = [((r.get("derivatives") or {}).get(venue) or {}) for r in rows]
        rates = [_finite(c.get("funding_rate")) for c in cells if c.get("status") == "OK"]
        rates = [x for x in rates if x is not None]
        if rates:
            funding[venue] = {"start": rates[0], "end": rates[-1]}
        ois = [_finite(c.get("oi_btc")) for c in cells if c.get("status") == "OK"]
        ois = [x for x in ois if x]
        if len(ois) >= 2:
            oi_change[venue] = round((ois[-1] - ois[0]) / ois[0] * 100.0, 4)
    long_usd = short_usd = 0.0
    long_n = short_n = 0
    burst_minutes = 0
    max_minute_usd = 0.0
    for r in rows:
        minute_total = 0.0
        for cell in (r.get("liquidations") or {}).values():
            if not isinstance(cell, Mapping):
                continue
            long_usd += float(cell.get("long_usd") or 0.0)
            short_usd += float(cell.get("short_usd") or 0.0)
            long_n += int(cell.get("long_n") or 0)
            short_n += int(cell.get("short_n") or 0)
            minute_total += float(cell.get("long_usd") or 0.0) + float(cell.get("short_usd") or 0.0)
        max_minute_usd = max(max_minute_usd, minute_total)
        burst_minutes += 1 if minute_total >= burst_usd else 0
    regime = (rows[0].get("regime") or {}) if isinstance(rows[0].get("regime"), Mapping) else {}
    out.update({
        "funding_rate": funding,
        "oi_change_pct": oi_change,
        "liquidations": {"long_usd": round(long_usd, 2), "short_usd": round(short_usd, 2),
                         "long_n": long_n, "short_n": short_n, "max_minute_usd": round(max_minute_usd, 2),
                         "burst_minutes": burst_minutes, "burst_threshold_usd": burst_usd},
        "entry_regime": {"rv15_bps": regime.get("rv15_bps"), "rank_pct": regime.get("rank_pct"),
                         "label": regime.get("label")},
    })
    return out


# --------------------------------------------------------------------------
# Record builder
# --------------------------------------------------------------------------
def build_record(*, source: str, trade_id: str, direction: str, ticks, signal_ts: float | None,
                 fill_ts: float | None, entry_price: float | None, shadow_set: Sequence[Mapping[str, Any]],
                 lane: str | None = None, research_lane: str | None = None, tile: str | None = None,
                 policy_signature: str | None = None, collection_epoch_id: str | None = None,
                 exit_ts: float | None = None, exit_reason: str | None = None, limit_price: Any = None,
                 atr_pct: Any = None, context: Mapping[str, Any] | None = None,
                 horizon_sec: float = DEFAULT_HORIZON_SEC, fee_bp: float = 0.0,
                 extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One ``shadow_exit_path_v1`` record from a normalised ``(abs_ts, bid, ask, last)`` path."""
    sign = direction_sign(direction)
    ticks = list(ticks or ())[-MAX_INPUT_TICKS:]
    ctx_in = dict(context or {})
    filled = bool(fill_ts is not None and _finite(entry_price))
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "recorder_version": RECORDER_VERSION,
        "record_id": "sxp:" + hashlib.sha256(f"{source}|{trade_id}".encode()).hexdigest()[:20],
        "source": source,
        "trade_id": str(trade_id),
        "lane": lane,
        "research_lane": research_lane,
        "tile": tile or research_lane,
        "policy_signature": policy_signature,
        "collection_epoch_id": collection_epoch_id,
        "direction": str(direction or "").upper() or None,
        "filled": filled,
        "signal_ts": None if signal_ts is None else round(float(signal_ts), 3),
        "fill_ts": None if fill_ts is None else round(float(fill_ts), 3),
        "entry_price": _finite(entry_price),
        "horizon_sec": int(horizon_sec),
        "fee_bp": fee_bp,
        "fill_model": "REALISTIC_V1",
        "exit_latency_sec": EXIT_LATENCY_SEC,
        "meaningful_profit_bp": MEANINGFUL_PROFIT_BP,
        "shadow_exit_set_id": shadow_exit_set_id(shadow_set),
        "tick_count": len(ticks),
    }
    first_quote = _first_quote_at(ticks, float(fill_ts if filled else (signal_ts or 0.0)))
    record["entry_context"] = entry_context(
        signal_ts=signal_ts, fill_ts=fill_ts if filled else None, atr_pct=atr_pct,
        adx=ctx_in.get("adx"), rv_pct_rank=ctx_in.get("rv_pct_rank"), rv_label=ctx_in.get("rv_label"),
        regime=ctx_in.get("regime"),
        bid=ctx_in.get("bid", first_quote[1] if first_quote else None),
        ask=ctx_in.get("ask", first_quote[2] if first_quote else None),
        bid_qty=ctx_in.get("bid_qty"), ask_qty=ctx_in.get("ask_qty"),
    )
    if sign == 0 or not ticks:
        record["skip_reason"] = "NO_DIRECTION" if sign == 0 else "NO_PATH"
        return record
    atr_bp = None
    atr = _finite(atr_pct)
    if atr and atr > 0:
        atr_bp = atr * 100.0
    if filled:
        entry = float(entry_price)
        series, fallback = _pnl_series(ticks, sign, entry, float(fill_ts), float(fill_ts) + horizon_sec)
        hold_end_t = None if exit_ts is None else float(exit_ts) - float(fill_ts)
        record["path_side"] = "BID" if sign > 0 else "ASK"
        record["side_fallback_ticks"] = fallback
        record["horizons"] = horizon_extremes(series)
        record["extremes"] = _peak_trough(series)
        record["hold_extremes"] = _peak_trough(series, hold_end_t) if hold_end_t is not None else None
        record["path"] = minute_path(series)
        record["path"]["exit_minute"] = None if hold_end_t is None else int(max(0.0, hold_end_t) // 60)
        exits = evaluate_shadow_exits(series, shadow_set, atr_bp=atr_bp, horizon_sec=horizon_sec, fee_bp=fee_bp)
        if hold_end_t is not None and series:
            at_exit = [row for row in series if row[0] <= hold_end_t]
            if at_exit:
                exits.insert(0, {"id": ACTUAL_EXIT_ID, "kind": "ACTUAL", "net_bp": round(at_exit[-1][1] - fee_bp, 2),
                                 "exit_t_sec": round(hold_end_t, 1),
                                 "mfe_before_exit_bp": round(max(p for _t, p in at_exit), 2),
                                 "reason": exit_reason or "PAPER_EXIT",
                                 "triggered": True, "basis": "PATH_MARK_AT_PAPER_EXIT"})
        record["shadow_exits"] = exits
        record["market_context"] = market_context_ref(fill_ts, float(fill_ts) + min(
            horizon_sec, series[-1][0] if series else 0.0))
    else:
        record["shadow_exits"] = []
        record["market_context"] = market_context_ref(signal_ts, None if signal_ts is None else float(signal_ts) + horizon_sec)
    if signal_ts is not None:
        record["counterfactual"] = counterfactual_entries(
            ticks, sign=sign, signal_ts=float(signal_ts), limit_price=limit_price, horizon_sec=horizon_sec)
        taker = record["counterfactual"].get("taker_at_signal")
        if not filled and taker:
            entry = taker["entry_price"]
            series, _fb = _pnl_series(ticks, sign, entry, taker["entry_ts"], taker["entry_ts"] + horizon_sec)
            record["counterfactual"]["taker_shadow_exits"] = evaluate_shadow_exits(
                series, shadow_set, atr_bp=atr_bp, horizon_sec=horizon_sec, fee_bp=fee_bp)
    if extra:
        record["extra"] = dict(extra)
    return record


def _replay_start_ts(replay: Mapping[str, Any]) -> float | None:
    raw = replay.get("start_ts")
    number = _finite(raw)
    if number is not None:
        return number
    if isinstance(raw, str):
        from datetime import datetime  # noqa: PLC0415
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def record_from_replay(replay: Mapping[str, Any], *, shadow_set: Sequence[Mapping[str, Any]],
                       source: str = SOURCE_RUNTIME, meta: Mapping[str, Any] | None = None,
                       horizon_sec: float = DEFAULT_HORIZON_SEC, fee_bp: float = 0.0) -> dict[str, Any]:
    """Adapter for a ``signal_replay_v4`` row (runtime buffer snapshot or mirrored ledger row)."""
    meta = dict(meta or {})
    start_ts = _replay_start_ts(replay) if meta.get("start_ts") is None else _finite(meta.get("start_ts"))
    fill_t = _finite(replay.get("virtual_fill_t"))
    entry = _finite(replay.get("virtual_entry")) or _finite(replay.get("entry_price"))
    fill_ts = start_ts + fill_t if start_ts is not None and fill_t is not None and entry else None
    exit_t = _finite(replay.get("exit_t_rel"))
    exit_ts = start_ts + exit_t if start_ts is not None and exit_t is not None and exit_t >= 0 else None
    ticks = ticks_from_replay(replay)
    features = meta.get("entry_features") if isinstance(meta.get("entry_features"), Mapping) else {}
    depth = next((t for t in replay.get("ticks") or () if isinstance(t, dict)
                  and (_finite(t.get("observed_ts")) or 0) >= (fill_ts or start_ts or 0)
                  and t.get("depth_bid_qty") is not None), None)
    context = {
        "adx": meta.get("adx_at_signal", features.get("adx_3m", features.get("adx"))),
        "rv_pct_rank": features.get("rv_pct_rank"),
        "rv_label": features.get("rv_label"),
        "regime": meta.get("regime") or features.get("regime"),
        "bid_qty": None if depth is None else depth.get("depth_bid_qty"),
        "ask_qty": None if depth is None else depth.get("depth_ask_qty"),
    }
    if depth is not None:
        context["bid"], context["ask"] = depth.get("depth_best_bid"), depth.get("depth_best_ask")
    return build_record(
        source=source, trade_id=str(replay.get("trade_id") or ""), direction=replay.get("direction"),
        ticks=ticks, signal_ts=start_ts, fill_ts=fill_ts, entry_price=entry if fill_ts is not None else None,
        shadow_set=shadow_set, lane=replay.get("lane"), research_lane=meta.get("research_lane"),
        tile=meta.get("tile") or meta.get("research_lane"), policy_signature=meta.get("policy_signature"),
        collection_epoch_id=meta.get("collection_epoch_id") or replay.get("collection_epoch_id"),
        exit_ts=exit_ts, exit_reason=replay.get("exit_reason"), limit_price=meta.get("limit_price"),
        atr_pct=meta.get("atr14_pct_3m", features.get("atr14_pct_3m")), context=context,
        horizon_sec=horizon_sec, fee_bp=fee_bp,
        extra={"replay_complete": replay.get("replay_complete"),
               "replay_completion_reason": replay.get("replay_completion_reason")},
    )


# --------------------------------------------------------------------------
# Runtime recorder (bounded queue + daemon worker, never on the hot path)
# --------------------------------------------------------------------------
def _pct(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))], 3)


class ShadowExitRecorder:
    """Drains finished replay snapshots into ``shadow_exit_paths.jsonl``.

    ``submit`` is O(1) and never blocks: a full queue drops the item and counts
    it. The worker sleeps between items so it yields the GIL to the trading
    threads, and every failure is counted instead of raised.
    """

    def __init__(self, *, writer: Callable[[dict], bool], shadow_set_for: Callable[[Mapping[str, Any]], Sequence],
                 enabled: bool = True, max_queue: int = 256, dedupe: int = 4096, recent: int = 50,
                 yield_sec: float = 0.005, fee_bp: float = 0.0, clock: Callable[[], float] = time.time) -> None:
        self._writer, self._shadow_set_for = writer, shadow_set_for
        self.enabled, self.max_queue, self._yield_sec, self._fee_bp, self._clock = enabled, max_queue, yield_sec, fee_bp, clock
        self._queue: deque = deque()
        self._cv = threading.Condition(threading.Lock())
        self._seen: deque = deque(maxlen=dedupe)
        self._seen_set: set = set()
        self._recent: deque = deque(maxlen=recent)
        self._compute_ms: deque = deque(maxlen=512)
        self._cpu_ms: deque = deque(maxlen=512)
        self._submit_us: deque = deque(maxlen=512)
        self._agg: dict = {}
        self._thread: threading.Thread | None = None
        self.counters = {"submitted": 0, "written": 0, "dropped_full": 0, "dropped_duplicate": 0,
                         "skipped": 0, "errors": 0, "write_failures": 0}
        self.last_submit_ts = None
        self.last_write_ts = None
        self.last_error = None
        self.started_ts = None

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self.started_ts = self._clock()
        self._thread = threading.Thread(target=self._run, name="shadow-exit-recorder", daemon=True)
        self._thread.start()

    def submit(self, item: Mapping[str, Any]) -> bool:
        started = time.perf_counter()
        try:
            if not self.enabled:
                return False
            replay = item.get("replay") if isinstance(item, Mapping) else None
            key = str((replay or {}).get("trade_id") or "")
            with self._cv:
                if not key or key in self._seen_set:
                    self.counters["dropped_duplicate"] += 1
                    return False
                if len(self._queue) >= self.max_queue:
                    self.counters["dropped_full"] += 1
                    return False
                if len(self._seen) == self._seen.maxlen:
                    self._seen_set.discard(self._seen[0])
                self._seen.append(key)
                self._seen_set.add(key)
                self._queue.append(item)
                self.counters["submitted"] += 1
                self.last_submit_ts = self._clock()
                self._cv.notify()
            return True
        finally:
            self._submit_us.append((time.perf_counter() - started) * 1e6)

    def process(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        """Build and write one record (worker thread, or synchronously in tests)."""
        started, cpu_started = time.perf_counter(), time.thread_time()
        try:
            replay, meta = item.get("replay") or {}, item.get("meta") or {}
            shadow_set = tuple(self._shadow_set_for(meta) or ())
            record = record_from_replay(replay, shadow_set=shadow_set, source=SOURCE_RUNTIME, meta=meta,
                                        horizon_sec=float(meta.get("horizon_sec") or DEFAULT_HORIZON_SEC),
                                        fee_bp=self._fee_bp)
            if record.get("skip_reason"):
                self.counters["skipped"] += 1
            if self._writer(record):
                self.counters["written"] += 1
                self.last_write_ts = self._clock()
                self._remember(record)
            else:
                self.counters["write_failures"] += 1
            return record
        except Exception as exc:  # noqa: BLE001 - observation must never raise into the bot
            self.counters["errors"] += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            return None
        finally:
            self._compute_ms.append((time.perf_counter() - started) * 1e3)
            self._cpu_ms.append((time.thread_time() - cpu_started) * 1e3)

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait(timeout=30.0)
                item = self._queue.popleft()
            _COOP.deadline = time.perf_counter() + COOPERATIVE_SLICE_SEC
            self.process(item)
            time.sleep(self._yield_sec)

    def _remember(self, record: Mapping[str, Any]) -> None:
        exits = {row.get("id"): row.get("net_bp") for row in record.get("shadow_exits") or ()}
        self._recent.append({
            "trade_id": record.get("trade_id"), "tile": record.get("tile"), "lane": record.get("lane"),
            "direction": record.get("direction"), "filled": record.get("filled"), "fill_ts": record.get("fill_ts"),
            "mfe_bp": (record.get("extremes") or {}).get("mfe_bp"),
            "mae_bp": (record.get("extremes") or {}).get("mae_bp"), "shadow_exit_net_bp": exits,
        })
        if not record.get("filled"):
            return
        group = self._agg.setdefault(group_key(record), {})
        for row in record.get("shadow_exits") or ():
            net = row.get("net_bp")
            if net is None:
                continue
            cell = group.setdefault(row.get("id"), {"n": 0, "sum_bp": 0.0, "wins": 0, "givebacks": 0})
            cell["n"] += 1
            cell["sum_bp"] += float(net)
            cell["wins"] += 1 if net > 0 else 0
            cell["givebacks"] += 1 if is_giveback(row) else 0

    def status(self, now: float | None = None) -> dict[str, Any]:
        now = self._clock() if now is None else now
        with self._cv:
            depth = len(self._queue)
        alive = bool(self._thread and self._thread.is_alive())
        return {
            "schema": STATUS_SCHEMA, "version": RECORDER_VERSION, "enabled": self.enabled,
            "worker_alive": alive, "queue_depth": depth, "max_queue": self.max_queue,
            **dict(self.counters),
            "last_submit_age_sec": None if self.last_submit_ts is None else round(now - self.last_submit_ts, 1),
            "last_write_age_sec": None if self.last_write_ts is None else round(now - self.last_write_ts, 1),
            "last_error": self.last_error,
            "compute_ms_p50": _pct(list(self._compute_ms), 0.5),
            "compute_ms_p95": _pct(list(self._compute_ms), 0.95),
            "compute_ms_max": _pct(list(self._compute_ms), 1.0),
            "compute_cpu_ms_p50": _pct(list(self._cpu_ms), 0.5),
            "compute_cpu_ms_p95": _pct(list(self._cpu_ms), 0.95),
            "submit_us_p50": _pct(list(self._submit_us), 0.5),
            "submit_us_p99": _pct(list(self._submit_us), 0.99),
            "submit_us_max": _pct(list(self._submit_us), 1.0),
            "file": FILE_NAME, "record_schema": SCHEMA,
        }

    def aggregates(self) -> dict[str, Any]:
        out = {}
        for tile, cells in self._agg.items():
            out[tile] = {sid: {"n": c["n"], "ev_bp": round(c["sum_bp"] / c["n"], 2) if c["n"] else None,
                               "win_rate": round(c["wins"] / c["n"], 4) if c["n"] else None,
                               "giveback_rate": round(c["givebacks"] / c["n"], 4) if c["n"] else None}
                         for sid, c in cells.items()}
        return out

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._recent)[-max(0, int(limit)):]

    def public_summary(self, now: float | None = None) -> dict[str, Any]:
        """Redacted: health counters only, no trade ids, prices or per-trade outcomes."""
        status = self.status(now)
        keys = ("enabled", "worker_alive", "queue_depth", "submitted", "written", "dropped_full",
                "errors", "write_failures", "last_write_age_sec", "compute_ms_p95", "submit_us_p99")
        return {"schema": SUMMARY_SCHEMA, **{k: status.get(k) for k in keys}}
