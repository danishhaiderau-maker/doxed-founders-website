"""Fill- and signal-anchored execution markouts (research evidence only).

Two append-only evidence streams share one sampler:

* ``fill_markouts.jsonl``: every paper fill, marked to the side-correct quote
  at +1s/+10s/+60s/+5m after the fill, so maker adverse selection and taker
  cost can be measured per fill.
* ``taker_signal_counterfactuals.jsonl``: for every directional shared AI
  signal, what an immediate taker entry would have paid after each modelled
  latency, and how that entry marked out afterwards. Nothing here places or
  changes an order.
"""
from __future__ import annotations

import math
import threading
from typing import Any, Mapping

FILL_SCHEMA = "fill_markout_v1"
FILL_FILE = "fill_markouts.jsonl"
TAKER_SCHEMA = "taker_signal_counterfactual_v1"
TAKER_FILE = "taker_signal_counterfactuals.jsonl"
MARKOUT_HORIZONS_SEC = (1, 10, 60, 300)
TAKER_LATENCIES_SEC = (0.25, 1.0, 2.0)
COUNTERFACTUAL_MARGIN_USD = 0.25
COUNTERFACTUAL_LEVERAGE = 100.0
# A horizon sampled later than this is recorded but flagged, never silently
# re-labelled as on-time.
MAX_SAMPLE_LAG_SEC = 2.5
MAX_PENDING = 4000


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def direction_sign(direction: str) -> int:
    direction = str(direction or "").upper()
    return 1 if direction == "LONG" else -1 if direction == "SHORT" else 0


def markout_bps(sign: int, entry: float, price: float) -> float | None:
    entry, price = _finite(entry), _finite(price)
    if not sign or not entry or not price or entry <= 0:
        return None
    return round(sign * (price - entry) / entry * 1e4, 4)


def quote_sample(*, sign: int, entry: float, bid, ask, last, quote_ts, now: float,
                 due_ts: float, horizon: float) -> dict[str, Any]:
    bid, ask, last, quote_ts = _finite(bid), _finite(ask), _finite(last), _finite(quote_ts)
    valid = bool(bid and ask and bid > 0 and ask >= bid)
    mid = (bid + ask) / 2.0 if valid else None
    exit_touch = (bid if sign > 0 else ask) if valid else None
    lag = float(now) - float(due_ts)
    return {
        "horizon_sec": horizon,
        "sampled_ts": round(float(now), 3),
        "sample_lag_sec": round(lag, 3),
        "on_time": 0.0 <= lag <= MAX_SAMPLE_LAG_SEC,
        "quote_age_sec": None if quote_ts is None else round(float(now) - quote_ts, 3),
        "bid": bid, "ask": ask, "last": last, "mid": mid,
        "markout_mid_bps": markout_bps(sign, entry, mid),
        "markout_exit_touch_bps": markout_bps(sign, entry, exit_touch),
    }


class MarkoutBook:
    """Thread-safe pending markouts; ``sample`` returns rows whose horizons are all due."""

    def __init__(self, horizons=MARKOUT_HORIZONS_SEC, max_pending: int = MAX_PENDING):
        self.horizons = tuple(float(h) for h in horizons)
        self.max_pending = int(max_pending)
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.dropped = 0

    def register(self, key: str, *, anchor_ts: float, entry_price: float,
                 direction: str, row: Mapping[str, Any]) -> bool:
        sign = direction_sign(direction)
        entry = _finite(entry_price)
        if not key or not sign or not entry or entry <= 0:
            return False
        with self._lock:
            if key in self._pending:
                return False
            if len(self._pending) >= self.max_pending:
                self.dropped += 1
                return False
            self._pending[key] = {
                "row": dict(row), "anchor_ts": float(anchor_ts),
                "entry": entry, "sign": sign, "samples": {},
            }
        return True

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def sample(self, *, now: float, bid, ask, last, quote_ts) -> list[dict[str, Any]]:
        done = []
        with self._lock:
            for key, item in list(self._pending.items()):
                for horizon in self.horizons:
                    label = f"{horizon:g}s"
                    due = item["anchor_ts"] + horizon
                    if label in item["samples"] or now < due:
                        continue
                    item["samples"][label] = quote_sample(
                        sign=item["sign"], entry=item["entry"], bid=bid, ask=ask,
                        last=last, quote_ts=quote_ts, now=now, due_ts=due, horizon=horizon,
                    )
                if len(item["samples"]) == len(self.horizons):
                    row = dict(item["row"])
                    row["markouts"] = item["samples"]
                    row["markouts_complete"] = all(s["on_time"] for s in item["samples"].values())
                    done.append(row)
                    del self._pending[key]
        return done
