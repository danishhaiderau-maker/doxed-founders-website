import csv
import importlib.util
import json
from pathlib import Path

from research.partial_reduction_reconciliation import (
    build_partial_reduction_reconciliation_report,
)


AGENT = Path(__file__).resolve().parent


def _write_trades(path: Path, rows):
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _receipt(receipt_id, remaining, close, *, original=1.0, prior=None):
    prior = (remaining + close) * original if prior is None else prior
    return {
        "receipt_id": receipt_id,
        "policy_id": "protected-policy",
        "ts": "2026-08-24T00:00:00+00:00",
        "remaining_fraction": remaining,
        "close_fraction": close,
        "original_qty": original,
        "prior_qty": prior,
        "closed_qty": close * original,
        "remaining_qty": remaining * original,
    }


def test_report_reconciles_open_and_terminal_remaining_quantity(tmp_path):
    lifecycle = {
        "schema": "paper_lifecycle_v1",
        "saved_at": "2026-08-24T00:00:00+00:00",
        "paper_only": True,
        "live_armed": False,
        "positions": [{
            "trade_id": "open-1", "research_lane": "OFFSET_029_ATR_PROTECTED",
            "policy_original_qty": 1.0, "qty": 0.5, "policy_remaining_fraction": 0.5,
            "partial_exit_receipts": [_receipt("r-open", 0.5, 0.5)],
        }],
    }
    (tmp_path / "paper_lifecycle_v1.json").write_text(json.dumps(lifecycle), encoding="utf-8")
    _write_trades(tmp_path / "trades_3factor.csv", [{
        "trade_id": "closed-1", "research_lane": "OFFSET_029_ATR_REGIME",
        "policy_remaining_fraction": 0.0,
        "partial_reduction_terminal_schema": "terminal_remaining_zero_v1",
        "partial_exit_receipts": json.dumps([
            _receipt("r-1", 0.5, 0.5), _receipt("r-2", 0.0, 0.5),
        ]),
    }])

    report = build_partial_reduction_reconciliation_report(tmp_path, tmp_path)

    assert report["status"] == "BLOCKED"
    assert report["summary"] == {
        "lifecycles": 2, "lifecycles_with_receipts": 2,
        "partial_reduction_receipts": 3, "signed_receipts": 3,
        "eligible_current_receipts": 3, "legacy_excluded_lifecycles": 0,
        "reconciled_lifecycles": 2,
    }
    # An open reduction proves quantity bookkeeping, but cannot prove the
    # terminal runner contract required for live-copy readiness.
    assert report["lanes"]["OFFSET_029_ATR_PROTECTED"]["live_copy_evidence_sufficient"] is False
    assert report["lanes"]["OFFSET_029_ATR_REGIME"]["live_copy_evidence_sufficient"] is True
    assert (
        "INSUFFICIENT_CURRENT_PARTIAL_REDUCTION_TERMINALS:OFFSET_029_ATR_PROTECTED"
        in report["blockers"]
    )
    assert report["live_copy_allowed"] is False
    assert json.loads((tmp_path / "partial_reduction_reconciliation_report.json").read_text()) == report


def test_terminal_reconciliation_uses_signed_receipt_quantity_basis_not_rounded_csv(tmp_path):
    original = 0.00031663
    first = _receipt("r-1", 0.75, 0.25, original=original)
    second = _receipt("r-2", 0.50, 0.25, original=original, prior=0.75 * original)
    # Match venue/storage precision from real receipts.
    for receipt in (first, second):
        for key in ("original_qty", "prior_qty", "closed_qty", "remaining_qty"):
            receipt[key] = round(receipt[key], 8)
    _write_trades(tmp_path / "trades_3factor.csv", [{
        "trade_id": "closed-rounded",
        "research_lane": "OFFSET_029_ATR_PROTECTED",
        "policy_original_qty": 0.000317,
        "policy_remaining_fraction": 0.0,
        "partial_reduction_terminal_schema": "terminal_remaining_zero_v1",
        "execution_qty": round(original * 0.50, 6),
        "partial_exit_receipts": json.dumps([first, second]),
    }])

    report = build_partial_reduction_reconciliation_report(tmp_path, tmp_path)

    assert report["integrity"]["passed"] is True
    assert report["lifecycle_audits"][0]["issues"] == []
    assert report["lifecycle_audits"][0]["remaining_fraction"] == 0.0


def test_legacy_terminal_fraction_is_excluded_without_poisoning_current_integrity(tmp_path):
    original = 0.00031747
    receipt = _receipt("legacy-r", 0.75, 0.25, original=original)
    for key in ("original_qty", "prior_qty", "closed_qty", "remaining_qty"):
        receipt[key] = round(receipt[key], 8)
    _write_trades(tmp_path / "trades_3factor.csv", [{
        "trade_id": "legacy-terminal",
        "research_lane": "OFFSET_029_ATR_PROTECTED",
        "policy_original_qty": round(original, 6),
        "policy_remaining_fraction": 0.75,
        "execution_qty": round(original * 0.75, 6),
        "partial_exit_receipts": json.dumps([receipt]),
    }])

    report = build_partial_reduction_reconciliation_report(tmp_path, tmp_path)

    audit = report["lifecycle_audits"][0]
    assert audit["issues"] == []
    assert audit["exclusions"] == ["LEGACY_TERMINAL_QUANTITY_SCHEMA"]
    assert audit["reconciled"] is False
    assert report["integrity"]["passed"] is True
    assert report["summary"]["eligible_current_receipts"] == 0
    assert report["summary"]["legacy_excluded_lifecycles"] == 1
    assert "INSUFFICIENT_CURRENT_PARTIAL_REDUCTION_TERMINALS" in report["blockers"]


def test_report_truthfully_blocks_unsigned_or_quantity_mismatched_receipts(tmp_path):
    lifecycle = {
        "schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
        "positions": [{
            "trade_id": "bad", "research_lane": "OFFSET_029_ATR_PROTECTED",
            "policy_original_qty": 1.0, "qty": 0.9, "policy_remaining_fraction": 0.5,
            "partial_exit_receipts": [{
                "policy_id": "p", "ts": "now", "remaining_fraction": 0.5,
                "close_fraction": 0.5, "closed_qty": 0.5,
            }],
        }],
    }
    (tmp_path / "paper_lifecycle_v1.json").write_text(json.dumps(lifecycle), encoding="utf-8")

    report = build_partial_reduction_reconciliation_report(tmp_path, tmp_path)

    assert report["status"] == "BLOCKED"
    assert "PARTIAL_REDUCTION_RECEIPTS_UNSIGNED_OR_UNIDENTIFIED" in report["blockers"]
    assert "REMAINING_QUANTITY_RECONCILIATION_FAILED" in report["blockers"]
    assert report["integrity"]["passed"] is False
    assert report["lanes"]["OFFSET_029_ATR_PROTECTED"]["live_copy_evidence_sufficient"] is False


def test_dashboard_api_matches_completed_partial_reduction_report(monkeypatch):
    path = AGENT / "research" / "research_dashboard.py"
    spec = importlib.util.spec_from_file_location("partial_dashboard", path)
    dashboard = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(dashboard)
    payload = {
        "schema": "partial_reduction_reconciliation_v1",
        "status": "BLOCKED", "qualification": "INSUFFICIENT",
        "live_copy_allowed": False, "lanes": {}, "summary": {},
        "integrity": {"passed": True, "issues": []},
        "blockers": ["INSUFFICIENT_PARTIAL_REDUCTION_RECEIPTS"],
    }
    monkeypatch.setattr(dashboard, "_read_report", lambda name, default=None: payload)
    dashboard._API_RESPONSE_CACHE.clear()
    client = dashboard.app.test_client()
    assert client.get("/api/partial-reduction-reconciliation").get_json() == payload
    page = client.get("/partial-reduction-reconciliation")
    assert page.status_code == 200
    assert b"Partial Reduction Reconciliation" in page.data
