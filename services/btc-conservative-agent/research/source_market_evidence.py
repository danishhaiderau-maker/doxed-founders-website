"""Pure helpers for canonical source-order market evidence.

The source strategy and the exchange copy are deliberately separate facts.
These helpers only describe what the public market feed showed while a source
order rested; they never promote a Showcase fill from an exchange fill.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()


def update_canonical_extrema(store: dict, order: dict, price: float) -> dict:
    """Update one lifecycle record even when signal/order are separate dicts."""
    trade_id = str((order or {}).get("trade_id") or "").strip()
    if not trade_id or not isinstance(price, (int, float)) or float(price) <= 0:
        return {}
    raw_direction = str(
        (order or {}).get("signal_dir") or (order or {}).get("dir") or (order or {}).get("side") or ""
    ).upper()
    direction = "LONG" if raw_direction in {"BUY", "LONG"} else "SHORT" if raw_direction in {"SELL", "SHORT"} else raw_direction
    record = store.setdefault(trade_id, {
        "schema": "source_order_market_evidence_v1",
        "canonical_trade_id": trade_id,
        "symbol": (order or {}).get("symbol") or "tBTCF0:USTF0",
        "direction": direction,
        "limit_price": (order or {}).get("limit_price"),
        "requested_quantity": (order or {}).get("qty"),
        "observations": [],
    })
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
    observation = {
        "schema": "source_order_market_observation_v1",
        "canonical_trade_id": record["canonical_trade_id"],
        "observed_at": _iso(observed_ts),
        "observed_at_ts": round(float(observed_ts), 3),
        "symbol": record.get("symbol"),
        "direction": direction,
        "limit_price": record.get("limit_price"),
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
            "schema", "canonical_trade_id", "symbol", "direction", "limit_price",
            "requested_quantity", "market_min_price", "market_max_price",
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
        # File/order semantics are immutable oldest-to-newest.  Hash the full
        # selected history so new evidence produces a new derived CF revision.
        result[trade_id] = {
            "schema": "source_order_market_evidence_v1",
            "canonical_trade_id": trade_id,
            "observations": observations,
            "latest_observation": observations[-1],
            "evidence_revision": hashlib.sha256(json.dumps(
                observations, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")).hexdigest(),
        }
    return result
