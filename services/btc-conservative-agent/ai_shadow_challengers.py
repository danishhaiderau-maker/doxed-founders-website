"""Shadow-only challengers for the shared DeepSeek direction call.

Every function here is research evidence. Nothing in this module can create,
modify, cancel or gate a paper or live order, and nothing reads or writes tile
toggles, relay state or policy identity. The runtime records, for each shared
AI call, which side each challenger would have taken and later matures those
sides from the durable one-second microstructure tape so the analyzer can
compare the LLM against cheap deterministic baselines on identical timestamps.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import threading
from collections import deque
from typing import Any, Iterable, Mapping, Optional, Sequence

CALL_SCHEMA = "ai_shadow_challenger_call_v1"
MARKOUT_SCHEMA = "ai_shadow_challenger_markout_v1"
GEOMETRY_SCHEMA = "ai_shadow_challenger_geometry_v1"
COMPACT_PROMPT_SCHEMA = "ai_shadow_compact_prompt_v1"
INPUT_HEALTH_SCHEMA = "ai_input_health_v1"
CHALLENGER_FILE = "ai_shadow_challengers.jsonl"
COMPACT_PROMPT_FILE = "ai_shadow_compact_prompt.jsonl"
COMPACT_PROMPT_ID = "shadow_compact_forecast_v5_20261001"
RANDOM_SEED = "ai-shadow-random-v1"

MARKOUT_HORIZONS_SEC = (10, 60, 300, 900, 3600)
ENTRY_DELAY_SEC = 1
MAX_PRICE_LAG_SEC = 5
MAX_TAPE_GAP_SEC = 60
MATURITY_GRACE_SEC = 120
TAPE_RING_SECONDS = 3 * 3600 + 900

ABSTAIN_GAP_FLOOR = 5
# The model's success probabilities are calibrated below 0.5 for both sides
# (median ~0.42/0.40), so an absolute edge over a coin flip never fired. The
# side is the more likely direction when the two probabilities differ by at
# least COMPACT_MIN_GAP; rows carry COMPACT_SIDE_RULE so cohorts never mix rules.
COMPACT_MIN_GAP = 0.04
COMPACT_SIDE_RULE = "relative_gap_0.04_v2"

GEOMETRY_MODEL = "TILE_GEOMETRY_PROXY_V1"
GEOMETRY_FILL_WINDOW_SEC = 1200
GEOMETRY_NOTE = (
    "Proxy, not tile P&L: static limit at the registry entry offset (chase not "
    "modelled), filled on a BBO touch within 20 min, then first touch of "
    "target vs stop from registry ATR multiples, time exit at max_duration."
)

LONG, SHORT, NONE = "LONG", "SHORT", "NONE"
CHALLENGERS = (
    "llm_score_led",
    "llm_abstain_respecting",
    "rule_vote",
    "inverted_ai",
    "ofi_1m",
    "contrarian_5m",
    "random",
    "compact_v5",
    "leader_10s",
)
COMPACT_DRIVERS = frozenset({"TREND", "FLOW", "LOCATION", "DERIVS", "VOL", "STALE", "CONFLICT"})

# A field that legitimately holds one value for an hour is not a dead input.
DEAD_INPUT_CONSTANT_EXEMPT = (
    "schema",
    "as_of_utc",
    "shared_ai_call_id",
    "prompt_id",
    "data_quality",
    "derivatives.",
    "raw.multi_tf.",
    "raw.ema_alignment.",
    "raw.sr_bias",
    "derived.",
)
# Fields the prompt audit found dead; checked for constancy even when integer.
DEAD_INPUT_CRITICAL = (
    "raw.ret_1m_bp",
    "raw.ret_5m_bp",
    "raw.stoch_rsi_k_3m",
    "raw.stoch_rsi_d_3m",
    "raw.closed_3m_ts",
    "raw.rsi_3m",
    "raw.atr14_pct_3m",
)


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


def _side_from_sign(value: Optional[float]) -> str:
    if value is None or value == 0:
        return NONE
    return LONG if value > 0 else SHORT


def _opposite(side: str) -> str:
    return {LONG: SHORT, SHORT: LONG}.get(side, NONE)


def side_sign(side: str) -> int:
    return {LONG: 1, SHORT: -1}.get(str(side or "").upper(), 0)


# --------------------------------------------------------------------------
# One-second tape ring
# --------------------------------------------------------------------------
class TapeRing:
    """Bounded, monotonic in-memory view of fresh 1s microstructure buckets."""

    def __init__(self, max_seconds: int = TAPE_RING_SECONDS) -> None:
        self._lock = threading.Lock()
        self._ts: deque = deque(maxlen=int(max_seconds))
        self._rows: deque = deque(maxlen=int(max_seconds))

    def append_bucket(self, row: Mapping[str, Any]) -> bool:
        if not isinstance(row, Mapping):
            return False
        if row.get("fresh") is not True or row.get("valid_bbo") is not True:
            return False
        try:
            ts = int(row.get("bucket_ts"))
        except (TypeError, ValueError):
            return False
        bid, ask = _finite(row.get("bid")), _finite(row.get("ask"))
        if not bid or not ask or bid <= 0 or ask < bid:
            return False
        item = (
            bid,
            ask,
            _finite(row.get("bid_qty")) or 0.0,
            _finite(row.get("ask_qty")) or 0.0,
            _finite(row.get("buy_qty")) or 0.0,
            _finite(row.get("sell_qty")) or 0.0,
        )
        with self._lock:
            if self._ts and ts <= self._ts[-1]:
                return False
            self._ts.append(ts)
            self._rows.append(item)
        return True

    def hydrate(self, rows: Iterable[Mapping[str, Any]]) -> int:
        return sum(1 for row in rows if self.append_bucket(row))

    def merge_history(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Prepend durable history older than the live head (boot hydration)."""
        staging = TapeRing(self._ts.maxlen)
        staging.hydrate(sorted(
            (r for r in rows if isinstance(r, Mapping)),
            key=lambda r: _finite(r.get("bucket_ts")) or 0.0,
        ))
        old_ts, old_rows = staging.snapshot()
        with self._lock:
            first_live = self._ts[0] if self._ts else None
            keep = [i for i, ts in enumerate(old_ts) if first_live is None or ts < first_live]
            merged_ts = [old_ts[i] for i in keep] + list(self._ts)
            merged_rows = [old_rows[i] for i in keep] + list(self._rows)
            self._ts.clear()
            self._rows.clear()
            self._ts.extend(merged_ts)
            self._rows.extend(merged_rows)
        return len(keep)

    def snapshot(self) -> tuple:
        with self._lock:
            return list(self._ts), list(self._rows)

    def latest_ts(self) -> Optional[int]:
        with self._lock:
            return self._ts[-1] if self._ts else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._ts)


def _index_at_or_before(ts_list: Sequence[int], t: float) -> int:
    return bisect.bisect_right(ts_list, t) - 1


def quote_at(ts_list, rows, t: float, max_lag: float = MAX_PRICE_LAG_SEC):
    """Latest fresh quote at or before ``t`` within ``max_lag`` seconds."""
    idx = _index_at_or_before(ts_list, t)
    if idx < 0 or t - ts_list[idx] > max_lag:
        return None
    return rows[idx]


def mid_at(ts_list, rows, t: float, max_lag: float = MAX_PRICE_LAG_SEC) -> Optional[float]:
    quote = quote_at(ts_list, rows, t, max_lag)
    return None if quote is None else (quote[0] + quote[1]) / 2.0


def max_gap(ts_list, start: float, end: float) -> Optional[float]:
    """Largest uncovered stretch of seconds inside [start, end]."""
    lo = bisect.bisect_left(ts_list, start)
    hi = bisect.bisect_right(ts_list, end)
    inside = ts_list[lo:hi]
    if not inside:
        return None
    gap = max(inside[0] - start, end - inside[-1])
    for prev, cur in zip(inside, inside[1:]):
        gap = max(gap, cur - prev)
    return float(gap)


def _flow(ts_list, rows, now: float, window: int) -> Optional[float]:
    lo = bisect.bisect_right(ts_list, now - window)
    hi = bisect.bisect_right(ts_list, now)
    buy = sum(rows[i][4] for i in range(lo, hi))
    sell = sum(rows[i][5] for i in range(lo, hi))
    total = buy + sell
    return None if total <= 0 else (buy - sell) / total


def _rv_bp(ts_list, rows, now: float, window: int = 900, step: int = 10) -> Optional[float]:
    mids = []
    t = now - window
    while t <= now:
        mids.append(mid_at(ts_list, rows, t, max_lag=step))
        t += step
    rets = [
        math.log(b / a) * 1e4
        for a, b in zip(mids, mids[1:])
        if a is not None and b is not None and a > 0 and b > 0
    ]
    if len(rets) < 10:
        return None
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))


def tape_features(ring: TapeRing, now: float) -> dict:
    """Causal tape facts at ``now``; every value is null when unobservable."""
    ts_list, rows = ring.snapshot()
    out = {
        "source": "market_microstructure_1s_ring",
        "as_of_ts": round(float(now), 3),
        "tape_age_s": None,
        "ret_1m_bp": None, "ret_5m_bp": None, "ret_15m_bp": None, "ret_60m_bp": None,
        "flow_1m": None, "flow_5m": None,
        "rv15_bp_10s": None, "spread_bp": None, "l1_imbalance": None,
        "ret_1m": None, "ret_5m": None,
    }
    if not ts_list:
        return out
    out["tape_age_s"] = round(float(now) - ts_list[-1], 3)
    quote = quote_at(ts_list, rows, now)
    if quote is None:
        return out
    bid, ask, bid_qty, ask_qty = quote[0], quote[1], quote[2], quote[3]
    mid_now = (bid + ask) / 2.0
    out["spread_bp"] = _round((ask - bid) / mid_now * 1e4, 3)
    depth = bid_qty + ask_qty
    out["l1_imbalance"] = _round((bid_qty - ask_qty) / depth, 4) if depth > 0 else None
    for window, key in ((60, "1m"), (300, "5m"), (900, "15m"), (3600, "60m")):
        then = mid_at(ts_list, rows, now - window)
        if then:
            ret = mid_now / then - 1.0
            out[f"ret_{key}_bp"] = _round(ret * 1e4, 3)
            if key in ("1m", "5m"):
                out[f"ret_{key}"] = _round(ret, 8)
    out["flow_1m"] = _round(_flow(ts_list, rows, now, 60), 4)
    out["flow_5m"] = _round(_flow(ts_list, rows, now, 300), 4)
    out["rv15_bp_10s"] = _round(_rv_bp(ts_list, rows, now), 4)
    return out


# --------------------------------------------------------------------------
# Challenger sides (causal, computed at the call)
# --------------------------------------------------------------------------
def _score_pair(ai_result: Mapping[str, Any]) -> tuple:
    factors = ai_result.get("factors") if isinstance(ai_result.get("factors"), Mapping) else {}
    long_score = _finite(ai_result.get("long_score"))
    short_score = _finite(ai_result.get("short_score"))
    if long_score is None:
        long_score = _finite(factors.get("long_score"))
    if short_score is None:
        short_score = _finite(factors.get("short_score"))
    return long_score, short_score


def rule_vote(ctx: Mapping[str, Any]) -> dict:
    """4-feature majority of the prompt's own trend labels (the consult's control)."""
    mc = ctx.get("market_context") if isinstance(ctx.get("market_context"), Mapping) else {}
    mtf = (mc.get("multi_tf") or {}).get("agreement") if isinstance(mc.get("multi_tf"), Mapping) else None
    ema = mc.get("ema_alignment") if isinstance(mc.get("ema_alignment"), Mapping) else {}
    health = ctx.get("trend_health") if isinstance(ctx.get("trend_health"), Mapping) else {}
    bull = _finite(health.get("bull_score")) or 0.0
    bear = _finite(health.get("bear_score")) or 0.0
    votes = {
        "mtf_agreement": {"BULL_ALIGNED": 1, "BEAR_ALIGNED": -1}.get(str(mtf or "").upper(), 0),
        "ema_stack": 1 if ema.get("stack_bull") is True else -1 if ema.get("stack_bear") is True else 0,
        "trend_health": (bull > bear) - (bull < bear),
        "sr_bias": {"LONG_PREFERRED": 1, "SHORT_PREFERRED": -1}.get(str(ctx.get("sr_bias") or "").upper(), 0),
    }
    return {"side": _side_from_sign(sum(votes.values())), "votes": votes}


def seeded_random_side(call_id: str) -> str:
    digest = hashlib.sha256(f"{RANDOM_SEED}:{call_id}".encode("utf-8")).hexdigest()
    return LONG if int(digest[:8], 16) % 2 == 0 else SHORT


def compact_side(parsed: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(parsed, Mapping) or parsed.get("parse_status") != "OK" or parsed.get("abstain"):
        return NONE
    p_long, p_short = parsed.get("p_long_success"), parsed.get("p_short_success")
    if p_long is None or p_short is None or p_long == p_short:
        return NONE
    side = LONG if p_long > p_short else SHORT
    return side if abs(p_long - p_short) >= COMPACT_MIN_GAP - 1e-9 else NONE


def compact_summary(parsed: Optional[Mapping[str, Any]]) -> dict:
    """Raw compact probabilities and the rule that turned them into a side."""
    parsed = parsed if isinstance(parsed, Mapping) else {}
    p_long, p_short = _finite(parsed.get("p_long_success")), _finite(parsed.get("p_short_success"))
    return {
        "side_rule": COMPACT_SIDE_RULE,
        "min_gap": COMPACT_MIN_GAP,
        "parse_status": parsed.get("parse_status"),
        "p_long_success": p_long,
        "p_short_success": p_short,
        "gap": None if p_long is None or p_short is None else round(p_long - p_short, 4),
        "side": compact_side(parsed),
    }


def compute_challenger_sides(ctx: Mapping[str, Any], ai_result: Mapping[str, Any],
                             tape: Mapping[str, Any], call_id: str,
                             compact: Optional[Mapping[str, Any]] = None,
                             leader: Optional[Mapping[str, Any]] = None) -> dict:
    ai_ok = not ai_result.get("ai_error")
    long_score, short_score = _score_pair(ai_result)
    gap = None
    score_led = NONE
    if ai_ok and long_score is not None and short_score is not None:
        gap = long_score - short_score
        score_led = _side_from_sign(gap)
    raw_direction = str(ai_result.get("raw_direction") or ai_result.get("direction") or "").upper() or None
    abstained = bool(
        not ai_ok or raw_direction == "NO_TRADE" or gap is None or abs(gap) < ABSTAIN_GAP_FLOOR
    )
    vote = rule_vote(ctx)
    sides = {
        "llm_score_led": score_led,
        "llm_abstain_respecting": NONE if abstained else score_led,
        "rule_vote": vote["side"],
        "inverted_ai": _opposite(score_led),
        "ofi_1m": _side_from_sign(_finite(tape.get("flow_1m"))),
        "contrarian_5m": _opposite(_side_from_sign(_finite(tape.get("ret_5m_bp")))),
        "random": seeded_random_side(call_id),
        "compact_v5": compact_side(compact),
        # Sign of the cross-venue leader's 10 s mid return when it moved >= 2 bp
        # (cross_venue_tape.leader_features); NONE when absent or below threshold.
        "leader_10s": str((leader or {}).get("side") or NONE),
    }
    return {
        "sides": sides,
        "llm": {
            "long_score": long_score,
            "short_score": short_score,
            "score_gap": gap,
            "raw_direction": raw_direction,
            "decision": ai_result.get("decision"),
            "abstained": abstained,
            "abstain_reason": (
                "AI_ERROR" if not ai_ok
                else "NO_TRADE" if raw_direction == "NO_TRADE"
                else "SCORE_GAP_BELOW_5" if abstained else None
            ),
            "ai_error": not ai_ok,
        },
        "rule_votes": vote["votes"],
        "compact": compact_summary(compact),
    }


# --------------------------------------------------------------------------
# Tile geometry proxy
# --------------------------------------------------------------------------
def tile_geometry_specs(manifest: Iterable[Mapping[str, Any]], leverage: float) -> list:
    """Registry-derived triple-barrier proxies; one per active tile in order."""
    lev = _finite(leverage) or 0.0
    specs = []
    for tile in manifest or ():
        entry = tile.get("entry_policy") or {}
        exit_ = tile.get("exit_policy") or {}
        offset_pct = _finite(entry.get("offset_pct"))
        if offset_pct is None:
            continue
        partials = exit_.get("partial_take_profits") or []
        first_partial = _finite(partials[0][0]) if partials and partials[0] else None
        target_k = (
            _finite(exit_.get("atr_tp_k"))
            or first_partial
            or _finite(exit_.get("trail_activation_atr_k"))
            or 1.0
        )
        hard_margin = _finite(exit_.get("hard_stop_margin_pct"))
        specs.append({
            "lane": str(tile.get("lane")),
            "entry_mode": entry.get("mode"),
            "offset_pct": offset_pct,
            "stop_atr_k": _finite(exit_.get("initial_stop_atr_k")),
            "hard_stop_pct": None if not hard_margin or lev <= 0 else round(hard_margin / lev, 6),
            "target_atr_k": target_k,
            "max_duration_sec": int(_finite(exit_.get("max_duration_sec")) or 7200),
        })
    return specs


def simulate_geometry(ts_list, rows, *, decision_ts: float, side: str, price: float,
                      atr_pct: Optional[float], spec: Mapping[str, Any]) -> dict:
    sign = side_sign(side)
    atr_frac = None if atr_pct is None else atr_pct / 100.0
    base = {"lane": spec["lane"], "side": side}
    if not sign or not price or atr_frac is None or atr_frac <= 0:
        return {**base, "result": "UNAVAILABLE_ATR_OR_PRICE"}
    offset = spec["offset_pct"] / 100.0
    limit = price * (1.0 - offset) if sign > 0 else price * (1.0 + offset)
    fill_end = decision_ts + GEOMETRY_FILL_WINDOW_SEC
    fill_gap = max_gap(ts_list, decision_ts, fill_end)
    lo = bisect.bisect_right(ts_list, decision_ts)
    hi = bisect.bisect_right(ts_list, fill_end)
    fill_idx = None
    for i in range(lo, hi):
        bid, ask = rows[i][0], rows[i][1]
        if (sign > 0 and ask <= limit) or (sign < 0 and bid >= limit):
            fill_idx = i
            break
    if fill_idx is None:
        if fill_gap is None or fill_gap > MAX_TAPE_GAP_SEC:
            return {**base, "result": "TAPE_GAP", "phase": "FILL_WINDOW", "max_gap_s": fill_gap}
        return {**base, "result": "NO_FILL", "limit": round(limit, 2)}
    fill_ts = ts_list[fill_idx]
    stop_dist = None
    if spec.get("stop_atr_k"):
        stop_dist = spec["stop_atr_k"] * atr_frac * limit
    if spec.get("hard_stop_pct"):
        hard = spec["hard_stop_pct"] / 100.0 * limit
        stop_dist = hard if stop_dist is None else min(stop_dist, hard)
    target_dist = spec["target_atr_k"] * atr_frac * limit
    exit_end = fill_ts + spec["max_duration_sec"]
    hold_gap = max_gap(ts_list, fill_ts, exit_end)
    if hold_gap is None or hold_gap > MAX_TAPE_GAP_SEC:
        return {**base, "result": "TAPE_GAP", "phase": "HOLD", "max_gap_s": hold_gap,
                "fill_ts": fill_ts, "limit": round(limit, 2)}
    result, exit_price, exit_ts = "TIME", None, None
    end_idx = bisect.bisect_right(ts_list, exit_end)
    for i in range(fill_idx + 1, end_idx):
        bid, ask = rows[i][0], rows[i][1]
        mark = bid if sign > 0 else ask
        move = (mark - limit) * sign
        if stop_dist is not None and move <= -stop_dist:
            result, exit_price, exit_ts = "STOP", mark, ts_list[i]
            break
        if move >= target_dist:
            result, exit_price, exit_ts = "TARGET", mark, ts_list[i]
            break
    if exit_price is None:
        last = rows[end_idx - 1] if end_idx > 0 else rows[fill_idx]
        exit_price = last[0] if sign > 0 else last[1]
        exit_ts = ts_list[end_idx - 1] if end_idx > 0 else fill_ts
    return {
        **base,
        "result": result,
        "limit": round(limit, 2),
        "fill_delay_s": fill_ts - decision_ts,
        "hold_s": exit_ts - fill_ts,
        "result_bp": round((exit_price - limit) / limit * 1e4 * sign, 3),
        "target_bp": round(target_dist / limit * 1e4, 3),
        "stop_bp": None if stop_dist is None else round(stop_dist / limit * 1e4, 3),
    }


# --------------------------------------------------------------------------
# Pending book and maturation
# --------------------------------------------------------------------------
class ChallengerBook:
    """Calls awaiting markout/geometry maturation from the tape ring."""

    def __init__(self, max_pending: int = 256) -> None:
        self._lock = threading.Lock()
        self._pending: dict = {}
        self._max_pending = int(max_pending)
        self.stats = {"registered": 0, "markouts_written": 0, "geometry_written": 0, "evicted": 0}

    def register(self, call_row: Mapping[str, Any]) -> bool:
        call_id = str(call_row.get("shared_ai_call_id") or "")
        decision_ts = _finite(call_row.get("decision_ts"))
        if not call_id or decision_ts is None:
            return False
        with self._lock:
            if call_id in self._pending:
                return False
            if len(self._pending) >= self._max_pending:
                oldest = min(self._pending, key=lambda k: self._pending[k]["decision_ts"])
                self._pending.pop(oldest, None)
                self.stats["evicted"] += 1
            self._pending[call_id] = {
                "decision_ts": decision_ts,
                "price": _finite(call_row.get("decision_price")),
                "atr_pct": _finite(call_row.get("atr14_pct_3m")),
                "geometry_specs": list(call_row.get("geometry_specs") or []),
                "done_horizons": set(call_row.get("_done_horizons") or ()),
                "geometry_done": bool(call_row.get("_geometry_done")),
                "epoch_id": call_row.get("epoch_id"),
                "prompt_id": call_row.get("prompt_id"),
            }
            self.stats["registered"] += 1
        return True

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def mature(self, ring: TapeRing, now: float) -> list:
        ts_list, rows = ring.snapshot()
        latest = ts_list[-1] if ts_list else None
        out = []
        with self._lock:
            items = list(self._pending.items())
        for call_id, item in items:
            d = item["decision_ts"]
            t0 = d + ENTRY_DELAY_SEC
            for h in MARKOUT_HORIZONS_SEC:
                if h in item["done_horizons"]:
                    continue
                th = t0 + h
                covered = latest is not None and latest >= th
                if not covered and now < th + MATURITY_GRACE_SEC:
                    continue
                out.append(_markout_row(call_id, item, h, ts_list, rows, t0, th))
                item["done_horizons"].add(h)
            if not item["geometry_done"]:
                horizon_end = d + GEOMETRY_FILL_WINDOW_SEC + max(
                    [s.get("max_duration_sec", 7200) for s in item["geometry_specs"]] or [0])
                covered = latest is not None and latest >= horizon_end
                if covered or now >= horizon_end + MATURITY_GRACE_SEC:
                    out.append(_geometry_row(call_id, item, ts_list, rows))
                    item["geometry_done"] = True
            if item["geometry_done"] and len(item["done_horizons"]) == len(MARKOUT_HORIZONS_SEC):
                with self._lock:
                    self._pending.pop(call_id, None)
        with self._lock:
            self.stats["markouts_written"] += sum(1 for r in out if r["schema"] == MARKOUT_SCHEMA)
            self.stats["geometry_written"] += sum(1 for r in out if r["schema"] == GEOMETRY_SCHEMA)
        return out


def _markout_row(call_id, item, h, ts_list, rows, t0, th) -> dict:
    q0 = quote_at(ts_list, rows, t0)
    qh = quote_at(ts_list, rows, th)
    gap = max_gap(ts_list, t0, th)
    row = {
        "schema": MARKOUT_SCHEMA,
        "row_kind": "MARKOUT",
        "shared_ai_call_id": call_id,
        "epoch_id": item.get("epoch_id"),
        "prompt_id": item.get("prompt_id"),
        "decision_ts": item["decision_ts"],
        "horizon_sec": h,
        "tape_ok": False,
        "maturity": "TAPE_GAP",
        "mid_ret_bp": None,
        "spread_in_bp": None,
        "spread_out_bp": None,
        "max_gap_s": gap,
    }
    if q0 is None or qh is None or gap is None or gap > max(MAX_PRICE_LAG_SEC, min(MAX_TAPE_GAP_SEC, h)):
        return row
    m0 = (q0[0] + q0[1]) / 2.0
    mh = (qh[0] + qh[1]) / 2.0
    row.update({
        "tape_ok": True,
        "maturity": "MATURED",
        "mid_ret_bp": round((mh / m0 - 1.0) * 1e4, 4),
        "spread_in_bp": round((q0[1] - q0[0]) / m0 * 1e4, 4),
        "spread_out_bp": round((qh[1] - qh[0]) / mh * 1e4, 4),
    })
    return row


def _geometry_row(call_id, item, ts_list, rows) -> dict:
    results = []
    for spec in item["geometry_specs"]:
        for side in (LONG, SHORT):
            results.append(simulate_geometry(
                ts_list, rows, decision_ts=item["decision_ts"], side=side,
                price=item["price"], atr_pct=item["atr_pct"], spec=spec,
            ))
    return {
        "schema": GEOMETRY_SCHEMA,
        "row_kind": "GEOMETRY",
        "shared_ai_call_id": call_id,
        "epoch_id": item.get("epoch_id"),
        "prompt_id": item.get("prompt_id"),
        "decision_ts": item["decision_ts"],
        "geometry_model": GEOMETRY_MODEL,
        "geometry_note": GEOMETRY_NOTE,
        "results": results,
    }


def pending_calls_from_rows(rows: Iterable[Mapping[str, Any]], now: float) -> list:
    """Rebuild unmatured calls after a restart from the challenger journal tail."""
    calls, done_h, geo = {}, {}, set()
    for row in rows:
        call_id = str(row.get("shared_ai_call_id") or "")
        if not call_id:
            continue
        kind = row.get("row_kind")
        if kind == "CALL":
            calls[call_id] = dict(row)
        elif kind == "MARKOUT":
            done_h.setdefault(call_id, set()).add(row.get("horizon_sec"))
        elif kind == "GEOMETRY":
            geo.add(call_id)
    out = []
    for call_id, row in calls.items():
        decision_ts = _finite(row.get("decision_ts"))
        if decision_ts is None or now - decision_ts > TAPE_RING_SECONDS:
            continue
        horizons = done_h.get(call_id, set())
        if call_id in geo and len(horizons) >= len(MARKOUT_HORIZONS_SEC):
            continue
        row["_done_horizons"] = horizons
        row["_geometry_done"] = call_id in geo
        out.append(row)
    return out


# --------------------------------------------------------------------------
# Shadow compact prompt (logged only; never gates orders)
# --------------------------------------------------------------------------
COMPACT_SYSTEM_PROMPT = (
    "You are a calibrated forecaster for BTC-USD perpetual, 30-minute horizon.\n"
    "You receive precomputed, normalized facts. Do not recompute them.\n"
    "Output a probability, not an opinion. If data is stale or the facts do not\n"
    "favour either side, abstain. Most 30-minute BTC outcomes are close to a coin\n"
    "flip; probabilities far from 0.50 must be rare and justified by at least two\n"
    "independent fact groups (trend, flow, location, derivatives).\n"
    "Respond with JSON only."
)

COMPACT_USER_TEMPLATE = (
    "QUESTION: If a LONG limit fills {entry_offset_bp} bp below now (or a SHORT fills "
    "{entry_offset_bp} bp above now) within {fill_window_min} min, what is the probability that "
    "price then moves +{target_atr} ATR in the trade direction before -{stop_atr} ATR "
    "against it, within {horizon_min} min? Answer for both sides.\n\n"
    "FACTS (as_of {as_of_utc}; stale={stale}; max_age_s={max_age_s})\n"
    "volatility: atr3m={atr3m_bp}bp rv15={rv15_bp}bp (std of 10s returns)\n"
    "returns_bp: 1m={ret_1m_bp} 5m={ret_5m_bp} 15m={ret_15m_bp} 60m={ret_60m_bp}  "
    "(z: {z1},{z5},{z15},{z60})\n"
    "trend: score={trend_score} (-3..+3 over 15m/1h/4h labels) adx15m={adx15m}\n"
    "location: to_swing_high={dist_high_atr} ATR, to_swing_low={dist_low_atr} ATR, "
    "donchian_3m={donchian_loc_3m}\n"
    "flow: flow_1m={flow_1m} flow_5m={flow_5m} (-1 sell..+1 buy) l1_imb={l1_imbalance} "
    "vol_ratio={volume_ratio}\n"
    "derivs: funding={funding_bp_8h}bp/8h (+ = longs pay) next_in={minutes_to_funding}min "
    "oi_1h={oi_change_1h_pct}% basis={basis_bp}bp\n"
    "micro: spread={spread_bp}bp session={session}\n\n"
    "Return:\n"
    '{{"p_long_success": 0.00-1.00, "p_short_success": 0.00-1.00, '
    '"abstain": true|false, '
    '"drivers": ["up to 3 of: TREND, FLOW, LOCATION, DERIVS, VOL, STALE, CONFLICT"]}}'
)

COMPACT_GEOMETRY = {
    "entry_offset_bp": 30,
    "fill_window_min": 20,
    "target_atr": 1.0,
    "stop_atr": 1.5,
    "horizon_min": 30,
}
COMPACT_QUESTION_LANE = "compact_v5_question"


def compact_question_spec() -> dict:
    """Geometry spec that matures the compact prompt's exact question for Brier scoring."""
    return {
        "lane": COMPACT_QUESTION_LANE,
        "offset_pct": COMPACT_GEOMETRY["entry_offset_bp"] / 100.0,
        "stop_atr_k": COMPACT_GEOMETRY["stop_atr"],
        "hard_stop_pct": None,
        "target_atr_k": COMPACT_GEOMETRY["target_atr"],
        "max_duration_sec": COMPACT_GEOMETRY["horizon_min"] * 60,
    }


class OpenInterestHistory:
    """Tracks sampled open interest so the 1h change can be stated honestly."""

    def __init__(self, max_samples: int = 256) -> None:
        self._lock = threading.Lock()
        self._rows: deque = deque(maxlen=max_samples)

    def observe(self, ts: float, oi: Optional[float]) -> Optional[float]:
        oi = _finite(oi)
        with self._lock:
            if oi is not None and oi > 0:
                self._rows.append((float(ts), oi))
            if oi is None:
                return None
            past = [row for row in self._rows if 3300 <= ts - row[0] <= 5400]
        if not past:
            return None
        base = past[-1][1]
        return round((oi / base - 1.0) * 100.0, 4) if base > 0 else None


def _z(ret_bp: Optional[float], rv10_bp: Optional[float], seconds: int) -> Optional[float]:
    if ret_bp is None or not rv10_bp:
        return None
    return round(ret_bp / (rv10_bp * math.sqrt(seconds / 10.0)), 3)


def _trend_score(ctx: Mapping[str, Any]) -> Optional[int]:
    mc = ctx.get("market_context") if isinstance(ctx.get("market_context"), Mapping) else {}
    trends = ((mc.get("multi_tf") or {}).get("trends") or {}) if isinstance(mc.get("multi_tf"), Mapping) else {}
    if not trends:
        return None
    score = 0
    for tf in ("15m", "1h", "4h"):
        label = str(trends.get(tf) or "").upper()
        score += 1 if label.startswith("BULL") else -1 if label.startswith("BEAR") else 0
    return score


def build_compact_facts(ctx: Mapping[str, Any], tape: Mapping[str, Any], *, now_ts: float,
                        as_of_utc: str, oi_change_1h_pct: Optional[float] = None) -> dict:
    cycle = ctx.get("cycle_3m_universe") or ctx.get("exhaustion_3m") or {}
    cycle = cycle if isinstance(cycle, Mapping) else {}
    mc = ctx.get("market_context") if isinstance(ctx.get("market_context"), Mapping) else {}
    funding = ctx.get("funding") if isinstance(ctx.get("funding"), Mapping) else {}
    price = _finite(ctx.get("price"))
    atr_pct = _finite(cycle.get("atr14_pct_3m"))
    atr_price = None if price is None or atr_pct is None else price * atr_pct / 100.0
    high, low = _finite(ctx.get("recent_high")), _finite(ctx.get("recent_low"))
    rate = _finite(funding.get("rate"))
    mark, index = _finite(funding.get("mark_price")), _finite(funding.get("index_price"))
    next_time = _finite(funding.get("next_time"))
    rv = _finite(tape.get("rv15_bp_10s"))
    closed_3m = _finite(cycle.get("cycle_bucket"))
    ages = {
        "tape_age_s": _finite(tape.get("tape_age_s")),
        "closed_3m_age_s": None if closed_3m is None else round(now_ts - closed_3m, 1),
        "funding_age_s": None if _finite(funding.get("updated_ts")) is None
        else round(now_ts - float(funding.get("updated_ts")), 1),
    }
    stale = bool(
        ages["tape_age_s"] is None or ages["tape_age_s"] > 5
        or ages["closed_3m_age_s"] is None or ages["closed_3m_age_s"] > 400
    )
    trend_strength = mc.get("trend_strength") if isinstance(mc.get("trend_strength"), Mapping) else {}
    facts = {
        "as_of_utc": as_of_utc,
        "stale": stale,
        "max_age_s": max([v for v in ages.values() if v is not None], default=None),
        **ages,
        "atr3m_bp": _round(None if atr_pct is None else atr_pct * 100.0, 3),
        "rv15_bp": _round(rv, 3),
        "ret_1m_bp": tape.get("ret_1m_bp"),
        "ret_5m_bp": tape.get("ret_5m_bp"),
        "ret_15m_bp": tape.get("ret_15m_bp"),
        "ret_60m_bp": tape.get("ret_60m_bp"),
        "z1": _z(_finite(tape.get("ret_1m_bp")), rv, 60),
        "z5": _z(_finite(tape.get("ret_5m_bp")), rv, 300),
        "z15": _z(_finite(tape.get("ret_15m_bp")), rv, 900),
        "z60": _z(_finite(tape.get("ret_60m_bp")), rv, 3600),
        "trend_score": _trend_score(ctx),
        "adx15m": _round(_finite(trend_strength.get("adx")) or _finite(ctx.get("adx")), 2),
        "dist_high_atr": None if not atr_price or high is None or price is None
        else round((high - price) / atr_price, 3),
        "dist_low_atr": None if not atr_price or low is None or price is None
        else round((price - low) / atr_price, 3),
        "donchian_loc_3m": _round(_finite(cycle.get("donchian_loc_3m")), 3),
        "flow_1m": tape.get("flow_1m"),
        "flow_5m": tape.get("flow_5m"),
        "l1_imbalance": tape.get("l1_imbalance"),
        "volume_ratio": _round(_finite(ctx.get("volume_ratio")), 3),
        "funding_bp_8h": _round(None if rate is None else rate * 1e4, 4),
        "minutes_to_funding": None if next_time is None else round((next_time - now_ts) / 60.0, 1),
        "oi_change_1h_pct": oi_change_1h_pct,
        "basis_bp": None if not mark or not index else round((mark - index) / index * 1e4, 3),
        "spread_bp": tape.get("spread_bp"),
        "session": cycle.get("session_utc"),
    }
    return facts


def render_compact_messages(facts: Mapping[str, Any]) -> list:
    values = {k: ("null" if v is None else v) for k, v in facts.items()}
    user = COMPACT_USER_TEMPLATE.format(**COMPACT_GEOMETRY, **values)
    return [
        {"role": "system", "content": COMPACT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_compact_response(text: str) -> dict:
    out = {"parse_status": "INVALID_JSON", "p_long_success": None, "p_short_success": None,
           "abstain": None, "drivers": []}
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        blob = json.loads(text[start:end])
    except (ValueError, TypeError, AttributeError):
        return out
    if not isinstance(blob, Mapping):
        return out
    p_long, p_short = _finite(blob.get("p_long_success")), _finite(blob.get("p_short_success"))
    abstain = blob.get("abstain")
    drivers = [str(d).upper() for d in (blob.get("drivers") or []) if isinstance(d, str)]
    out.update({
        "p_long_success": p_long,
        "p_short_success": p_short,
        "abstain": abstain if isinstance(abstain, bool) else None,
        "drivers": [d for d in drivers if d in COMPACT_DRIVERS][:3],
    })
    if (p_long is None or p_short is None or not 0.0 <= p_long <= 1.0
            or not 0.0 <= p_short <= 1.0 or out["abstain"] is None):
        out["parse_status"] = "OUT_OF_RANGE_OR_MISSING"
        return out
    out["parse_status"] = "OK"
    return out


class CompactPromptBudget:
    """Minimum spacing plus a UTC-day cap for the shadow model call."""

    def __init__(self, min_interval_sec: float, daily_cap: int) -> None:
        self._lock = threading.Lock()
        self.min_interval_sec = float(min_interval_sec)
        self.daily_cap = int(daily_cap)
        self._last_ts = 0.0
        self._day = None
        self._count = 0

    def acquire(self, now: float) -> tuple:
        day = int(now // 86400)
        with self._lock:
            if day != self._day:
                self._day, self._count = day, 0
            if now - self._last_ts < self.min_interval_sec:
                return False, "MIN_INTERVAL"
            if self._count >= self.daily_cap:
                return False, "DAILY_CAP"
            self._last_ts = now
            self._count += 1
            return True, None

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls_today": self._count, "daily_cap": self.daily_cap,
                    "min_interval_sec": self.min_interval_sec}


# --------------------------------------------------------------------------
# Dead-input detector
# --------------------------------------------------------------------------
def flatten_payload(payload: Any, prefix: str = "") -> dict:
    out = {}
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                out.update(flatten_payload(value, path))
            elif isinstance(value, (list, tuple)):
                continue
            else:
                out[path] = value
    return out


def _constant_checked(path: str, value: Any) -> bool:
    if path in DEAD_INPUT_CRITICAL:
        return True
    if any(path == p or (p.endswith(".") and path.startswith(p)) for p in DEAD_INPUT_CONSTANT_EXEMPT):
        return False
    return isinstance(value, float)


class DeadInputDetector:
    """Streams prompt payloads; flags leaves null or unchanged for >= threshold calls."""

    def __init__(self, threshold_calls: int = 20) -> None:
        self._lock = threading.Lock()
        self.threshold = max(2, int(threshold_calls))
        self._null_run: dict = {}
        self._same_run: dict = {}
        self._last: dict = {}
        self._known: set = set()
        self.calls = 0

    def observe(self, payload: Mapping[str, Any]) -> dict:
        flat = flatten_payload(payload)
        with self._lock:
            self.calls += 1
            self._known.update(flat)
            for path in self._known:
                value = flat.get(path)
                if value is None:
                    self._null_run[path] = self._null_run.get(path, 0) + 1
                    self._same_run.pop(path, None)
                    self._last.pop(path, None)
                    continue
                self._null_run[path] = 0
                if _constant_checked(path, value) and self._last.get(path, object()) == value:
                    self._same_run[path] = self._same_run.get(path, 1) + 1
                else:
                    self._same_run[path] = 1
                self._last[path] = value
            return self._report_locked()

    def _report_locked(self) -> dict:
        dead = []
        for path in sorted(self._known):
            if self._null_run.get(path, 0) >= self.threshold:
                dead.append({"path": path, "kind": "NULL", "calls": self._null_run[path]})
            elif self._same_run.get(path, 0) >= self.threshold:
                dead.append({"path": path, "kind": "CONSTANT", "calls": self._same_run[path],
                             "value": self._last.get(path)})
        status = "WARMING" if self.calls < self.threshold else ("DEAD_INPUT" if dead else "OK")
        return {"schema": INPUT_HEALTH_SCHEMA, "status": status, "threshold_calls": self.threshold,
                "observed_calls": self.calls, "dead_fields": dead}

    def report(self) -> dict:
        with self._lock:
            return self._report_locked()


def dead_input_report(payloads: Iterable[Mapping[str, Any]], threshold_calls: int = 20) -> dict:
    detector = DeadInputDetector(threshold_calls)
    report = detector.report()
    for payload in payloads:
        if isinstance(payload, Mapping):
            report = detector.observe(payload)
    return report
