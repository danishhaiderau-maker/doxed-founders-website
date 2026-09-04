from __future__ import annotations

from pathlib import Path

from research import research_dashboard as dashboard


def test_research_design_api_exposes_signed_definitions_and_truthful_coverage(monkeypatch):
    dashboard._API_RESPONSE_CACHE.clear()
    report = {
        "conservative_evaluator": {
            "regime_feature_coverage": {
                "schema": "phase7_regime_feature_coverage_v1",
                "row_count": 3,
                "dimensions": [
                    {"name": "realized_volatility", "observed_rows": 2,
                     "unknown_rows": 1, "status": "PARTIAL"},
                    {"name": "volatility_of_volatility", "observed_rows": 0,
                     "unknown_rows": 3, "status": "UNKNOWN"},
                ],
                "qualification_allowed": False,
                "profitability_calculated": False,
            }
        }
    }
    source = {"manifest": {"generation_id": "generation-1",
                            "generation_revision": "revision-1"}}
    monkeypatch.setattr(
        dashboard, "_declared_atomic_generation_report",
        lambda _name: (report, source),
    )
    monkeypatch.setattr(
        dashboard, "_generation_freshness_meta",
        lambda _manifest=None: {"current": True, "stale": False, "reasons": []},
    )

    payload = dashboard.app.test_client().get("/api/research-design").get_json()
    assert payload["status"] == "CURRENT"
    assert payload["qualification_allowed"] is False
    assert payload["profitability_calculated"] is False
    assert payload["profitability_status"] == "NOT_CALCULATED_FROM_DEFINITIONS_OR_COVERAGE"
    assert len(payload["entry_baselines"]) == 11
    assert all(row["execution_class"] == "RESEARCH_ONLY" for row in payload["entry_baselines"])
    assert all(row["relay_eligible"] is False for row in payload["entry_baselines"])
    assert all(row["places_order"] is False for row in payload["entry_baselines"])
    assert payload["regime_feature_coverage"]["dimensions"][1]["status"] == "UNKNOWN"


def test_research_design_api_keeps_definitions_but_marks_missing_generation_unavailable(monkeypatch):
    dashboard._API_RESPONSE_CACHE.clear()
    monkeypatch.setattr(
        dashboard, "_declared_atomic_generation_report",
        lambda _name: (None, {"reason": "REPORT_NOT_IN_CURRENT_GENERATION", "manifest": {}}),
    )
    monkeypatch.setattr(
        dashboard, "_generation_freshness_meta",
        lambda _manifest=None: {"current": False, "stale": True, "reasons": ["missing"]},
    )

    payload = dashboard.app.test_client().get("/api/research-design").get_json()
    assert payload["status"] == "UNAVAILABLE_CURRENT_GENERATION"
    assert payload["reason"] == "REPORT_NOT_IN_CURRENT_GENERATION"
    assert len(payload["entry_baselines"]) == 11
    assert payload["regime_feature_coverage"]["status"] == "UNKNOWN_CURRENT_GENERATION"
    assert payload["qualification_allowed"] is False


def test_research_design_navigation_and_non_fabrication_labels_render():
    dashboard._API_RESPONSE_CACHE.clear()
    html = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert '"research-design", "Entry & Regime Evidence"' in html
    assert 'id="sec-research-design"' in html
    assert "Definitions and coverage never create fills, PnL, profitability, qualification" in html
    assert "Observed / unknown regime dimensions" in html
    assert "loadResearchDesign" in html


def test_research_design_client_failure_is_visible_and_fail_closed():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    assert "if (!r.ok) throw new Error(`HTTP ${r.status}`)" in source
    assert "RESEARCH_DESIGN_LOAD_FAILED" in source
    assert "signed baseline definitions and outcomes remain UNKNOWN" in source
    assert "regime feature coverage remains UNKNOWN" in source
    assert "['Qualification', 'DISABLED']" in source


def test_stale_header_and_forensic_export_provenance_are_unambiguous():
    dashboard._API_RESPONSE_CACHE.clear()
    html = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert "STALE SAVED ANALYZER GENERATION · READ-ONLY" in html
    assert 'id="bundle-provenance"' in html
    assert "ZIP checksums prove captured bytes; they do not prove" in html
    assert "Download Forensic Research Evidence Bundle" in html
    assert "inspect MANIFEST.json generation_current and provenance before use" in html
    assert "Show exact parity and freshness receipts" in html
    assert "Wait for the verified Fly mirror and its single owner analyzer publication" in html
    assert "do not start a duplicate analyzer" in html
    assert "python analyzer_research_engine_v62.py" not in html
    assert "Show exact integrity receipts" in html


def test_report_explorer_and_archives_fail_closed_without_duplicate_run_advice():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    assert "REPORT_MANIFEST_LOAD_FAILED" in source
    assert "Report inventory is unavailable. No report content was inferred." in source
    assert "No reports are declared by the current verified manifest." in source
    assert "No archives are declared by the current verified manifest." in source
    assert "li.setAttribute('role', 'status')" in source
    explorer = source.split("async function loadExplorer()", 1)[1].split(
        "async function loadArchives()", 1
    )[0]
    assert "if (!r.ok) throw new Error(`HTTP ${r.status}`)" in explorer
    assert "try {" in explorer and "catch (error)" in explorer
    assert "run analyzer once" not in explorer.lower()
