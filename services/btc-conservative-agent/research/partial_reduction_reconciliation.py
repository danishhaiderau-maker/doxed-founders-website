"""Read-only evidence report for protected Patient Chase partial reductions."""
from __future__ import annotations

import ast
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


REPORT_FILE = "partial_reduction_reconciliation_report.json"
LANES = ("OFFSET_029_ATR_PROTECTED", "OFFSET_029_ATR_REGIME")
EPSILON = 1e-8
TERMINAL_SCHEMA = "terminal_remaining_zero_v1"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _receipts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if not isinstance(value, str) or not value.strip():
        return []
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(value)
            if isinstance(parsed, list):
                return [row for row in parsed if isinstance(row, dict)]
        except (ValueError, SyntaxError, json.JSONDecodeError):
            continue
    return []


def _rows_from_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _paper_positions(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not path.is_file():
        return [], {"available": False, "schema": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [], {"available": True, "parse_error": type(exc).__name__}
    if not isinstance(payload, dict):
        return [], {"available": True, "parse_error": "NOT_OBJECT"}
    return [row for row in (payload.get("positions") or []) if isinstance(row, dict)], {
        "available": True,
        "schema": payload.get("schema"),
        "saved_at": payload.get("saved_at"),
        "paper_only": payload.get("paper_only"),
        "live_armed": payload.get("live_armed"),
    }


def _audit_row(row: dict[str, Any], *, terminal: bool) -> dict[str, Any]:
    receipts = _receipts(row.get("partial_exit_receipts"))
    original_qty = _number(row.get("policy_original_qty"))
    current_qty = _number(row.get("qty"))
    current_fraction = _number(row.get("policy_remaining_fraction"))
    issues: list[str] = []
    exclusions: list[str] = []
    previous = 1.0
    last_remaining_qty: float | None = None
    signed = 0
    for index, receipt in enumerate(receipts):
        remaining = _number(receipt.get("remaining_fraction"))
        close_fraction = _number(receipt.get("close_fraction"))
        closed_qty = _number(receipt.get("closed_qty"))
        receipt_original_qty = _number(receipt.get("original_qty"))
        receipt_prior_qty = _number(receipt.get("prior_qty"))
        receipt_remaining_qty = _number(receipt.get("remaining_qty"))
        if receipt.get("receipt_id") and receipt.get("policy_id") and receipt.get("ts"):
            signed += 1
        if remaining is None or close_fraction is None:
            issues.append(f"RECEIPT_{index}_MISSING_FRACTIONS")
            continue
        if remaining < -EPSILON or remaining > previous + EPSILON:
            issues.append(f"RECEIPT_{index}_NON_MONOTONIC_REMAINING")
        expected_close = max(0.0, previous - remaining)
        if abs(close_fraction - expected_close) > EPSILON:
            issues.append(f"RECEIPT_{index}_CLOSE_FRACTION_MISMATCH")
        # CSV quantities are display-rounded, while each signed reduction
        # receipt carries the execution-time quantity basis.  Reconcile against
        # that receipt basis when present; using the rounded CSV value creates
        # false mismatches on valid small-quantity reductions.
        quantity_basis = receipt_original_qty or original_qty
        if quantity_basis is not None and closed_qty is not None:
            quantity_tolerance = max(EPSILON, quantity_basis * 1e-6)
            if abs(closed_qty - quantity_basis * close_fraction) > quantity_tolerance:
                issues.append(f"RECEIPT_{index}_CLOSED_QTY_MISMATCH")
            if receipt_prior_qty is not None and abs(
                receipt_prior_qty - quantity_basis * previous
            ) > quantity_tolerance:
                issues.append(f"RECEIPT_{index}_PRIOR_QTY_MISMATCH")
            if receipt_remaining_qty is not None and abs(
                receipt_remaining_qty - quantity_basis * remaining
            ) > quantity_tolerance:
                issues.append(f"RECEIPT_{index}_REMAINING_QTY_MISMATCH")
        last_remaining_qty = receipt_remaining_qty
        previous = remaining
    if receipts and not terminal:
        if current_fraction is None or abs(current_fraction - previous) > EPSILON:
            issues.append("OPEN_REMAINING_FRACTION_MISMATCH")
        if original_qty is None or current_qty is None:
            issues.append("OPEN_QUANTITY_BASIS_MISSING")
        elif current_fraction is not None and abs(current_qty - original_qty * current_fraction) > max(EPSILON, original_qty * 1e-6):
            issues.append("OPEN_REMAINING_QTY_MISMATCH")
    if receipts and terminal:
        # Partial receipts describe only the reductions before the final exit.
        # A terminal trade row proves the last runner was closed through its
        # explicit zero remaining fraction and final execution quantity.
        terminal_close_qty = _number(row.get("execution_qty"))
        terminal_schema = str(row.get("partial_reduction_terminal_schema") or "")
        if terminal_schema != TERMINAL_SCHEMA:
            exclusions.append("LEGACY_TERMINAL_QUANTITY_SCHEMA")
        elif current_fraction is None or abs(current_fraction) > EPSILON:
            issues.append("TERMINAL_REMAINING_NOT_ZERO")
        if previous > EPSILON and (
            terminal_close_qty is None or last_remaining_qty is None
        ):
            issues.append("TERMINAL_CLOSE_QUANTITY_BASIS_MISSING")
        elif previous > EPSILON:
            basis = original_qty or terminal_close_qty
            if abs(terminal_close_qty - last_remaining_qty) > max(
                # trades_3factor.csv stores the final execution quantity at
                # six decimal places; allow only its half-unit quantization.
                EPSILON, abs(basis) * 1e-6, 5.0e-7
            ):
                issues.append("TERMINAL_CLOSE_QTY_MISMATCH")
    if receipts and signed != len(receipts):
        issues.append("UNSIGNED_OR_UNIDENTIFIED_RECEIPT")
    return {
        "trade_id": row.get("trade_id"),
        "research_lane": str(row.get("research_lane") or "").upper(),
        "terminal": terminal,
        "receipt_count": len(receipts),
        "signed_receipt_count": signed,
        "remaining_fraction": current_fraction if terminal else (
            previous if receipts else current_fraction
        ),
        "original_qty": original_qty,
        "current_qty": current_qty,
        "issues": issues,
        "exclusions": exclusions,
        "eligible_current_evidence": bool(receipts) and not exclusions,
        "reconciled": bool(receipts) and not issues and not exclusions,
    }


def build_partial_reduction_reconciliation_report(data_dir=".", report_dir=".") -> dict[str, Any]:
    data = Path(data_dir)
    positions, lifecycle = _paper_positions(data / "paper_lifecycle_v1.json")
    trades = _rows_from_csv(data / "trades_3factor.csv")
    audits = []
    for row in positions:
        if str(row.get("research_lane") or "").upper() in LANES:
            audits.append(_audit_row(row, terminal=False))
    for row in trades:
        if str(row.get("research_lane") or "").upper() in LANES:
            audits.append(_audit_row(row, terminal=True))
    with_receipts = [row for row in audits if row["receipt_count"]]
    eligible_with_receipts = [
        row for row in with_receipts if row["eligible_current_evidence"]
    ]
    issues = sorted({issue for row in audits for issue in row["issues"]})
    blockers = []
    if not with_receipts:
        blockers.append("INSUFFICIENT_PARTIAL_REDUCTION_RECEIPTS")
    if not any(row["terminal"] for row in eligible_with_receipts):
        blockers.append("INSUFFICIENT_CURRENT_PARTIAL_REDUCTION_TERMINALS")
    for lane in LANES:
        lane_receipt_rows = [
            row for row in with_receipts if row["research_lane"] == lane
        ]
        if lane_receipt_rows and not any(
            row["terminal"] and row["eligible_current_evidence"]
            for row in lane_receipt_rows
        ):
            blockers.append(
                f"INSUFFICIENT_CURRENT_PARTIAL_REDUCTION_TERMINALS:{lane}"
            )
    if any("UNSIGNED_OR_UNIDENTIFIED" in issue for issue in issues):
        blockers.append("PARTIAL_REDUCTION_RECEIPTS_UNSIGNED_OR_UNIDENTIFIED")
    if any(issue for issue in issues if "UNSIGNED_OR_UNIDENTIFIED" not in issue):
        blockers.append("REMAINING_QUANTITY_RECONCILIATION_FAILED")
    per_lane = {}
    for lane in LANES:
        lane_rows = [row for row in audits if row["research_lane"] == lane]
        lane_receipts = sum(row["receipt_count"] for row in lane_rows)
        lane_eligible = [
            row for row in lane_rows if row["eligible_current_evidence"]
        ]
        per_lane[lane] = {
            "lifecycles": len(lane_rows),
            "open_positions": sum(not row["terminal"] for row in lane_rows),
            "terminal_trades": sum(row["terminal"] for row in lane_rows),
            "partial_reduction_receipts": lane_receipts,
            "signed_receipts": sum(row["signed_receipt_count"] for row in lane_rows),
            "eligible_current_receipts": sum(
                row["receipt_count"] for row in lane_eligible
            ),
            "legacy_excluded_lifecycles": sum(bool(row["exclusions"]) for row in lane_rows),
            "reconciled_lifecycles": sum(row["reconciled"] for row in lane_rows),
            "live_copy_evidence_sufficient": bool(lane_eligible) and any(
                row["terminal"] and row["reconciled"] for row in lane_eligible
            ) and all(row["reconciled"] for row in lane_eligible),
        }
    report = {
        "schema": "partial_reduction_reconciliation_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "BLOCKED" if blockers else "DESCRIPTIVE_RECONCILED",
        "qualification": "INSUFFICIENT" if blockers else "DESCRIPTIVE_ONLY",
        "live_copy_allowed": False,
        "lanes": per_lane,
        "summary": {
            "lifecycles": len(audits),
            "lifecycles_with_receipts": len(with_receipts),
            "partial_reduction_receipts": sum(row["receipt_count"] for row in audits),
            "signed_receipts": sum(row["signed_receipt_count"] for row in audits),
            "eligible_current_receipts": sum(row["receipt_count"] for row in eligible_with_receipts),
            "legacy_excluded_lifecycles": sum(bool(row["exclusions"]) for row in audits),
            "reconciled_lifecycles": sum(row["reconciled"] for row in audits),
        },
        "integrity": {"passed": not any(issue for issue in issues if "UNSIGNED_OR_UNIDENTIFIED" not in issue), "issues": issues},
        "blockers": blockers,
        "paper_lifecycle_source": lifecycle,
        "lifecycle_audits": audits[:200],
        "note": "Paper evidence only. Legacy terminal rows are preserved but excluded from current reconciliation. Tiles 3/4 remain live fail-closed until post-repair partial-reduction terminals and remaining-quantity reconciliation are proven.",
    }
    output = Path(report_dir) / REPORT_FILE
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temp.replace(output)
    return report
