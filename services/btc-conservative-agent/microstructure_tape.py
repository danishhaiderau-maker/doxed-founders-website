"""Pure contracts for the shared conservative-execution evidence tape.

The runtime producer and analyzer intentionally share this module so a policy
cannot qualify against a looser interpretation than the one that was stored.
"""
from __future__ import annotations

import hashlib
import json
import math

SCHEMA = "market_microstructure_1s_v1"
FILE_NAME = "market_microstructure_1s.jsonl"
MAX_SOURCE_AGE_SEC = 3.5


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_bucket(*, bucket_ts, bid, ask, bid_qty, ask_qty, last,
                 source_ts, trades=(), symbol="tBTCF0:USTF0") -> dict:
    """Build one immutable one-second BBO/depth/trade aggregate.

    Invalid or stale market state is retained as negative evidence rather than
    silently dropped. This lets the analyzer distinguish no-fill from no-data.
    """
    bucket_ts = int(float(bucket_ts))
    bid, ask = _finite(bid), _finite(ask)
    bid_qty, ask_qty = _finite(bid_qty), _finite(ask_qty)
    last, source_ts = _finite(last), _finite(source_ts)
    age = None if source_ts is None else max(0.0, bucket_ts + 1.0 - source_ts)
    valid_bbo = bool(bid and ask and bid > 0 and ask >= bid)
    fresh = bool(valid_bbo and age is not None and age <= MAX_SOURCE_AGE_SEC)
    buy_qty = sell_qty = 0.0
    buy_notional = sell_notional = 0.0
    trade_count = 0
    for trade in trades or ():
        ts = _finite((trade or {}).get("received_ts"))
        price = _finite((trade or {}).get("p"))
        qty = _finite((trade or {}).get("v"))
        if ts is None or not (bucket_ts <= ts < bucket_ts + 1) or not price or not qty:
            continue
        side = str((trade or {}).get("S") or "").upper()
        trade_count += 1
        if side == "BUY":
            buy_qty += abs(qty); buy_notional += abs(qty) * price
        elif side == "SELL":
            sell_qty += abs(qty); sell_notional += abs(qty) * price
    row = {
        "schema": SCHEMA, "symbol": symbol, "bucket_ts": bucket_ts,
        "source_ts": source_ts, "source_age_sec": None if age is None else round(age, 6),
        "fresh": fresh, "valid_bbo": valid_bbo,
        "bid": bid, "ask": ask,
        "bid_qty": None if bid_qty is None else abs(bid_qty),
        "ask_qty": None if ask_qty is None else abs(ask_qty),
        "last": last,
        "spread_usd": None if not valid_bbo else round(ask - bid, 8),
        "trade_count": trade_count,
        "buy_qty": round(buy_qty, 8), "sell_qty": round(sell_qty, 8),
        "buy_vwap": None if not buy_qty else round(buy_notional / buy_qty, 8),
        "sell_vwap": None if not sell_qty else round(sell_notional / sell_qty, 8),
    }
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
    row["row_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return row


def window_reference(signal_ts, required_end_ts) -> dict:
    start = int(float(signal_ts))
    end = int(math.ceil(float(required_end_ts)))
    return {
        "schema": "microstructure_window_reference_v1",
        "source_file": FILE_NAME,
        "required_start_ts": start,
        "required_end_ts": end,
        "required_bucket_count": max(0, end - start),
        "qualification_model": "CONSERVATIVE_BBO_DEPTH_TAPE",
    }


def validate_window(rows, reference) -> dict:
    """Require one fresh, valid, unique bucket for every required second."""
    start = int(reference.get("required_start_ts") or 0)
    end = int(reference.get("required_end_ts") or 0)
    expected = set(range(start, end))
    seen, duplicate, invalid = set(), set(), []
    for row in rows or ():
        try:
            ts = int(row.get("bucket_ts"))
        except (TypeError, ValueError, AttributeError):
            continue
        if ts not in expected:
            continue
        if ts in seen:
            duplicate.add(ts)
        seen.add(ts)
        if row.get("schema") != SCHEMA or row.get("fresh") is not True or row.get("valid_bbo") is not True:
            invalid.append(ts)
    missing = sorted(expected - seen)
    eligible = bool(expected and not missing and not duplicate and not invalid)
    return {
        "schema": "microstructure_window_eligibility_v1", "eligible": eligible,
        "expected_buckets": len(expected), "observed_buckets": len(seen),
        "missing_buckets": missing[:100], "duplicate_buckets": sorted(duplicate)[:100],
        "invalid_or_stale_buckets": sorted(set(invalid))[:100],
        "reason": "COMPLETE" if eligible else "INCOMPLETE_OR_STALE_MICROSTRUCTURE",
    }
