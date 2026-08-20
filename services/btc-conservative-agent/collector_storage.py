"""Storage pressure monitoring — never silent truncate."""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Mapping, Optional

from collector_v22_schema import (
    OBS_STORAGE_PRESSURE,
    STORAGE_PRESSURE_THRESHOLD,
    STORAGE_STATE_FILE,
)

# Measured v2.2 write-once OM row (pre_signal ~117 KB + canonical 1m + hyp fills).
BYTES_PER_EVENT_TYPICAL = 210_000
BYTES_PRE_SIGNAL_CONTEXT_TYPICAL = 117_000


def _data_root(data_dir: Optional[str] = None) -> str:
    return data_dir or os.getcwd()


def disk_usage_fraction(path: Optional[str] = None) -> float:
    root = _data_root(path)
    try:
        usage = shutil.disk_usage(root)
        if usage.total <= 0:
            return 0.0
        return float(usage.used) / float(usage.total)
    except OSError:
        return 0.0


def storage_state(data_dir: Optional[str] = None) -> dict:
    root = _data_root(data_dir)
    frac = disk_usage_fraction(root)
    pressure = frac >= STORAGE_PRESSURE_THRESHOLD
    state_path = os.path.join(root, STORAGE_STATE_FILE)
    prev = {}
    if os.path.isfile(state_path):
        try:
            with open(state_path, encoding="utf-8") as handle:
                prev = json.load(handle)
        except (json.JSONDecodeError, OSError):
            prev = {}
    state = {
        "schema": "collector_storage_v1",
        "used_fraction": round(frac, 6),
        "pressure": bool(pressure),
        "threshold": STORAGE_PRESSURE_THRESHOLD,
        "observation_status_if_blocked": OBS_STORAGE_PRESSURE,
        "note": "At pressure: pause new research events; allow open paths to complete",
        "prev_pressure": prev.get("pressure"),
    }
    try:
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
    except OSError:
        pass
    return state


def storage_blocks_new_events(data_dir: Optional[str] = None) -> bool:
    return bool(storage_state(data_dir).get("pressure"))


def project_capacity(
    *,
    data_dir: Optional[str] = None,
    bytes_per_event_typical: float = BYTES_PER_EVENT_TYPICAL,
    events_per_day: float = 40.0,
) -> dict:
    root = _data_root(data_dir)
    usage = shutil.disk_usage(root)
    total = float(usage.total)
    used = float(usage.used)
    free = max(0.0, total - used)
    daily = max(1.0, float(bytes_per_event_typical) * float(events_per_day))
    thresholds = {}
    for pct in (0.70, 0.80, 0.85, 0.90, 1.0):
        target = total * pct
        remaining = max(0.0, target - used)
        thresholds[f"pct_{int(pct * 100)}"] = {
            "days": None if daily <= 0 else round(remaining / daily, 2),
            "bytes_remaining": int(remaining),
        }
    return {
        "schema": "capacity_projection_v1",
        "data_root": root,
        "total_bytes": int(total),
        "used_bytes": int(used),
        "free_bytes": int(free),
        "used_fraction": round(used / total, 6) if total else None,
        "bytes_per_event_typical": int(bytes_per_event_typical),
        "events_per_day_assumed": events_per_day,
        "daily_growth_bytes": int(daily),
        "thresholds": thresholds,
    }
