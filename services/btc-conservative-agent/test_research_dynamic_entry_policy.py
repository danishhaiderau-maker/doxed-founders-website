from copy import deepcopy

import pytest

from research_dynamic_entry_policy import (
    evaluate_frozen_dynamic_policy,
    nested_purged_walk_forward_dynamic,
    train_frozen_dynamic_policy,
    verify_frozen_dynamic_policy,
    write_immutable_policy_receipt,
)
from research_entry_baselines import ENTRY_BASELINE_REGISTRY


FEATURES = ("atr_bucket", "regime")


def candidates():
    rows = {row["baseline_id"]: row for row in ENTRY_BASELINE_REGISTRY["baselines"]}
    return [rows["MARKET_ENTRY_AT_SIGNAL"], rows["NO_CHASE_LIMIT"]]


def episode(index, *, late_feature=False, missing_outcome=False):
    signal_ts = index * 20_000.0
    high = index % 2 == 0
    policies = {
        "MARKET_ENTRY_AT_SIGNAL": {"outcome_state": "FULL_FILL", "net_pnl_usd": -1 if high else 3},
        "NO_CHASE_LIMIT": {"outcome_state": "FULL_FILL", "net_pnl_usd": 4 if high else -2},
    }
    if missing_outcome:
        policies.pop("NO_CHASE_LIMIT")
    return {
        "episode_id": f"e-{index}", "signal_ts": signal_ts,
        "required_end_ts": signal_ts + 1000,
        "pre_entry_features": {
            "atr_bucket": {"value": "HIGH" if high else "LOW", "observed_ts": signal_ts + (1 if late_feature else -1)},
            "regime": {"value": "BULL" if high else "RANGE", "observed_ts": signal_ts - 1},
        },
        "policy_outcomes": policies,
    }


def train_rows():
    return [episode(index) for index in range(60)]


def test_training_is_deterministic_nested_purged_and_research_only():
    kwargs = dict(candidates=candidates(), feature_names=FEATURES, inner_folds=4,
                  purge_sec=1000, embargo_sec=100, minimum_bucket_support=3,
                  training_run_id="run-1")
    first = train_frozen_dynamic_policy(train_rows(), **kwargs)
    second = train_frozen_dynamic_policy(train_rows(), **kwargs)
    assert first == second
    assert verify_frozen_dynamic_policy(first)
    assert first["relay_eligible"] is False
    assert first["selection_semantics"].startswith("NESTED_INNER_PURGED")
    rules = {tuple(row["feature_values"]): row["selected_policy_id"] for row in first["rules"]}
    assert rules[("HIGH", "BULL")] == "NO_CHASE_LIMIT"
    assert rules[("LOW", "RANGE")] == "MARKET_ENTRY_AT_SIGNAL"


def test_post_entry_feature_and_missing_candidate_outcome_are_never_trained_as_zero():
    rows = train_rows()
    rows[45] = episode(45, late_feature=True)
    rows[46] = episode(46, missing_outcome=True)
    receipt = train_frozen_dynamic_policy(
        rows, candidates=candidates(), feature_names=FEATURES, inner_folds=4,
        purge_sec=1000, embargo_sec=100, minimum_bucket_support=3,
        training_run_id="run-defects",
    )
    assert any("POST_ENTRY_FEATURE_LEAKAGE" in reason for reason in receipt["training_unknown_reasons"])
    assert any("UNKNOWN_CANDIDATE_OUTCOME" in reason for reason in receipt["training_unknown_reasons"])


def test_evaluation_is_frozen_compares_signed_baselines_and_missing_is_unknown():
    receipt = train_frozen_dynamic_policy(
        train_rows(), candidates=candidates(), feature_names=FEATURES, inner_folds=4,
        purge_sec=1000, embargo_sec=100, minimum_bucket_support=3,
        training_run_id="run-eval",
    )
    rows = [episode(100), episode(101), episode(102, late_feature=True)]
    result = evaluate_frozen_dynamic_policy(receipt, rows, evaluation_mode="SEALED_HOLDOUT")
    assert result["policy_frozen_before_evaluation"] is True
    assert result["selection_after_evaluation"] is False
    assert result["episodes_scored"] == 2
    assert result["dynamic"]["net_pnl_usd"] == 7
    assert result["signed_static_baselines"]["MARKET_ENTRY_AT_SIGNAL"]["episodes"] == 2
    assert result["unknown_episodes"][0]["reasons"][0].startswith("POST_ENTRY_FEATURE_LEAKAGE")
    assert result["sealed_holdout_evaluation_verified"] is False
    assert "VALID_SEALED_HOLDOUT_EVALUATION_REQUIRED" in result["qualification_blockers"]
    assert result["qualification_eligible"] is False


def test_static_comparison_uses_identical_supported_episode_cohort():
    receipt = train_frozen_dynamic_policy(
        train_rows(), candidates=candidates(), feature_names=FEATURES, inner_folds=4,
        purge_sec=1000, embargo_sec=100, minimum_bucket_support=3,
        training_run_id="run-identical",
    )
    result = evaluate_frozen_dynamic_policy(
        receipt, [episode(100, missing_outcome=True)],
        evaluation_mode="OUTER_PURGED_VALIDATION",
    )
    assert result["episodes_scored"] == 0
    assert result["dynamic"]["expectancy_usd_per_opportunity"] is None
    assert result["signed_static_baselines"]["MARKET_ENTRY_AT_SIGNAL"]["episodes"] == 0
    assert result["unknown_episodes"][0]["reasons"] == [
        "STATIC_BASELINE_OUTCOME_UNKNOWN:NO_CHASE_LIMIT"
    ]


def test_evaluation_rejects_training_overlap_and_receipt_tampering():
    receipt = train_frozen_dynamic_policy(
        train_rows(), candidates=candidates(), feature_names=FEATURES, inner_folds=4,
        purge_sec=1000, embargo_sec=100, minimum_bucket_support=3,
        training_run_id="run-overlap",
    )
    result = evaluate_frozen_dynamic_policy(receipt, [episode(20)], evaluation_mode="OUTER_PURGED_VALIDATION")
    assert result["episodes_scored"] == 0
    assert result["unknown_episodes"][0]["reasons"] == ["TRAIN_EVALUATION_OVERLAP_OR_EMBARGO_BREACH"]
    tampered = deepcopy(receipt)
    tampered["fallback_policy_id"] = "NO_CHASE_LIMIT"
    assert not verify_frozen_dynamic_policy(tampered)
    with pytest.raises(ValueError, match="INVALID_FROZEN"):
        evaluate_frozen_dynamic_policy(tampered, [episode(100)], evaluation_mode="SEALED_HOLDOUT")


def test_immutable_receipt_is_contained_and_conflicts_fail_closed(tmp_path):
    receipt = train_frozen_dynamic_policy(
        train_rows(), candidates=candidates(), feature_names=FEATURES, inner_folds=4,
        purge_sec=1000, embargo_sec=100, minimum_bucket_support=3,
        training_run_id="run-write",
    )
    target = write_immutable_policy_receipt(tmp_path, "policies/frozen.json", receipt)
    assert target.exists()
    assert write_immutable_policy_receipt(tmp_path, "policies/frozen.json", receipt) == target
    changed = deepcopy(receipt)
    changed["training_run_id"] = "different"
    with pytest.raises(ValueError, match="INVALID_FROZEN"):
        write_immutable_policy_receipt(tmp_path, "policies/other.json", changed)
    with pytest.raises(ValueError, match="OUTSIDE_ROOT"):
        write_immutable_policy_receipt(tmp_path, "../escape.json", receipt)


def test_unsigned_or_mismatched_static_baseline_is_rejected():
    bad = deepcopy(candidates())
    bad[0]["policy_signature"] = "fabricated"
    with pytest.raises(ValueError, match="SIGNATURE_MISMATCH"):
        train_frozen_dynamic_policy(
            train_rows(), candidates=bad, feature_names=FEATURES, inner_folds=4,
            purge_sec=1000, embargo_sec=100, minimum_bucket_support=3,
            training_run_id="bad",
        )


def test_nested_protocol_never_uses_outer_validation_to_train_its_receipt():
    rows = [episode(index) for index in range(180)]
    result = nested_purged_walk_forward_dynamic(
        rows, candidates=candidates(), feature_names=FEATURES,
        outer_folds=3, inner_folds=3, purge_sec=1000, embargo_sec=100,
        minimum_bucket_support=2, protocol_run_id="nested-1",
    )
    assert result["passed"] is True
    for fold in result["folds"]:
        assert set(fold["train_episode_ids"]).isdisjoint(fold["validation_episode_ids"])
        trained = {
            row["episode_id"]
            for row in fold["frozen_policy_receipt"]["training_episode_identities"]
        }
        assert trained.isdisjoint(fold["validation_episode_ids"])
        assert fold["evaluation"]["policy_frozen_before_evaluation"] is True
