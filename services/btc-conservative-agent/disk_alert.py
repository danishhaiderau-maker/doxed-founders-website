"""Disk pressure alert for /health and the monitor safety block (DISK-CAP).

AMBER at used ratio >= 0.70, RED at >= 0.85; ``hours_to_full`` is carried from
the volume snapshot. R5 retention paths (v22 drain, segment/bundle caps) may
raise a RED condition when they had to discard pending evidence; those are
reported here too. PRIME RULE: neutral (telemetry only). Pure stdlib.

HM C6 (23 Oct): REDs are held 24 h after their last occurrence, then clear,
and are persisted to a small state file (``configure``) so a restart neither
drops them silently nor keeps them forever. The segment shipper's R5 fields
(``r5_segment_red``/``over_cap``/``last_deleted``) are folded in from the
/health ``volume.transfer`` block.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

AMBER_RATIO = 0.70
RED_RATIO = 0.85
SCHEMA = "disk_alert_v1"
# HM C6 (23 Oct): a retention RED stays visible this long after its LAST
# occurrence (``since`` = first occurrence of the episode), then clears. Same
# hold as the segment shipper's R5_RED_HOLD_SECONDS.
RED_HOLD_SECONDS = 24 * 3600.0
STATE_SCHEMA = "disk_alert_red_state_v1"
MAX_RED_REASONS = 50

_lock = threading.Lock()
_red_conditions: dict[str, dict[str, Any]] = {}
_state_path: Path | None = None


def _persist_locked() -> None:
    """Best effort: a small JSON file, atomically replaced. Never raises."""
    if _state_path is None:
        return
    try:
        _state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = _state_path.with_name(f".{_state_path.name}.tmp")
        tmp.write_text(json.dumps({"schema": STATE_SCHEMA, "red": _red_conditions},
                                  sort_keys=True, default=str), encoding="utf-8")
        os.replace(tmp, _state_path)
    except (OSError, TypeError, ValueError):
        pass


def _expire_locked(now: float) -> bool:
    expired = [k for k, v in _red_conditions.items()
               if now - float(v.get("last") or v.get("since") or 0.0) >= RED_HOLD_SECONDS]
    for key in expired:
        _red_conditions.pop(key, None)
    return bool(expired)


def configure(state_path: str | os.PathLike | None, *, now: float | None = None) -> None:
    """Persist REDs to ``state_path`` and reload the ones held there.

    REDs raised before a restart survive it (still subject to the 24 h hold);
    a corrupt or missing file just starts empty.
    """
    global _state_path
    now = float(now if now is not None else time.time())
    with _lock:
        _state_path = Path(state_path) if state_path else None
        if _state_path is None:
            return
        try:
            doc = json.loads(_state_path.read_text(encoding="utf-8"))
            stored = doc.get("red") if isinstance(doc, dict) else None
        except (OSError, ValueError):
            stored = None
        if isinstance(stored, dict):
            for key, row in list(stored.items())[:MAX_RED_REASONS]:
                if isinstance(row, dict) and str(key) not in _red_conditions:
                    _red_conditions[str(key)] = dict(row)
        _expire_locked(now)
        _persist_locked()


def raise_red(reason: str, detail: Mapping[str, Any] | None = None, *, now: float | None = None) -> None:
    """Record a retention RED condition (e.g. pending evidence discarded at the cap).

    ``since`` is the first occurrence of the episode, ``last`` the latest;
    the RED clears RED_HOLD_SECONDS after ``last``.
    """
    now = float(now if now is not None else time.time())
    with _lock:
        _expire_locked(now)
        previous = _red_conditions.get(str(reason)) or {}
        _red_conditions[str(reason)] = {
            **{k: v for k, v in dict(detail or {}).items() if k not in {"reason", "since", "last", "occurrences"}},
            "reason": str(reason),
            "since": float(previous.get("since") or now),
            "last": now,
            "occurrences": int(previous.get("occurrences") or 0) + 1,
        }
        _persist_locked()


def clear_red(reason: str) -> None:
    with _lock:
        if _red_conditions.pop(str(reason), None) is not None:
            _persist_locked()


def red_conditions(now: float | None = None) -> list[dict[str, Any]]:
    now = float(now if now is not None else time.time())
    with _lock:
        if _expire_locked(now):
            _persist_locked()
        return [dict(v) for _, v in sorted(_red_conditions.items())]


def segment_store_red(transfer: Mapping[str, Any] | None) -> dict[str, Any]:
    """Fold the segment shipper's R5 fields (status.json via /health volume.transfer).

    The shipper already holds its REDs 24 h after the last drop, so they are
    taken as-is; ``r5_segment_over_cap`` is RED in its own right.
    """
    t = dict(transfer or {})
    red_map = t.get("r5_segment_red") if isinstance(t.get("r5_segment_red"), Mapping) else {}
    reasons = []
    for reason, row in sorted(red_map.items()):
        row = row if isinstance(row, Mapping) else {}
        reasons.append({"reason": str(reason), "source": "segment_shipper",
                        "since": row.get("since"), "last": row.get("last")})
    if t.get("r5_segment_over_cap"):
        reasons.append({"reason": "SEGMENT_STORE_OVER_CAP", "source": "segment_shipper"})
    return {"over_cap": t.get("r5_segment_over_cap"),
            "last_deleted": t.get("r5_segment_last_deleted"),
            "last_at": t.get("r5_segment_last_at"),
            "red": reasons}


def level_for_ratio(ratio: float | None) -> str:
    if ratio is None:
        return "UNKNOWN"
    if ratio >= RED_RATIO:
        return "RED"
    if ratio >= AMBER_RATIO:
        return "AMBER"
    return "GREEN"


def stream_size_cap_status(data_dir: str | os.PathLike | None) -> dict[str, Any]:
    """Read strategist collector size-cap status (cross-process; RED => STREAM_SIZE_CAP)."""
    if not data_dir:
        return {"level": "UNKNOWN", "red": []}
    path = Path(data_dir) / "strategist_stream_size_cap.json"
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"level": "UNKNOWN", "red": [], "exists": False}
    if not isinstance(row, dict):
        return {"level": "UNKNOWN", "red": [], "exists": False}
    level = str(row.get("level") or "UNKNOWN")
    red = []
    if level == "RED" or row.get("stop_writing"):
        red.append({
            "reason": str(row.get("reason") or "STREAM_SIZE_CAP"),
            "source": "strategist_streams",
            "path": row.get("path"),
            "ratio": row.get("ratio"),
            "used_bytes": row.get("used_bytes"),
            "cap_bytes": row.get("cap_bytes"),
            "card": row.get("card"),
        })
    return {"level": level, "red": red, "exists": True, "status": row}


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
    segment = segment_store_red(volume.get("transfer") if isinstance(volume.get("transfer"), Mapping) else None)
    retention = retention + segment["red"]
    data_dir = volume.get("data_dir") or volume.get("root") or os.getcwd()
    stream_cap = stream_size_cap_status(data_dir)
    retention = retention + list(stream_cap.get("red") or [])
    if retention or stream_cap.get("level") == "RED":
        level = "RED"
    return {
        "schema": SCHEMA,
        "level": level,
        "used_ratio": None if ratio is None else round(ratio, 4),
        "hours_to_full": volume.get("hours_to_full"),
        "amber_at": AMBER_RATIO,
        "red_at": RED_RATIO,
        "retention_red": retention,
        "red_hold_seconds": RED_HOLD_SECONDS,
        "segment_store": segment,
        "stream_size_cap": stream_cap,
    }
