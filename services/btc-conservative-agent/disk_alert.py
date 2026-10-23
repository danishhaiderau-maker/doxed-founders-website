"""Disk pressure alert for /health and the monitor safety block (DISK-CAP).

AMBER at used ratio >= 0.70, RED at >= 0.85; ``hours_to_full`` is carried from
the volume snapshot. R5 retention paths (v22 drain, segment/bundle caps) may
raise a RED condition when they had to discard pending evidence; those are
reported here too. PRIME RULE: neutral (telemetry only). Pure stdlib.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Mapping

AMBER_RATIO = 0.70
RED_RATIO = 0.85
SCHEMA = "disk_alert_v1"

_lock = threading.Lock()
_red_conditions: dict[str, dict[str, Any]] = {}


def raise_red(reason: str, detail: Mapping[str, Any] | None = None, *, now: float | None = None) -> None:
    """Record a retention RED condition (e.g. custody stalled, pending discarded)."""
    with _lock:
        _red_conditions[str(reason)] = {
            "reason": str(reason), "since": float(now if now is not None else time.time()),
            **{k: v for k, v in dict(detail or {}).items() if k not in {"reason", "since"}},
        }


def clear_red(reason: str) -> None:
    with _lock:
        _red_conditions.pop(str(reason), None)


def red_conditions() -> list[dict[str, Any]]:
    with _lock:
        return [dict(v) for _, v in sorted(_red_conditions.items())]


def level_for_ratio(ratio: float | None) -> str:
    if ratio is None:
        return "UNKNOWN"
    if ratio >= RED_RATIO:
        return "RED"
    if ratio >= AMBER_RATIO:
        return "AMBER"
    return "GREEN"


def compute_disk_alert(volume: Mapping[str, Any] | None) -> dict[str, Any]:
    volume = volume or {}
    total, used = volume.get("total_bytes"), volume.get("used_bytes")
    ratio = None
    try:
        if total and float(total) > 0 and used is not None:
            ratio = float(used) / float(total)
    except (TypeError, ValueError):
        ratio = None
    level = level_for_ratio(ratio)
    retention = red_conditions()
    if retention:
        level = "RED"
    return {
        "schema": SCHEMA,
        "level": level,
        "used_ratio": None if ratio is None else round(ratio, 4),
        "hours_to_full": volume.get("hours_to_full"),
        "amber_at": AMBER_RATIO,
        "red_at": RED_RATIO,
        "retention_red": retention,
    }
