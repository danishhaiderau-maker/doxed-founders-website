from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from research import research_dashboard as dashboard
import analyzer_research_engine_v62 as engine


GENERATION = {
    "generation_id": "generation-1",
    "generated_at": "2026-08-28T00:03:04+10:00",
    "generation_revision": "rev-current",
    "source_data_revision": "data-current",
    "fresh_epoch": {"epoch_id": "epoch-current"},
}


def _install_empty_generation(monkeypatch, report_payload=None):
    report_payload = report_payload or {}

    def fake_read_json(name, default=None):
        if str(name).endswith("report_manifest.json"):
            return dict(GENERATION)
        return dict(report_payload)

    monkeypatch.setattr(dashboard, "_read_json", fake_read_json)
    monkeypatch.setattr(dashboard, "_read_report", lambda *_args, **_kwargs: dict(report_payload))


def _assert_identity_and_reason(payload):
    assert payload["generation_id"] == "generation-1"
    assert payload["generated_at"] == GENERATION["generated_at"]
    assert payload["generation_revision"] == "rev-current"
    assert payload["source_data_revision"] == "data-current"
    assert payload["epoch_id"] == "epoch-current"
    assert payload["empty_reason"].startswith("INSUFFICIENT_")


def test_empty_lightweight_apis_expose_generation_identity_and_exact_reason(monkeypatch):
    _install_empty_generation(monkeypatch)

    _assert_identity_and_reason(dashboard._feature_payload())
    _assert_identity_and_reason(dashboard._leakage_payload())
    _assert_identity_and_reason(dashboard._spread_performance_payload())
    horizon = dashboard._horizon_payload()
    _assert_identity_and_reason(horizon)
    assert horizon["coverage_reason"] == horizon["empty_reason"]
    assert horizon["max_horizon_coverage_pct"] == 0.0
    assert {row["coverage_pct"] for row in horizon["horizons"]} == {0.0}


def test_archive_row_uses_manifest_time_and_labels_summary_time(monkeypatch, tmp_path):
    archive = tmp_path / "session-1"
    archive.mkdir()
    manifest = archive / "report_manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def fake_read_json(name, default=None):
        if str(name) == str(manifest):
            return {
                **GENERATION,
                "report_count": 60,
            }
        return default or {}

    monkeypatch.setattr(dashboard, "_read_json", fake_read_json)
    row = dashboard._normalize_archive_session({
        "id": "session-1",
        "path": str(archive),
        "generated_at": "2026-08-27T23:56:04+10:00",
    })
    assert row["generated_at"] == GENERATION["generated_at"]
    assert row["manifest_generated_at"] == GENERATION["generated_at"]
    assert row["summary_generated_at"] == "2026-08-27T23:56:04+10:00"
    assert row["generation_id"] == "generation-1"
    assert row["report_count"] == 60


def test_status_scopes_observed_and_active_tile_signatures(monkeypatch):
    active_tiles = [
        {"policy_signature": f"tile-{index}"}
        for index in range(5)
    ]
    manifest = {
        **GENERATION,
        "analyzer_sync_id": dashboard.EXPECTED_ANALYZER_SYNC_ID,
        "active_tiles": active_tiles,
    }
    safe = {
        "collection": {
            "effective_paper_execution_identities": [
                {"policy_signature": f"observed-{index}"}
                for index in range(4)
            ]
        }
    }
    monkeypatch.setattr(
        dashboard,
        "_read_json",
        lambda name, default=None: manifest
        if str(name).endswith("report_manifest.json")
        else {},
    )
    monkeypatch.setattr(dashboard, "_current_generation_report", lambda _name: safe)
    monkeypatch.setattr(dashboard, "_manifest_reports", lambda: manifest["active_tiles"])
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: "rev-current")
    monkeypatch.setattr(
        dashboard,
        "_analyzer_run_state",
        lambda: {"in_progress": False, "last_completed_at": GENERATION["generated_at"]},
    )
    monkeypatch.setattr(
        dashboard,
        "_DASHBOARD_STARTED_AT",
        datetime(2026, 8, 28, 0, 12, tzinfo=timezone.utc),
    )

    payload = dashboard.app.test_client().get("/api/status").get_json()
    assert payload["policy_signatures_scope"] == "OBSERVED_EXECUTION_IDENTITIES"
    assert payload["policy_signatures"] == payload["observed_execution_policy_signatures"]
    assert payload["policy_signature_counts"] == {
        "active_tile_registry": 5,
        "observed_execution": 4,
    }
    assert payload["active_tile_policy_signatures"] == [f"tile-{index}" for index in range(5)]
    assert payload["active_tile_policy_signatures_match_manifest"] is True
    assert payload["availability_receipt"]["restart_observed_with_preserved_reports"] is True
    assert payload["availability_receipt"]["restart_classification"] == "RESTART_OBSERVED_UNCLASSIFIED"


def test_executive_summary_uses_manifest_report_count_over_disk_count():
    text = engine.format_executive_summary_short({
        "manifest_report_count": 60,
        "json_reports_written": 62,
        "performance": {},
        "real_edge": {},
        "coverage": {},
        "dataset": {},
    })
    assert "all 60 JSON reports" in text
    assert "all 62 JSON reports" not in text


def test_status_fails_closed_when_a_required_core_report_is_missing(monkeypatch):
    manifest = {
        **GENERATION,
        "analyzer_sync_id": dashboard.EXPECTED_ANALYZER_SYNC_ID,
        "required_report_status": {
            "best_policy_research_report.json": {
                "available_in_generation": False,
                "generation_error": "ValueError: collision",
            },
            "exit_reports_validation.json": {
                "available_in_generation": True,
                "current_generation_valid": True,
            },
        },
    }
    monkeypatch.setattr(
        dashboard,
        "_read_json",
        lambda name, default=None: manifest
        if str(name).endswith("report_manifest.json")
        else {},
    )
    monkeypatch.setattr(dashboard, "_current_generation_report", lambda _name: {})
    monkeypatch.setattr(dashboard, "_manifest_reports", lambda: [])
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: "rev-current")
    monkeypatch.setattr(
        dashboard,
        "_analyzer_run_state",
        lambda: {"in_progress": False, "last_completed_at": GENERATION["generated_at"]},
    )

    payload = dashboard.app.test_client().get("/api/status").get_json()

    assert payload["ready"] is False
    assert payload["ok"] is False
    assert payload["required_reports_ok"] is False
    assert payload["required_report_failures"] == ["best_policy_research_report.json"]
