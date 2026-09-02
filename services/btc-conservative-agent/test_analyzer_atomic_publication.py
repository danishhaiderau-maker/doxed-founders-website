import importlib.util
import json
import os
from pathlib import Path


AGENT = Path(__file__).resolve().parent


def _load(name, path):
    # The desktop launcher intentionally exports the real canonical mirror for
    # long-lived processes.  Unit imports must not inherit that machine-level
    # selection: each test redirects ROOT/DATA_ROOT to its own isolated store
    # after import, and production containment remains enforced by the module.
    inherited_data_root = os.environ.pop("BTC_AGENT_DATA_DIR", None)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
    finally:
        if inherited_data_root is not None:
            os.environ["BTC_AGENT_DATA_DIR"] = inherited_data_root
    return module


def test_completed_generation_publication_is_atomic_and_preserves_full_artifact(tmp_path, monkeypatch):
    analyzer = _load("atomic_analyzer", AGENT / "analyzer_research_engine_v62.py")
    import research.mirror_coherence as mirror_coherence
    import research.canonical_data_store as canonical_data_store

    monkeypatch.setattr(mirror_coherence, "assert_mirror_coherent", lambda **_kwargs: None)
    monkeypatch.setattr(canonical_data_store, "record_analyzer_completion", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
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


def test_publication_coherence_uses_dataset_identity_not_analyzer_code_revision(
    tmp_path, monkeypatch
):
    analyzer = _load("revision_separation_analyzer", AGENT / "analyzer_research_engine_v62.py")
    import research.mirror_coherence as mirror_coherence
    import research.canonical_data_store as canonical_data_store

    observed = {}
    monkeypatch.setattr(
        mirror_coherence,
        "assert_mirror_coherent",
        lambda **kwargs: observed.update(kwargs),
    )
    monkeypatch.setattr(canonical_data_store, "record_analyzer_completion", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    manifest = {
        "generation_id": "code-data-separated",
        "generation_revision": "c2ddb218edd9",
        "source_revision": "577a188d2abc",
        "deployed_revision": "577a188d2abc",
        "manifest_entry_hash": "3" * 64,
        "dataset_checksum": "4" * 64,
        "reports": [],
        "text_artifacts": [],
    }

    analyzer._publish_completed_report_generation(manifest)

    assert manifest["generation_revision"] != manifest["source_revision"]
    assert observed["expected_revision"] == manifest["source_revision"]
    assert observed["expected_deployed_revision"] == manifest["deployed_revision"]
    assert observed["expected_manifest_entry_hash"] == manifest["manifest_entry_hash"]
    assert observed["expected_dataset_checksum"] == manifest["dataset_checksum"]


def test_report_provenance_maps_canonical_entry_hash_to_manifest_identity(
    tmp_path, monkeypatch
):
    analyzer = _load("canonical_entry_identity_analyzer", AGENT / "analyzer_research_engine_v62.py")
    canonical = {
        "source_revision": "577a188d2abc",
        "deployed_revision": "577a188d2abc",
        "dataset_epoch": "epoch-current",
        "tile_config_signature": "config-current",
        "entry_hash": "3" * 64,
        "dataset_checksum": "4" * 64,
    }
    (tmp_path / "canonical_dataset_current.json").write_text(
        json.dumps(canonical), encoding="utf-8"
    )
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))

    provenance = analyzer._report_source_evidence_provenance()

    assert provenance["manifest_entry_hash"] == canonical["entry_hash"]
    assert provenance["dataset_checksum"] == canonical["dataset_checksum"]


def test_report_provenance_does_not_use_conflicting_manifest_entry_alias(
    tmp_path, monkeypatch
):
    analyzer = _load("canonical_entry_alias_analyzer", AGENT / "analyzer_research_engine_v62.py")
    canonical = {
        "source_revision": "577a188d2abc",
        "deployed_revision": "577a188d2abc",
        "dataset_epoch": "epoch-current",
        "tile_config_signature": "config-current",
        "entry_hash": "3" * 64,
        "manifest_entry_hash": "9" * 64,
        "dataset_checksum": "4" * 64,
    }
    (tmp_path / "canonical_dataset_current.json").write_text(
        json.dumps(canonical), encoding="utf-8"
    )
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))

    provenance = analyzer._report_source_evidence_provenance()

    assert provenance["manifest_entry_hash"] == canonical["entry_hash"]
    assert provenance["manifest_entry_hash"] != canonical["manifest_entry_hash"]


def test_policy_evidence_library_manifest_is_declared_inventory_and_status_only(tmp_path, monkeypatch):
    analyzer = _load("policy_library_atomic_analyzer", AGENT / "analyzer_research_engine_v62.py")
    dashboard = _load("policy_library_atomic_dashboard", AGENT / "research" / "research_dashboard.py")
    import research.mirror_coherence as mirror_coherence
    import research.canonical_data_store as canonical_data_store

    monkeypatch.setattr(mirror_coherence, "assert_mirror_coherent", lambda **_kwargs: None)
    monkeypatch.setattr(canonical_data_store, "record_analyzer_completion", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    filename = analyzer.POLICY_EVIDENCE_LIBRARY_MANIFEST_FILE
    binding_filename = analyzer.POLICY_EVIDENCE_BINDING_REPORT_FILE
    Path(filename).write_text(json.dumps({
        "schema":"policy_evidence_library_v1", "cache_status":"NOT_BUILT",
        "evaluation_triggered":False, "qualification_allowed":False,
    }), encoding="utf-8")
    Path(binding_filename).write_text(json.dumps({
        "schema": "v3_policy_evidence_binding_index_v1", "exactly_bound_count": 0,
    }), encoding="utf-8")
    manifest = {"generation_id":"g", "reports":[
        {"file":filename}, {"file":binding_filename},
    ], "text_artifacts":[]}
    analyzer._publish_completed_report_generation(manifest)
    assert (Path(analyzer.PUBLISHED_REPORTS_DIR) / filename).is_file()
    assert (Path(analyzer.PUBLISHED_REPORTS_DIR) / binding_filename).is_file()

    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    dashboard._API_RESPONSE_CACHE.clear()
    response = dashboard.app.test_client().get("/api/policy-evidence-library")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["cache_status"] == "NOT_BUILT"
    assert payload["evaluation_triggered"] is False


def test_policy_evidence_reports_are_atomically_mirrored_for_archive(tmp_path, monkeypatch):
    analyzer = _load("policy_report_mirror_analyzer", AGENT / "analyzer_research_engine_v62.py")
    monkeypatch.chdir(tmp_path)
    source = Path(analyzer.POLICY_EVIDENCE_BINDING_REPORT_FILE)
    source.write_text('{"generation":"current"}', encoding="utf-8")
    destination = analyzer._atomic_mirror_analyzer_report(source.name)
    assert destination == Path(analyzer.REPORTS_DIR) / source.name
    assert json.loads(destination.read_text()) == {"generation": "current"}
    source.write_text('{"generation":"replacement"}', encoding="utf-8")
    analyzer._atomic_mirror_analyzer_report(source.name)
    assert json.loads(destination.read_text()) == {"generation": "replacement"}
    assert not list(Path(analyzer.REPORTS_DIR).glob(f".{source.name}.*.tmp"))


def test_lifecycle_bundle_inventory_is_declared_and_atomically_published(tmp_path, monkeypatch):
    analyzer = _load("lifecycle_inventory_atomic_analyzer", AGENT / "analyzer_research_engine_v62.py")
    import research.mirror_coherence as mirror_coherence
    import research.canonical_data_store as canonical_data_store

    monkeypatch.setattr(mirror_coherence, "assert_mirror_coherent", lambda **_kwargs: None)
    monkeypatch.setattr(canonical_data_store, "record_analyzer_completion", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    filename = analyzer.LIFECYCLE_BUNDLE_INVENTORY_REPORT_FILE
    Path(filename).write_text(json.dumps({
        "schema": "lifecycle_bundle_inventory_v1",
        "qualification": {"unique_lifecycle_count": 1},
        "transfer": {
            "audit_only": True, "profitability_supported": False,
            "ranking_eligible": False, "source_cleanup_authorized": False,
        },
    }), encoding="utf-8")
    manifest = {
        "generation_id": "lifecycle-generation",
        "reports": [{"file": filename}], "text_artifacts": [],
    }
    analyzer._publish_completed_report_generation(manifest)
    published = Path(analyzer.PUBLISHED_REPORTS_DIR) / filename
    assert published.is_file()
    payload = json.loads(published.read_text(encoding="utf-8"))
    assert payload["transfer"]["audit_only"] is True
    assert payload["transfer"]["ranking_eligible"] is False


def test_static_analyzer_dashboard_labels_transfer_bundles_as_audit_only(tmp_path, monkeypatch):
    analyzer = _load("lifecycle_inventory_static_analyzer", AGENT / "analyzer_research_engine_v62.py")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    assert analyzer.write_analysis_dashboard_html({
        "generated_at": "2026-09-01T03:00:00Z", "performance": {},
        "analysis_provenance": {"generation_revision": "r"},
    }) is True
    html = Path(analyzer.ANALYSIS_DASHBOARD_HTML).read_text(encoding="utf-8")
    assert "manifest-verified qualification bundles" in html
    assert "Inventory completeness and parity cover manifests only" in html
    assert "UNKNOWN_NOT_SCANNED" in html
    assert "payload files read is 0" in html
    assert "no payload integrity or ranking qualification is claimed" in html
    assert "transfer-ready audit copies" in html
    assert "audit-only=true" in html
    assert "ranking eligible=false" in html
    assert "profitability supported=false" in html
    assert "source cleanup authorized=false" in html


def test_static_dashboard_uses_supplied_inventory_snapshot_after_tree_mutation(tmp_path, monkeypatch):
    analyzer = _load("lifecycle_inventory_snapshot_analyzer", AGENT / "analyzer_research_engine_v62.py")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    snapshot = {
        "schema": "lifecycle_bundle_inventory_v1", "inventory_scope": "MANIFEST_ONLY",
        "complete": True, "complete_scope": "MANIFEST_INVENTORY",
        "payload_verification_status": "UNKNOWN_NOT_SCANNED",
        "payload_files_read": 0,
        "qualification": {"unique_lifecycle_count": 7},
        "transfer": {
            "unique_lifecycle_count": 5, "audit_only": True,
            "profitability_supported": False, "ranking_eligible": False,
            "source_cleanup_authorized": False,
        },
        "parity": {"scope": "MANIFEST_INVENTORY", "intersection_count": 5},
        "invalid_manifest_count": 0,
    }
    # A new on-disk bundle appearing after snapshot creation must not trigger a
    # second scan or alter the static generation counts.
    (tmp_path / "v3" / "lifecycle_transfer_bundles" / "aa" / "transfer-new").mkdir(parents=True)
    monkeypatch.setattr(
        analyzer, "build_lifecycle_bundle_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("rescanned")),
    )
    assert analyzer.write_analysis_dashboard_html(
        {"performance": {}, "analysis_provenance": {"generation_revision": "r"}},
        lifecycle_inventory=snapshot,
    ) is True
    html = Path(analyzer.ANALYSIS_DASHBOARD_HTML).read_text(encoding="utf-8")
    assert '<div class="val">7</div>' in html
    assert '<div class="val">5</div>' in html


def test_policy_library_manifest_records_identity_skip_without_claiming_fill(tmp_path, monkeypatch):
    analyzer = _load("policy_status_stamp_analyzer", AGENT / "analyzer_research_engine_v62.py")
    monkeypatch.chdir(tmp_path)
    target = Path(analyzer.POLICY_EVIDENCE_LIBRARY_MANIFEST_FILE)
    target.write_text('{"schema":"policy_evidence_library_v1"}', encoding="utf-8")
    payload = analyzer._stamp_policy_evaluator_status(target, {
        "schema": "v3_conservative_policy_evidence_v1", "row_count": 3,
        "classification_counts": {"UNKNOWN": 3}, "cache_rows_ingested": 2,
        "cache_rows_skipped_missing_identity": 1,
        "cache_skip_reason_counts": {"RESULT_IDENTITY_MISSING_OPPORTUNITY_ID": 1},
    })
    assert payload["evaluation_triggered"] is True
    assert payload["conservative_evaluator"]["classification_counts"] == {"UNKNOWN": 3}
    assert payload["conservative_evaluator"]["cache_rows_skipped_missing_identity"] == 1
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))


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
    # Zero-evidence rows in the ordinary descriptive list remain filtered;
    # only the explicitly named diagnostic shortlist may provide fallback.
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
