import hashlib
import json
from pathlib import Path

import pytest

from research.shadow_model_input import (
    PATH_ENV, HASH_ENV, load_shadow_model_input, assert_publication_shadow_model_input,
)
from test_declared_shadow_model import contract
from test_conservative_shadow_report import GEN, _fixture
from test_discovery_scorecard_manifest import _load_analyzer


@pytest.fixture(autouse=True)
def clear_input(monkeypatch):
    monkeypatch.delenv(PATH_ENV, raising=False)
    monkeypatch.delenv(HASH_ENV, raising=False)


def pin(tmp_path, monkeypatch):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(contract()), encoding="utf-8")
    monkeypatch.setenv(PATH_ENV, str(path))
    monkeypatch.setenv(HASH_ENV, hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_disabled_has_no_implicit_cost_model():
    source = load_shadow_model_input()
    assert source.resolve(GEN) is None and source.provenance() is None
    assert_publication_shadow_model_input({})


def test_file_is_pinned_and_exact_generation_required(tmp_path, monkeypatch):
    pin(tmp_path, monkeypatch)
    source = load_shadow_model_input()
    assert source.resolve(GEN) == contract()
    with pytest.raises(ValueError, match="GENERATION_MISMATCH"):
        source.resolve({**GEN, "epoch_id": "other"})
    assert_publication_shadow_model_input({"analysis_provenance": {"shadow_model_input": source.provenance()}})
    with pytest.raises(ValueError, match="PUBLICATION_INPUT_MISMATCH"):
        assert_publication_shadow_model_input({})


def test_mutation_and_removal_cannot_publish_old_input(tmp_path, monkeypatch):
    path = pin(tmp_path, monkeypatch)
    source = load_shadow_model_input()
    manifest = {"analysis_provenance": {"shadow_model_input": source.provenance()}}
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="INPUT_CHANGED"):
        source.assert_unchanged()
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        assert_publication_shadow_model_input(manifest)
    monkeypatch.delenv(PATH_ENV)
    monkeypatch.delenv(HASH_ENV)
    with pytest.raises(ValueError, match="PUBLICATION_INPUT_MISMATCH"):
        assert_publication_shadow_model_input(manifest)


def test_explicit_argument_is_deep_snapshot_and_no_env_ambiguity(tmp_path, monkeypatch):
    value = contract()
    source = load_shadow_model_input(value)
    value["fee_rates"]["entry"] = .99
    assert source.resolve(GEN)["fee_rates"]["entry"] == .001
    pin(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        load_shadow_model_input(value)


@pytest.mark.parametrize("raw,code", [
    (b'{"schema":"declared_shadow_model_v1","schema":"other"}', "DUPLICATE_KEY"),
    (b'{"schema":"declared_shadow_model_v1","value":NaN}', "NONFINITE"),
    (b'[]', "OBJECT_REQUIRED"),
])
def test_malformed_pinned_model_rejected(tmp_path, monkeypatch, raw, code):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    monkeypatch.setenv(PATH_ENV, str(path))
    monkeypatch.setenv(HASH_ENV, hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match=code):
        load_shadow_model_input()


def test_normal_shadow_helper_writes_verifiable_full_stream(tmp_path, monkeypatch):
    analyzer = _load_analyzer("shadow_stream_normal_publisher")
    import research.policy_evidence_schema as schema
    import research.conservative_shadow_report as shadow
    from research.shadow_result_stream import verify_result_stream
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    baseline, candidates, artifact, model = _fixture(evidence)
    (evidence / "canonical_dataset_current.json").write_text("{}")
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.chdir(output)
    monkeypatch.setattr(schema, "generation_identity", lambda *a, **k: GEN)
    monkeypatch.setattr(shadow, "load_current_policy_candidates", lambda *a, **k: (candidates, artifact))
    monkeypatch.setattr(analyzer, "_atomic_mirror_analyzer_report", lambda name: output / name)
    result, _ = analyzer._write_conservative_shadow_report(
        evidence, output, baseline, policy_cycle_succeeded=True, research_model=model)
    assert result["complete_replay_count"] == 1
    assert result["result_stream"]["complete"] is True
    with verify_result_stream(output, result, GEN) as index:
        assert index.verified_summary["verified"] is True
        assert index.verified_summary["complete_replay_count"] == 1
    again, _ = analyzer._write_conservative_shadow_report(
        evidence, output, baseline, policy_cycle_succeeded=True, research_model=model)
    assert again["result_stream"]["relative_path"] == "conservative_shadow_results.jsonl.gz"
    assert len(list(output.glob("*.jsonl.gz"))) == 1
    assert not list(output.glob(".shadow-results-*.tmp"))
    def fail(*a, **k):
        k["result_sink"]({"temporary": "unpublished"})
        raise ValueError("TEST_REPLAY_FAILURE")
    monkeypatch.setattr(shadow, "build_conservative_shadow_report", fail)
    failed, _ = analyzer._write_conservative_shadow_report(
        evidence, output, baseline, policy_cycle_succeeded=True, research_model=model)
    assert failed["status"] == "UNKNOWN" and "result_stream" not in failed
    assert not list(output.glob(".shadow-results-*.tmp"))
    assert not list(evidence.glob("shadow-results-*"))


@pytest.mark.parametrize("corrupt", [False, True])
def test_atomic_publisher_verifies_copied_binary_and_preserves_prior(tmp_path, monkeypatch, corrupt):
    analyzer = _load_analyzer("shadow_binary_atomic_publisher")
    import research.mirror_coherence as coherence
    import research.canonical_data_store as store
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(coherence, "assert_mirror_coherent", lambda **k: None)
    monkeypatch.setattr(store, "record_analyzer_completion", lambda *a, **k: {})
    source = Path("shadow-results-test.jsonl.gz")
    source.write_bytes(b"complete stream")
    published = Path(analyzer.PUBLISHED_REPORTS_DIR)
    published.mkdir()
    (published / analyzer.REPORT_MANIFEST_FILE).write_text('{"generation_id":"old"}')
    manifest = {"generation_id": "new", "reports": [{"file": str(source),
        "size_bytes": source.stat().st_size,
        "artifact_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}
    if corrupt:
        source.write_bytes(b"changed stream")
        with pytest.raises(ValueError, match="COPY_HASH_MISMATCH"):
            analyzer._publish_completed_report_generation(manifest)
    else:
        analyzer._publish_completed_report_generation(manifest)
        assert (published / source).read_bytes() == source.read_bytes()
    actual = json.loads((published / analyzer.REPORT_MANIFEST_FILE).read_text())
    assert actual["generation_id"] == ("old" if corrupt else "new")


def test_atomic_publisher_rejects_escape_before_copy(tmp_path, monkeypatch):
    analyzer = _load_analyzer("shadow_binary_path_fence")
    import research.mirror_coherence as coherence
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(coherence, "assert_mirror_coherent", lambda **k: None)
    copied = []
    monkeypatch.setattr(analyzer.shutil, "copy2", lambda *args: copied.append(args))
    with pytest.raises(ValueError, match="ARTIFACT_PATH_INVALID"):
        analyzer._publish_completed_report_generation({"reports": [{"file": "../outside.gz"}]})
    assert copied == []
    assert not list(tmp_path.glob(".*.staging-*"))


def test_variant_full_streams_bound_and_mirrored(tmp_path, monkeypatch):
    import copy
    import research.policy_evidence_schema as schema
    import research.conservative_shadow_report as shadow
    from research.shadow_result_stream import verify_result_stream
    analyzer = _load_analyzer("variant_full_stream_publisher")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    baseline, candidates, artifact, model = _fixture(evidence)
    (evidence / "canonical_dataset_current.json").write_text("{}")
    episode = baseline["episode_receipts"][0]
    episode["delayed_variants"] = [
        {"timing_model_sha256": c * 64, "results": copy.deepcopy(episode["results"])}
        for c in "ab"]
    output = tmp_path / "output"
    output.mkdir()
    archive = tmp_path / "archive"
    monkeypatch.chdir(output)
    monkeypatch.setattr(analyzer, "REPORTS_DIR", str(archive))
    monkeypatch.setattr(schema, "generation_identity", lambda *a, **k: GEN)
    monkeypatch.setattr(shadow, "load_current_policy_candidates", lambda *a, **k: (candidates, artifact))
    result, _ = analyzer._write_conservative_shadow_report(
        evidence, output, baseline, policy_cycle_succeeded=True, research_model=model)
    assert len(result.get("delayed_variant_reports", [])) == 2, result
    for report in [result] + [v["report"] for v in result["delayed_variant_reports"]]:
        for root in (output, archive):
            with verify_result_stream(root, report, GEN) as index:
                assert index.verified_summary["complete_replay_count"] == 1
    assert len(list(archive.glob("*.jsonl.gz"))) == 3
    original = shadow.build_conservative_shadow_report
    calls = []
    def fail_variant(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("VARIANT_STREAM_FAILED")
        return original(*args, **kwargs)
    monkeypatch.setattr(shadow, "build_conservative_shadow_report", fail_variant)
    result, _ = analyzer._write_conservative_shadow_report(
        evidence, output, baseline, policy_cycle_succeeded=True, research_model=model)
    assert result["status"] == "UNKNOWN" and "result_stream" not in result
    assert not list(output.glob(".shadow-*.tmp"))


@pytest.mark.parametrize("key", ["../" + "a" * 61, "A" * 64, "g" * 64])
def test_variant_cohort_rejects_hostile_hash(key):
    from research.entry_baseline_replay import delayed_variant_cohorts
    with pytest.raises(ValueError, match="DELAYED_VARIANT_IDENTITY_CONFLICT"):
        delayed_variant_cohorts({"episode_receipts": [{"delayed_variants": [
            {"timing_model_sha256": key, "results": []}]}]})
