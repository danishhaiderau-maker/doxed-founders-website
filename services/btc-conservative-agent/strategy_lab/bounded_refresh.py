"""One-slot TTL/single-flight refresh with a bounded caller wait.

A timed-out Python thread cannot be safely killed. It retains the SINGLE daemon
slot until it finishes; no replacement workers/queues accumulate. Recovery is
automatic when it returns; a permanently stuck loader needs a process restart.
No caller holds the lock while doing I/O or invoking a loader.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


class BoundedRefresh:
    def __init__(self, *, ttl=60.0, retry=5.0, clock=time.monotonic, wall=time.time):
        self.ttl, self.retry, self.clock, self.wall = ttl, retry, clock, wall
        self._lock = threading.Lock()
        self._flight = None
        self._good = None
        self._failure = None

    def _reply(self, key, status, reason=None, *, bound=None):
        # Last-good of this key, unless a flight-bound result is supplied.
        # FRESH is only honest when bound to this key's own completed result.
        good = bound if bound is not None else (
            self._good if self._good is not None and self._good[0] == key else None
        )
        if status == "FRESH" and (good is None or good[0] != key):
            status, reason, good = "ERROR", reason or "fresh result missing matching identity", None
        age = max(0.0, self.clock()-good[1]) if good else None
        return {"value": good[3] if good else None, "cache": {
            "status": status, "partial": status not in ("FRESH", "CACHED"),
            "stale": good is not None and (age >= self.ttl or status not in ("FRESH", "CACHED")),
            "cached_as_of": datetime.fromtimestamp(good[2], timezone.utc).isoformat() if good else None,
            "cache_age_sec": round(age, 3) if age is not None else None,
            "ttl_sec": self.ttl, "reason": reason,
            "refresh_in_flight": self._flight is not None,
        }}

    def get(self, key, loader, *, timeout=2.0):
        deadline = self.clock()+max(0.0, timeout)
        remaining = lambda: max(0.0, deadline-self.clock())
        if not self._lock.acquire(timeout=remaining()):
            return {"value": None, "cache": {"status": "TIMEOUT", "partial": True, "stale": False,
                    "cached_as_of": None, "cache_age_sec": None, "ttl_sec": self.ttl,
                    "reason": "cache lock deadline", "refresh_in_flight": None}}
        try:
            if self._good and self._good[0] == key and self.clock()-self._good[1] < self.ttl:
                return self._reply(key, "CACHED")
            if self._failure and self._failure[0] == key and self.clock() < self._failure[1]:
                return self._reply(key, "ERROR", self._failure[2])
            if self._flight is None:
                flight = {"key": key, "event": threading.Event(), "error": None, "result": None}
                try:
                    thread = threading.Thread(target=self._run, args=(flight, loader),
                                              name="bounded-read-refresh", daemon=True)
                    self._flight = flight
                    thread.start()
                except Exception:
                    # Unstarted flights must not occupy the slot. Re-raise so the
                    # caller sees the start error; later calls can start a worker.
                    if self._flight is flight:
                        self._flight = None
                    raise
            else:
                flight = self._flight
                if flight["key"] != key:
                    return self._reply(key, "BUSY", "single refresh slot occupied by another input key")
        finally:
            self._lock.release()
        finished = flight["event"].wait(remaining())
        if not self._lock.acquire(timeout=remaining()):
            return {"value": None, "cache": {"status": "TIMEOUT", "partial": True, "stale": False,
                    "cached_as_of": None, "cache_age_sec": None, "ttl_sec": self.ttl,
                    "reason": "response deadline", "refresh_in_flight": not finished}}
        try:
            if not finished:
                return self._reply(key, "TIMEOUT", "refresh deadline; single worker retained, no replacement spawned")
            if flight["error"]:
                return self._reply(key, "ERROR", flight["error"])
            bound = flight.get("result")
            if bound is None or bound[0] != key:
                return self._reply(key, "ERROR", "refresh result identity superseded")
            return self._reply(key, "FRESH", bound=bound)
        finally:
            self._lock.release()

    def _run(self, flight, loader):
        value, error = None, None
        try:
            value = loader()
        except Exception as exc:  # one broken source never wedges waiting callers
            error = type(exc).__name__+": "+str(exc)[:300]
        finally:
            with self._lock:
                if error is None:
                    result = (flight["key"], self.clock(), self.wall(), value)
                    flight["result"] = result
                    self._good = result
                    self._failure = None
                else:
                    self._failure = (flight["key"], self.clock()+self.retry, error)
                flight["error"] = error
                self._flight = None
                flight["event"].set()
