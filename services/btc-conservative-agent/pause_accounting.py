"""Durable pause episode accounting across process boots.

``boot_at`` and the in-memory ``_DEPLOY_MAINTENANCE_SHED`` clock reset every
boot, so operators could not see how long a pause had lasted or how many
pause/resume retry cycles had happened. This module tracks one contiguous
pause episode on the persistent config volume:

* ``pause_started_at`` — unix seconds when the episode began (unchanged across
  boots and re-assert pauses)
* ``pause_attempt_count`` — pause POSTs + boots while the episode is active

Read-only with respect to trading: nothing here opens, closes, arms, or wipes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping

SCHEMA = "pause_accounting_v1"


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        return ts if ts > 0 else None
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def begin_or_retry(
    fields: MutableMapping[str, Any],
    *,
    owner: str,
    now: float,
) -> dict:
    """Start a new pause episode or count another pause attempt on the same one.

    Same owner with an existing ``pause_started_at`` keeps the clock and
    increments ``pause_attempt_count``. A new owner (or first pause) resets the
    clock and sets attempt count to 1.
    """
    owner_s = str(owner or "").strip().upper() or "UNATTRIBUTED"
    started = _as_float(fields.get("pause_started_at"))
    prev_owner = str(fields.get("pause_intent") or fields.get("pause_owner") or "").strip().upper()
    same_episode = started is not None and prev_owner == owner_s and bool(
        fields.get("manual_admin_pause") or fields.get("execution_paused")
    )
    if same_episode:
        attempts = max(1, _as_int(fields.get("pause_attempt_count"))) + 1
        fields["pause_attempt_count"] = attempts
        fields["pause_started_at"] = started
        return {"started_at": started, "attempt_count": attempts, "owner": owner_s, "event": "retry"}
    fields["pause_started_at"] = float(now)
    fields["pause_attempt_count"] = 1
    return {"started_at": float(now), "attempt_count": 1, "owner": owner_s, "event": "begin"}


def note_boot(fields: MutableMapping[str, Any], *, now: float) -> dict | None:
    """Count one process boot while a durable pause episode is still active.

    Seeds ``pause_started_at`` when a legacy paused config lacks it so age is
    never silently zero after the first post-deploy boot.
    """
    if not bool(fields.get("manual_admin_pause") or fields.get("execution_paused")):
        return None
    started = _as_float(fields.get("pause_started_at"))
    if started is None:
        fields["pause_started_at"] = float(now)
        fields["pause_attempt_count"] = max(1, _as_int(fields.get("pause_attempt_count")))
        return {
            "started_at": float(now),
            "attempt_count": int(fields["pause_attempt_count"]),
            "event": "seed_boot",
        }
    attempts = max(1, _as_int(fields.get("pause_attempt_count"))) + 1
    fields["pause_attempt_count"] = attempts
    return {"started_at": started, "attempt_count": attempts, "event": "boot"}


def clear(fields: MutableMapping[str, Any]) -> None:
    """End the pause episode (resume path)."""
    fields["pause_started_at"] = None
    fields["pause_attempt_count"] = 0


def age_sec(fields: Mapping[str, Any], *, now: float) -> float:
    """Seconds since durable pause start; 0 when no episode."""
    started = _as_float(fields.get("pause_started_at"))
    if started is None:
        return 0.0
    return max(0.0, float(now) - started)


def snapshot(
    fields: Mapping[str, Any],
    *,
    now: float,
    paused: bool,
    owner: str | None,
) -> dict:
    """Public read model for ``/api/status`` and ``/health``."""
    started = _as_float(fields.get("pause_started_at"))
    attempts = _as_int(fields.get("pause_attempt_count"))
    active = bool(paused) and started is not None
    if not active and not paused:
        return {
            "schema": SCHEMA,
            "active": False,
            "owner": None,
            "started_at": None,
            "total_pause_sec": 0,
            "attempt_count": 0,
        }
    # Paused without a durable start (legacy / mid-write): report live but zero age
    # until begin_or_retry / note_boot seeds the clock.
    total = int(age_sec(fields, now=now)) if started is not None else 0
    return {
        "schema": SCHEMA,
        "active": bool(paused),
        "owner": (str(owner).strip().upper() if owner else None),
        "started_at": _iso(started),
        "total_pause_sec": total,
        "attempt_count": attempts if paused else 0,
    }
