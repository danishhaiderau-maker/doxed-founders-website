from dynamic_policy_analyzer import orchestrate_dynamic_policy_analysis
from research_dynamic_entry_policy import train_frozen_dynamic_policy
from research_v3_sealed_holdout import consume_seal, create_seal
from test_research_dynamic_entry_policy import FEATURES, candidates, episode


def _rows(count):
    rows = [episode(index) for index in range(count)]
    for row in rows:
        row["bucket_definition_signature"] = "signed-buckets-v1"
        row["pre_entry_features"]["regime"]["value"] = (
            "TRENDING" if row["pre_entry_features"]["atr_bucket"]["value"] == "HIGH" else "RANGE"
        )
    return rows


def test_orchestration_is_chronological_fold_local_and_holdout_fails_closed_without_seal():
    result = orchestrate_dynamic_policy_analysis(
        _rows(180), _rows(10), candidates=candidates(), feature_names=FEATURES,
        outer_folds=3, inner_folds=3, purge_sec=1000, embargo_sec=100,
        minimum_bucket_support=2, protocol_run_id="orchestrated-1",
        sealed_holdout_evaluation=None,
    )
    assert result["status"] == "UNKNOWN"
    assert result["nested_protocol"]["passed"] is True
    assert "VALID_SEALED_HOLDOUT_EVALUATION_REQUIRED" in result["blockers"]
    for row in result["fold_local_taxonomy_bindings"]:
        binding = row["binding"]
        assert binding["projection"] == "IDENTITY_RUNTIME_TAXONOMY"
        assert binding["bull_bear_range_projection"] is None
        assert binding["signature"]


def test_orchestration_reports_missing_taxonomy_evidence_unknown():
    rows = _rows(180)
    rows[0].pop("bucket_definition_signature")
    result = orchestrate_dynamic_policy_analysis(
        rows, _rows(10), candidates=candidates(), feature_names=FEATURES,
        outer_folds=3, inner_folds=3, purge_sec=1000, embargo_sec=100,
        minimum_bucket_support=2, protocol_run_id="orchestrated-missing",
        sealed_holdout_evaluation=None,
    )
    assert result["status"] == "UNKNOWN"
    assert any(reason.startswith("BUCKET_DEFINITION_SIGNATURE_MISSING") for reason in result["blockers"])
    assert result["sealed_holdout"] is None


def test_orchestration_accepts_only_exact_policy_and_holdout_bound_seal(tmp_path):
    training = _rows(180)
    holdout = [episode(index) for index in range(200, 205)]
    identity = {
        "dataset_epoch": "epoch-1", "source_revision": "source-1",
        "deployed_revision": "deploy-1", "tile_config_signature": "tiles-1",
        "cohort_signature": "cohort-1",
    }
    for row in holdout:
        row["bucket_definition_signature"] = "signed-buckets-v1"
        row.update(identity)
        row["evidence_collected_at"] = row["signal_ts"] + 2000
    frozen = train_frozen_dynamic_policy(
        training, candidates=candidates(), feature_names=FEATURES, inner_folds=3,
        purge_sec=1000, embargo_sec=100, minimum_bucket_support=2,
        training_run_id="orchestrated-sealed:final-pre-holdout",
    )
    policy_candidate = [{
        "policy_id": frozen["policy_id"], "policy_signature": frozen["content_sha256"],
    }]
    seal = create_seal(
        tmp_path, **identity, training_snapshot_hash=frozen["training_cohort_hash"],
        training_completed_at=3_590_000, sealed_at=3_595_000,
        holdout_start_ts=holdout[0]["signal_ts"], policy_candidates=policy_candidate,
    )
    evaluation = consume_seal(
        tmp_path, seal_id=seal["seal_id"], policy_candidates=policy_candidate,
        holdout_episodes=holdout, evaluation_started_at=holdout[-1]["evidence_collected_at"] + 1,
    )
    result = orchestrate_dynamic_policy_analysis(
        training, holdout, candidates=candidates(), feature_names=FEATURES,
        outer_folds=3, inner_folds=3, purge_sec=1000, embargo_sec=100,
        minimum_bucket_support=2, protocol_run_id="orchestrated-sealed",
        sealed_holdout_evaluation=evaluation,
    )
    assert result["status"] == "PASS"
    assert result["sealed_holdout"]["sealed_holdout_evaluation_verified"] is True
    assert result["sealed_holdout"]["qualification_eligible"] is True
