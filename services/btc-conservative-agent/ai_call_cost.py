"""Per-call DeepSeek cost from token usage (``ai_call_cost_v1``).

Cost is derived from the provider's ``usage`` block with a static USD per
million-token table (cache-hit input, cache-miss input, output). Rolling 24h
totals are kept in five-minute buckets and persisted to one small JSON file
with an atomic replace, so ``cost_usd_24h`` / ``calls_24h`` survive restarts.
Prices are a static table plus env overrides, not a provider quote: an
unknown model is priced with the default row and flagged unmatched.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

SCHEMA = "ai_call_cost_v1"
STATE_FILE = "ai_call_cost_state.json"
BUCKET_SEC = 300
WINDOW_SEC = 24 * 3600
PRICING_VERSION = "deepseek_static_v3_2_2025_09"

# USD per 1M tokens.
_DEEPSEEK_V3_2 = {"input_cache_hit": 0.028, "input_cache_miss": 0.28, "output": 0.42}
PRICING_PER_M = {
    "deepseek-chat": _DEEPSEEK_V3_2,
    "deepseek-reasoner": _DEEPSEEK_V3_2,
}
DEFAULT_PRICING_PER_M = _DEEPSEEK_V3_2
_ENV_OVERRIDES = {
    "input_cache_hit": "DEEPSEEK_PRICE_INPUT_CACHE_HIT_PER_M",
    "input_cache_miss": "DEEPSEEK_PRICE_INPUT_CACHE_MISS_PER_M",
    "output": "DEEPSEEK_PRICE_OUTPUT_PER_M",
}


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def pricing_for(model) -> tuple[dict, bool, bool]:
    """Return (USD-per-1M row, model_matched, env_overridden)."""
    key = str(model or "").strip().lower()
    row = dict(PRICING_PER_M.get(key) or DEFAULT_PRICING_PER_M)
    overridden = False
    for field, env in _ENV_OVERRIDES.items():
        raw = (os.environ.get(env) or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value >= 0:
            row[field] = value
            overridden = True
    return row, key in PRICING_PER_M, overridden


def compute_cost(model, usage) -> dict:
    """USD cost of one call from a provider ``usage`` dict."""
    usage = usage if isinstance(usage, dict) else {}
    prompt = _int(usage.get("prompt_tokens"))
    completion = _int(usage.get("completion_tokens"))
    hit = _int(usage.get("prompt_cache_hit_tokens"))
    miss = _int(usage.get("prompt_cache_miss_tokens"))
    cache_split_reported = "prompt_cache_hit_tokens" in usage or "prompt_cache_miss_tokens" in usage
    if not cache_split_reported:
        hit, miss = 0, prompt
    price, matched, overridden = pricing_for(model)
    cost = (
        hit * price["input_cache_hit"]
        + miss * price["input_cache_miss"]
        + completion * price["output"]
    ) / 1_000_000.0
    return {
        "cost_usd": round(cost, 8),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cache_hit_tokens": hit,
        "cache_miss_tokens": miss,
        "cache_split_reported": cache_split_reported,
        "pricing_model_matched": matched,
        "pricing_env_override": overridden,
    }


class AiCostLedger:
    def __init__(self, path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[int, list] = {}
        self._last: dict = {}
        self._persist_failures = 0
        self._last_persist_error = None
        self._load_error = None
        self._load()

    def _load(self) -> None:
        try:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"[:200]
            return
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            return
        for start, row in (raw.get("buckets") or {}).items():
            try:
                calls, cost = int(row[0]), float(row[1])
                self._buckets[int(start)] = [calls, cost]
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        if isinstance(raw.get("last"), dict):
            self._last = dict(raw["last"])

    def _prune_locked(self, now: float) -> None:
        floor = now - WINDOW_SEC - BUCKET_SEC
        for start in [s for s in self._buckets if s < floor]:
            del self._buckets[start]

    def _persist_locked(self) -> None:
        body = json.dumps({
            "schema": SCHEMA,
            "pricing_version": PRICING_VERSION,
            "buckets": {str(k): v for k, v in sorted(self._buckets.items())},
            "last": self._last,
        }, separators=(",", ":"), sort_keys=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(body)
                os.replace(name, self.path)
            finally:
                try:
                    os.unlink(name)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            self._persist_failures += 1
            self._last_persist_error = f"{type(exc).__name__}: {exc}"[:200]

    def record(self, model, usage, *, purpose: str = "", estimated: bool = False,
               now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        row = compute_cost(model, usage)
        row.update({"ts": round(now, 3), "model": str(model or "")[:64],
                    "purpose": str(purpose or "")[:48], "tokens_estimated": bool(estimated)})
        with self._lock:
            start = int(now // BUCKET_SEC) * BUCKET_SEC
            bucket = self._buckets.setdefault(start, [0, 0.0])
            bucket[0] += 1
            bucket[1] = round(bucket[1] + row["cost_usd"], 8)
            self._last = row
            self._prune_locked(now)
            self._persist_locked()
        return row

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        floor = now - WINDOW_SEC
        with self._lock:
            window = [row for start, row in self._buckets.items() if start + BUCKET_SEC > floor]
            last = dict(self._last)
            failures = self._persist_failures
            last_error = self._last_persist_error
        last_ts = last.get("ts")
        return {
            "schema": SCHEMA,
            "pricing_version": PRICING_VERSION,
            "last_call_cost_usd": last.get("cost_usd"),
            "last_call_at_ts": last_ts,
            "last_call_age_sec": round(max(0.0, now - float(last_ts)), 1) if last_ts else None,
            "last_call_model": last.get("model"),
            "last_call_purpose": last.get("purpose"),
            "last_call_tokens": {
                key: last.get(key) for key in (
                    "prompt_tokens", "completion_tokens", "cache_hit_tokens", "cache_miss_tokens",
                )
            } if last else None,
            "last_call_tokens_estimated": last.get("tokens_estimated"),
            "pricing_model_matched": last.get("pricing_model_matched"),
            "cost_usd_24h": round(sum(row[1] for row in window), 6),
            "calls_24h": int(sum(row[0] for row in window)),
            "window_bucket_sec": BUCKET_SEC,
            "persisted_file": self.path.name,
            "persist_failures": failures,
            "persist_last_error": last_error,
            "load_error": self._load_error,
        }
