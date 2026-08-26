"""Fail-closed reconciliation between policy-cycle and analyzer integrity reports."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


LIFECYCLE_INTEGRITY_BLOCKERS = frozenset({
    "ORPHAN_EXPECTED_ORDER",
    "BLOCKED_ORDER_RESOLUTION_INTEGRITY",
    "V3_ORDER_RESOLUTION_INTEGRITY_FAILED",
    "V3_DATA_INTEGRITY_FAILED",
    "V3_INTEGRITY_FAILED",
    "V3_EPOCH_CONTAMINATION_BLOCKED",
    "POLICY_IDENTITY_CONTAMINATION",
    "CAUSAL_IDENTITY_ALIAS_EXCLUDED",
})


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _policy_lifecycle_defects(report: Mapping[str, Any]) -> set[str]:
    defects = {
        str(blocker).strip().upper()
        for blocker in (report.get("blockers") or [])
        if str(blocker).strip().upper() in LIFECYCLE_INTEGRITY_BLOCKERS
    }

    integrity = report.get("integrity") or {}
    if isinstance(integrity, Mapping) and integrity.get("passed") is False:
        defects.add("V3_DATA_INTEGRITY_FAILED")

    collection = report.get("collection") or {}
    resolution = (
        collection.get("entry_resolution_integrity") or {}
        if isinstance(collection, Mapping)
        else {}
    )
    if isinstance(resolution, Mapping):
        try:
            overdue = int(resolution.get("overdue_orphan") or 0)
        except (TypeError, ValueError):
            overdue = 0
        orphan_rows = resolution.get("orphan_expected_orders") or []
        resolution_passed = resolution.get("passed")
        if overdue > 0 or orphan_rows or resolution_passed is False:
            defects.add("ORPHAN_EXPECTED_ORDER")
    return defects


def reconcile_analyzer_integrity_with_policy_reports(
    integrity_path: str | os.PathLike[str],
    policy_reports: Iterable[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Atomically make analyzer integrity reflect current policy lifecycle defects.

    The ordinary analyzer checks run before the expensive policy-cycle replay.
    This final reconciliation is therefore required after those reports are built.
    It is idempotent and never turns an unrelated failed analyzer check green.
    """
    target = Path(integrity_path)
    report_items = list(policy_reports)
    payload = _load_json(target)
    if not payload:
        payload = {
            "schema": "analyzer_integrity_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "checks": [{
                "check": "analyzer_integrity_base_receipt",
                "passed": False,
                "expected": "pre-policy analyzer integrity receipt",
                "found": "MISSING_OR_UNREADABLE",
                "detail": "Policy reconciliation cannot replace the ordinary analyzer integrity pass.",
            }],
        }

    sources: dict[str, list[str]] = {}
    for name, report in report_items:
        defects = sorted(_policy_lifecycle_defects(report))
        if defects:
            sources[str(name)] = defects

    checks = [
        check for check in (payload.get("checks") or [])
        if not (
            isinstance(check, Mapping)
            and check.get("check") == "v3_policy_lifecycle_integrity"
        )
    ]
    defects = sorted({item for values in sources.values() for item in values})
    policy_check = {
        "check": "v3_policy_lifecycle_integrity",
        "passed": not defects,
        "expected": "no current-generation lifecycle/order-resolution integrity blockers",
        "found": defects or "NONE",
        "detail": (
            "Current policy-cycle reports contain fail-closed lifecycle defects: "
            + "; ".join(f"{name}={','.join(values)}" for name, values in sorted(sources.items()))
            if defects
            else "Current policy-cycle reports contain no lifecycle/order-resolution integrity blockers."
        ),
        "source_reports": sorted(sources) if sources else [name for name, _ in report_items],
    }
    checks.append(policy_check)

    valid = all(bool(check.get("passed")) for check in checks if isinstance(check, Mapping))
    payload.update({
        "valid": valid,
        "report_status": "VALID" if valid else "INVALID",
        "banner": None if valid else "⚠ REPORT INVALID — reconcile lifecycle/order-resolution defects before trusting policy results",
        "checks": checks,
        "failed_checks": [check for check in checks if not check.get("passed")],
        "policy_lifecycle_reconciled_at": datetime.now(timezone.utc).isoformat(),
    })
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp, target)
    return payload


def reconcile_analyzer_integrity_from_files(
    integrity_path: str | os.PathLike[str],
    report_dir: str | os.PathLike[str],
    report_names: Iterable[str],
) -> dict[str, Any]:
    root = Path(report_dir)
    reports = [(name, _load_json(root / name)) for name in report_names]
    return reconcile_analyzer_integrity_with_policy_reports(integrity_path, reports)
