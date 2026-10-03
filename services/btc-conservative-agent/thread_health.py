"""Per-thread heartbeats and swallowed-failure counters (``thread_health_v1``).

Instrumentation for loops whose errors were previously log-only or silently
swallowed. Every primitive is in-memory, O(1) per call, guarded by one short
private mutex, and never raises into its caller: instrumentation must not be
able to crash or stall trading. ``snapshot()`` / ``summary()`` return plain
JSON-safe dicts so a periodic sampler (``runtime_telemetry_v1``) can fold them
in without importing bot.py.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Mapping, Optional

SCHEMA = "thread_health_v1"
STALE_MULTIPLIER = 3.0
_ERROR_TEXT_MAX = 200

_RATE_LIMIT_MARKERS = ("429", "rate limit", "ratelimit", "too many requests")


def _error_text(exc) -> str:
    if exc is None:
        return ""
    if isinstance(exc, BaseException):
        text = f"{type(exc).__name__}: {exc}"
    else:
        text = str(exc)
    return text[:_ERROR_TEXT_MAX]


def _age(now: float, ts) -> Optional[float]:
    try:
        ts = float(ts or 0.0)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, now - ts), 3) if ts > 0 else None


def is_rate_limit_error(exc) -> bool:
    """True for HTTP 429 / provider rate-limit failures (status attr or text)."""
    if exc is None:
        return False
    for attr in ("http_status", "status_code", "status"):
        if getattr(exc, attr, None) == 429:
            return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    if type(exc).__name__ in ("RateLimitExceeded", "DDoSProtection"):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


class ThreadHealthRegistry:
    """Liveness, progress and restart evidence for named long-running loops.

    A loop is *monitored* once ``register`` gives it an interval; ``stale``
    applies only to monitored loops that have started. ``restart`` is fed by
    the generic ``safe_thread`` supervisor for every name, monitored or not.
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}

    def _row(self, name: str) -> dict:
        row = self._rows.get(name)
        if row is None:
            row = {
                "interval_sec": None, "stale_after_sec": None,
                "started_ts": 0.0, "thread": None,
                "last_tick_ts": 0.0, "iterations": 0,
                "error_count": 0, "last_error": None, "last_error_ts": 0.0,
                "restarts": 0, "last_restart_ts": 0.0, "last_restart_reason": None,
            }
            self._rows[name] = row
        return row

    def register(self, name: str, interval_sec: float, *, min_stale_sec: float = 0.0) -> None:
        try:
            interval = max(0.001, float(interval_sec))
            stale_after = max(STALE_MULTIPLIER * interval, float(min_stale_sec or 0.0))
            with self._lock:
                row = self._row(str(name))
                row["interval_sec"] = interval
                row["stale_after_sec"] = stale_after
        except Exception:
            pass

    def bind(self, name: str, thread: Optional[threading.Thread] = None, now: Optional[float] = None) -> None:
        """Record the thread running ``name`` (alive evidence) and its start time."""
        try:
            now = float(self._clock() if now is None else now)
            with self._lock:
                row = self._row(str(name))
                row["thread"] = thread or threading.current_thread()
                row["started_ts"] = row["started_ts"] or now
        except Exception:
            pass

    def beat(self, name: str, now: Optional[float] = None) -> None:
        try:
            now = float(self._clock() if now is None else now)
            with self._lock:
                row = self._row(str(name))
                row["last_tick_ts"] = now
                row["iterations"] += 1
                row["started_ts"] = row["started_ts"] or now
        except Exception:
            pass

    def error(self, name: str, exc, now: Optional[float] = None) -> None:
        try:
            now = float(self._clock() if now is None else now)
            text = _error_text(exc)
            with self._lock:
                row = self._row(str(name))
                row["error_count"] += 1
                row["last_error"] = text
                row["last_error_ts"] = now
        except Exception:
            pass

    def restart(self, name: str, reason, now: Optional[float] = None) -> None:
        try:
            now = float(self._clock() if now is None else now)
            text = _error_text(reason)
            with self._lock:
                row = self._row(str(name))
                row["restarts"] += 1
                row["last_restart_ts"] = now
                row["last_restart_reason"] = text
        except Exception:
            pass

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        with self._lock:
            rows = {name: dict(row) for name, row in self._rows.items()}
        out = {}
        for name, row in sorted(rows.items()):
            thread = row.get("thread")
            started = bool(row["started_ts"] or row["last_tick_ts"])
            alive = thread.is_alive() if thread is not None else None
            tick_age = _age(now, row["last_tick_ts"])
            reference_age = tick_age if tick_age is not None else _age(now, row["started_ts"])
            stale_after = row["stale_after_sec"]
            stale = bool(
                stale_after is not None and started
                and (alive is False or (reference_age is not None and reference_age > stale_after))
            )
            out[name] = {
                "monitored": stale_after is not None,
                "started": started,
                "alive": alive,
                "last_tick_ts": row["last_tick_ts"] or None,
                "last_tick_age_sec": tick_age,
                "interval_sec": row["interval_sec"],
                "stale_after_sec": stale_after,
                "iterations": int(row["iterations"]),
                "restarts": int(row["restarts"]),
                "last_restart_age_sec": _age(now, row["last_restart_ts"]),
                "last_restart_reason": row["last_restart_reason"],
                "error_count": int(row["error_count"]),
                "last_error": row["last_error"],
                "last_error_age_sec": _age(now, row["last_error_ts"]),
                "stale": stale,
            }
        return out

    def summary(self, now: Optional[float] = None, snapshot: Optional[Mapping] = None) -> dict:
        threads = snapshot if snapshot is not None else self.snapshot(now)
        monitored = {name: row for name, row in threads.items() if row.get("monitored")}
        stale = sorted(name for name, row in monitored.items() if row.get("stale"))
        dead = sorted(name for name, row in monitored.items() if row.get("alive") is False)
        not_started = sorted(name for name, row in monitored.items() if not row.get("started"))
        return {
            "schema": SCHEMA,
            "ok": not stale and not dead,
            "monitored": len(monitored),
            "stale": stale,
            "dead": dead,
            "not_started": not_started,
            "restarts_total": sum(int(row.get("restarts") or 0) for row in threads.values()),
            "errors_total": sum(int(row.get("error_count") or 0) for row in monitored.values()),
        }


class FailureCounters:
    """Per-key failure counts for non-fatal writers/hooks that must stay non-fatal."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}

    def _row(self, key: str) -> dict:
        row = self._rows.get(key)
        if row is None:
            row = {"successes": 0, "failures": 0, "last_success_ts": 0.0,
                   "last_error": None, "last_error_ts": 0.0}
            self._rows[key] = row
        return row

    def success(self, key: str, now: Optional[float] = None) -> None:
        try:
            now = float(self._clock() if now is None else now)
            with self._lock:
                row = self._row(str(key))
                row["successes"] += 1
                row["last_success_ts"] = now
        except Exception:
            pass

    def failure(self, key: str, exc, now: Optional[float] = None) -> None:
        try:
            now = float(self._clock() if now is None else now)
            text = _error_text(exc)
            with self._lock:
                row = self._row(str(key))
                row["failures"] += 1
                row["last_error"] = text
                row["last_error_ts"] = now
        except Exception:
            pass

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        with self._lock:
            rows = {key: dict(row) for key, row in self._rows.items()}
        return {
            key: {
                "successes": int(row["successes"]),
                "failures": int(row["failures"]),
                "last_success_ts": row["last_success_ts"] or None,
                "last_error": row["last_error"],
                "last_error_ts": row["last_error_ts"] or None,
                "last_error_age_sec": _age(now, row["last_error_ts"]),
            }
            for key, row in sorted(rows.items())
        }

    def failures_total(self) -> int:
        with self._lock:
            return sum(int(row["failures"]) for row in self._rows.values())

    def recent_failure_keys(self, window_sec: float, now: Optional[float] = None,
                            exclude_error_prefixes: tuple = ()) -> list[str]:
        now = float(self._clock() if now is None else now)
        with self._lock:
            return sorted(
                key for key, row in self._rows.items()
                if row["failures"] and row["last_error_ts"] and now - row["last_error_ts"] <= window_sec
                and not str(row["last_error"] or "").startswith(tuple(exclude_error_prefixes))
            )

    def keys_with_error_prefix(self, prefixes: tuple) -> list[str]:
        with self._lock:
            return sorted(
                key for key, row in self._rows.items()
                if str(row["last_error"] or "").startswith(tuple(prefixes))
            )


class RateLimitCounters:
    """HTTP 429 / rate-limit hits per venue and client label."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}

    def hit(self, venue: str, client: str = "", now: Optional[float] = None) -> None:
        try:
            now = float(self._clock() if now is None else now)
            with self._lock:
                row = self._rows.setdefault(str(venue), {"hits_429": 0, "last_429_ts": 0.0, "clients": {}})
                row["hits_429"] += 1
                row["last_429_ts"] = now
                if client:
                    label = str(client)[:48]
                    row["clients"][label] = int(row["clients"].get(label, 0)) + 1
        except Exception:
            pass

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        with self._lock:
            rows = {venue: {**row, "clients": dict(row["clients"])} for venue, row in self._rows.items()}
        return {
            venue: {
                "hits_429": int(row["hits_429"]),
                "last_429_ts": row["last_429_ts"] or None,
                "last_429_age_sec": _age(now, row["last_429_ts"]),
                "clients": row["clients"],
            }
            for venue, row in sorted(rows.items())
        }


class QueueCounters:
    """Dropped-item counters for bounded queues; qsize is read at snapshot time."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._dropped: dict[str, int] = {}
        self._last_drop_ts: dict[str, float] = {}

    def dropped(self, name: str, count: int = 1, now: Optional[float] = None) -> None:
        try:
            now = float(self._clock() if now is None else now)
            with self._lock:
                key = str(name)
                self._dropped[key] = int(self._dropped.get(key, 0)) + int(count)
                self._last_drop_ts[key] = now
        except Exception:
            pass

    def snapshot(self, queues: Mapping[str, object], now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        with self._lock:
            dropped = dict(self._dropped)
            last = dict(self._last_drop_ts)
        out = {}
        for name in sorted(set(queues) | set(dropped)):
            queue = queues.get(name)
            try:
                qsize = int(queue.qsize()) if queue is not None else None
            except Exception:
                qsize = None
            try:
                maxsize = int(getattr(queue, "maxsize", 0)) or None
            except Exception:
                maxsize = None
            out[name] = {
                "qsize": qsize,
                "maxsize": maxsize,
                "dropped": int(dropped.get(name, 0)),
                "last_drop_age_sec": _age(now, last.get(name)),
            }
        return out
