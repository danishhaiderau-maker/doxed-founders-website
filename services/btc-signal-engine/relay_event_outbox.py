"""Crash-safe source outbox for signed Showcase relay lifecycle events.

The file contains no credentials.  Enqueue and acknowledgement are atomic file
replacements so an event is durable before any HTTP attempt and survives the
source position/order being removed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path


class RelayEventOutbox:
    SCHEMA = "relay_event_outbox_v1"

    def __init__(self, path: str | os.PathLike[str], ack_limit: int = 512, shared_lock=None):
        self.path = Path(path)
        self.ack_limit = max(1, int(ack_limit))
        self._lock = shared_lock or threading.RLock()
        self._wake = threading.Event()
        self._pending: dict[str, dict] = {}
        self._acks: list[dict] = []
        self._highwater: dict[str, int] = {}
        self.healthy = True
        self.recovery_error = None
        self._load()

    @staticmethod
    def canonical_body(payload: dict) -> bytes:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    @classmethod
    def payload_sha256(cls, payload: dict) -> str:
        return hashlib.sha256(cls.canonical_body(payload)).hexdigest()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self._lock, self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # Never spin-crash on a corrupt delivery file. Preserve the exact
            # bytes and fail closed; an operator can reconcile it against the
            # canonical paper lifecycle generation.
            quarantine = self.path.with_name(
                f"{self.path.name}.corrupt-{int(time.time() * 1000)}"
            )
            try:
                os.replace(self.path, quarantine)
                self._fsync_parent()
            except OSError:
                quarantine = None
            self.healthy = False
            self.recovery_error = (
                f"relay lifecycle corrupt; quarantined={quarantine}: {exc}"
            )
            return
        with self._lock:
            if not isinstance(value, dict) or value.get("schema") not in (
                self.SCHEMA, "paper_lifecycle_v1"
            ):
                quarantine = self.path.with_name(
                    f"{self.path.name}.corrupt-{int(time.time() * 1000)}"
                )
                try:
                    os.replace(self.path, quarantine)
                    self._fsync_parent()
                except OSError:
                    quarantine = None
                self.healthy = False
                self.recovery_error = (
                    "unsupported relay lifecycle generation; "
                    f"quarantined={quarantine}"
                )
                return
            source = value.get("relay_events") if value.get("schema") == "paper_lifecycle_v1" else value
            source = source if isinstance(source, dict) else {}
            for row in source.get("pending") or []:
                if isinstance(row, dict) and row.get("event_id"):
                    self._pending[str(row["event_id"])] = row
            self._acks = [row for row in (source.get("acks") or []) if isinstance(row, dict)][-self.ack_limit:]
            self._highwater = {
                str(key): int(value)
                for key, value in (source.get("sequence_highwater") or {}).items()
                if isinstance(value, int) and value >= 0
            }
            for row in [*self._pending.values(), *self._acks]:
                trade = str(row.get("trade_id") or "")
                if trade:
                    self._highwater[trade] = max(
                        self._highwater.get(trade, -1), int(row.get("event_seq") or 0)
                    )
            if self._pending:
                self._wake.set()

    def _persist(self, state_payload: dict | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        relay_value = {
            "schema": self.SCHEMA,
            "saved_at_unix": time.time(),
            "pending": list(self._pending.values()),
            "acks": self._acks[-self.ack_limit:],
            "sequence_highwater": self._highwater,
        }
        value = state_payload
        if value is None and self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as existing:
                    candidate = json.load(existing)
                if candidate.get("schema") == "paper_lifecycle_v1":
                    value = candidate
            except (OSError, ValueError, json.JSONDecodeError):
                value = None
        if isinstance(value, dict) and value.get("schema") == "paper_lifecycle_v1":
            value = copy.deepcopy(value)
            value["relay_events"] = relay_value
        else:
            value = relay_value
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            self._fsync_parent()
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def _fsync_parent(self) -> None:
        """Durably publish a rename where directory fsync is supported."""
        descriptor = None
        try:
            descriptor = os.open(str(self.path.parent), os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            # Windows does not permit opening a directory with os.open. The
            # file itself was flushed above; POSIX must not silently skip the
            # directory fence.
            if os.name != "nt":
                raise
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def enqueue_next(
        self, payload: dict, suggested: int | None = None, state_payload: dict | None = None
    ) -> dict:
        """Allocate sequence and persist the event in one critical section."""
        if not self.healthy:
            raise ValueError(self.recovery_error or "relay lifecycle unavailable")
        trade_id = str(payload.get("trade_id") or "").strip()
        event_type = str(payload.get("event") or "").strip()
        if not trade_id or not event_type:
            raise ValueError("relay event requires trade and type")
        with self._lock:
            floor = self._highwater.get(trade_id, -1) + 1
            sequence = max(floor, int(suggested)) if suggested is not None else floor
            stamped = copy.deepcopy(payload)
            stamped["event_seq"] = sequence
            stamped["event_id"] = str(stamped.get("event_id") or (
                f"{trade_id}:{event_type}:{sequence}:"
                f"{stamped.get('ts') or time.time_ns()}"
            ))
            if event_type == "POSITION_REDUCED":
                stamped["reduction_id"] = stamped["event_id"]
            row = self.enqueue(stamped, state_payload=state_payload)
            return row

    def enqueue(self, payload: dict, state_payload: dict | None = None) -> dict:
        event_id = str(payload.get("event_id") or "").strip()
        trade_id = str(payload.get("trade_id") or "").strip()
        event_type = str(payload.get("event") or "").strip()
        event_seq = payload.get("event_seq")
        if not event_id or not trade_id or not event_type or not isinstance(event_seq, int) or event_seq < 0:
            raise ValueError("relay event requires stable id, trade, type and non-negative integer sequence")
        digest = self.payload_sha256(payload)
        with self._lock:
            existing = self._pending.get(event_id)
            if existing:
                if existing.get("payload_sha256") != digest:
                    raise ValueError("conflicting relay event id")
                return copy.deepcopy(existing)
            if any(row.get("event_id") == event_id and row.get("payload_sha256") != digest for row in self._acks):
                raise ValueError("conflicting acknowledged relay event id")
            row = {
                "event_id": event_id, "trade_id": trade_id, "event_type": event_type,
                "event_seq": event_seq, "payload_sha256": digest, "payload": copy.deepcopy(payload),
                "created_at_unix": time.time(), "attempts": 0, "next_attempt_at_unix": 0.0,
                "last_error": None,
            }
            prior_highwater = self._highwater.get(trade_id)
            self._pending[event_id] = row
            self._highwater[trade_id] = max(
                self._highwater.get(trade_id, -1), int(event_seq)
            )
            try:
                self._persist(state_payload=state_payload)
            except Exception:
                self._pending.pop(event_id, None)
                if prior_highwater is None:
                    self._highwater.pop(trade_id, None)
                else:
                    self._highwater[trade_id] = prior_highwater
                raise
            self._wake.set()
            return copy.deepcopy(row)

    def due(self, now: float | None = None, limit: int = 100) -> list[dict]:
        if not self.healthy:
            return []
        now = time.time() if now is None else float(now)
        with self._lock:
            groups: dict[str, list[dict]] = {}
            for row in self._pending.values():
                groups.setdefault(str(row.get("trade_id")), []).append(row)
            for rows in groups.values():
                rows.sort(key=lambda row: (int(row.get("event_seq") or 0), float(row.get("created_at_unix") or 0)))
            # Never deliver N+1 while N is backing off. Interleave trade heads
            # so one busy lifecycle cannot starve every other trade.
            ready = [rows[0] for rows in groups.values()
                     if rows and float(rows[0].get("next_attempt_at_unix") or 0) <= now]
            ready.sort(key=lambda row: float(row.get("created_at_unix") or 0))
            return copy.deepcopy(ready[:max(1, int(limit))])

    def fail(self, event_id: str, error: object, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        with self._lock:
            row = self._pending.get(event_id)
            if not row:
                return
            before = copy.deepcopy(row)
            row["attempts"] = int(row.get("attempts") or 0) + 1
            row["last_error"] = str(error)[:160]
            row["next_attempt_at_unix"] = now + min(60.0, 0.5 * (2 ** min(row["attempts"], 7)))
            try:
                self._persist()
            except Exception:
                self._pending[event_id] = before
                raise

    def acknowledge(
        self, event_id: str, receipt: dict, state_payload: dict | None = None
    ) -> bool:
        with self._lock:
            row = self._pending.get(event_id)
            if not row:
                return False
            ack = receipt.get("durable_ack") if isinstance(receipt, dict) else None
            if not isinstance(ack, dict) or any((
                ack.get("event_id") != row["event_id"],
                ack.get("event_type") != row["event_type"],
                ack.get("trade_id") != row["trade_id"],
                ack.get("event_seq") != row["event_seq"],
                ack.get("payload_sha256") != row["payload_sha256"],
            )):
                return False
            before_acks = copy.deepcopy(self._acks)
            del self._pending[event_id]
            self._acks.append({**copy.deepcopy(ack), "acknowledged_at_unix": time.time()})
            self._acks = self._acks[-self.ack_limit:]
            try:
                self._persist(state_payload=state_payload)
            except Exception:
                self._pending[event_id] = row
                self._acks = before_acks
                raise
            if not self._pending:
                self._wake.clear()
            return True

    def wait(self, timeout: float) -> bool:
        return self._wake.wait(timeout)

    def consume_wake(self, timeout: float) -> bool:
        woke = self._wake.wait(timeout)
        if woke:
            self._wake.clear()
        return woke

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def decorate_lifecycle(self, payload: dict) -> dict:
        """Bind delivery state into the same canonical lifecycle generation."""
        with self._lock:
            value = copy.deepcopy(payload)
            value["relay_events"] = {
                "schema": self.SCHEMA,
                "saved_at_unix": time.time(),
                "pending": copy.deepcopy(list(self._pending.values())),
                "acks": copy.deepcopy(self._acks[-self.ack_limit:]),
                "sequence_highwater": copy.deepcopy(self._highwater),
            }
            return value

    def next_sequence(self, trade_id: str, suggested: int | None = None) -> int:
        """Return a sequence above every durable pending/acknowledged event."""
        with self._lock:
            floor = self._highwater.get(trade_id, -1) + 1
            return max(floor, int(suggested)) if suggested is not None else floor
