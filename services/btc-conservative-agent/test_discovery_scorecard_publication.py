import gzip
import hashlib
import json

from research.discovery_scorecard_publication import build_discovery_scorecard_publication


GENERATION = {
    "manifest_entry_hash": "a" * 64, "epoch_id": "epoch-1", "source_revision": "src",
    "deployed_revision": "dep", "tile_config_signature": "b" * 64,
    "analyzer_revision": "ana", "evaluator_version": "v1", "generation_key": "g1",
}


def evaluator_row(**extra):
    row = {
        "episode_id": "e1", "opportunity_id": "o1", "epoch_id": "epoch-1",
        "source_revision": "src", "deployed_revision": "dep", "tile_config_signature": "b" * 64,
        "side": "LONG",
        "adx_bucket": "STRONG", "entry_offset_pct": .1, "chase_policy": "w234",
        "exit_family": "FIXED", "policy_id": "p1", "policy_signature": "ps",
        "schedule_sha256": "s" * 64, "requested_qty": .1, "tape_hashes": ["t" * 64],
        "tape_ids": ["tape-1"], "config_signature": "cfg", "cost_model_id": "cost-v1",
        "simulation_model": "CONSERVATIVE_BBO_DEPTH_TAPE", "classification": "FULL_FILL",
        "terminal_outcome_status": "UNKNOWN", "profitability_supported": False,
    }
    row.update(extra)
    return row


def inputs(tmp_path, rows=None):
    root = tmp_path / "canonical-research-data"
    target = root / "derived" / "results.jsonl.gz"
    target.parent.mkdir(parents=True)
    rows = [evaluator_row()] if rows is None else rows
    with gzip.open(target, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    status = {"schema": "v3_conservative_policy_evidence_v1", "generation": dict(GENERATION),
              "relative_path": "derived/results.jsonl.gz",
              "artifact_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "row_count": len(rows)}
    baseline = {"schema": "entry_baseline_same_opportunity_replay_v1", "generation": dict(GENERATION),
                "same_opportunity_count": 0, "episode_receipts": []}
    return root, status, baseline


def test_valid_same_generation_verifies_gzip_hash_and_rows(tmp_path):
    root, status, baseline = inputs(tmp_path)
    report = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status, baseline_report=baseline,
    )
    assert report["status"] == "BUILT_INCOMPLETE"
    assert report["input_artifacts"]["evaluator"]["verified_row_count"] == 1
    assert report["profitability_supported"] is False
    assert report["winner"] is None


def test_generation_mismatch_is_unknown(tmp_path):
    root, status, baseline = inputs(tmp_path)
    baseline["generation"]["epoch_id"] = "stale"
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["status"] == "UNKNOWN"
    assert report["scorecard"] is None
    assert "INPUT_GENERATION_MISMATCH" in report["blockers"]


def test_hash_mismatch_is_unknown(tmp_path):
    root, status, baseline = inputs(tmp_path)
    status["artifact_sha256"] = "0" * 64
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["status"] == "UNKNOWN"
    assert "EVALUATOR_ARTIFACT_SHA256_MISMATCH" in report["blockers"]


def test_path_escape_is_unknown(tmp_path):
    root, status, baseline = inputs(tmp_path)
    outside = tmp_path / "outside.jsonl.gz"
    outside.write_bytes(b"not relevant")
    status["relative_path"] = "../outside.jsonl.gz"
    status["artifact_sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert "EVALUATOR_ARTIFACT_OUTSIDE_CANONICAL_ROOT" in report["blockers"]


def test_unknowns_and_missing_fields_are_retained(tmp_path):
    root, status, baseline = inputs(tmp_path, [evaluator_row(
        classification="UNKNOWN", schedule_sha256=None, terminal_outcome_status="UNKNOWN",
    )])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["status"] == "BUILT_INCOMPLETE"
    assert report["unjoinable_counts"]["evaluator_unknown_classification"] == 1
    assert report["missing_field_diagnostics"]["evaluator_conservative:schedule_sha256"] == 1
    assert report["profitability_supported"] is False


def test_raw_adx_is_not_relabelled_as_adx_bucket(tmp_path):
    row = evaluator_row(adx_bucket=None, regime_features_at_signal={"adx": {"status": "OBSERVED", "value": 27.5}})
    root, status, baseline = inputs(tmp_path, [row])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["missing_field_diagnostics"]["evaluator_conservative:adx_bucket"] == 1


def test_row_generation_mismatch_is_unjoinable_not_relabelled(tmp_path):
    root, status, baseline = inputs(tmp_path, [evaluator_row(source_revision="stale")])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["unjoinable_counts"]["evaluator_row:source_revision_mismatch"] == 1
    assert report["input_counts"]["adapted_rows"] == 0
    assert report["status"] == "BUILT_INCOMPLETE"


def test_observed_paper_does_not_inherit_conservative_model_identity(tmp_path):
    root, status, baseline = inputs(tmp_path, [evaluator_row(
        terminal_outcome_status="REALIZED_COST_COMPLETE", profitability_supported=True,
        net_pnl_usd=2.0,
    )])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["missing_field_diagnostics"]["observed_paper:cost_model_id"] == 1
    assert report["missing_field_diagnostics"]["observed_paper:simulation_model"] == 1
    assert report["profitability_supported"] is False


def test_empty_inputs_remain_incomplete(tmp_path):
    root, status, baseline = inputs(tmp_path, [])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["status"] == "BUILT_INCOMPLETE"
    assert report["input_counts"]["adapted_rows"] == 0
    assert report["profitability_supported"] is False


def test_invalid_baseline_receipt_reports_declared_and_valid_counts(tmp_path):
    root, status, baseline = inputs(tmp_path)
    baseline["episode_receipts"] = ["not-an-object"]
    baseline["same_opportunity_count"] = 1
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["status"] == "UNKNOWN"
    assert report["input_counts"]["declared_baseline_episode_receipts"] == 1
    assert report["input_counts"]["valid_baseline_episode_receipts"] == 0
    assert report["unjoinable_counts"]["baseline_invalid_receipt"] == 1


def test_baseline_order_does_not_change_diagnostics(tmp_path):
    root, status, baseline = inputs(tmp_path)
    receipts = [
        {"episode_id": "e2", "opportunity_id": "o2", "dataset_epoch": "epoch-1",
         "source_revision": "src", "tile_config_signature": "b" * 64, "results": []},
        {"episode_id": "e1", "opportunity_id": "o1", "dataset_epoch": "epoch-1",
         "source_revision": "src", "tile_config_signature": "b" * 64, "results": []},
    ]
    baseline["episode_receipts"] = receipts
    baseline["same_opportunity_count"] = len(receipts)
    first = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                  evaluator_status=status, baseline_report=baseline)
    baseline["episode_receipts"] = list(reversed(receipts))
    second = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert first == second
