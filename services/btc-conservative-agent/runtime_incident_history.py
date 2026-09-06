"""Bounded, read-only projection of retained runtime incident receipts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


MAX_TAIL_BYTES = 262_144


def _utc_timestamp(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def _tail_lines(path: Path, *, max_bytes: int = MAX_TAIL_BYTES) -> list[bytes]:
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        start = max(0, size - max(1, int(max_bytes)))
        with path.open("rb") as handle:
            handle.seek(start)
            payload = handle.read()
        if start and b"\n" in payload:
            payload = payload.split(b"\n", 1)[1]
        return payload.splitlines()
    except OSError:
        return []


def build_runtime_incident_history(
    crash_dump_path: str | Path,
    *,
    current_started_at: str | None,
    current_instance_id: str | None,
    current_revision: str | None,
    limit: int = 10,
) -> dict:
    """Project only explicitly retained facts; never infer a Fly restart cause."""
    incidents: list[dict] = []
    malformed = 0
    for raw in _tail_lines(Path(crash_dump_path)):
        try:
            receipt = json.loads(raw)
        except (TypeError, ValueError):
            malformed += 1
            continue
        if not isinstance(receipt, dict):
            malformed += 1
            continue
        watchdog = receipt.get("watchdog")
        if isinstance(watchdog, dict):
            requested = watchdog.get("restart_allowed")
            requested = requested if type(requested) is bool else None
            classification = (
                "APPLICATION_WATCHDOG_RESTART_REQUESTED"
                if requested
                else "APPLICATION_WATCHDOG_INCIDENT"
            )
            evidence = "watchdog_crash_context_v1"
            reason = str(watchdog.get("trigger") or "UNSPECIFIED")[:96]
            revision = str(watchdog.get("source_revision") or "")
            instance_id = str(watchdog.get("bot_instance_id") or "")
            exit_code = watchdog.get("exit_code")
        else:
            requested = None
            classification = "APPLICATION_CRASH_DUMP_UNATTRIBUTED"
            evidence = "legacy_crash_dump"
            reason = "No structured watchdog trigger was retained"
            revision = ""
            instance_id = ""
            exit_code = None
        incidents.append({
            "time": _utc_timestamp(receipt.get("time")),
            "classification": classification,
            "reason": reason,
            "restart_requested": requested,
            "exit_code": exit_code,
            "source_revision": revision or None,
            "bot_instance_id": instance_id or None,
            "evidence_source": evidence,
        })

    incidents = incidents[-max(1, int(limit)):]
    return {
        "schema": "runtime_incident_history_v1",
        "current_process": {
            "started_at": current_started_at,
            "bot_instance_id": current_instance_id,
            "source_revision": current_revision,
            "classification": "CURRENT_PROCESS",
        },
        "application_incidents": incidents,
        "application_incident_count_in_bounded_tail": len(incidents),
        "malformed_receipts_skipped": malformed,
        "platform_events": [],
        "platform_history_status": "UNAVAILABLE_NO_AUTHORITATIVE_PLATFORM_EVENT_RECEIPTS",
        "platform_history_note": (
            "Fly machine, deployment, and platform restart causes are not inferred "
            "from process uptime or missing application logs."
        ),
    }
