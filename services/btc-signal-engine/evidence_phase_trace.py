"""Bounded in-memory timing for optional evidence IO; never stores payloads."""
from collections import deque
from contextlib import contextmanager
import hashlib
import threading
import time


class EvidencePhaseTrace:
    HOOKS = frozenset(("ai_reason", "reversal_study"))
    PHASES = frozenset(("hook", "research_gate_wait", "path_lock_wait", "validation",
                        "rotation", "append_write", "file_fsync", "validation_receipt",
                        "replay_start", "completion_lock_wait"))

    def __init__(self, *, clock=time.monotonic, max_active=8, history=32):
        self.clock = clock
        self.max_active = max(1, min(int(max_active), 32))
        self._history = deque(maxlen=max(1, min(int(history), 128)))
        self._active = {}
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sequence = 0
        self._dropped = 0

    @contextmanager
    def hook(self, hook, key):
        if hook not in self.HOOKS:
            raise ValueError("UNSUPPORTED_EVIDENCE_HOOK")
        previous = getattr(self._local, "token", None)
        now = self.clock()
        with self._lock:
            self._sequence += 1
            token = self._sequence
            if len(self._active) >= self.max_active:
                self._dropped += 1
                token = None
            else:
                self._active[token] = {"sequence": token, "hook": hook,
                    "key_sha256": hashlib.sha256((key[:256] if type(key) is str else "NON_STRING_KEY").encode()).hexdigest(),
                    "started": now, "phase": "hook", "phase_started": now,
                    "phase_seconds": {}, "outcome": "RUNNING"}
        self._local.token = token
        outcome = "RETURNED"
        try:
            yield
        except BaseException:
            outcome = "RAISED"
            raise
        finally:
            with self._lock:
                row = self._active.pop(token, None)
                if row is not None:
                    row["elapsed_seconds"] = max(0.0, self.clock() - row["started"])
                    row["outcome"] = outcome
                    self._history.append(row)
            self._local.token = previous

    @contextmanager
    def phase(self, phase):
        if phase not in self.PHASES:
            raise ValueError("UNSUPPORTED_EVIDENCE_PHASE")
        token = getattr(self._local, "token", None)
        start = self.clock()
        with self._lock:
            row = self._active.get(token)
            previous = (row["phase"], row["phase_started"]) if row else ("hook", start)
            if row:
                row.update(phase=phase, phase_started=start)
        try:
            yield
        finally:
            with self._lock:
                row = self._active.get(token)
                if row:
                    durations = row["phase_seconds"]
                    durations[phase] = durations.get(phase, 0.0) + max(0.0, self.clock() - start)
                    row.update(phase=previous[0], phase_started=previous[1])

    @contextmanager
    def acquire(self, lock, phase):
        """Retain the original blocking lock semantics; time acquisition only."""
        with self.phase(phase):
            lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def snapshot(self):
        now = self.clock()
        with self._lock:
            active = [{"sequence": row["sequence"], "hook": row["hook"],
                       "key_sha256": row["key_sha256"], "phase": row["phase"],
                       "phase_age_seconds": max(0.0, now - row["phase_started"]),
                       "elapsed_seconds": max(0.0, now - row["started"]),
                       "phase_seconds": dict(row["phase_seconds"])} for row in self._active.values()]
            history = [{key: (dict(value) if isinstance(value, dict) else value)
                        for key, value in row.items() if key not in ("started", "phase_started")}
                       for row in self._history]
            return {"schema": "evidence_phase_timing_v1", "active": active,
                    "recent": history, "dropped_traces": self._dropped}
