"""Shadow chase-bucket outcomes: one record per compressed shadow chase order.

Each compressed shadow schedule (``compressed_chase_shadow_v1``) reprices a
virtual limit at stages 0..5.  This tracker follows the same virtual limit,
decides a conservative fill (quote cross or strict trade-through only), then
marks the filled position for a fixed horizon and emits a single
``shadow_chase_bucket_v1`` record.  Records aggregate into the dashboard's
0..5+ chase buckets.

SHADOW ONLY: nothing here may create, submit, reprice or relay an order.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

SHADOW_CHASE_BUCKET_SCHEMA = "shadow_chase_bucket_v1"
SHADOW_CHASE_SOURCE_SCHEMA = "compressed_chase_shadow_v1"
SHADOW_CHASE_EXIT_MODEL = "MARK_AT_FILL_PLUS_1800S_MAKER_ENTRY_TAKER_EXIT"
OUTCOME_HORIZON_SEC = 1800.0
BUCKET_KEYS = ("0_chases", "1_chase", "2_chases", "3_chases", "4_chases", "5+_chases")
SIMULATED_LABEL = "SIMULATED (shadow) — no live tile chases"


def bucket_key(chase_count: Any) -> str:
    n = min(max(int(chase_count or 0), 0), 5)
    return BUCKET_KEYS[n]


def new_tracker(shadow: Mapping[str, Any]) -> dict:
    """Start tracking one armed compressed shadow schedule."""
    return {
        "trade_id": str(shadow.get("trade_id") or ""),
        "shared_ai_call_id": str(shadow.get("shared_ai_call_id") or ""),
        "epoch_id": str(shadow.get("epoch_id") or ""),
        "source_policy_signature": str(shadow.get("policy_signature") or ""),
        "direction": str(shadow.get("direction") or "").upper(),
        "signal_price": float(shadow.get("signal_price") or 0),
        "signal_ts": float(shadow.get("signal_ts") or 0),
        "expires_ts": float(shadow.get("expires_ts") or 0),
        "virtual_limit_price": float(shadow.get("virtual_limit_price") or 0),
        "entry_fee_rate": shadow.get("entry_fee_rate"),
        "exit_fee_rate": shadow.get("exit_fee_rate"),
        "leverage": shadow.get("leverage"),
        "requested_margin_usd": shadow.get("requested_margin_usd"),
        "max_stage_reached": 0,
        "filled": False,
        "fill_ts": None,
        "fill_price": None,
        "chase_count_at_fill": None,
        "mfe_bp": None,
        "mae_bp": None,
        "last_move_bp": None,
        "closed": False,
    }


def _crossed(direction: str, limit: float, bid: Optional[float], ask: Optional[float],
             last: Optional[float]) -> bool:
    if limit <= 0:
        return False
    if direction == "LONG":
        return (ask not in (None, 0) and float(ask) <= limit) or (
            last not in (None, 0) and float(last) < limit)
    if direction == "SHORT":
        return (bid not in (None, 0) and float(bid) >= limit) or (
            last not in (None, 0) and float(last) > limit)
    return False


def _mark(bid: Optional[float], ask: Optional[float], last: Optional[float]) -> Optional[float]:
    if bid not in (None, 0) and ask not in (None, 0) and float(ask) >= float(bid):
        return (float(bid) + float(ask)) / 2.0
    return None if last in (None, 0) else float(last)


def _fee_bp(tracker: Mapping[str, Any]) -> Optional[float]:
    try:
        return round((float(tracker["entry_fee_rate"]) + float(tracker["exit_fee_rate"])) * 1e4, 6)
    except (KeyError, TypeError, ValueError):
        return None


def _record(tracker: Mapping[str, Any], *, now_ts: float, outcome: str) -> dict:
    filled = bool(tracker["filled"])
    fee_bp = _fee_bp(tracker)
    net_bp = None
    if filled and tracker.get("last_move_bp") is not None and fee_bp is not None:
        net_bp = round(float(tracker["last_move_bp"]) - fee_bp, 4)
    chase = tracker["chase_count_at_fill"] if filled else tracker["max_stage_reached"]
    fill_vs_signal_bp = None
    if filled and tracker["signal_price"] > 0:
        sign = 1.0 if tracker["direction"] == "LONG" else -1.0
        fill_vs_signal_bp = round(
            sign * (float(tracker["fill_price"]) - tracker["signal_price"]) / tracker["signal_price"] * 1e4, 4)
    return {
        "schema": SHADOW_CHASE_BUCKET_SCHEMA,
        "source_schema": SHADOW_CHASE_SOURCE_SCHEMA,
        "source_policy_signature": tracker["source_policy_signature"],
        "execution_class": "SHADOW_ONLY",
        "places_order": False,
        "relay_eligible": False,
        "label": SIMULATED_LABEL,
        "trade_id": tracker["trade_id"],
        "shared_ai_call_id": tracker["shared_ai_call_id"],
        "epoch_id": tracker["epoch_id"],
        "direction": tracker["direction"],
        "signal_price": tracker["signal_price"],
        "signal_ts": tracker["signal_ts"],
        "ts": float(now_ts),
        "outcome": outcome,
        "filled": filled,
        "chase_count": int(chase or 0),
        "bucket": bucket_key(chase),
        "max_stage_reached": int(tracker["max_stage_reached"]),
        "fill_ts": tracker["fill_ts"],
        "fill_price": tracker["fill_price"],
        "time_to_fill_sec": (
            round(float(tracker["fill_ts"]) - tracker["signal_ts"], 3) if filled else None),
        "fill_vs_signal_bp": fill_vs_signal_bp,
        "mfe_bp": tracker["mfe_bp"],
        "mae_bp": tracker["mae_bp"],
        "gross_bp": tracker["last_move_bp"] if filled else None,
        "fee_bp": fee_bp,
        "net_bp": net_bp,
        "win": None if net_bp is None else net_bp > 0,
        "exit_model": SHADOW_CHASE_EXIT_MODEL,
        "outcome_horizon_sec": OUTCOME_HORIZON_SEC,
        "leverage": tracker["leverage"],
        "requested_margin_usd": tracker["requested_margin_usd"],
    }


def observe(tracker: dict, *, now_ts: float, bid: Optional[float], ask: Optional[float],
            last: Optional[float], stage_index: Optional[int] = None,
            virtual_limit_price: Optional[float] = None) -> list[dict]:
    """Advance one tracker; returns at most one terminal record."""
    if tracker.get("closed"):
        return []
    now = float(now_ts)
    direction = tracker["direction"]
    if not tracker["filled"]:
        if stage_index is not None:
            tracker["max_stage_reached"] = min(max(int(tracker["max_stage_reached"]), int(stage_index)), 5)
        if virtual_limit_price not in (None, 0):
            tracker["virtual_limit_price"] = float(virtual_limit_price)
        limit = float(tracker["virtual_limit_price"] or 0)
        if _crossed(direction, limit, bid, ask, last):
            tracker.update(filled=True, fill_ts=now, fill_price=limit,
                           chase_count_at_fill=int(tracker["max_stage_reached"]),
                           mfe_bp=0.0, mae_bp=0.0, last_move_bp=0.0)
            return []
        if now >= tracker["expires_ts"]:
            tracker["closed"] = True
            return [_record(tracker, now_ts=now, outcome="NO_FILL")]
        return []
    mark = _mark(bid, ask, last)
    if mark is not None and tracker["fill_price"]:
        sign = 1.0 if direction == "LONG" else -1.0
        move = sign * (mark - float(tracker["fill_price"])) / float(tracker["fill_price"]) * 1e4
        tracker["last_move_bp"] = round(move, 4)
        tracker["mfe_bp"] = round(max(float(tracker["mfe_bp"] or 0), move), 4)
        tracker["mae_bp"] = round(min(float(tracker["mae_bp"] or 0), move), 4)
    if now >= float(tracker["fill_ts"]) + OUTCOME_HORIZON_SEC:
        tracker["closed"] = True
        return [_record(tracker, now_ts=now, outcome="FILLED")]
    return []


def aggregate(rows: Iterable[Mapping[str, Any]], *, epoch_id: Optional[str] = None,
              margin_usd: float = 0.25, leverage: float = 100.0) -> dict:
    """Per-bucket N, fill rate, WR and EV for shadow chase records."""
    reached = [0] * 6
    fills = [0] * 6
    wins = [0] * 6
    scored = [0] * 6
    net = [0.0] * 6
    ttf = [0.0] * 6
    records = 0
    for row in rows or []:
        if not isinstance(row, Mapping) or row.get("schema") != SHADOW_CHASE_BUCKET_SCHEMA:
            continue
        if row.get("places_order") is not False or row.get("execution_class") != "SHADOW_ONLY":
            continue
        if epoch_id and row.get("epoch_id") != epoch_id:
            continue
        records += 1
        top = min(max(int(row.get("chase_count") or 0), 0), 5)
        for k in range(top + 1):
            reached[k] += 1
        if not row.get("filled"):
            continue
        fills[top] += 1
        if row.get("time_to_fill_sec") is not None:
            ttf[top] += float(row["time_to_fill_sec"])
        if row.get("net_bp") is not None:
            scored[top] += 1
            net[top] += float(row["net_bp"])
            wins[top] += 1 if float(row["net_bp"]) > 0 else 0
    buckets = []
    for k, key in enumerate(BUCKET_KEYS):
        ev_bp = round(net[k] / scored[k], 3) if scored[k] else None
        buckets.append({
            "bucket": key,
            "reached": reached[k],
            "trades": fills[k],
            "fill_rate_pct": round(100.0 * fills[k] / reached[k], 2) if reached[k] else None,
            "win_rate_pct": round(100.0 * wins[k] / scored[k], 2) if scored[k] else None,
            "ev_bp": ev_bp,
            "ev_usd": None if ev_bp is None else round(ev_bp / 1e4 * margin_usd * leverage, 5),
            "sum_pnl_usd": (round(net[k] / 1e4 * margin_usd * leverage, 5) if scored[k] else None),
            "avg_time_to_fill_sec": round(ttf[k] / fills[k], 1) if fills[k] else None,
        })
    return {"records": records, "buckets": buckets}
