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

# The only data epoch a receipt may describe. Any other epoch id (including the collector sub-epoch
# epoch-<hash>, which lives inside this data epoch) is hard-rejected: RED and incomplete.
EXPECTED_DATA_EPOCH_ID = "ce-20261004-v31-freeze21b"
QUARANTINED_INPUTS = {"exit_ladder_simulator": "BLOCKED_QUARANTINED"}
OPTIONAL_INPUTS = {"cross_world_evidence": "OPTIONAL_MISSING"}
_NON_GATING = {"BLOCKED_QUARANTINED", "OPTIONAL_MISSING", "RETIRED"}
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



INTEGRITY_CHECK_UNNAMED = "INTEGRITY_CHECK_UNNAMED"
INTEGRITY_CHECK_UNCHECKED = "INTEGRITY_CHECK_UNCHECKED"


def _check_name(check: Any) -> str:
    """Integrity checks are written as ``{"check": name, ...}``; ``name`` is accepted for older payloads."""
    if isinstance(check, Mapping):
        raw = check.get("check")
        if raw in (None, ""):
            raw = check.get("name")
    else:
        raw = check
    text = "" if raw is None else str(raw).strip()
    return text if text and text.lower() != "none" else ""


def integrity_verdict(integrity: Mapping | None) -> tuple[str, list[str]]:
    """(status, failing check names), fail closed (RK R1 ruling 25 Oct 2026).

    VALID only when the report says VALID, every check carries a name and every check's ``passed`` is exactly
    True.  An unnamed check is INVALID with ``INTEGRITY_CHECK_UNNAMED``; a check whose ``passed`` is None/unknown
    is INVALID with ``INTEGRITY_CHECK_UNCHECKED:<name>``.  Failing checks are always reported by name.
    """
    payload = integrity if isinstance(integrity, Mapping) else {}
    status = str(payload.get("report_status") or "MISSING").upper()
    checks = [c for c in (payload.get("checks") or []) if c is not None]
    failed = list(payload.get("failed_checks") or [])
    names: list[str] = []
    for check in failed + [c for c in checks if not isinstance(c, Mapping) or c.get("passed") is not True]:
        name = _check_name(check)
        passed = check.get("passed") if isinstance(check, Mapping) else None
        if not name:
            label = INTEGRITY_CHECK_UNNAMED
        elif passed is None or (isinstance(check, Mapping) and not isinstance(passed, bool)):
            label = f"{INTEGRITY_CHECK_UNCHECKED}:{name}"
        else:
            label = name
        if label not in names:
            names.append(label)
    if status == "VALID" and (names or not checks):
        status = "INVALID"
        if not checks and not names:
            names.append(INTEGRITY_CHECK_UNCHECKED + ":NO_CHECKS")
    return status, names

def build_generation_receipt(
    manifest: Mapping[str, Any],
    *,
    optional_errors: Mapping[str, str | None] | None = None,
    integrity: Mapping[str, Any] | None = None,
    input_blockers: Mapping[str, Any] | None = None,
    protection_replay_window: Mapping[str, Any] | None = None,
    data_epoch: Mapping[str, Any] | None = None,
    source_revision: str | None = None,
    expected_epoch_id: str = EXPECTED_DATA_EPOCH_ID,
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

    integrity_status, integrity_failed_names = integrity_verdict(integrity)
    integrity_level = GREEN if integrity_status == "VALID" else RED

    # Exit-ladder sim (quarantined) and cross-world evidence (optional) are listed, never gating.
    blocker_items = [i for i in (input_blockers or {}).get("items") or [] if isinstance(i, Mapping)]
    quarantined, optional_missing, gating = [], [], []
    for item in blocker_items:
        name, status = item.get("input"), item.get("status")
        if status == "OK":
            continue
        if name in QUARANTINED_INPUTS:
            quarantined.append({"input": name, "status": QUARANTINED_INPUTS[name],
                                "reason_code": item.get("reason_code")})
        elif name in OPTIONAL_INPUTS:
            optional_missing.append({"input": name, "status": OPTIONAL_INPUTS[name],
                                     "reason_code": item.get("reason_code")})
        elif status not in _NON_GATING:
            gating.append(item)
    if input_blockers and blocker_items:
        blockers_level = GREEN
        for item in gating:
            blockers_level = _worst(blockers_level, RED if item.get("status") == "BLOCKED" else AMBER)
        if any(i.get("input") == "protection_replay_window" and (i.get("evidence") or {}).get("alert_level") == RED
               for i in blocker_items):
            blockers_level = RED
    else:
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
        failed_checks = integrity_failed_names
        reasons.append(f"integrity {integrity_status}"
                       + (f" (failed: {', '.join(failed_checks[:8])})" if failed_checks else ""))
    if failed_optional:
        reasons.append("optional studies failed: " + ", ".join(failed_optional))
    if window_level != GREEN and window.get("truncated", True):
        reasons.append(
            "protection replay truncated "
            f"{window.get('events_selected')}/{window.get('events_eligible')} events"
        )
    elif window_level != GREEN:
        marks = window.get("mark_source") or {}
        fallback = window.get("mark_fallback_events", marks.get("fallback_events"))
        reasons.append(
            f"protection replay {window.get('reason') or marks.get('reason') or 'degraded'} "
            f"({fallback} events R17_UNPROVEN outside book coverage)"
        )
    if blockers_level != GREEN:
        blocked = [f"{item.get('input')}={item.get('status')}" for item in gating]
        if blocked:
            reasons.append("inputs: " + ", ".join(blocked))
    # Book-only replay: a non-book pricing basis is never countable.
    marks = window.get("mark_events_by_basis") or (window.get("mark_source") or {}).get("events_by_basis") or {}
    non_book = sorted(b for b, n in dict(marks).items() if n and b not in ("BOOK10L_WALK_TRADE_STOP_V1", "R17_UNPROVEN"))
    if non_book:
        level = RED
        reasons.append("protection replay priced off-book: " + ", ".join(non_book))
    epoch_admitted = (data_epoch or {}).get("pre_epoch_rows_admitted")
    if data_epoch and (epoch_admitted is None or int(epoch_admitted) > 0 or not data_epoch.get("epoch_id")):
        level = RED
        reasons.append(
            f"clean epoch {data_epoch.get('epoch_id')}: "
            + (f"{epoch_admitted} pre-epoch rows can enter results" if epoch_admitted is not None
               else f"purity unproven ({data_epoch.get('error') or 'no audit'})")
        )
    epoch_id = (data_epoch or {}).get("epoch_id")
    epoch_rejected = data_epoch is not None and epoch_id is not None and str(epoch_id) != expected_epoch_id
    if data_epoch is None or epoch_rejected:
        level = RED
        reasons.append(f"epoch hard-rejected: receipt epoch {epoch_id!r} != {expected_epoch_id}")
    unplaced = int((data_epoch or {}).get("straddle_rows_unguarded") or 0) + int(
        (data_epoch or {}).get("unstamped_rows_unguarded") or 0)
    if unplaced:
        # Item 5: straddle/unstamped rows are EXCLUDED; any that still reach a reader unguarded is RED.
        level = RED
        reasons.append(f"{unplaced} straddle/unstamped rows read unguarded (must be excluded)")
    if not source_revision:
        level = _worst(level, AMBER)
        reasons.append("source_revision missing")
    receipt = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generation_id": manifest.get("generation_id"),
        "generation_revision": manifest.get("generation_revision"),
        "level": level,
        "source_revision": source_revision or None,
        "expected_epoch_id": expected_epoch_id,
        "epoch_rejected": bool(data_epoch is None or epoch_rejected),
        "complete": (not failed_required and integrity_status == "VALID" and data_epoch is not None
                     and not epoch_rejected and not unplaced and not non_book),
        "quarantined_inputs": quarantined,
        "optional_inputs": optional_missing,
        "protection_replay_excluded_by_reason": window.get("mark_excluded_events_by_reason")
        or (window.get("mark_source") or {}).get("excluded_events_by_reason"),
        "reasons": reasons,
        "failed_required_studies": failed_required,
        "failed_optional_studies": failed_optional,
        "integrity_status": integrity_status,
        "integrity_failed_checks": integrity_failed_names,
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
