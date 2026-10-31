"""Retention stop-write + 70/85 alarms (PR2 remaining-deleters kill-switch).

Never deletes. Cap or free-disk pressure latches stop-write for that family and
raises AMBER (>=70%) / RED (>=85%) using ``disk_alert.AMBER_RATIO`` /
``disk_alert.RED_RATIO`` (``disk_alert.py:23-24``). Free-disk pressure uses the
same ladder. Shipping to the laptop continues; ``RESEARCH_SEGMENTS_ENABLED`` is
not flipped here.

PRIME RULE: neutral. Retention / disk-pressure only — no fill, price, or exit
inputs.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import disk_alert

AMBER_RATIO = disk_alert.AMBER_RATIO  # 0.70
RED_RATIO = disk_alert.RED_RATIO      # 0.85
RECEIPT_SCHEMA = "retention_stop_write_receipt_v1"
ANALYZER_GENERATION_RECEIPT_SCHEMA = "analyzer_generation_cap_receipt_v1"

_lock = threading.Lock()
_latches: dict[str, dict[str, Any]] = {}
_volume_used_ratio: float | None = None


def set_volume_used_ratio(ratio: float | None) -> None:
    """Optional free-disk used ratio (0..1) from the volume snapshot."""
    global _volume_used_ratio
    with _lock:
        _volume_used_ratio = None if ratio is None else float(ratio)


def volume_used_ratio() -> float | None:
    with _lock:
        return _volume_used_ratio


def free_disk_used_ratio(path: str | os.PathLike | None = None) -> float | None:
    """Best-effort used ratio for the volume containing ``path`` (or cwd)."""
    try:
        target = str(path or os.getcwd())
        usage = os.statvfs(target)
        total = int(usage.f_blocks) * int(usage.f_frsize)
        free = int(usage.f_bavail) * int(usage.f_frsize)
        if total <= 0:
            return None
        return max(0.0, min(1.0, 1.0 - (free / total)))
    except (AttributeError, OSError, TypeError, ValueError, ZeroDivisionError):
        return None


def used_ratio(used: float | int | None, cap: float | int | None) -> float | None:
    try:
        if used is None or cap is None:
            return None
        cap_f = float(cap)
        if cap_f <= 0:
            return None
        return max(0.0, float(used) / cap_f)
    except (TypeError, ValueError):
        return None


def level_for_ratios(*ratios: float | None) -> str:
    """Worst level across cap ratios and free-disk (AMBER 0.70 / RED 0.85).

    UNKNOWN ranks below AMBER for fold (GREEN→UNKNOWN only). F1: UNKNOWN alone
    must not stop-write — see ``should_stop_write`` / ``is_stopped``.
    """
    level = "GREEN"
    for ratio in ratios:
        candidate = disk_alert.level_for_ratio(ratio)
        if candidate == "RED":
            return "RED"
        if candidate == "AMBER":
            level = "AMBER"
        elif candidate == "UNKNOWN" and level == "GREEN":
            level = "UNKNOWN"
    return level


_RECEIPT_LEVELS = frozenset({"RED", "AMBER", "GREEN"})


def receipt_alarm_level(level: Any | None) -> str:
    """F3/C3: lifecycle receipt reports a normalised RED/AMBER/GREEN (never soft-GREEN).

    UNKNOWN / missing / empty / unrecognised → RED on the receipt (alarm truth).
    Actuation stays separate — callers must not use this for stop-write / latch (F1).
    """
    text = str(level).strip().upper() if level is not None else ""
    if not text or text == "UNKNOWN":
        return "RED"
    if text in _RECEIPT_LEVELS:
        return text
    return "RED"


def should_stop_write(*, used: float | int | None = None, cap: float | int | None = None,
                      volume_ratio: float | None = None, hard_over: bool = False) -> bool:
    """Stop at hard over-cap, cap RED (>=85%), or free-disk RED (>=85%).

    F1: measured RED ratio only — UNKNOWN alone must NOT stop-write.
    """
    if hard_over:
        return True
    ratio = used_ratio(used, cap)
    vol = volume_used_ratio() if volume_ratio is None else volume_ratio
    return level_for_ratios(ratio, vol) == "RED"


def latch(family: str, *, reason: str, used: float | int | None = None,
          cap: float | int | None = None, volume_ratio: float | None = None,
          detail: Mapping[str, Any] | None = None, now: float | None = None) -> dict[str, Any]:
    """Latch stop-write for ``family`` and raise disk_alert RED when level is RED."""
    now = float(now if now is not None else time.time())
    ratio = used_ratio(used, cap)
    vol = volume_used_ratio() if volume_ratio is None else volume_ratio
    level = level_for_ratios(ratio, vol)
    if hard := (cap is not None and used is not None and float(cap) >= 0 and float(used) >= float(cap)):
        if level != "RED":
            level = "RED"
    row = {
        "family": str(family),
        "reason": str(reason),
        "alarm_level": level,
        "used_ratio": None if ratio is None else round(float(ratio), 6),
        "volume_used_ratio": None if vol is None else round(float(vol), 6),
        "used": used,
        "cap": cap,
        "hard_over": bool(hard) if cap is not None and used is not None else False,
        "ts": now,
        "amber_at": AMBER_RATIO,
        "red_at": RED_RATIO,
        **dict(detail or {}),
    }
    with _lock:
        previous = _latches.get(str(family)) or {}
        row["since"] = float(previous.get("since") or now)
        row["occurrences"] = int(previous.get("occurrences") or 0) + 1
        _latches[str(family)] = row
    if level == "RED":
        disk_alert.raise_red(f"STOP_WRITE:{family}", {
            "reason": reason, "used_ratio": row["used_ratio"],
            "volume_used_ratio": row["volume_used_ratio"],
        }, now=now)
    # AMBER is progressive (visible on latch_state / receipts / GET); do not
    # force disk_alert RED — that ladder stays for true RED (>=85%) only.
    return dict(row)


def is_stopped(family: str) -> bool:
    """True only when a latch exists with normalised alarm_level RED.

    F1: AMBER / UNKNOWN / empty latch must NOT count as stopped — ``bool(row)`` alone is forbidden.
    """
    with _lock:
        row = _latches.get(str(family))
        if not row:
            return False
        return str(row.get("alarm_level") or "").strip().upper() == "RED"


def latch_state(family: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
    with _lock:
        if family is None:
            return [dict(v) for _, v in sorted(_latches.items())]
        row = _latches.get(str(family))
        return dict(row) if row else {}


def clear_latches(*families: str) -> None:
    with _lock:
        if not families:
            _latches.clear()
            return
        for family in families:
            _latches.pop(str(family), None)


def write_receipt(directory: str | os.PathLike | None, row: Mapping[str, Any]) -> bool:
    """Append one stop-write receipt via rotation_cap receipt helper when available."""
    if directory is None:
        return False
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    payload = dict(row)
    if "schema" not in payload:
        payload["schema"] = RECEIPT_SCHEMA
    try:
        import rotation_cap
        return bool(rotation_cap.write_cap_receipt(str(directory), payload))
    except Exception:  # noqa: BLE001 - receipt never blocks stop-write
        try:
            path = Path(directory) / "cap_receipts.jsonl"
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            return True
        except Exception:  # noqa: BLE001
            return False


def evaluate_and_latch(family: str, *, used: float | int, cap: float | int,
                       reason: str, volume_ratio: float | None = None,
                       receipt_dir: str | os.PathLike | None = None,
                       detail: Mapping[str, Any] | None = None,
                       now: float | None = None) -> dict[str, Any]:
    """Latch + receipt when AMBER/RED or hard over; always returns the decision row."""
    now = float(now if now is not None else time.time())
    vol = volume_used_ratio() if volume_ratio is None else volume_ratio
    ratio = used_ratio(used, cap)
    level = level_for_ratios(ratio, vol)
    hard_over = float(cap) >= 0 and float(used) >= float(cap) if cap is not None else False
    if hard_over and level != "RED":
        level = "RED"
    stop = should_stop_write(used=used, cap=cap, volume_ratio=vol, hard_over=hard_over)
    row = {
        "family": family, "reason": reason, "alarm_level": level,
        "used_ratio": None if ratio is None else round(float(ratio), 6),
        "volume_used_ratio": None if vol is None else round(float(vol), 6),
        "stop_write": stop, "hard_over": hard_over,
        "used": used, "cap": cap, "ts": now,
        "amber_at": AMBER_RATIO, "red_at": RED_RATIO,
        **dict(detail or {}),
    }
    if level in ("AMBER", "RED") or stop:
        latched = latch(family, reason=reason, used=used, cap=cap,
                        volume_ratio=vol, detail=detail, now=now)
        row.update({"since": latched.get("since"), "occurrences": latched.get("occurrences")})
        write_receipt(receipt_dir, row)
    return row
