import importlib.util
import json
from pathlib import Path


AGENT = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_completed_generation_publication_is_atomic_and_preserves_full_artifact(tmp_path, monkeypatch):
    analyzer = _load("atomic_analyzer", AGENT / "analyzer_research_engine_v62.py")
    import research.mirror_coherence as mirror_coherence

    monkeypatch.setattr(mirror_coherence, "assert_mirror_coherent", lambda **_kwargs: None)
    monkeypatch.chdir(tmp_path)
    Path("safe_policy_genome_v3_report.json").write_text(
        json.dumps({"generated_at": "new", "candidate_screen": {"full_grid": [1, 2, 3]}}),
        encoding="utf-8",
    )
    published = Path(analyzer.PUBLISHED_REPORTS_DIR)
    published.mkdir()
    (published / "safe_policy_genome_v3_report.json").write_text(
        json.dumps({"generated_at": "old"}), encoding="utf-8"
    )
    (published / analyzer.REPORT_MANIFEST_FILE).write_text(
        json.dumps({"generation_id": "old"}), encoding="utf-8"
    )
    manifest = {
        "generation_id": "new-generation",
        "reports": [{"file": "safe_policy_genome_v3_report.json"}],
        "text_artifacts": [],
    }

    analyzer._publish_completed_report_generation(manifest)

    assert json.loads((published / analyzer.REPORT_MANIFEST_FILE).read_text())["generation_id"] == "new-generation"
    full = json.loads((published / "safe_policy_genome_v3_report.json").read_text())
    assert full["candidate_screen"]["full_grid"] == [1, 2, 3]
    assert json.loads(Path(analyzer.REPORT_MANIFEST_FILE).read_text())["generation_id"] == "new-generation"
    assert not list(tmp_path.glob(".published_reports.staging-*"))
    assert not list(tmp_path.glob(".published_reports.previous-*"))


def test_dashboard_reads_declared_artifacts_only_from_completed_generation(tmp_path, monkeypatch):
    dashboard = _load("atomic_dashboard", AGENT / "research" / "research_dashboard.py")
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    published = tmp_path / dashboard.PUBLISHED_REPORTS_DIR
    published.mkdir()
    manifest = {
        "generation_id": "complete",
        "reports": [{"file": dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE}],
        "text_artifacts": [],
    }
    (published / dashboard.REPORT_MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    (published / dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE).write_text(
        json.dumps({"generated_at": "complete"}), encoding="utf-8"
    )
    (tmp_path / dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE).write_text(
        json.dumps({"generated_at": "in-progress"}), encoding="utf-8"
    )
    (tmp_path / "not_published_report.json").write_text(
        json.dumps({"generated_at": "in-progress"}), encoding="utf-8"
    )

    assert dashboard._read_report(dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE)["generated_at"] == "complete"
    assert dashboard._read_json(dashboard.REPORT_MANIFEST_FILE)["generation_id"] == "complete"
    assert dashboard._read_report("not_published_report.json") == {}
    assert dashboard._best_report_path("not_published_report.json") is None


def test_five_family_routes_do_not_revive_an_undeclared_stale_safe_report(
    tmp_path, monkeypatch
):
    dashboard = _load("manifest_strict_dashboard", AGENT / "research" / "research_dashboard.py")
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    published = tmp_path / dashboard.PUBLISHED_REPORTS_DIR
    published.mkdir()
    manifest = {
        "generation_id": "current-generation",
        "generated_at": "2026-08-26T03:38:22+00:00",
        "generation_revision": "current-revision",
        "source_data_revision": "current-source",
        "fresh_epoch": {"epoch_id": "epoch-current"},
        "reports": [{"file": dashboard.REPORT_MANIFEST_FILE}],
        "text_artifacts": [],
    }
    (published / dashboard.REPORT_MANIFEST_FILE).write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (tmp_path / dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE).write_text(
        json.dumps({
            "generated_at": "2026-08-26T01:11:00+00:00",
            "epoch_id": "epoch-stale",
            "candidate_screen": {
                "descriptive_top_100": [{"policy_id": "stale-winner"}]
            },
        }),
        encoding="utf-8",
    )

    dashboard._API_RESPONSE_CACHE.clear()
    client = dashboard.app.test_client()
    for route in (
        "/api/safe-policy-genome-v3.1",
        "/api/best-policy-research",
        "/api/static-policy-research",
        "/api/dynamic-policy-research",
        "/api/shadow-policy-research",
        "/api/risk-drawdown",
        "/api/chronological-oos",
        "/api/evidence-maturity",
        "/api/partial-reduction",
    ):
        response = client.get(route)
        assert response.status_code == 200, route
        payload = response.get_json()
        assert payload.get("epoch_id") == "epoch-current", route
        assert "REPORT_NOT_IN_CURRENT_GENERATION" in (payload.get("blockers") or []), route
        assert "stale-winner" not in response.get_data(as_text=True), route


def test_safe_and_combo_public_payloads_are_bounded(monkeypatch):
    dashboard = _load("bounded_dashboard", AGENT / "research" / "research_dashboard.py")
    rows = [{"policy_id": f"p-{idx}", "ranking_eligible": False} for idx in range(500)]
    report = {
        "schema": "safe_policy_genome_v3_1_report_v1",
        "status": "V3_COLLECTING",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "generation_revision": "generation-revision-123",
        "source_data_revision": "source-revision-456",
        "cohort_schema": "analysis_cohorts_v1",
        "policy_comparability_key": "epoch:policy:fill-world",
        "analysis_provenance": {
            "data_root_kind": "CANONICAL_LOCAL_FLY_MIRROR",
            "source_data_revision": "source-revision-456",
        },
        "cohorts": {"SAFE_POLICY": {"included_row_count": 9}},
        "report_eligibility": {"eligible": False, "reasons": ["IMMATURE"]},
        "candidate_screen": {
            "unique_policies_evaluated": 500,
            "descriptive_top_100": rows,
            "drawdown_control_leaders": rows,
            "profit_capture_leaders": {"atr": rows},
            "scenario_c_atr_stop_sweep": {
                "qualification": "DESCRIPTIVE_ONLY",
                "leaders_by_stop": {"1": rows},
                "best_by_chase_and_stop": {
                    "patient": {"1": {"policy_id": "scenario-c-patient-stop-1"}}
                },
            },
            "full_grid": rows,
        },
        "safe_policy_ranking": {
            "policies_evaluated": 500,
            "ranked_policies": rows,
            "blocked_policies": rows,
        },
    }
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: {
        "report": report, "screen": report["candidate_screen"], "epoch_id": None,
        "qualified": False, "blockers": [],
    })
    dashboard._API_RESPONSE_CACHE.clear()
    client = dashboard.app.test_client()

    safe = client.get("/api/safe-policy-genome-v3.1").get_json()
    assert safe["generation_revision"] == "generation-revision-123"
    assert safe["source_data_revision"] == "source-revision-456"
    assert safe["cohort_schema"] == "analysis_cohorts_v1"
    assert safe["policy_comparability_key"] == "epoch:policy:fill-world"
    assert safe["analysis_provenance"]["data_root_kind"] == "CANONICAL_LOCAL_FLY_MIRROR"
    assert safe["cohorts"]["SAFE_POLICY"]["included_row_count"] == 9
    assert safe["report_eligibility"]["reasons"] == ["IMMATURE"]
    # Zero-evidence rows must not be published merely because the source list
    # is large.  The public API is bounded *and* fail-closed on execution
    # evidence, so this fixture's 500 ineligible rows produce no leaders.
    assert safe["candidate_screen"]["descriptive_top_100"] == []
    assert safe["candidate_screen"]["profit_capture_leaders"] == {}
    sweep = safe["candidate_screen"]["scenario_c_atr_stop_sweep"]
    assert sweep["best_by_chase_and_stop"] == {}
    assert sweep["diagnostic_hypotheses_by_chase_and_stop"]["patient"]["1"][
        "policy_id"
    ] == "scenario-c-patient-stop-1"
    assert "full_grid" not in safe["candidate_screen"]
    assert "blocked_policies" not in safe["safe_policy_ranking"]
    assert safe["full_artifact"].endswith("safe_policy_genome_v3_report.json")
    combos = client.get("/api/combos").get_json()
    assert len(combos["top"]) <= 100
    assert "blocked_policies" not in combos["policy_grid"]["policy_search_statistics"]
