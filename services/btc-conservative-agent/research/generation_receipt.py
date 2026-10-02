"""Per-generation study receipt: which studies ran, failed, or had no usable input.

An analyzer pass exits 0 even when individual studies throw.  This receipt is
the single place every consumer (health watcher, /api/insights, agent export,
self-aware) reads to decide whether a generation is complete, so a failing
required study can never surface as GREEN.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "analyzer_generation_receipt_v1"
RECEIPT_FILE = "analyzer_generation_receipt.json"

GREEN, AMBER, RED = "GREEN", "AMBER", "RED"
_RANK = {GREEN: 0, AMBER: 1, RED: 2}


def _worst(*levels: str) -> str:
    return max((lvl for lvl in levels if lvl in _RANK), key=_RANK.get, default=GREEN)


def _study_from_required(name: str, status: Any) -> dict[str, Any]:
    status = status if isinstance(status, Mapping) else {}
    error = status.get("generation_error")
    available = status.get("available_in_generation") is True
    invalid = status.get("current_generation_valid") is False
    if error:
        state = "ERROR"
    elif not available:
        state = "MISSING"
    elif invalid:
        state = "INVALID"
    else:
        state = "OK"
    error_text = str(error or "")
    return {
        "name": name,
        "required": True,
        "status": state,
        "error_class": error_text.split(":", 1)[0] if error_text else None,
        "error_head": error_text[:300] or None,
        "level": GREEN if state == "OK" else RED,
    }


def _window_level(window: Mapping[str, Any]) -> str:
    level = str(window.get("alert_level") or "").upper()
    if level in _RANK:
        return level
    if not window.get("truncated"):
        return GREEN
    eligible = window.get("events_eligible") or 0
    selected = window.get("events_selected") or 0
    return RED if eligible and selected / eligible < 0.5 else AMBER


def build_generation_receipt(
    manifest: Mapping[str, Any],
    *,
    optional_errors: Mapping[str, str | None] | None = None,
    integrity: Mapping[str, Any] | None = None,
    input_blockers: Mapping[str, Any] | None = None,
    protection_replay_window: Mapping[str, Any] | None = None,
    data_epoch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    studies = [
        _study_from_required(str(name), status)
        for name, status in sorted((manifest.get("required_report_status") or {}).items())
    ]
    for name, error in sorted((optional_errors or {}).items()):
        studies.append({
            "name": name,
            "required": False,
            "status": "ERROR" if error else "OK",
            "error_class": str(error).split(":", 1)[0] if error else None,
            "error_head": str(error)[:300] if error else None,
            "level": AMBER if error else GREEN,
        })

    integrity_status = str((integrity or {}).get("report_status") or "MISSING").upper()
    integrity_level = GREEN if integrity_status == "VALID" else RED

    blockers_level = str((input_blockers or {}).get("level") or GREEN).upper()
    if blockers_level not in _RANK:
        blockers_level = AMBER
    window = dict(protection_replay_window or {})
    window_level = _window_level(window) if window else GREEN

    failed_required = [s["name"] for s in studies if s["required"] and s["status"] != "OK"]
    failed_optional = [s["name"] for s in studies if not s["required"] and s["status"] != "OK"]
    # Known input limitations stay RED in analyzer_input_blockers.json but only degrade
    # the generation; RED here means a failed required study or non-VALID integrity.
    input_level = AMBER if _worst(blockers_level, window_level) != GREEN else GREEN
    level = _worst(*(s["level"] for s in studies), integrity_level, input_level)
    reasons = []
    if failed_required:
        reasons.append("required studies failed: " + ", ".join(failed_required))
    if integrity_status != "VALID":
        reasons.append(f"integrity {integrity_status}")
    if failed_optional:
        reasons.append("optional studies failed: " + ", ".join(failed_optional))
    if window_level != GREEN and window.get("truncated", True):
        reasons.append(
            "protection replay truncated "
            f"{window.get('events_selected')}/{window.get('events_eligible')} events"
        )
    elif window_level != GREEN:
        reasons.append(
            f"protection replay {window.get('reason') or 'degraded'} "
            f"({window.get('mark_fallback_events')} events off side-correct 1s marks)"
        )
    if blockers_level != GREEN:
        blocked = [
            f"{item.get('input')}={item.get('status')}"
            for item in (input_blockers or {}).get("items") or []
            if isinstance(item, Mapping) and item.get("status") != "OK"
        ]
        if blocked:
            reasons.append("inputs: " + ", ".join(blocked))
    epoch_admitted = (data_epoch or {}).get("pre_epoch_rows_admitted")
    if data_epoch and (epoch_admitted is None or int(epoch_admitted) > 0 or not data_epoch.get("epoch_id")):
        level = RED
        reasons.append(
            f"clean epoch {data_epoch.get('epoch_id')}: "
            + (f"{epoch_admitted} pre-epoch rows can enter results" if epoch_admitted is not None
               else f"purity unproven ({data_epoch.get('error') or 'no audit'})")
        )
    receipt = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generation_id": manifest.get("generation_id"),
        "generation_revision": manifest.get("generation_revision"),
        "level": level,
        "complete": not failed_required and integrity_status == "VALID",
        "reasons": reasons,
        "failed_required_studies": failed_required,
        "failed_optional_studies": failed_optional,
        "integrity_status": integrity_status,
        "studies": studies,
        "protection_replay_window": window or None,
        "input_blockers": dict(input_blockers) if input_blockers else None,
    }
    if data_epoch is not None:
        receipt["data_epoch"] = dict(data_epoch)
    return receipt


def write_generation_receipt(report_dir: str | os.PathLike[str], receipt: Mapping[str, Any]) -> Path:
    target = Path(report_dir) / RECEIPT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    os.replace(temp, target)
    return target


def load_generation_receipt(report_dir: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        payload = json.loads((Path(report_dir) / RECEIPT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}
