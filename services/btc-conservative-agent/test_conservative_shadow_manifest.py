import json

from test_discovery_scorecard_manifest import _function_source, _load_analyzer
from test_conservative_shadow_report import _fixture
from research import conservative_shadow_report as builder
from research.policy_evidence_schema import generation_identity, stable_hash


def _pin(tmp_path, baseline, receipt, model):
    manifest = {"entry_hash": "manifest-1", "dataset_epoch": "epoch_id-1",
                "source_revision": "source_revision-1", "deployed_revision": "deployed_revision-1",
                "tile_config_signature": "tile_config_signature-1"}
    (tmp_path / "canonical_dataset_current.json").write_text(json.dumps(manifest))
    generation = generation_identity(manifest, analyzer_revision="analyzer_revision-1",
                                     evaluator_version="evaluator_version-1")
    baseline["generation"] = receipt["evaluation_generation"] = generation
    model["generation"] = generation
    model["signature"] = stable_hash("conservative-shadow-research-model",
                                    {key: value for key, value in model.items() if key != "signature"})


def test_terminal_report_is_before_atomic_manifest_and_registered():
    source = _function_source("write_report_manifest")
    baseline = source.index('baseline_replay["generation"] = baseline_generation')
    terminal = source.index("shadow_terminal, shadow_terminal_mirror = _write_conservative_shadow_report(")
    final = source.index('manifest = {\n        "schema": "report_manifest_v1"')
    assert baseline < terminal < final
    assert "policy_cycle_succeeded=policy_cycle_error is None" in source
    assert "research_model=shadow_research_model" in source
    assert 'CONSERVATIVE_SHADOW_TERMINAL_REPORT_FILE: {' in source
    assert '"generation_error": shadow_terminal_error' in source


def test_actual_terminal_builder_is_staged_with_explicit_model(tmp_path, monkeypatch):
    baseline, candidates, receipt, model = _fixture(tmp_path)
    _pin(tmp_path, baseline, receipt, model)
    analyzer = _load_analyzer("shadow_manifest_complete")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builder, "load_current_policy_candidates", lambda *a, **kw: (candidates, receipt))
    target = tmp_path / analyzer.CONSERVATIVE_SHADOW_TERMINAL_REPORT_FILE
    monkeypatch.setattr(analyzer, "_atomic_mirror_analyzer_report", lambda name: target)
    report, mirrored = analyzer._write_conservative_shadow_report(
        tmp_path, tmp_path, baseline, policy_cycle_succeeded=True, research_model=model,
    )
    assert mirrored == target
    assert json.loads(json.dumps(report)) == json.loads(target.read_text())
    assert report["complete_replay_count"] == 1
    assert report["results"][0]["net_pnl_usd"] == 1.07
    assert report["live_qualification"] is False
    assert not target.with_suffix(".json.tmp").exists()


def test_failed_policy_cycle_replaces_stale_winner_without_loading_artifact(tmp_path, monkeypatch):
    baseline, _, _, _ = _fixture(tmp_path)
    analyzer = _load_analyzer("shadow_manifest_failed_cycle")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / analyzer.CONSERVATIVE_SHADOW_TERMINAL_REPORT_FILE
    target.write_text('{"winner":"STALE","profitability_supported":true}')
    monkeypatch.setattr(analyzer, "_atomic_mirror_analyzer_report", lambda name: target)
    report, _ = analyzer._write_conservative_shadow_report(
        tmp_path, tmp_path / "does-not-exist", baseline, policy_cycle_succeeded=False,
    )
    assert report["status"] == "UNKNOWN"
    assert report["blockers"] == ["POLICY_CYCLE_NOT_SUCCESSFUL"]
    assert report["generation"] == baseline["generation"]
    assert report["profitability_supported"] is False
    assert "STALE" not in target.read_text()


def test_unexpected_failure_does_not_export_exception_secrets(tmp_path, monkeypatch):
    baseline, candidates, receipt, model = _fixture(tmp_path)
    _pin(tmp_path, baseline, receipt, model)
    analyzer = _load_analyzer("shadow_manifest_redaction")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / analyzer.CONSERVATIVE_SHADOW_TERMINAL_REPORT_FILE
    monkeypatch.setattr(analyzer, "_atomic_mirror_analyzer_report", lambda name: target)
    def fail(*args, **kwargs):
        raise OSError("private-token=do-not-publish")
    monkeypatch.setattr(builder, "load_current_policy_candidates", fail)
    report, _ = analyzer._write_conservative_shadow_report(
        tmp_path, tmp_path, baseline, policy_cycle_succeeded=True,
    )
    assert report["blockers"] == ["SHADOW_REPORT_BUILD_FAILED"]
    assert report["failure_class"] == "OSError"
    assert "private-token" not in target.read_text()


def test_changed_manifest_during_build_rejects_successful_outcomes(tmp_path, monkeypatch):
    baseline, candidates, receipt, model = _fixture(tmp_path)
    _pin(tmp_path, baseline, receipt, model)
    analyzer = _load_analyzer("shadow_manifest_changed")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builder, "load_current_policy_candidates", lambda *a, **kw: (candidates, receipt))
    target = tmp_path / analyzer.CONSERVATIVE_SHADOW_TERMINAL_REPORT_FILE
    monkeypatch.setattr(analyzer, "_atomic_mirror_analyzer_report", lambda name: target)
    original = builder.build_conservative_shadow_report
    def swap(*args, **kwargs):
        result = original(*args, **kwargs)
        (tmp_path / "canonical_dataset_current.json").write_text('{}')
        return result
    monkeypatch.setattr(builder, "build_conservative_shadow_report", swap)
    report, _ = analyzer._write_conservative_shadow_report(
        tmp_path, tmp_path, baseline, policy_cycle_succeeded=True, research_model=model,
    )
    assert report["status"] == "UNKNOWN"
    assert report["blockers"] == ["SHADOW_CANONICAL_GENERATION_CHANGED_DURING_REPLAY"]
    assert report["results"] == []
