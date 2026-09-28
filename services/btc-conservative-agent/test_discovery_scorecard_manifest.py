import ast
import importlib.util
import json
import os
from pathlib import Path

from research.discovery_scorecard_publication import (
    SCHEMA,
    build_discovery_scorecard_publication,
)


AGENT = Path(__file__).resolve().parent
ANALYZER = AGENT / "analyzer_research_engine_v62.py"


def _load_analyzer(name):
    inherited = os.environ.pop("BTC_AGENT_DATA_DIR", None)
    spec = importlib.util.spec_from_file_location(name, ANALYZER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
    finally:
        if inherited is not None:
            os.environ["BTC_AGENT_DATA_DIR"] = inherited
    return module


def _function_source(name):
    source = ANALYZER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    return ast.get_source_segment(source, node)


def _generation(suffix):
    return {
        "manifest_entry_hash": f"manifest-{suffix}",
        "epoch_id": f"epoch-{suffix}",
        "source_revision": f"source-{suffix}",
        "deployed_revision": f"deployed-{suffix}",
        "tile_config_signature": f"tile-{suffix}",
        "analyzer_revision": f"analyzer-{suffix}",
        "evaluator_version": f"evaluator-{suffix}",
        "generation_key": f"generation-{suffix}",
    }


def test_manifest_builds_scorecard_after_current_evaluator_and_before_final_manifest():
    source = _function_source("write_report_manifest")
    evaluator = source.index("conservative_evaluator_status = persist_v3_conservative_results(")
    scorecard = source.index("scorecard, scorecard_mirror = _write_discovery_scorecard_report(")
    final_manifest = source.index('manifest = {\n        "schema": "report_manifest_v1"')

    assert evaluator < scorecard < final_manifest
    shadow = source.index("shadow_terminal, shadow_terminal_mirror = _write_conservative_shadow_report(")
    assert evaluator < shadow < scorecard
    assert "shadow_terminal = None" in source
    assert "shadow_terminal_report={} if shadow_terminal_error else shadow_terminal" in source
    assert '"file": DISCOVERY_COHORT_SCORECARD_REPORT_FILE' in source
    assert 'DISCOVERY_COHORT_SCORECARD_REPORT_FILE: {' in source
    assert '"generation_error": discovery_scorecard_error' in source


def test_baseline_is_invocation_bound_and_never_reloaded_by_report_mtime():
    source = _function_source("write_report_manifest")
    helper = _function_source("_write_discovery_scorecard_report")

    assert "baseline_replay = None" in source
    assert "baseline_manifest_bytes = baseline_manifest_path.read_bytes()" in source
    assert "baseline_generation = generation_identity(" in source
    assert "if baseline_manifest_path.read_bytes() != baseline_manifest_bytes:" in source
    assert 'raise ValueError("BASELINE_GENERATION_CHANGED_DURING_REPLAY")' in source
    assert 'baseline_replay["generation"] = baseline_generation' in source
    assert "None if baseline_replay_error else baseline_replay" in source
    assert "baseline_report=baseline_report or {}" in helper
    assert "getmtime" not in helper
    assert "read_text" not in helper
    assert "read_bytes" not in helper


def test_rejected_generation_returns_fresh_unknown_not_prior_publication(tmp_path, monkeypatch):
    analyzer = _load_analyzer("discovery_scorecard_manifest_analyzer")
    monkeypatch.chdir(tmp_path)
    stale = {
        "schema": SCHEMA,
        "status": "BUILT",
        "winner": {"policy_id": "stale-winner"},
        "profitability_supported": True,
    }
    target = tmp_path / analyzer.DISCOVERY_COHORT_SCORECARD_REPORT_FILE
    target.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(analyzer, "_atomic_mirror_analyzer_report", lambda _name: target)

    expected = _generation("current")
    report, mirrored = analyzer._write_discovery_scorecard_report(
        tmp_path,
        {"generation": expected},
        {},
    )
    persisted = json.loads(target.read_text(encoding="utf-8"))

    assert mirrored == target
    assert report == persisted
    assert persisted["status"] == "UNKNOWN"
    assert "BASELINE_GENERATION_MISSING" in persisted["blockers"]
    assert persisted["profitability_supported"] is False
    assert persisted["winner"] is None
    assert "stale-winner" not in target.read_text(encoding="utf-8")


def test_generation_mismatch_fails_closed_before_artifact_read(tmp_path):
    expected = _generation("expected")
    result = build_discovery_scorecard_publication(
        tmp_path,
        expected_generation=expected,
        evaluator_status={
            "schema": "v3_conservative_policy_evidence_v1",
            "generation": _generation("evaluator"),
        },
        baseline_report={
            "schema": "entry_baseline_same_opportunity_replay_v1",
            "generation": _generation("baseline"),
        },
    )

    assert result["schema"] == SCHEMA
    assert result["status"] == "UNKNOWN"
    assert result["blockers"] == ["INPUT_GENERATION_MISMATCH"]
    assert result["input_artifacts"] == {}
    assert result["scorecard"] is None
    assert result["live_qualification"] is False


def test_atomic_helper_passes_this_invocation_shadow_object(tmp_path, monkeypatch):
    analyzer = _load_analyzer("discovery_shadow_atomic_integration")
    monkeypatch.chdir(tmp_path)
    import research.discovery_scorecard_publication as publication
    captured = {}
    current_shadow = {"generation": _generation("current"), "results": []}

    def build(_root, **kwargs):
        captured.update(kwargs)
        return {"schema": SCHEMA, "status": "UNKNOWN", "winner": None}

    monkeypatch.setattr(publication, "build_discovery_scorecard_publication", build)
    target = tmp_path / analyzer.DISCOVERY_COHORT_SCORECARD_REPORT_FILE
    monkeypatch.setattr(analyzer, "_atomic_mirror_analyzer_report", lambda _name: target)
    analyzer._write_discovery_scorecard_report(
        tmp_path, {"generation": _generation("current")}, {}, current_shadow,
    )
    assert captured["shadow_terminal_report"] is current_shadow
    assert json.loads(target.read_text())["winner"] is None
