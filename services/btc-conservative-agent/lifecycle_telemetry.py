"""Full trade-lifecycle telemetry (Phase 5 observability).

Captures signal -> order -> fill -> open -> partial -> close with per-stage
timestamps and computed latencies (signal lag, order transit, fill latency,
paper-vs-real price comparison), plus feed freshness and CPU/memory/slowness
telemetry so every gap is visible in the API.

This is a lightweight, in-memory, thread-safe recorder. The slow-path flush is
the caller's concern; the recorder itself never does network I/O and never
touches exchange credentials. It fails closed: an unknown stage or an
out-of-order timestamp is rejected rather than silently mis-recorded.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

SCHEMA = "trade_lifecycle_telemetry_v1"

STAGE_SIGNAL = "SIGNAL"
STAGE_ORDER = "ORDER"
STAGE_FILL = "FILL"
STAGE_OPEN = "OPEN"
STAGE_PARTIAL = "PARTIAL"
STAGE_CLOSE = "CLOSE"
STAGES = (STAGE_SIGNAL, STAGE_ORDER, STAGE_FILL, STAGE_OPEN, STAGE_PARTIAL, STAGE_CLOSE)
_ORDER = {s: i for i, s in enumerate(STAGES)}

# Latency field labels (computed from adjacent stage timestamps).
LATENCY_SIGNAL_LAG = "signal_lag_sec"
LATENCY_ORDER_TRANSIT = "order_transit_sec"
LATENCY_FILL_LATENCY = "fill_latency_sec"
LATENCY_HOLD_SEC = "hold_sec"


class LifecycleTelemetry:
    def __init__(self, max_trades: int = 10_000):
        self._lock = threading.RLock()
        self._trades: dict[str, dict] = {}
        self._max = max_trades
        self._feed: dict[str, Any] = {}
        self._resource: dict[str, Any] = {}

    def begin(self, trade_id: str, *, ts: float | None = None) -> dict:
        ts = time.time() if ts is None else float(ts)
        with self._lock:
            row = self._trades.get(trade_id)
            if row is None:
                row = {"trade_id": trade_id, "stages": {}, "latencies": {}, "paper_vs_real": {}}
                self._trades[trade_id] = row
                self._trim()
            row["began_ts"] = ts
            return dict(row)

    def stage(self, trade_id: str, stage: str, *, ts: float | None = None,
              extra: dict | None = None) -> dict:
        """Record a stage timestamp. Out-of-order stages are refused (fail closed)."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage: {stage!r}")
        ts = time.time() if ts is None else float(ts)
        with self._lock:
            row = self._trades.get(trade_id)
            if row is None:
                row = {"trade_id": trade_id, "stages": {}, "latencies": {}, "paper_vs_real": {}}
                self._trades[trade_id] = row
                self._trim()
                row["began_ts"] = ts
            stages = row.setdefault("stages", {})
            prior = stages.get(stage)
            if prior is not None:
                return dict(row)  # idempotent: never move a recorded stage backward
            # Refuse to regress: an earlier stage recorded after a later one.
            for later in STAGES[_ORDER[stage] + 1:]:
                if later in stages:
                    raise ValueError(f"stage {stage} for {trade_id} recorded after {later}")
            # Refuse to regress: a later stage must not precede an earlier one.
            for earlier in STAGES[:_ORDER[stage]]:
                if earlier in stages and float(stages[earlier]) > ts:
                    raise ValueError(f"stage {stage} for {trade_id} precedes {earlier}")
            stages[stage] = ts
            if extra:
                row.setdefault("extra", {}).setdefault(stage, {}).update(extra)
            self._recompute_latencies(row)
            return dict(row)

    def paper_vs_real(self, trade_id: str, *, paper_price: float, real_price: float,
                      ts: float | None = None) -> dict:
        ts = time.time() if ts is None else float(ts)
        with self._lock:
            row = self._trades.get(trade_id)
            if row is None:
                row = {"trade_id": trade_id, "stages": {}, "latencies": {}, "paper_vs_real": {}}
                self._trades[trade_id] = row
                self._trim()
                row["began_ts"] = ts
            base = abs(real_price) or 1.0
            row["paper_vs_real"] = {
                "paper_price": paper_price,
                "real_price": real_price,
                "diff_usd": round(float(paper_price) - float(real_price), 8),
                "diff_pct": round((float(paper_price) - float(real_price)) / base, 8),
                "measured_at": ts,
            }
            return dict(row)

    def update_feed(self, *, freshness_sec: float | None, stale: bool,
                    venue: str = "bitfinex", ts: float | None = None) -> None:
        ts = time.time() if ts is None else float(ts)
        with self._lock:
            self._feed = {"venue": venue, "freshness_sec": freshness_sec,
                          "stale": bool(stale), "updated_at": ts}

    def update_resource(self, *, cpu_pct: float | None = None, rss_mb: float | None = None,
                        slowdown_ratio: float | None = None, ts: float | None = None) -> None:
        ts = time.time() if ts is None else float(ts)
        with self._lock:
            self._resource = {"cpu_pct": cpu_pct, "rss_mb": rss_mb,
                              "slowdown_ratio": slowdown_ratio, "updated_at": ts}

    def _recompute_latencies(self, row: dict) -> None:
        stages = row.get("stages") or {}
        lat = {}
        if STAGE_SIGNAL in stages:
            if row.get("began_ts"):
                lat[LATENCY_SIGNAL_LAG] = round(float(stages[STAGE_SIGNAL]) - float(row["began_ts"]), 6)
        if STAGE_ORDER in stages and STAGE_SIGNAL in stages:
            lat[LATENCY_ORDER_TRANSIT] = round(float(stages[STAGE_ORDER]) - float(stages[STAGE_SIGNAL]), 6)
        if STAGE_FILL in stages and STAGE_ORDER in stages:
            lat[LATENCY_FILL_LATENCY] = round(float(stages[STAGE_FILL]) - float(stages[STAGE_ORDER]), 6)
        if STAGE_CLOSE in stages and STAGE_OPEN in stages:
            lat[LATENCY_HOLD_SEC] = round(float(stages[STAGE_CLOSE]) - float(stages[STAGE_OPEN]), 6)
        row["latencies"] = lat

    def _trim(self) -> None:
        if len(self._trades) <= self._max:
            return
        # Drop the oldest-started trades; telemetry is a rolling window.
        by_age = sorted(self._trades.items(), key=lambda kv: kv[1].get("began_ts") or 0.0)
        for trade_id, _ in by_age[: len(self._trades) - self._max]:
            self._trades.pop(trade_id, None)

    def get(self, trade_id: str) -> dict | None:
        with self._lock:
            row = self._trades.get(trade_id)
            return dict(row) if row is not None else None

    def snapshot(self) -> dict:
        with self._lock:
            trades = [dict(v) for v in self._trades.values()]
        stalled = [t for t in trades if t.get("began_ts") and STAGE_CLOSE not in t.get("stages", {})
                   and (time.time() - float(t["began_ts"])) > 8 * 3600]
        return {
            "schema": SCHEMA,
            "trade_count": len(trades),
            "trades": trades,
            "stalled_trades": stalled,
            "feed": dict(self._feed),
            "resource": dict(self._resource),
            "computed_at": time.time(),
        }

    def close(self, trade_id: str, *, ts: float | None = None) -> dict:
        return self.stage(trade_id, STAGE_CLOSE, ts=ts)
