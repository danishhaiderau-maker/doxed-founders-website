"""Shared, versioned paper fill model (research + paper accounting, never order placement).

``REALISTIC_V1`` is the single headline fill model for every paper / simulated
result in the system (Fly paper ledgers, analyzer engine, genome grid, edge
tracker, conservative limit-fill receipts, Continuous replica). The optimistic
``OPTIMISTIC_TOUCH_V1`` result may be computed alongside, but only as a
labelled comparison shadow; it must never be the headline number.

Rules (the 1 s Bitfinex tape ``market_microstructure_1s_v1`` carries L1 BBO with
top-of-book size and per-second trade aggregates; there is no L2 depth):

* Taker / marketable: the order reaches the venue at decision time plus the
  measured decision->order latency and fills at the opposite side of the first
  fresh BBO observed at or after arrival (ask for buys, bid for sells). If the
  size exceeds top-of-book size the excess walks the book; without L2 each
  further level is assumed to hold the same size one spread worse.
* Maker / resting limit: a price merely touching the limit is not a fill. A
  resting buy at L fills fully when sell-aggressor prints trade below L
  (trade-through: every bid at L, including ours, must have been consumed
  first), or partially / fully at L when the sell-aggressor volume printed at
  L after placement exceeds the estimated queue ahead. Print evidence is the
  aggressor-side 1 s VWAP only (sell VWAP < L proves a print below L; == L is
  at-limit volume), never the bucket trade low/high, because the side VWAPs
  cover the whole tape while trade low/high only exist from 2026-09-30 and a
  mixed standard would confine maker fills to recent data. The queue estimate is the
  top-of-book size at placement when the limit is at the touch, zero when the
  limit improves the touch, and the touch size at the first second the touch
  reaches L for deeper limits. A limit that is marketable at placement is a
  taker fill. Sells mirror. A BBO cross without a print is recorded as a
  diagnostic but is not a fill trigger. Every repricing loses queue priority.
* Exits: stops, floors and time exits are marketable (executable-side BBO at
  trigger + latency); targets and partial take-profits are resting makers with
  the same trade-through rule (buy-aggressor VWAP above a LONG's target, sell
  VWAP below a SHORT's), booked exactly at the target level.
* Fees: Bitfinex derivatives fees from ``bitfinex_cost_profile`` (currently
  zero since 2025-12-17); explicit maker/taker fields so non-zero fees apply.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    import bitfinex_cost_profile as _cost
except ImportError:  # pragma: no cover - research tools run with the agent dir on sys.path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import bitfinex_cost_profile as _cost

FILL_MODEL_VERSION = "REALISTIC_V1"
SHADOW_FILL_MODEL = "OPTIMISTIC_TOUCH_V1"
HEADLINE_ROLE = "HEADLINE"
SHADOW_ROLE = "COMPARISON_SHADOW_NOT_HEADLINE"
FILL_RECORD_SCHEMA = "fill_model_record_v1"

DEFAULT_DECISION_LATENCY_SEC = 6.0
TAKER_MAX_WAIT_SEC = 5
TAPE_STALE_SEC = 3.5
ADVERSE_SELECTION_HORIZON_SEC = 60
# Exit trigger -> marketable exit order: the next observed 1 s quote after the trigger (not yet measured live).
EXIT_LATENCY_SEC = 1.0
PRICE_EPS = 1e-9

SPEC: dict[str, Any] = {
    "fill_model": FILL_MODEL_VERSION,
    "shadow_fill_model": SHADOW_FILL_MODEL,
    "evidence": "market_microstructure_1s_v1 (L1 BBO + top-of-book size + 1 s trade aggregates; no L2)",
    "taker": "opposite BBO (ask buy / bid sell) of first fresh quote at or after decision_ts + measured latency; "
             "size walk beyond top-of-book size at one spread per extra top-size level (no-L2 proxy)",
    "maker": "fill only on trade-through (aggressor-side VWAP strictly beyond limit) or at-limit aggressor volume "
             "(aggressor VWAP == limit) after placement exceeding queue_ahead; partial fills allowed; "
             "marketable-at-placement = taker; BBO cross without print is not a fill; reprice resets queue",
    "print_evidence": "aggressor-side 1 s VWAP (sell_vwap for resting buys, buy_vwap for resting sells); trade_low/"
                      "trade_high are diagnostics only (absent before 2026-09-30, uniform evidence across the tape)",
    "tick": "resting limits rounded passively to Bitfinex 5-significant-digit price precision",
    "queue_estimate": "top-of-book size at placement when limit == touch; 0 when limit improves the touch; "
                      "touch size when the touch first reaches a deeper limit",
    "exits": "stop/floor/time = marketable at executable-side BBO at trigger + exit latency "
             f"({EXIT_LATENCY_SEC:g}s), booked at the worse of trigger mark and post-latency mark until exit "
             "latency is measured live; target/partial TP = maker with trade-through rule (exit-side aggressor VWAP "
             "beyond the level), booked at the target level",
    "fees": "bitfinex_cost_profile maker/taker rates (explicit fields)",
    "adverse_selection": f"signed mid markout {ADVERSE_SELECTION_HORIZON_SEC}s after a maker fill (bp, negative = adverse)",
    "shadow": "OPTIMISTIC_TOUCH_V1: taker at last price with no latency; maker at limit on any touch of the traded "
              "low/high (last-price fallback); exits booked at the triggering mark with no latency",
}


def fill_model_fingerprint() -> str:
    """Identity of the fill model + fee profile; part of the clean-epoch compatibility fingerprint."""
    blob = json.dumps({"spec": SPEC, "fees": _cost.cost_profile_signature()}, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def fill_model_declaration(**extra: Any) -> dict[str, Any]:
    """The block every headline result carries (checked by the self-aware section contracts)."""
    return {"fill_model": FILL_MODEL_VERSION, "fill_model_fingerprint": fill_model_fingerprint(),
            "headline_role": HEADLINE_ROLE, "shadow_fill_model": SHADOW_FILL_MODEL, "shadow_role": SHADOW_ROLE,
            "fee_profile_id": _cost.FEE_PROFILE_ID, **extra}


def fee_fields(notional_usd: float, *, maker: bool) -> dict[str, Any]:
    maker_rate, taker_rate = _cost.fee_rates()
    return {"fee_profile_id": _cost.FEE_PROFILE_ID, "maker_fee_rate": maker_rate, "taker_fee_rate": taker_rate,
            "liquidity": "MAKER" if maker else "TAKER", "fee_usd": _cost.fee_usd(notional_usd, maker=maker)}


# ------------------------------------------------------------------ latency

def measure_decision_latency(ledger_dir: Path | str, *, exclude_lanes: Iterable[str] = ()) -> dict[str, Any]:
    """Measured signal -> first paper order submit latency from the V3 ledgers (opportunity + order_intent)."""
    led = Path(ledger_dir)
    excluded = set(exclude_lanes)
    signal: dict[str, float] = {}
    samples: list[float] = []
    try:
        with open(led / "opportunity.jsonl", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    signal[str(row["episode_id"])] = float(row["signal_ts"])
                except (ValueError, KeyError, TypeError):
                    continue
        with open(led / "order_intent.jsonl", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("research_lane") in excluded or str(row.get("episode_id")) not in signal:
                    continue
                for act in (row.get("chase_schedule") or {}).get("action_timing_receipts") or []:
                    if act.get("action_type") == "INITIAL_SUBMIT" and act.get("eligibility_ts") is not None:
                        lat = float(act["eligibility_ts"]) - signal[str(row["episode_id"])]
                        if 0 <= lat <= 120:
                            samples.append(lat)
    except OSError:
        pass
    if len(samples) < 20:
        return {"source": "DEFAULT_NO_MEASUREMENT", "latency_sec": DEFAULT_DECISION_LATENCY_SEC, "n": len(samples)}
    samples.sort()
    return {"source": "MEASURED_SIGNAL_TO_INITIAL_SUBMIT_P50", "latency_sec": round(statistics.median(samples), 3),
            "p10": round(samples[len(samples) // 10], 3), "p90": round(samples[int(len(samples) * 0.9)], 3),
            "n": len(samples)}


# ------------------------------------------------------------------ helpers

def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _sign(side: str) -> float:
    s = str(side).upper()
    if s in ("LONG", "BUY"):
        return 1.0
    if s in ("SHORT", "SELL"):
        return -1.0
    raise ValueError(f"INVALID_SIDE:{side}")


def size_walk(top_price: float, top_qty: float | None, spread: float | None, qty: float, side: str) -> dict[str, Any]:
    """VWAP for ``qty`` taken against one side of the book (no L2: further levels = top size, one spread worse)."""
    sign = _sign(side)
    top = _num(top_qty)
    step = max(_num(spread) or 0.0, 0.0)
    if top is None or top <= 0 or qty <= top:
        return {"vwap": float(top_price), "levels": 1, "binding": False, "walk_bp": 0.0,
                "top_qty": top, "basis": "TOP_OF_BOOK" if top else "TOP_QTY_UNKNOWN"}
    remaining, level, cost = qty, 0, 0.0
    while remaining > PRICE_EPS:
        take = min(top, remaining)
        cost += take * (top_price + sign * step * level)
        remaining -= take
        level += 1
    vwap = cost / qty
    return {"vwap": vwap, "levels": level, "binding": True, "walk_bp": sign * (vwap - top_price) / top_price * 1e4,
            "top_qty": top, "basis": "NO_L2_SPREAD_STEP_PROXY"}


def price_tol(price: Any) -> Any:
    """Price equality tolerance (relative 1e-9, i.e. ~0.0001 USD at BTC prices; works on floats and arrays)."""
    try:
        import numpy as _np
        return _np.maximum(PRICE_EPS, 1e-9 * _np.abs(price))
    except ImportError:  # pragma: no cover
        return max(PRICE_EPS, 1e-9 * abs(price))


def tick_size(price: Any) -> Any:
    """Bitfinex price precision: 5 significant digits (1 USD at 10k-99,999; 10 USD at 100k+)."""
    import numpy as _np
    p = _np.abs(_np.asarray(price, dtype=float))
    return 10.0 ** (_np.floor(_np.log10(_np.maximum(p, 1e-12))) - 4)


def round_limit_passive(price: Any, side: str) -> Any:
    """A resting limit rests on the tick grid, rounded away from the touch (buy down, sell up)."""
    import numpy as _np
    tick = tick_size(price)
    p = _np.asarray(price, dtype=float)
    out = _np.floor(p / tick + 1e-9) * tick if _sign(side) > 0 else _np.ceil(p / tick - 1e-9) * tick
    return float(out) if _np.ndim(out) == 0 else out


def _row_fresh(row: Mapping[str, Any]) -> bool:
    age = _num(row.get("source_age_sec"))
    return (row.get("valid_bbo", True) is not False and row.get("fresh", True) is not False
            and (age is None or age <= TAPE_STALE_SEC))


def maker_print_evidence(row: Mapping[str, Any], side: str, limit: float) -> dict[str, Any]:
    """Trade evidence for one resting limit in one 1 s bucket.

    Evidence is the aggressor-side VWAP only (sell prints for a resting buy). ``through``: VWAP strictly beyond
    L, which proves at least one print beyond L. ``at_limit_qty``: the aggressor volume when its VWAP equals L
    (every print at L, or some beyond it - counted conservatively as at-limit volume against the queue).
    ``extreme_through_unproven`` flags a trade low/high beyond L whose side VWAP does not prove it (diagnostic).
    """
    sign = _sign(side)
    if sign > 0:
        extreme, agg_qty, agg_vwap = _num(row.get("trade_low")), _num(row.get("sell_qty")) or 0.0, _num(row.get("sell_vwap"))
    else:
        extreme, agg_qty, agg_vwap = _num(row.get("trade_high")), _num(row.get("buy_qty")) or 0.0, _num(row.get("buy_vwap"))
    out = {"through": False, "at_limit_qty": 0.0, "at_limit_ambiguous": False, "extreme_through_unproven": False}
    if agg_qty <= 0 or agg_vwap is None:
        return out
    tol = float(price_tol(limit))
    if sign * (limit - agg_vwap) > tol:
        out["through"] = True
    elif abs(agg_vwap - limit) <= tol:
        out["at_limit_qty"] = agg_qty
    elif extreme is not None and sign * (limit - extreme) > tol:
        out["extreme_through_unproven"] = True
    return out


# ------------------------------------------------------- row (receipt) API

def taker_fill_rows(rows_by_ts: Mapping[int, Mapping[str, Any]], *, side: str, qty: float, decision_ts: float,
                    latency_sec: float, max_wait_sec: int = TAKER_MAX_WAIT_SEC) -> dict[str, Any]:
    """REALISTIC_V1 taker fill from 1 s rows keyed by integer bucket_ts."""
    arrival = float(decision_ts) + float(latency_sec)
    first = int(math.ceil(arrival - PRICE_EPS))
    sign = _sign(side)
    for ts in range(first, first + max_wait_sec + 1):
        row = rows_by_ts.get(ts)
        if not row or not _row_fresh(row):
            continue
        bid, ask = _num(row.get("bid")), _num(row.get("ask"))
        if not bid or not ask or ask < bid:
            continue
        top = ask if sign > 0 else bid
        walk = size_walk(top, row.get("ask_qty" if sign > 0 else "bid_qty"), ask - bid, qty, side)
        return {"status": "FILLED", "fill_ts": ts, "fill_price": walk["vwap"], "filled_qty": qty, "liquidity": "TAKER",
                "basis": "MARKETABLE_OPPOSITE_BBO", "arrival_ts": arrival, "latency_sec": float(latency_sec),
                "bbo": {"bid": bid, "ask": ask, "bid_qty": _num(row.get("bid_qty")), "ask_qty": _num(row.get("ask_qty")),
                        "bucket_ts": ts}, "size_walk": walk}
    return {"status": "NO_FRESH_BBO", "arrival_ts": arrival, "latency_sec": float(latency_sec)}


def _queue_qty(value: Any) -> float:
    qty = _num(value)
    return math.inf if qty is None else qty


def maker_fill_rows(rows_by_ts: Mapping[int, Mapping[str, Any]], *, side: str, qty: float,
                    schedule: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """REALISTIC_V1 resting-limit fill over a chase schedule of ``{start_ts, end_ts, limit_price}`` intervals.

    Each interval is one resting order; a reprice cancels and re-enters (queue priority lost, partials kept).
    """
    sign = _sign(side)
    touch_key = "bid_qty" if sign > 0 else "ask_qty"
    filled, cost, first_fill_ts, queue_used = 0.0, 0.0, None, None
    diag = {"bbo_cross_without_print_sec": 0, "at_limit_ambiguous_sec": 0, "missing_sec": 0}
    for interval in sorted(schedule, key=lambda x: float(x["start_ts"])):
        limit = float(interval["limit_price"])
        tol = float(price_tol(limit))
        start, end = int(interval["start_ts"]), int(interval["end_ts"])
        queue, cum, seg_filled, seg_first, need = None, 0.0, 0.0, None, qty - filled
        for ts in range(start, end):
            row = rows_by_ts.get(ts)
            if not row:
                diag["missing_sec"] += 1
                continue
            bid, ask = _num(row.get("bid")), _num(row.get("ask"))
            fresh = bool(_row_fresh(row) and bid and ask and ask >= bid)
            touch, opp = (bid, ask) if sign > 0 else (ask, bid)
            if ts == start and fresh and filled == 0 and sign * (limit - opp) >= -tol:
                walk = size_walk(opp, row.get("ask_qty" if sign > 0 else "bid_qty"), ask - bid, qty, side)
                return {"status": "FILLED", "fill_ts": ts, "fill_price": walk["vwap"], "filled_qty": qty,
                        "filled_fraction": 1.0, "liquidity": "TAKER", "basis": "MARKETABLE_AT_PLACEMENT",
                        "limit_price": limit, "queue_estimate": 0.0, "size_walk": walk, "diagnostics": diag}
            if queue is None and fresh and sign * (limit - touch) >= -tol:
                queue = _queue_qty(row.get(touch_key)) if abs(limit - touch) <= tol else 0.0
            ev = maker_print_evidence(row, side, limit)
            if ev["through"]:
                cost += (qty - filled) * limit
                return {"status": "FILLED", "fill_ts": first_fill_ts or ts, "fill_price": cost / qty, "filled_qty": qty,
                        "filled_fraction": 1.0, "liquidity": "MAKER", "basis": "TRADE_THROUGH", "limit_price": limit,
                        "queue_estimate": queue, "completed_ts": ts, "diagnostics": diag}
            diag["at_limit_ambiguous_sec"] += int(ev["at_limit_ambiguous"])
            if fresh and sign * (limit - opp) >= -tol:
                diag["bbo_cross_without_print_sec"] += 1
            if ev["at_limit_qty"] > 0:
                if queue is None:
                    queue = _queue_qty(row.get(touch_key))
                cum += ev["at_limit_qty"]
                avail = cum - queue
                if avail >= need - 1e-15:
                    cost += need * limit
                    return {"status": "FILLED", "fill_ts": first_fill_ts or ts, "fill_price": cost / qty,
                            "filled_qty": qty, "filled_fraction": 1.0, "liquidity": "MAKER",
                            "basis": "QUEUE_CONSUMED_AT_LIMIT", "limit_price": limit, "queue_estimate": queue,
                            "completed_ts": ts, "diagnostics": diag}
                if avail > 0:
                    seg_first = seg_first or ts
                    seg_filled, queue_used = min(need, avail), queue
        if seg_filled > 0:
            first_fill_ts = first_fill_ts or seg_first
            filled += seg_filled
            cost += seg_filled * limit
    if filled > 0:
        return {"status": "PARTIAL", "fill_ts": first_fill_ts, "fill_price": cost / filled, "filled_qty": filled,
                "filled_fraction": filled / qty, "liquidity": "MAKER", "basis": "QUEUE_CONSUMED_AT_LIMIT_PARTIAL",
                "queue_estimate": queue_used, "diagnostics": diag}
    return {"status": "NO_FILL", "diagnostics": diag}


def optimistic_touch_rows(rows_by_ts: Mapping[int, Mapping[str, Any]], *, side: str,
                          schedule: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """OPTIMISTIC_TOUCH_V1 shadow: fill at the limit on any touch of the traded low/high (last-price fallback)."""
    sign = _sign(side)
    for interval in sorted(schedule, key=lambda x: float(x["start_ts"])):
        limit = float(interval["limit_price"])
        for ts in range(int(interval["start_ts"]), int(interval["end_ts"])):
            row = rows_by_ts.get(ts) or {}
            last = _num(row.get("last"))
            px = _num(row.get("trade_low" if sign > 0 else "trade_high")) or last
            if px is not None and sign * (limit - px) >= -PRICE_EPS:
                return {"status": "FILLED", "fill_ts": ts, "fill_price": limit, "model": SHADOW_FILL_MODEL}
    return {"status": "NO_FILL", "model": SHADOW_FILL_MODEL}


def fill_record(*, realistic: Mapping[str, Any], shadow: Mapping[str, Any] | None, side: str, qty: float,
                latency_sec: float | None, tape_id: str | None = None, fill_id: str | None = None,
                inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Per-fill record: model version, inputs, queue estimate, latency, fees and the optimistic shadow alongside."""
    price = _num(realistic.get("fill_price"))
    notional = (price or 0.0) * float(realistic.get("filled_qty") or 0.0)
    return {
        "schema": FILL_RECORD_SCHEMA, "fill_model": FILL_MODEL_VERSION, "fill_model_fingerprint": fill_model_fingerprint(),
        "tape_id": tape_id, "fill_id": fill_id, "side": str(side).upper(), "requested_qty": qty,
        "status": realistic.get("status"), "basis": realistic.get("basis"), "fill_ts": realistic.get("fill_ts"),
        "fill_price": price, "filled_qty": realistic.get("filled_qty"), "filled_fraction": realistic.get("filled_fraction"),
        "queue_estimate": realistic.get("queue_estimate"), "latency_sec": latency_sec,
        "inputs": dict(inputs or {}) | {"bbo": realistic.get("bbo"), "size_walk": realistic.get("size_walk"),
                                        "diagnostics": realistic.get("diagnostics")},
        "fees": fee_fields(notional, maker=realistic.get("liquidity") == "MAKER") if price else None,
        "optimistic_shadow": (dict(shadow) | {"role": SHADOW_ROLE}) if shadow else None,
    }


def realistic_exit_margin(cur: Sequence[float], age: Sequence[float], exit_idx: int, reason: str, *,
                          latency_sec: float, target_margin: float | None = None) -> tuple[float, int]:
    """REALISTIC_V1 exit booking on an executable-side margin path: (margin %, booked index).

    Targets are resting makers booked exactly at the target level; every other exit is marketable and books
    the WORSE of the trigger mark and the executable-side mark of the first observation at or after
    trigger + latency. Trigger marks sit at local extremes and usually revert within a second, so letting
    the (assumed, not yet live-measured) exit latency improve the booked price would flatter results.
    """
    if reason == "ATR_TAKE_PROFIT" and target_margin is not None:
        return float(target_margin), int(exit_idx)
    n = len(cur)
    due = float(age[exit_idx]) + float(latency_sec)
    j = int(exit_idx)
    while j < n - 1 and float(age[j]) < due - PRICE_EPS:
        j += 1
    if float(cur[exit_idx]) < float(cur[j]):
        return float(cur[exit_idx]), int(exit_idx)
    return float(cur[j]), j


def adverse_selection_bp(mid_after: float | None, fill_price: float, side: str) -> float | None:
    """Signed mid markout after a fill in bp (negative = the market moved against the filled side)."""
    m = _num(mid_after)
    if m is None or not fill_price:
        return None
    return _sign(side) * (m - fill_price) / fill_price * 1e4
