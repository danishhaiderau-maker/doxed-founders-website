"""Single-worker, bounded delivery for non-authoritative research evidence.

The caller must complete authoritative in-memory state before enqueueing.  This
worker is deliberately generic so its queueing, retry, and shutdown behaviour
can be tested without importing the trading runtime.
"""
from __future__ import annotations

import copy
import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, Optional


class BoundedEvidenceWorker:
    def __init__(
        self,
        handler: Callable[[Dict[str, Any]], None],
        *,
        max_queue: int = 256,
        max_retries: int = 2,
        completed_keys: int = 2048,
        name: str = "evidence-writer",
        clock: Callable[[], float] = time.time,
        on_dead_letter: Optional[Callable[[Dict[str, Any]], None]] = None,
        handler_timeout_sec: Optional[float] = None,
    ) -> None:
        self._handler = handler
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._max_retries = max(0, int(max_retries))
        self._completed_limit = max(1, int(completed_keys))
        self._clock = clock
        self._on_dead_letter = on_dead_letter
        self._handler_timeout_sec = (
            None
            if handler_timeout_sec is None
            else max(0.01, float(handler_timeout_sec))
        )
        self._lock = threading.Lock()
        self._active_keys = set()
        self._completed = deque()
        self._completed_set = set()
        self._dead_letters = deque(maxlen=max_queue)
        self._timed_out_handler: Optional[threading.Thread] = None
        self._accepting = True
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _invoke_handler(self, job: Dict[str, Any]) -> None:
        """Run one handler with an optional hard wall-clock budget.

        The timeout is opt-in because some evidence writers are authoritative
        and must retain their existing synchronous delivery semantics.  For
        optional research hooks, the handler runs in a daemon helper so a
        wedged filesystem/library call cannot consume the sole queue worker
        forever.  Python cannot safely kill a blocked thread; timed-out work is
        therefore dead-lettered and any eventual late write remains derived,
        idempotent evidence rather than scheduler authority.
        """
        if self._handler_timeout_sec is None:
            self._handler(job)
            return
        with self._lock:
            prior = self._timed_out_handler
            if prior is not None and prior.is_alive():
                raise TimeoutError(
                    "previous timed-out evidence handler is still running"
                )
            self._timed_out_handler = None
        finished = threading.Event()
        outcome: Dict[str, Any] = {}

        def invoke() -> None:
            try:
                self._handler(job)
            except BaseException as exc:  # propagated on the owning worker
                outcome["error"] = exc
            finally:
                finished.set()

        helper = threading.Thread(
            target=invoke,
            name=f"{self._thread.name}-handler",
            daemon=True,
        )
        helper.start()
        if not finished.wait(self._handler_timeout_sec):
            with self._lock:
                self._timed_out_handler = helper
            raise TimeoutError(
                f"evidence handler exceeded {self._handler_timeout_sec:.3f}s"
            )
        if "error" in outcome:
            raise outcome["error"]

    def _dead_letter(self, record: Dict[str, Any]) -> None:
        with self._lock:
            self._dead_letters.append(record)
        if self._on_dead_letter is not None:
            try:
                self._on_dead_letter(copy.deepcopy(record))
            except Exception:
                pass

    def submit(
        self,
        key: str,
        payload: Dict[str, Any],
        *,
        source_ts: Optional[float] = None,
    ) -> bool:
        """Enqueue one immutable snapshot; duplicate active/completed keys are OK."""
        now = self._clock()
        stable_key = str(key or "")
        if not stable_key:
            raise ValueError("evidence key is required")
        with self._lock:
            if stable_key in self._active_keys or stable_key in self._completed_set:
                return False
            if not self._accepting:
                stopped_record = {
                    "key": stable_key, "reason": "worker_stopped", "failed_ts": now,
                }
            else:
                stopped_record = None
                self._active_keys.add(stable_key)
        if stopped_record is not None:
            self._dead_letter(stopped_record)
            return False
        job = {
            "key": stable_key,
            "payload": copy.deepcopy(payload),
            "source_ts": float(source_ts if source_ts is not None else now),
            "enqueued_ts": now,
            "attempt": 0,
        }
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            with self._lock:
                self._active_keys.discard(stable_key)
            self._dead_letter({
                **job, "reason": "queue_full", "failed_ts": self._clock(),
            })
            return False

    def _mark_completed(self, key: str) -> None:
        with self._lock:
            self._active_keys.discard(key)
            if key in self._completed_set:
                return
            self._completed.append(key)
            self._completed_set.add(key)
            while len(self._completed) > self._completed_limit:
                self._completed_set.discard(self._completed.popleft())

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                try:
                    self._invoke_handler(job)
                except Exception as exc:
                    job["attempt"] += 1
                    if job["attempt"] <= self._max_retries:
                        try:
                            self._queue.put_nowait(job)
                        except queue.Full:
                            with self._lock:
                                self._active_keys.discard(job["key"])
                            self._dead_letter({
                                **job,
                                "reason": "retry_queue_full",
                                "error": repr(exc),
                                "failed_ts": self._clock(),
                            })
                    else:
                        with self._lock:
                            self._active_keys.discard(job["key"])
                        self._dead_letter({
                            **job,
                            "reason": "retries_exhausted",
                            "error": repr(exc),
                            "failed_ts": self._clock(),
                        })
                else:
                    self._mark_completed(job["key"])
            finally:
                self._queue.task_done()

    def shutdown(self, *, drain_timeout: float = 5.0) -> bool:
        """Stop accepting, drain bounded work, then terminate the daemon thread."""
        with self._lock:
            self._accepting = False
        deadline = time.monotonic() + max(0.0, drain_timeout)
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        drained = self._queue.unfinished_tasks == 0
        if drained and self._thread.is_alive():
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                drained = False
            else:
                self._thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return drained

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "queued": self._queue.qsize(),
                "unfinished": self._queue.unfinished_tasks,
                "active": len(self._active_keys),
                "completed": len(self._completed_set),
                "dead_letters": copy.deepcopy(list(self._dead_letters)),
                "timed_out_handler_alive": bool(
                    self._timed_out_handler is not None
                    and self._timed_out_handler.is_alive()
                ),
                "accepting": self._accepting,
            }
