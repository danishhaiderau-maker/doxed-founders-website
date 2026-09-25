"""Storage pressure monitoring — never silent truncate."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any, Mapping, Optional

from collector_v22_schema import (
    OBS_STORAGE_PRESSURE,
    STORAGE_PRESSURE_THRESHOLD,
    STORAGE_STATE_FILE,
)

# Measured v2.2 write-once OM row (pre_signal ~117 KB + canonical 1m + hyp fills).
BYTES_PER_EVENT_TYPICAL = 210_000
BYTES_PRE_SIGNAL_CONTEXT_TYPICAL = 117_000
STORAGE_EMERGENCY_THRESHOLD = 0.90


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


def _canonical_volume_guard_enabled(data_dir: Optional[str] = None) -> bool:
    root = os.path.realpath(_data_root(data_dir))
    configured = str(os.getenv("BOT_DATA_DIR") or "").strip()
    if not configured:
        return False
    configured_root = os.path.realpath(configured)
    try:
        return os.path.commonpath((root, configured_root)) == configured_root
    except ValueError:
        return False


def storage_state(data_dir: Optional[str] = None) -> dict:
    root = _data_root(data_dir)
    frac = disk_usage_fraction(root)
    pressure = frac >= STORAGE_PRESSURE_THRESHOLD
    emergency = frac >= STORAGE_EMERGENCY_THRESHOLD
    emergency_guard_enforced = _canonical_volume_guard_enabled(root)
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
        "emergency": bool(emergency),
        "emergency_guard_enforced": emergency_guard_enforced,
        "threshold": STORAGE_PRESSURE_THRESHOLD,
        "emergency_threshold": STORAGE_EMERGENCY_THRESHOLD,
        "new_nonessential_research_allowed": not (emergency and emergency_guard_enforced),
        "observation_status_if_blocked": OBS_STORAGE_PRESSURE,
        "note": (
            "At pressure: pause large legacy events. At emergency: stop new "
            "nonessential research expansion; allow active and terminal lifecycles."
        ),
        "prev_pressure": prev.get("pressure"),
    }
    candidate_path = None
    try:
        # The Fly mirror may read this receipt while the collector is updating
        # it.  Publishing directly to the final path exposed partial JSON and
        # correctly stopped the whole mirror as corrupt.  Build and fsync a
        # same-directory candidate, then replace atomically so readers see the
        # complete previous or complete next receipt, never half a document.
        fd, candidate_path = tempfile.mkstemp(
            prefix=f".{STORAGE_STATE_FILE}.", suffix=".tmp", dir=root
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate_path, state_path)
        candidate_path = None
    except OSError:
        pass
    finally:
        if candidate_path:
            try:
                os.unlink(candidate_path)
            except OSError:
                pass
    return state


def storage_blocks_new_events(data_dir: Optional[str] = None) -> bool:
    return bool(storage_state(data_dir).get("pressure"))


def storage_blocks_new_nonessential_research(data_dir: Optional[str] = None) -> bool:
    """Fail closed only for new optional research at the existing 90% line.

    Callers must make lifecycle classification explicitly. This primitive never
    authorizes deletion and must not be used to suppress trading safety,
    reconciliation, or terminal evidence.
    """
    root = os.path.realpath(_data_root(data_dir))
    if not _canonical_volume_guard_enabled(root):
        # The guard protects the explicitly mounted canonical data volume. A
        # developer checkout can share a nearly-full workstation system drive;
        # treating that as the Fly volume would silently change local tests and
        # tools while providing no production protection.
        return False
    return disk_usage_fraction(root) >= STORAGE_EMERGENCY_THRESHOLD


def emergency_admission(
    *, data_dir: Optional[str] = None, purpose: str,
    lifecycle_required: bool = False, lifecycle_existing: bool = False,
) -> dict[str, Any]:
    emergency = storage_blocks_new_nonessential_research(data_dir)
    allowed = (not emergency) or bool(lifecycle_required) or bool(lifecycle_existing)
    return {
        "schema": "research_storage_emergency_admission_v1",
        "allowed": allowed,
        "emergency": emergency,
        "threshold": STORAGE_EMERGENCY_THRESHOLD,
        "purpose": str(purpose),
        "lifecycle_required": bool(lifecycle_required),
        "lifecycle_existing": bool(lifecycle_existing),
        "reason": None if allowed else "NEW_NONESSENTIAL_RESEARCH_BLOCKED_AT_STORAGE_EMERGENCY",
    }


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
