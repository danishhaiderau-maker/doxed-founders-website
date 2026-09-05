"""Pinned, separate causal features reach shadow-only baseline opportunities."""
import hashlib
import json

import pytest

from research import entry_baseline_replay as replay
from research.policy_evidence_schema import canonical_json, generation_identity
from research_dynamic_entry_policy import DEFAULT_CAUSAL_FEATURES
from test_baseline_execution_context_integration import _dataset


SOURCE = "v3/ledgers/pre_entry_features.jsonl"


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def _repin(root, *, include_features=True):
    state = json.loads((root / ".fly-sync-state.json").read_text())
    if include_features:
        state[SOURCE] = {}
    for relative in state:
        raw = (root / relative).read_bytes()
        state[relative] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    (root / ".fly-sync-state.json").write_text(json.dumps(state), encoding="utf-8")
    manifest = json.loads((root / "canonical_dataset_current.json").read_text())
    manifest.pop("entry_hash")
    material = {"revision": manifest["source_revision"], "epoch": manifest["dataset_epoch"], "files": state}
    manifest["dataset_checksum"] = hashlib.sha256(canonical_json(material).encode()).hexdigest()
    manifest["entry_hash"] = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    return generation_identity(manifest, analyzer_revision="analyzer-1"), manifest


def _fixture(root):
    _dataset(root)
    receipt = {
        "epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
        "receipt_schema": "pre_entry_features_v1", "availability_boundary": "PRE_DECISION_ONLY",
        "captured_at_ts": 99.0,
        "bucket_definition_signature": "collected-historical-taxonomy-42",
        "bucket_definition_schema": "test-collector-search-schema",
        "bucket_definition_version": "42",
        "features": {name: {"value": "LOW", "observed_ts": 98.0} for name in DEFAULT_CAUSAL_FEATURES},
    }
    receipt["features"]["regime"]["value"] = "BULL"
    receipt["features"]["direction"]["value"] = "LONG"
    _write_rows(root / SOURCE, [receipt])
    generation, manifest = _repin(root)
    return receipt, generation, manifest


def _run(root, generation, manifest):
    return replay.materialize_v3_opportunity_replay(
        root, generation=generation, canonical_manifest=manifest)["episode_receipts"][0]


def test_shadow_only_opportunity_gets_pinned_features_and_original_signal(tmp_path):
    _, generation, manifest = _fixture(tmp_path)
    assert not (tmp_path / "v3/ledgers/decision.jsonl").exists()
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}
    row = _run(tmp_path, generation, manifest)
    assert row["signal_ts"] == 100
    assert row["pre_entry_feature_status"] == "COMPLETE"
    assert row["bucket_definition_signature"] == "collected-historical-taxonomy-42"
    assert row["bucket_definition_status"] == "VERIFIED"
    assert row["pre_entry_feature_evidence"]["bucket_definition_signature"] == row["bucket_definition_signature"]
    for name in DEFAULT_CAUSAL_FEATURES:
        assert row["pre_entry_features"][name]["observed_ts"] == 98.0
        assert row["regime_features_at_signal"][name]["observed_ts"] == 98.0
    proof = row["pre_entry_feature_evidence"]
    assert proof["source_sha256"] == hashlib.sha256((tmp_path / SOURCE).read_bytes()).hexdigest()
    assert proof["opportunity_row_sha256"] and proof["receipt_row_sha256"]
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("field", ["epoch_id", "opportunity_id", "episode_id"])
def test_wrong_identity_never_supplies_features(tmp_path, field):
    receipt, _, _ = _fixture(tmp_path)
    receipt[field] = "foreign"
    _write_rows(tmp_path / SOURCE, [receipt])
    generation, manifest = _repin(tmp_path)
    row = _run(tmp_path, generation, manifest)
    assert row["pre_entry_feature_status"] == "UNKNOWN"
    assert row["pre_entry_features"] == {}


@pytest.mark.parametrize("field", ["source_revision", "deployed_revision", "tile_config_signature", "config_signature"])
def test_conflicting_provenance_is_rejected(tmp_path, field):
    receipt, _, _ = _fixture(tmp_path)
    path = tmp_path / "v3/ledgers/opportunity.jsonl"
    opportunity = json.loads(path.read_text().strip())
    opportunity[field] = "original"
    _write_rows(path, [opportunity])
    receipt[field] = "foreign"
    _write_rows(tmp_path / SOURCE, [receipt])
    generation, manifest = _repin(tmp_path)
    row = _run(tmp_path, generation, manifest)
    assert row["pre_entry_feature_blockers"] == ["PRE_ENTRY_CAUSAL_PROVENANCE_MISMATCH"]
    assert row["pre_entry_features"] == {}


@pytest.mark.parametrize("defect", ["duplicate", "post_capture", "post_observation", "missing_timestamp"])
def test_causal_receipt_defects_stay_unknown(tmp_path, defect):
    receipt, _, _ = _fixture(tmp_path)
    rows = [receipt]
    if defect == "duplicate":
        rows *= 3
    elif defect == "post_capture":
        receipt["captured_at_ts"] = 101
    elif defect == "post_observation":
        receipt["features"]["depth_bucket"]["observed_ts"] = 101
    else:
        receipt["features"]["depth_bucket"].pop("observed_ts")
    _write_rows(tmp_path / SOURCE, rows)
    generation, manifest = _repin(tmp_path)
    row = _run(tmp_path, generation, manifest)
    assert row["pre_entry_feature_status"] == "UNKNOWN"
    assert "depth_bucket" not in row["pre_entry_features"]
    if defect == "duplicate":
        assert row["pre_entry_feature_evidence"]["matching_receipts_ambiguous"] is True
        assert row["pre_entry_feature_blockers"] == ["PRE_ENTRY_FEATURE_RECEIPT_AMBIGUOUS"]


def test_unpinned_late_feature_file_is_not_consumed(tmp_path):
    generation, manifest, _ = _dataset(tmp_path)
    _write_rows(tmp_path / SOURCE, [{"features": {"depth_bucket": "DEEP"}}])
    row = _run(tmp_path, generation, manifest)
    assert row["pre_entry_feature_blockers"] == ["PRE_ENTRY_FEATURE_SOURCE_NOT_VERIFIED"]


def test_hash_mismatched_feature_source_is_not_consumed(tmp_path):
    receipt, generation, manifest = _fixture(tmp_path)
    receipt["features"]["depth_bucket"]["value"] = "BIG"  # Same byte length; SHA alone catches it.
    _write_rows(tmp_path / SOURCE, [receipt])
    row = _run(tmp_path, generation, manifest)
    assert row["pre_entry_feature_blockers"] == ["PRE_ENTRY_FEATURE_SOURCE_NOT_VERIFIED"]
    assert row["pre_entry_features"] == {}


def test_verified_source_change_during_replay_invalidates_publication(tmp_path, monkeypatch):
    _, generation, manifest = _fixture(tmp_path)
    original = replay._causal_feature_projection
    def mutate_after_projection(opportunity, index):
        result = original(opportunity, index)
        with (tmp_path / SOURCE).open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return result
    monkeypatch.setattr(replay, "_causal_feature_projection", mutate_after_projection)
    with pytest.raises(ValueError, match="BASELINE_CONTEXT_SOURCE_CHANGED_DURING_REPLAY"):
        _run(tmp_path, generation, manifest)


def test_without_generation_does_not_trust_available_feature_file(tmp_path):
    _fixture(tmp_path)
    row = replay.materialize_v3_opportunity_replay(tmp_path)["episode_receipts"][0]
    assert row["pre_entry_feature_blockers"] == ["PRE_ENTRY_FEATURE_SOURCE_NOT_VERIFIED"]
    assert row["bucket_definition_signature"] is None


@pytest.mark.parametrize("signature", [None, "", True, "UNKNOWN"])
def test_missing_taxonomy_never_falls_back_to_current_registry(tmp_path, signature):
    receipt, _, _ = _fixture(tmp_path)
    receipt["bucket_definition_signature"] = signature
    _write_rows(tmp_path / SOURCE, [receipt])
    generation, manifest = _repin(tmp_path)
    row = _run(tmp_path, generation, manifest)
    assert row["pre_entry_feature_status"] == "COMPLETE"
    assert row["bucket_definition_signature"] is None
    assert row["bucket_definition_blockers"] == ["PRE_ENTRY_BUCKET_SIGNATURE_MISSING_OR_INVALID"]


@pytest.mark.parametrize("field", ["bucket_definition_signature", "bucket_definition_schema", "bucket_definition_version"])
def test_conflicting_opportunity_taxonomy_is_not_relabelled(tmp_path, field):
    _, _, _ = _fixture(tmp_path)
    path = tmp_path / "v3/ledgers/opportunity.jsonl"
    opportunity = json.loads(path.read_text().strip())
    opportunity[field] = "different"
    _write_rows(path, [opportunity])
    generation, manifest = _repin(tmp_path)
    row = _run(tmp_path, generation, manifest)
    assert row["bucket_definition_signature"] is None
    assert row["bucket_definition_blockers"] == ["PRE_ENTRY_BUCKET_DEFINITION_CONFLICT"]


def test_ambiguous_receipts_cannot_choose_a_taxonomy(tmp_path):
    receipt, _, _ = _fixture(tmp_path)
    _write_rows(tmp_path / SOURCE, [receipt, {**receipt, "bucket_definition_signature": "other"}])
    generation, manifest = _repin(tmp_path)
    row = _run(tmp_path, generation, manifest)
    assert row["bucket_definition_signature"] is None
    assert row["bucket_definition_blockers"] == ["PRE_ENTRY_BUCKET_RECEIPT_NOT_UNIQUE_VERIFIED_CAUSAL"]


def test_actual_materializer_taxonomy_reaches_same_publication_adapter(tmp_path, monkeypatch):
    import gzip
    from research import dynamic_cohort_adapter
    from research.discovery_scorecard_publication import build_discovery_scorecard_publication
    receipt, generation, manifest = _fixture(tmp_path)
    baseline = replay.materialize_v3_opportunity_replay(tmp_path, generation=generation, canonical_manifest=manifest)
    artifact = tmp_path / "evaluator.jsonl.gz"
    with gzip.open(artifact, "wb") as handle:
        handle.write(b"")
    evaluator = {"schema": "v3_conservative_policy_evidence_v1", "generation": generation,
                 "relative_path": artifact.name, "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                 "row_count": 0}
    actual_adapter = dynamic_cohort_adapter.adapt_dynamic_cohorts
    consumed = []
    def inspect_then_adapt(rows, **kwargs):
        values = list(rows)
        consumed.extend(values)
        return actual_adapter(values, **kwargs)
    monkeypatch.setattr(dynamic_cohort_adapter, "adapt_dynamic_cohorts", inspect_then_adapt)
    report = build_discovery_scorecard_publication(tmp_path, expected_generation=generation,
        evaluator_status=evaluator, baseline_report=baseline)
    assert consumed
    assert all(row["bucket_definition_signature"] == receipt["bucket_definition_signature"] for row in consumed)
    assert all(row["signal_ts"] == 100 for row in consumed)
    assert all(row["pre_entry_features"]["regime"]["observed_ts"] == 98 for row in consumed)
    # Entry-only evidence has no terminal economics/horizon: carrying taxonomy is not a made-up outcome.
    assert report["dynamic_cohorts"]["status"] == "UNAVAILABLE"
    assert report["dynamic_cohorts"]["counts"]["supported_outcomes"] == 0
