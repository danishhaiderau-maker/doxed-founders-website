"""Pure helpers for canonical source-order market evidence.

The source strategy and the exchange copy are deliberately separate facts.
These helpers only describe what the public market feed showed while a source
order rested; they never promote a Showcase fill from an exchange fill.

One pending-order generation is authoritative: original limit, current ACK
limit, chase generation/seq, and qty all live on the same store record used by
the fill gate, market-evidence collector, fill-quality, and dashboard.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()


def _direction_from_order(order: dict) -> str:
    raw_direction = str(
        (order or {}).get("signal_dir") or (order or {}).get("dir") or (order or {}).get("side") or ""
    ).upper()
    if raw_direction in {"BUY", "LONG"}:
        return "LONG"
    if raw_direction in {"SELL", "SHORT"}:
        return "SHORT"
    return raw_direction


def _order_generation(order: dict) -> int:
    for key in ("limit_chase_count", "submitted_order_event_seq", "limit_generation", "event_seq"):
        try:
            value = int((order or {}).get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value >= 0:
            return value
    return 0


def _finite_price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def sync_canonical_pending_order(
    store: dict,
    order: dict,
    *,
    chase_acked: bool = False,
    observed_ts: float | None = None,
) -> dict:
    """Atomically refresh the single pending-order record from the live order.

    Chase ACK advances generation + current limit. Failed chase persistence
    must not call this with chase_acked=True, so the prior ACK limit remains
    authoritative.
    """
    trade_id = str((order or {}).get("trade_id") or "").strip()
    if not trade_id:
        return {}
    direction = _direction_from_order(order)
    current_limit = _finite_price((order or {}).get("limit_price"))
    original_limit = _finite_price(
        (order or {}).get("original_limit_price")
        or (order or {}).get("planned_limit_price")
        or (order or {}).get("originalLimitPrice")
    )
    generation = _order_generation(order)
    qty = (order or {}).get("qty")
    record = store.setdefault(trade_id, {
        "schema": "source_order_market_evidence_v1",
        "canonical_trade_id": trade_id,
        "symbol": (order or {}).get("symbol") or "tBTCF0:USTF0",
        "direction": direction,
        "original_limit_price": original_limit,
        "current_limit_price": current_limit,
        "limit_price": current_limit,
        "limit_generation": generation,
        "requested_quantity": qty,
        "status": str((order or {}).get("status") or "PENDING"),
        "created_at_ts": (order or {}).get("created_ts") or observed_ts,
        "last_chase_ack_at": None,
        "observations": [],
    })
    if direction:
        record["direction"] = direction
    if qty is not None:
        record["requested_quantity"] = qty
    if original_limit is not None and record.get("original_limit_price") is None:
        record["original_limit_price"] = original_limit
    prev_generation = int(record.get("limit_generation") or 0)
    if chase_acked:
        # Only advance on authenticated chase ACK. Never regress.
        if generation >= prev_generation and current_limit is not None:
            record["limit_generation"] = generation
            record["current_limit_price"] = current_limit
            record["limit_price"] = current_limit
            if observed_ts is not None:
                record["last_chase_ack_at"] = _iso(observed_ts)
            elif (order or {}).get("last_chase_ts"):
                try:
                    record["last_chase_ack_at"] = _iso(float(order.get("last_chase_ts")))
                except (TypeError, ValueError):
                    pass
    else:
        # Keep store identical to the live order object used by the fill gate.
        if generation > prev_generation and current_limit is not None:
            # Order already advanced (e.g. in-process chase) — mirror it.
            record["limit_generation"] = generation
            record["current_limit_price"] = current_limit
            record["limit_price"] = current_limit
        elif generation == prev_generation and current_limit is not None:
            record["current_limit_price"] = current_limit
            record["limit_price"] = current_limit
        elif record.get("limit_price") is None and current_limit is not None:
            record["limit_generation"] = generation
            record["current_limit_price"] = current_limit
            record["limit_price"] = current_limit
    if record.get("original_limit_price") is None and current_limit is not None:
        record["original_limit_price"] = current_limit
    status = str((order or {}).get("status") or "").strip()
    if status:
        record["status"] = status
    return record


def update_canonical_extrema(store: dict, order: dict, price: float) -> dict:
    """Update one lifecycle record even when signal/order are separate dicts."""
    trade_id = str((order or {}).get("trade_id") or "").strip()
    if not trade_id or not isinstance(price, (int, float)) or float(price) <= 0:
        return {}
    record = sync_canonical_pending_order(store, order)
    if not record:
        return {}
    value = float(price)
    current_min = record.get("market_min_price")
    current_max = record.get("market_max_price")
    record["market_min_price"] = value if current_min is None else min(float(current_min), value)
    record["market_max_price"] = value if current_max is None else max(float(current_max), value)
    return record


def append_market_observation(
    store: dict,
    order: dict,
    *,
    market_price: float,
    bid: float,
    ask: float,
    venue_snapshot: dict,
    gate_evidence: dict,
    observed_ts: float,
    max_in_memory: int = 64,
) -> tuple[dict, dict]:
    """Append a timestamped, side-correct BBO/depth/aggressor observation."""
    record = update_canonical_extrema(store, order, market_price)
    if not record:
        return {}, {}
    direction = str(record.get("direction") or "").upper()
    executable_quote = float(ask or 0) if direction == "LONG" else float(bid or 0)
    # Observations always stamp the current ACK generation/limit — never a
    # stale original captured at first sighting.
    current_limit = record.get("current_limit_price")
    if current_limit is None:
        current_limit = record.get("limit_price")
    observation = {
        "schema": "source_order_market_observation_v1",
        "canonical_trade_id": record["canonical_trade_id"],
        "observed_at": _iso(observed_ts),
        "observed_at_ts": round(float(observed_ts), 3),
        "symbol": record.get("symbol"),
        "direction": direction,
        "limit_price": current_limit,
        "original_limit_price": record.get("original_limit_price"),
        "current_limit_price": current_limit,
        "limit_generation": int(record.get("limit_generation") or 0),
        "requested_quantity": record.get("requested_quantity"),
        "market_last": float(market_price or 0),
        "best_bid": float(bid or 0),
        "best_ask": float(ask or 0),
        "side_correct_executable_quote": executable_quote,
        "book_ts": (venue_snapshot or {}).get("book_ts"),
        "book_age_sec": (gate_evidence or {}).get("book_age_sec"),
        "visible_executable_qty": (gate_evidence or {}).get("visible_executable_qty"),
        "recent_executable_aggressor_qty": (gate_evidence or {}).get("recent_executable_trade_qty"),
        "fill_gate_policy": (gate_evidence or {}).get("policy"),
        "fill_gate_verdict": (gate_evidence or {}).get("reason"),
        "source_strategy_state_unchanged": True,
    }
    record["observations"].append(observation)
    if len(record["observations"]) > max(1, int(max_in_memory)):
        del record["observations"][:-max(1, int(max_in_memory))]
    record["latest_observation"] = observation
    record["observation_count"] = int(record.get("observation_count") or 0) + 1
    return copy.deepcopy(record), copy.deepcopy(observation)


def evidence_summary(record: dict) -> dict:
    if not isinstance(record, dict) or not record:
        return {}
    return {
        key: copy.deepcopy(record.get(key))
        for key in (
            "schema", "canonical_trade_id", "symbol", "direction",
            "original_limit_price", "current_limit_price", "limit_price",
            "limit_generation", "requested_quantity", "status",
            "created_at_ts", "last_chase_ack_at",
            "market_min_price", "market_max_price",
            "observation_count", "latest_observation",
        )
        if record.get(key) is not None
    }


def load_market_evidence_index(path, target_trade_ids=None, max_rotations: int = 128) -> dict:
    """Stream bounded rotations and retain only requested canonical IDs."""
    active = Path(path)
    rotations = []
    try:
        for candidate in active.parent.glob(active.name + ".*"):
            suffix = candidate.name[len(active.name) + 1:]
            if candidate.is_file() and suffix.isdigit():
                rotations.append((int(suffix), candidate))
    except OSError:
        rotations = []
    rotations = sorted(rotations, reverse=True)[-max(0, int(max_rotations)):]
    paths = [candidate for _, candidate in rotations]
    if active.is_file():
        paths.append(active)
    targets = {str(value) for value in target_trade_ids} if target_trade_ids is not None else None
    grouped = {}
    for candidate in paths:
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip() or len(line) > 1024 * 1024:
                        continue
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    trade_id = str(row.get("canonical_trade_id") or row.get("trade_id") or "")
                    if not trade_id or (targets is not None and trade_id not in targets):
                        continue
                    grouped.setdefault(trade_id, []).append(row)
        except OSError:
            continue
    result = {}
    for trade_id, observations in grouped.items():
        latest = observations[-1] if observations else {}
        result[trade_id] = {
            "schema": "source_order_market_evidence_v1",
            "canonical_trade_id": trade_id,
            "observations": observations,
            "latest_observation": latest,
            "limit_price": latest.get("current_limit_price", latest.get("limit_price")),
            "current_limit_price": latest.get("current_limit_price", latest.get("limit_price")),
            "original_limit_price": latest.get("original_limit_price"),
            "limit_generation": latest.get("limit_generation"),
            "evidence_revision": hashlib.sha256(json.dumps(
                observations, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")).hexdigest(),
        }
    return result
