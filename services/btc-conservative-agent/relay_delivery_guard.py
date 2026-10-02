"""Bot-side hard guard for platform relay delivery (``relay_delivery_guard_v1``).

An outbox event may be POSTed only when it was created by the current
dashboard owner identity and, while live is armed, after the current arming
timestamp. Everything else is *held*: it stays in the durable outbox exactly
as written (never rewritten, re-signed, acknowledged or deleted). Stale-owner,
missing-owner and pre-arming holds are additionally quarantined: identity plus
reason is appended once to an append-only ledger and the hold is permanent
for that event id. This guard is independent of ``RelayEventOutbox.delivery_plan``'s
owner filter so that a mode change (FORCE_PAPER_MODE off, live armed) can
never re-open delivery of historical events.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

SCHEMA = "relay_delivery_guard_v1"
QUARANTINE_FILE = "relay_outbox_quarantine.jsonl"
STALE_OWNER_ALARM_SEC = 1800.0
OBSERVATION_MAX_AGE_SEC = 120.0

HOLD_OWNER_UNVERIFIED = "OWNER_UNVERIFIED"
HOLD_MISSING_OWNER = "MISSING_OWNER"
HOLD_STALE_OWNER = "STALE_OWNER"
HOLD_ARMING_TS_UNKNOWN = "ARMING_TS_UNKNOWN"
HOLD_PRE_ARMING = "PRE_ARMING"

# Intrinsic to the event's identity/birth time: quarantined permanently, so a
# later disarm or owner change can never make the event deliverable again.
# Owner-unverified and unknown-arming-time are process states: held, not recorded.
STICKY_HOLDS = frozenset({HOLD_MISSING_OWNER, HOLD_STALE_OWNER, HOLD_PRE_ARMING})

ARM_BLOCK_UNOBSERVED = "RELAY_OUTBOX_GUARD_UNOBSERVED"
ARM_BLOCK_OWNER_UNVERIFIED = "RELAY_OUTBOX_OWNER_UNVERIFIED"
ARM_BLOCK_UNQUARANTINED = "RELAY_OUTBOX_STALE_OWNER_UNQUARANTINED"


def _owner_of(record: dict):
    owner = record.get("bot_instance_id")
    if owner is None:
        owner = (record.get("payload") or {}).get("bot_instance_id")
    return owner


def hold_reason(record: dict, *, owner_id, armed: bool, armed_at_ts) -> Optional[str]:
    """Return why ``record`` must not be delivered, or None when deliverable."""
    owner = owner_id if isinstance(owner_id, str) else ""
    if not owner or owner != owner.strip():
        return HOLD_OWNER_UNVERIFIED
    record_owner = _owner_of(record)
    if not isinstance(record_owner, str) or not record_owner:
        return HOLD_MISSING_OWNER
    if record_owner != owner:
        return HOLD_STALE_OWNER
    if armed:
        try:
            armed_at = float(armed_at_ts)
        except (TypeError, ValueError):
            return HOLD_ARMING_TS_UNKNOWN
        if armed_at <= 0:
            return HOLD_ARMING_TS_UNKNOWN
        try:
            created = float(record.get("created_at_unix") or 0.0)
        except (TypeError, ValueError):
            created = 0.0
        if created <= 0 or created < armed_at:
            return HOLD_PRE_ARMING
    return None


class RelayDeliveryGuard:
    def __init__(self, quarantine_path, clock: Callable[[], float] = time.time):
        self.path = Path(quarantine_path)
        self._clock = clock
        self._lock = threading.Lock()
        self._quarantined: dict[str, str] = {}
        self._load_error = None
        self._write_failures = 0
        self._last_write_error = None
        self._last_write_error_ts = 0.0
        self._held_since_boot = 0
        self._stale_since_ts = 0.0
        self._observed: dict = {}
        self._observed_ts = 0.0
        self._load()

    def _load(self) -> None:
        try:
            if not self.path.exists():
                return
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(row, dict) and row.get("event_id"):
                        self._quarantined.setdefault(str(row["event_id"]), str(row.get("reason") or ""))
        except OSError as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"[:200]

    def _quarantine(self, record: dict, reason: str, now: float) -> bool:
        """Append one identity row per event; caller holds ``self._lock``."""
        event_id = str(record.get("event_id") or "")
        if not event_id:
            return False
        if event_id in self._quarantined:
            return True
        row = {
            "schema": SCHEMA,
            "event_id": event_id,
            "trade_id": record.get("trade_id"),
            "event_type": record.get("event_type"),
            "event_seq": record.get("event_seq"),
            "payload_sha256": record.get("payload_sha256"),
            "created_at_unix": record.get("created_at_unix"),
            "bot_instance_id": _owner_of(record),
            "reason": reason,
            "quarantined_at_unix": round(now, 3),
            "outbox_record_retained": True,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            self._write_failures += 1
            self._last_write_error = f"{type(exc).__name__}: {exc}"[:200]
            self._last_write_error_ts = now
            return False
        self._quarantined[event_id] = reason
        return True

    def filter_deliverable(self, records: Iterable[dict], *, owner_id, armed: bool,
                           armed_at_ts, now: Optional[float] = None) -> list[dict]:
        """Drop held records from a delivery batch, quarantining each one."""
        now = float(self._clock() if now is None else now)
        deliverable = []
        with self._lock:
            for record in records or []:
                reason = self._reason_locked(record, owner_id=owner_id, armed=armed, armed_at_ts=armed_at_ts)
                if reason is None:
                    deliverable.append(record)
                    continue
                self._held_since_boot += 1
                if reason in STICKY_HOLDS:
                    self._quarantine(record, reason, now)
        return deliverable

    def _reason_locked(self, record: dict, *, owner_id, armed: bool, armed_at_ts) -> Optional[str]:
        prior = self._quarantined.get(str(record.get("event_id") or ""))
        if prior is not None:
            return prior or HOLD_STALE_OWNER
        return hold_reason(record, owner_id=owner_id, armed=armed, armed_at_ts=armed_at_ts)

    def observe(self, pending: Iterable[dict], *, owner_id, armed: bool, armed_at_ts,
                last_ack_ts=None, now: Optional[float] = None) -> dict:
        """Classify every pending event, quarantine held ones, cache a status row."""
        now = float(self._clock() if now is None else now)
        counts = {"pending_total": 0, "stale_owner_pending": 0, "missing_owner_pending": 0,
                  "owner_unverified_pending": 0, "pre_arming_pending": 0,
                  "quarantined_pending": 0, "unquarantined_held_pending": 0}
        oldest = None
        with self._lock:
            for record in pending or []:
                counts["pending_total"] += 1
                try:
                    created = float(record.get("created_at_unix") or 0.0)
                except (TypeError, ValueError):
                    created = 0.0
                if created > 0 and (oldest is None or created < oldest):
                    oldest = created
                reason = self._reason_locked(record, owner_id=owner_id, armed=armed, armed_at_ts=armed_at_ts)
                if reason is None:
                    continue
                key = {
                    HOLD_STALE_OWNER: "stale_owner_pending",
                    HOLD_MISSING_OWNER: "missing_owner_pending",
                    HOLD_OWNER_UNVERIFIED: "owner_unverified_pending",
                }.get(reason, "pre_arming_pending")
                counts[key] += 1
                if reason not in STICKY_HOLDS:
                    continue
                if self._quarantine(record, reason, now):
                    counts["quarantined_pending"] += 1
                else:
                    counts["unquarantined_held_pending"] += 1
            stale = counts["stale_owner_pending"] + counts["missing_owner_pending"]
            if stale:
                self._stale_since_ts = self._stale_since_ts or now
            else:
                self._stale_since_ts = 0.0
            try:
                ack_ts = float(last_ack_ts or 0.0)
            except (TypeError, ValueError):
                ack_ts = 0.0
            self._observed = {
                **counts,
                "oldest_pending_created_ts": oldest,
                "last_ack_ts": ack_ts or None,
                "armed": bool(armed),
                "armed_at_ts": armed_at_ts,
                "owner_verified": bool(isinstance(owner_id, str) and owner_id and owner_id == owner_id.strip()),
            }
            self._observed_ts = now
            return dict(self._observed)

    def arming_block_reason(self, now: Optional[float] = None) -> Optional[str]:
        now = float(self._clock() if now is None else now)
        with self._lock:
            observed = dict(self._observed)
            observed_ts = self._observed_ts
        if not observed_ts or now - observed_ts > OBSERVATION_MAX_AGE_SEC:
            return ARM_BLOCK_UNOBSERVED
        if not observed.get("owner_verified"):
            return ARM_BLOCK_OWNER_UNVERIFIED
        if int(observed.get("unquarantined_held_pending") or 0) > 0:
            return ARM_BLOCK_UNQUARANTINED
        return None

    def stale_owner_alarm(self, now: Optional[float] = None) -> bool:
        now = float(self._clock() if now is None else now)
        with self._lock:
            since = self._stale_since_ts
        return bool(since and now - since > STALE_OWNER_ALARM_SEC)

    def status(self, now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        with self._lock:
            observed = dict(self._observed)
            observed_ts = self._observed_ts
            since = self._stale_since_ts
            quarantined_total = len(self._quarantined)
            failures = self._write_failures
            last_error = self._last_write_error
            last_error_ts = self._last_write_error_ts
            held = self._held_since_boot
        oldest = observed.pop("oldest_pending_created_ts", None)
        last_ack = observed.pop("last_ack_ts", None)
        return {
            "schema": SCHEMA,
            **observed,
            "observed_age_sec": round(now - observed_ts, 3) if observed_ts else None,
            "oldest_pending_age_sec": round(max(0.0, now - oldest), 1) if oldest else None,
            "last_ack_age_sec": round(max(0.0, now - last_ack), 1) if last_ack else None,
            "stale_owner_since_ts": since or None,
            "stale_owner_age_sec": round(now - since, 1) if since else None,
            "stale_owner_alarm_after_sec": STALE_OWNER_ALARM_SEC,
            "stale_owner_alarm": bool(since and now - since > STALE_OWNER_ALARM_SEC),
            "held_from_delivery_since_boot": held,
            "quarantine_file": self.path.name,
            "quarantined_events_total": quarantined_total,
            "quarantine_write_failures": failures,
            "quarantine_last_error": last_error,
            "quarantine_last_error_ts": last_error_ts or None,
            "quarantine_load_error": self._load_error,
            "arming_block_reason": self.arming_block_reason(now),
            "delivery_rule": "current owner identity AND (disarmed OR created_at >= live_armed_at)",
        }
