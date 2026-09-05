"""Normal discovery producer -> compact manifest-published dynamic report."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from research.policy_evidence_schema import canonical_json
from test_discovery_dynamic_publication import build, fixture
from test_discovery_scorecard_publication import GENERATION
from test_dynamic_policy_publication_integration import _load


AGENT = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def analyzer():
    return _load("same_publication_dynamic_engine", AGENT / "analyzer_research_engine_v62.py")


def provenance():
    return {"generation_revision": GENERATION["analyzer_revision"],
            "source_revision": GENERATION["source_revision"], "dataset_epoch": GENERATION["epoch_id"]}


def legacy():
    return {"schema": "dynamic_policy_analyzer_orchestration_v1", "status": "UNKNOWN",
            "sealed_holdout": None, "blockers": ["DYNAMIC_INPUT_MISSING"]}


def project(analyzer, scorecard, old=None):
    return analyzer._same_publication_dynamic_report(legacy() if old is None else old,
                                                    scorecard, GENERATION, provenance())


def test_actual_discovery_producer_reaches_compact_dynamic_report(analyzer, tmp_path):
    scorecard = build(fixture(tmp_path))
    result = project(analyzer, scorecard)
    assert result["status"] == "RESEARCH_DIAGNOSTIC"
    assert result["sealed_holdout"] is None
    assert result["comparison_complete"] is False and result["live_qualification"] is False
    assert result["relay_eligible"] is False and result["live_policy_change_allowed"] is False
    summary = result["same_publication_diagnostic"]
    assert summary["counts"]["supported_outcomes"] == 1
    assert summary["counts"] == scorecard["dynamic_cohorts"]["counts"]
    assert all("episodes" not in group and "candidates" not in group for group in summary["groups"])
    assert "candidate_universe" not in summary
    assert summary["detail_artifact_canonical_sha256"] == hashlib.sha256(canonical_json(scorecard).encode()).hexdigest()
    assert result["nested_protocol"]["passed"] is False
    assert result["legacy_input_blockers"] == ["DYNAMIC_INPUT_MISSING"]
    assert "DYNAMIC_INPUT_MISSING" not in result["blockers"]
    assert "SEALED_HOLDOUT_EVIDENCE_MISSING" in result["blockers"]


@pytest.mark.parametrize("change", ["generation", "digest", "provenance", "absent"])
def test_wrong_generation_or_tamper_never_promotes(analyzer, tmp_path, change):
    scorecard = build(fixture(tmp_path))
    bindings = provenance()
    if change == "generation":
        scorecard["dynamic_cohorts"]["expected_generation"]["source_revision"] = "wrong"
    elif change == "digest":
        scorecard["dynamic_cohorts"]["counts"]["supported_outcomes"] = 400
    elif change == "provenance":
        bindings["dataset_epoch"] = "wrong"
    else:
        scorecard = None
    result = analyzer._same_publication_dynamic_report(legacy(), scorecard, GENERATION, bindings)
    assert result["status"] == "UNKNOWN"
    assert result["same_publication_diagnostic"]["blockers"] == ["SAME_PUBLICATION_DYNAMIC_BINDING_UNAVAILABLE"]


def test_current_builder_sealed_result_is_not_overwritten(analyzer, tmp_path):
    old = {**legacy(), **provenance(), "status": "PASS", "sealed_holdout": {"qualification_eligible": True},
           "input_receipt": {"verification": "CHECKSUM_VERIFIED_CANONICAL_MIRROR"},
           "nested_protocol": {"passed": True}, "orchestration_receipt_id": "existing", "blockers": []}
    before = deepcopy(old)
    result = project(analyzer, build(fixture(tmp_path)), old)
    assert {key: result[key] for key in before} == before
    assert old == before
    assert result["same_publication_diagnostic"]["comparison_complete"] is False


@pytest.mark.parametrize("discovery_available", [True, False])
@pytest.mark.parametrize("defect", ["source_revision", "dataset_epoch", "generation_revision",
                                    "unknown_current", "empty_current", "missing_receipt", "unverified_receipt"])
def test_unbound_sealed_result_is_historical_only_even_when_discovery_fails(
        analyzer, tmp_path, discovery_available, defect):
    old = {**legacy(), **provenance(), "status": "PASS",
           "sealed_holdout": {"qualification_eligible": True},
           "input_receipt": {"verification": "CHECKSUM_VERIFIED_CANONICAL_MIRROR"}}
    current = provenance()
    if defect in current:
        old[defect] = "old-identity"
    elif defect == "unknown_current":
        old["source_revision"] = current["source_revision"] = "UNKNOWN"
    elif defect == "empty_current":
        old["source_revision"] = current["source_revision"] = " "
    elif defect == "missing_receipt":
        old.pop("input_receipt")
    else:
        old["input_receipt"]["verification"] = "UNKNOWN"
    before = deepcopy(old)
    scorecard = build(fixture(tmp_path)) if discovery_available else None
    result = analyzer._same_publication_dynamic_report(old, scorecard, GENERATION, current)
    assert result["status"] != "PASS"
    assert result["sealed_holdout"] is None and result["live_qualification"] is False
    assert result["relay_eligible"] is False
    assert result["historical_legacy_diagnostic"]["original_report"] == before
    assert result["historical_legacy_diagnostic"]["current_generation_eligible"] is False
    assert old == before


def test_bound_sealed_result_survives_discovery_failure(analyzer):
    old = {**legacy(), **provenance(), "status": "PASS",
           "sealed_holdout": {"qualification_eligible": True},
           "input_receipt": {"verification": "CHECKSUM_VERIFIED_CANONICAL_MIRROR"}}
    result = project(analyzer, None, old)
    assert result["status"] == "PASS" and result["sealed_holdout"] == old["sealed_holdout"]
    assert "historical_legacy_diagnostic" not in result


def test_multiple_evaluation_groups_do_not_choose_an_aggregate_winner(analyzer, tmp_path):
    scorecard = build(fixture(tmp_path))
    cohorts = scorecard["dynamic_cohorts"]
    cohorts["nested_research_evaluations"] *= 2
    cohorts["publication_sha256"] = hashlib.sha256(canonical_json(
        {k: v for k, v in cohorts.items() if k != "publication_sha256"}).encode()).hexdigest()
    result = project(analyzer, scorecard)
    assert result["nested_protocol"] is None
    assert result["nested_protocol_scope"] == "SEPARATE_COHORTS_NO_AGGREGATE_WINNER"


def test_actual_projection_is_atomically_published_and_route_visible(analyzer, tmp_path, monkeypatch):
    result = project(analyzer, build(fixture(tmp_path)))
    import research.mirror_coherence as coherence
    import research.canonical_data_store as store
    monkeypatch.setattr(coherence, "assert_mirror_coherent", lambda **kwargs: None)
    monkeypatch.setattr(store, "record_analyzer_completion", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    Path(analyzer.DYNAMIC_POLICY_ANALYSIS_REPORT_FILE).write_text(json.dumps(result), encoding="utf-8")
    analyzer._publish_completed_report_generation({"generation_id": "same-publication",
        "reports": [{"file": analyzer.DYNAMIC_POLICY_ANALYSIS_REPORT_FILE}], "text_artifacts": []})
    published = tmp_path / analyzer.PUBLISHED_REPORTS_DIR
    assert json.loads((published / analyzer.DYNAMIC_POLICY_ANALYSIS_REPORT_FILE).read_text())["status"] == "RESEARCH_DIAGNOSTIC"
    dashboard = _load("same_publication_dynamic_dashboard", AGENT / "research" / "research_dashboard.py")
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    dashboard._API_RESPONSE_CACHE.clear()
    response = dashboard.app.test_client().get("/api/dynamic-policy-research").get_json()
    assert response["status"] == "RESEARCH_DIAGNOSTIC"
    assert response["qualification"] == "UNKNOWN" and response["relay_eligible"] is False


def test_engine_build_order_and_single_manifest_entry():
    source = (AGENT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "write_report_manifest")
    calls = {node.func.id: node.lineno for node in ast.walk(function)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert calls["_write_discovery_scorecard_report"] < calls["_same_publication_dynamic_report"]
    text = ast.get_source_segment(source, function)
    assert "if fname == DYNAMIC_POLICY_ANALYSIS_REPORT_FILE:" in text
    assert text.count('"file": DYNAMIC_POLICY_ANALYSIS_REPORT_FILE') == 1
