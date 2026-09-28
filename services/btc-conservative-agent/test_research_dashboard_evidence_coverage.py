from __future__ import annotations

import json

from research import research_dashboard as dashboard
from research.evidence_coverage_triage import build_evidence_coverage_triage_report


def _write_generation(root, *, declare_report: bool = True):
    published = root / dashboard.PUBLISHED_REPORTS_DIR
    published.mkdir(parents=True)
    report = build_evidence_coverage_triage_report(
        {
            "bindings": [{
                "episode_id": "episode-exact",
                "exact_binding_complete": True,
                "schedule_id": "schedule-1",
                "schedule_status": "EXACT",
                "tape_ids": ["segment-1"],
            }]
        },
        [{
            "episode_id": "episode-exact",
            "classification": "PARTIAL_FILL",
            "supported": True,
        }],
        source_counts={
            "opportunities": 6, "decisions": 7, "order_intents": 8,
            "executions": 9, "lifecycles": 10, "market_segments": 11,
        },
        archive_summary={
            "archive_session_count": 3, "verified_session_count": 1,
            "unverifiable_session_count": 1, "invalid_session_count": 1,
            "retained_file_count": 4, "retained_unique_checksum_count": 4,
            "sessions": [],
        },
    )
    (published / dashboard.EVIDENCE_COVERAGE_TRIAGE_REPORT_FILE).write_text(
        json.dumps(report), encoding="utf-8"
    )
    manifest = {
        "schema": "report_manifest_v1",
        "generation_id": "generation-current",
        "generation_revision": "revision-current",
        "generated_at": "2026-08-31T00:00:00+00:00",
        "reports": ([{"file": dashboard.EVIDENCE_COVERAGE_TRIAGE_REPORT_FILE}]
                    if declare_report else []),
    }
    (published / dashboard.REPORT_MANIFEST_FILE).write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_evidence_coverage_api_is_bounded_and_reads_declared_atomic_generation(monkeypatch, tmp_path):
    _write_generation(tmp_path)
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        dashboard, "_generation_freshness_meta",
        lambda _manifest=None: {"current": True, "stale": False, "reasons": []},
    )
    dashboard._API_RESPONSE_CACHE.clear()

    response = dashboard.app.test_client().get("/api/evidence-coverage")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "CURRENT"
    assert payload["checksum_valid"] is True
    assert payload["qualification_allowed"] is False
    assert payload["authoritative_source_record_counts"] == {
        "opportunities": 6, "decisions": 7, "order_intents": 8,
        "executions": 9, "lifecycles": 10, "market_segments": 11,
    }
    assert payload["episode_coverage"] == {
        "exact": 1, "reconstructed": 0, "unknown": 0, "total": 1,
    }
    assert payload["terminal_outcome_counts"] == {
        "FULL_FILL": 0, "PARTIAL_FILL": 1, "NO_FILL": 0, "UNKNOWN": 0,
    }
    assert payload["archive_recovery_retention"]["verified_session_count"] == 1
    assert payload["archive_recovery_retention"]["unverifiable_session_count"] == 1
    assert payload["archive_recovery_retention"]["invalid_session_count"] == 1
    assert payload["quarantined_orphan"]["status"] == "UNKNOWN"
    assert payload["quarantined_orphan"]["separate_from_general_triage"] is True
    assert "episodes" not in payload


def test_evidence_coverage_rejects_loose_file_not_declared_by_atomic_manifest(monkeypatch, tmp_path):
    _write_generation(tmp_path, declare_report=False)
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        dashboard, "_generation_freshness_meta",
        lambda _manifest=None: {"current": False, "stale": True, "reasons": ["test"]},
    )
    dashboard._API_RESPONSE_CACHE.clear()

    payload = dashboard.app.test_client().get("/api/evidence-coverage").get_json()
    assert payload["available"] is False
    assert payload["status"] == "UNAVAILABLE_CURRENT_GENERATION"
    assert payload["reason"] == "REPORT_NOT_IN_CURRENT_GENERATION"
    assert payload["qualification_allowed"] is False


def test_evidence_coverage_declared_report_checksum_failure_is_unavailable(monkeypatch, tmp_path):
    _write_generation(tmp_path)
    report_path = (
        tmp_path / dashboard.PUBLISHED_REPORTS_DIR
        / dashboard.EVIDENCE_COVERAGE_TRIAGE_REPORT_FILE
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["totals"]["opportunities"] = 999
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        dashboard, "_generation_freshness_meta",
        lambda _manifest=None: {"current": True, "stale": False, "reasons": []},
    )
    dashboard._API_RESPONSE_CACHE.clear()

    payload = dashboard.app.test_client().get("/api/evidence-coverage").get_json()
    assert payload["available"] is False
    assert payload["status"] == "INVALID_CURRENT_GENERATION"
    assert payload["reason"] == "DECLARED_REPORT_INTEGRITY_INVALID"
    assert payload["checksum_valid"] is False
    assert "authoritative_source_record_counts" not in payload


def test_evidence_coverage_navigation_and_truthful_labels_are_rendered():
    response = dashboard.app.test_client().get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '"evidence-coverage", "Evidence Coverage"' in html
    assert 'id="sec-evidence-coverage"' in html
    assert "Missing evidence remains UNKNOWN and never becomes NO_FILL" in html
    assert "loadEvidenceCoverage" in html
