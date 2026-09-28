import json

from research.analyzer_integrity_reconciliation import (
    reconcile_analyzer_integrity_with_policy_reports,
)


def _write_integrity(path, *, passed=True):
    payload = {
        "schema": "analyzer_integrity_v1",
        "valid": passed,
        "report_status": "VALID" if passed else "INVALID",
        "checks": [{
            "check": "existing_check",
            "passed": passed,
            "expected": "ok",
            "found": "ok" if passed else "bad",
        }],
        "failed_checks": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_orphan_policy_blocker_makes_integrity_fail_closed(tmp_path):
    target = tmp_path / "analyzer_integrity_report.json"
    _write_integrity(target)

    result = reconcile_analyzer_integrity_with_policy_reports(target, [
        ("safe_policy_genome_v3_report.json", {
            "blockers": ["ORPHAN_EXPECTED_ORDER", "NO_SAFE_QUALIFIED_POLICY"],
            "collection": {"entry_resolution_integrity": {"overdue_orphan": 7}},
        }),
        ("best_policy_research_report.json", {"blockers": ["NO_SAFE_QUALIFIED_POLICY"]}),
    ])

    assert result["valid"] is False
    assert result["report_status"] == "INVALID"
    assert len(result["failed_checks"]) == 1
    check = result["failed_checks"][0]
    assert check["check"] == "v3_policy_lifecycle_integrity"
    assert check["found"] == ["ORPHAN_EXPECTED_ORDER"]
    assert json.loads(target.read_text())["valid"] is False


def test_structural_resolution_failure_is_detected_without_top_level_blocker(tmp_path):
    target = tmp_path / "analyzer_integrity_report.json"
    _write_integrity(target)

    result = reconcile_analyzer_integrity_with_policy_reports(target, [
        ("safe_policy_genome_v3_report.json", {
            "blockers": [],
            "collection": {
                "entry_resolution_integrity": {
                    "passed": False,
                    "orphan_expected_orders": [{"episode_id": "episode-1"}],
                },
            },
        }),
    ])

    assert result["valid"] is False
    assert result["failed_checks"][0]["found"] == ["ORPHAN_EXPECTED_ORDER"]


def test_clean_policy_reports_restore_only_policy_check_not_other_failures(tmp_path):
    target = tmp_path / "analyzer_integrity_report.json"
    _write_integrity(target, passed=False)

    result = reconcile_analyzer_integrity_with_policy_reports(target, [
        ("safe_policy_genome_v3_report.json", {
            "blockers": ["NO_SAFE_QUALIFIED_POLICY"],
            "integrity": {"passed": True},
            "collection": {"entry_resolution_integrity": {"passed": True, "overdue_orphan": 0}},
        }),
    ])

    assert result["valid"] is False
    assert [row["check"] for row in result["failed_checks"]] == ["existing_check"]
    policy_check = next(row for row in result["checks"] if row["check"] == "v3_policy_lifecycle_integrity")
    assert policy_check["passed"] is True


def test_reconciliation_is_idempotent_and_can_clear_resolved_policy_defect(tmp_path):
    target = tmp_path / "analyzer_integrity_report.json"
    _write_integrity(target)
    reconcile_analyzer_integrity_with_policy_reports(target, [
        ("safe_policy_genome_v3_report.json", {"blockers": ["POLICY_IDENTITY_CONTAMINATION"]}),
    ])

    result = reconcile_analyzer_integrity_with_policy_reports(target, [
        ("safe_policy_genome_v3_report.json", {"blockers": ["NO_SAFE_QUALIFIED_POLICY"]}),
    ])

    assert result["valid"] is True
    assert result["report_status"] == "VALID"
    assert result["failed_checks"] == []
    assert sum(row["check"] == "v3_policy_lifecycle_integrity" for row in result["checks"]) == 1


def test_missing_base_integrity_receipt_never_becomes_valid(tmp_path):
    target = tmp_path / "analyzer_integrity_report.json"

    result = reconcile_analyzer_integrity_with_policy_reports(target, iter([
        ("safe_policy_genome_v3_report.json", {"blockers": ["NO_SAFE_QUALIFIED_POLICY"]}),
    ]))

    assert result["valid"] is False
    assert result["report_status"] == "INVALID"
    assert [row["check"] for row in result["failed_checks"]] == [
        "analyzer_integrity_base_receipt"
    ]
    policy_check = next(row for row in result["checks"] if row["check"] == "v3_policy_lifecycle_integrity")
    assert policy_check["source_reports"] == ["safe_policy_genome_v3_report.json"]


def test_integrity_api_returns_503_for_reconciled_policy_defect(tmp_path, monkeypatch):
    from research import research_dashboard as dashboard

    target = tmp_path / "analyzer_integrity_report.json"
    _write_integrity(target)
    result = reconcile_analyzer_integrity_with_policy_reports(target, [
        ("best_policy_research_report.json", {"blockers": ["V3_ORDER_RESOLUTION_INTEGRITY_FAILED"]}),
    ])
    monkeypatch.setattr(dashboard, "_read_json", lambda _name: result)
    monkeypatch.setattr(
        dashboard,
        "_generation_freshness_meta",
        lambda *_args, **_kwargs: {
            "current": True, "stale": False, "revision_parity": "MATCH",
            "epoch_parity": "MATCH", "reasons": [],
        },
    )

    response = dashboard.app.test_client().get("/api/integrity")

    assert response.status_code == 503
    assert response.get_json()["ok"] is False
    assert response.get_json()["report_status"] == "INVALID"
