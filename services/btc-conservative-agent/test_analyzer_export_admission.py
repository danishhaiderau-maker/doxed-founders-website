"""Atomic current-report and bounded complete-export admission regressions."""

from __future__ import annotations

import json
from pathlib import Path

from research import research_dashboard as dashboard


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _set_roots(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(dashboard, "ROOT", root)
    monkeypatch.setattr(dashboard, "DATA_ROOT", root)
    monkeypatch.setattr(dashboard, "HISTORY_ROOT", root)
    dashboard._API_RESPONSE_CACHE.clear()


def test_current_generation_report_requires_atomic_manifest(monkeypatch, tmp_path):
    name = dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE
    _set_roots(monkeypatch, tmp_path)
    _write_json(tmp_path / name, {"schema": "orphan-stale-report", "qualified": True})

    blocked = dashboard._current_generation_report(name)

    assert blocked["report_unavailable"] is True
    assert blocked["blockers"] == ["ATOMIC_GENERATION_UNAVAILABLE", name]
    assert blocked.get("qualified") is None


def test_current_generation_report_reads_declared_atomic_publication(monkeypatch, tmp_path):
    name = dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE
    _set_roots(monkeypatch, tmp_path)
    published = tmp_path / dashboard.PUBLISHED_REPORTS_DIR
    report = {"schema": "safe_policy_genome_v3", "epoch_id": "epoch-current"}
    _write_json(published / name, report)
    _write_json(
        published / dashboard.REPORT_MANIFEST_FILE,
        {
            "generation_id": "generation-current",
            "fresh_epoch": {"epoch_id": "epoch-current"},
            "reports": [{"file": name}],
        },
    )

    assert dashboard._current_generation_report(name) == report


def test_export_admission_is_bounded_and_direct_export_remains_guarded(monkeypatch, tmp_path):
    _set_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(
        dashboard,
        "_generation_freshness_meta",
        lambda: (_ for _ in ()).throw(AssertionError("ZIP path must not start")),
    )
    client = dashboard.app.test_client()

    admission = client.get("/api/export-admission", headers={"Accept": "application/json"})
    assert admission.status_code == 503
    assert admission.get_json() == {
        "ok": False,
        "admitted": False,
        "status": "MIRROR_GENERATION_UNBOUND",
        "message": (
            "Complete export is unavailable. Finish verification and promotion "
            "of the fresh local Fly mirror before retrying. No archive was created."
        ),
    }

    direct = client.get("/download/everything", headers={"Accept": "application/json"})
    assert direct.status_code == 503
    assert direct.get_json()["status"] == "MIRROR_GENERATION_UNBOUND"

    _write_json(
        tmp_path / "canonical_generation_retired.json",
        {"generation_current": False},
    )
    retired = client.get("/api/export-admission", headers={"Accept": "application/json"})
    assert retired.status_code == 409
    assert retired.get_json()["status"] == "MIRROR_RETIRED_AWAITING_VERIFIED_PROMOTION"


def test_export_admission_positive_identity_and_disabled_ui(monkeypatch, tmp_path):
    _set_roots(monkeypatch, tmp_path)
    _write_json(
        tmp_path / "canonical_dataset_current.json",
        {
            "entry_hash": "a" * 64,
            "dataset_epoch": "epoch-current",
            "source_revision": "source-revision",
            "deployed_revision": "deployed-revision",
            "tile_config_signature": "tile-signature",
        },
    )
    client = dashboard.app.test_client()

    admission = client.get("/api/export-admission", headers={"Accept": "application/json"})
    assert admission.status_code == 200
    assert admission.get_json() == {
        "ok": True,
        "admitted": True,
        "status": "MIRROR_GENERATION_BOUND",
    }

    page = client.get("/").get_data(as_text=True)
    button = page[page.index('id="dl-everything"') - 80:page.index('id="dl-everything"') + 240]
    assert 'aria-disabled="true"' in button
    assert 'href="/download/everything"' not in button
    assert "MIRROR_GENERATION_UNBOUND" in page
    assert "MIRROR_RETIRED_AWAITING_VERIFIED_PROMOTION" in page

