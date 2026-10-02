"""Edge-triggered self-diagnosis events into the watcher's ``alarms.jsonl``.

The analyzer (:9001 /alerts), the watcher (:9011) and Fly's /alerts all render
that file, so self-diagnosis appears in the existing Alerts section with no
dashboard code change. Appends hold the watcher's own ``health/tick.lock`` so
a line is never interleaved with a watcher tick; if the lock is busy the
events wait in ``state.json`` for the next run. Only ``selfaware.*`` check ids
are written; watcher-owned checks are never touched.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import ALARM_PREFIX
from .facts import iso

EVENT_SCHEMA = "system_health_alarm_v1"


class TickLock:
    """Same non-blocking byte lock as ``system_health.TickLock`` on ``health/tick.lock``."""

    def __init__(self, path: Path) -> None:
        self.path, self.handle = path, None

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt  # noqa: PLC0415

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False

    def __exit__(self, *exc: Any) -> None:
        if self.handle:
            try:
                if os.name == "nt":
                    import msvcrt  # noqa: PLC0415

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.handle.close()


def _event(kind: str, check: str, status: str, finding: dict[str, Any], now: float, opened_at: float | None) -> dict:
    causes = finding.get("causes") or []
    hint = "; ".join(c.get("text", "") for c in causes[:2] if c.get("text"))
    row = {"schema": EVENT_SCHEMA, "at": iso(now), "event": kind, "check": check, "status": status,
           "observed": ("[self-diagnosis] " + str(finding.get("observed") or ""))[:240],
           "threshold": str(finding.get("expected") or "")[:200], "hint": hint[:300] or None,
           "runbook": finding.get("runbook"), "source": "self_aware"}
    if opened_at:
        row["opened_at"] = iso(opened_at)
    return row


def events_for(transitions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    out = []
    for t in transitions:
        fd = t["finding"]
        if not fd.get("emit_alarm", True):
            continue
        check = ALARM_PREFIX + t["id"]
        old, new = t["from"], t["to"]
        opened = None
        if old in ("AMBER", "RED"):
            out.append(_event("AMBER_CLEAR" if old == "AMBER" else "RECOVERED", check, new, fd, now, opened))
        if new == "AMBER":
            out.append(_event("AMBER", check, new, fd, now, None))
        elif new == "RED":
            out.append(_event("OPEN", check, new, fd, now, None))
    return out


def digest_event(digest: dict[str, Any], active: bool, now: float) -> dict[str, Any] | None:
    """Hourly digest as an Alerts entry: AMBER while it reports something, AMBER_CLEAR when quiet again."""
    finding = {"observed": digest.get("headline"), "expected": "hourly digest: nothing broke, no edge candidate",
               "causes": [{"text": digest.get("summary_line", "")}], "runbook": "docs/SELF_AWARE_RUNBOOK.md#hourly-digest"}
    if digest.get("attention"):
        return _event("AMBER", ALARM_PREFIX + "digest", "AMBER", finding, now, None)
    if active:
        return _event("AMBER_CLEAR", ALARM_PREFIX + "digest", "GREEN", finding, now, None)
    return None


def flush(health_dir: Path, state: dict[str, Any], new_events: list[dict[str, Any]], *, wait_sec: float = 20.0) -> dict:
    pending = list(state.get("pending_alarm_events") or []) + list(new_events)
    if not pending:
        return {"written": 0, "pending": 0}
    deadline = time.time() + wait_sec
    while True:
        with TickLock(health_dir / "tick.lock") as owned:
            if owned:
                with open(health_dir / "alarms.jsonl", "a", encoding="utf-8") as handle:
                    for e in pending:
                        if not str(e.get("check", "")).startswith(ALARM_PREFIX):
                            continue
                        handle.write(json.dumps(e, sort_keys=True) + "\n")
                state["pending_alarm_events"] = []
                return {"written": len(pending), "pending": 0}
        if time.time() >= deadline:
            state["pending_alarm_events"] = pending[-200:]
            return {"written": 0, "pending": len(state["pending_alarm_events"])}
        time.sleep(1.0)
