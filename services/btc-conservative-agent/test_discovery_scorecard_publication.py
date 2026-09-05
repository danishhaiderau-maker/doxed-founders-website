import gzip
import hashlib
import json

from research.discovery_scorecard_publication import build_discovery_scorecard_publication
from research.conservative_shadow_report import build_conservative_shadow_report


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


def shadow_inputs(root):
    from test_conservative_shadow_report import _fixture
    baseline, candidates, artifact, model = _fixture(root)
    baseline["generation"] = dict(GENERATION)
    artifact_identity = {"epoch_id": GENERATION["epoch_id"],
        "source_revision": GENERATION["source_revision"],
        "analyzer_generation_revision": GENERATION["analyzer_revision"],
        "tile_config_signature": GENERATION["tile_config_signature"]}
    artifact["evaluation_generation"] = dict(GENERATION)
    artifact["artifact_identity"] = artifact_identity
    artifact["artifact_verified_identity_fields"] = sorted(artifact_identity)
    model["generation"] = dict(GENERATION)
    baseline["same_opportunity_count"] = 1
    episode = baseline["episode_receipts"][0]
    episode.update({"dataset_epoch": "epoch-1", "source_revision": "src",
                    "deployed_revision": "dep", "tile_config_signature": "b" * 64,
                    "config_signature": "cfg", "direction": "LONG",
                    "adx_bucket": "STRONG"})
    result = episode["results"][0]
    result["baseline_spec"]["initial_offset_pct"] = .1
    unsigned = {key: value for key, value in result["baseline_spec"].items()
                if key != "policy_signature"}
    from research_v3_contract import canonical_hash
    result["policy_signature"] = canonical_hash("entry-baseline", unsigned)
    result["baseline_spec"]["policy_signature"] = result["policy_signature"]
    receipt = result["conservative_receipt"]
    receipt.update({"schedule_sha256": "s" * 64, "requested_qty": .4,
                    "tape_hashes": ["t" * 64], "tape_ids": ["tape-1"]})
    # Entry identity changed above; rebuild the composite-bound model context.
    from research.conservative_shadow_report import build_composite_policy_identity
    identity = build_composite_policy_identity(result, candidates[0])[1]
    model["contexts"][0]["composite_policy_signature"] = identity["composite_policy_signature"]
    from research.policy_evidence_schema import stable_hash
    body = {key: value for key, value in model.items() if key != "signature"}
    model["signature"] = stable_hash("conservative-shadow-research-model", body)
    shadow = build_conservative_shadow_report(
        root, expected_generation=GENERATION, baseline_report=baseline,
        policy_candidates=candidates, policy_artifact_receipt=artifact, research_model=model)
    assert shadow["complete_replay_count"] == 1
    return baseline, shadow


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


def test_evaluator_version_and_separate_segment_ids_are_consumed(tmp_path):
    root, status, baseline = inputs(tmp_path, [evaluator_row(
        simulation_model=None, evaluator_receipt={"evaluator_version": "conservative-v1"},
        tape_ids=["legacy-content-hash"], market_segment_ids=["record-1"],
    )])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert "evaluator_conservative:simulation_model" not in report["missing_field_diagnostics"]
    assert "evaluator_conservative:tape_ids" not in report["missing_field_diagnostics"]


def test_missing_explicit_segment_ids_do_not_fall_back_to_legacy_content_hash(tmp_path):
    root, status, baseline = inputs(tmp_path, [evaluator_row(market_segment_ids=[])])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["missing_field_diagnostics"]["evaluator_conservative:tape_ids"] == 1


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


def test_identity_complete_pair_is_not_profit_complete_without_replay_pnl(tmp_path):
    root, status, baseline = inputs(tmp_path, [evaluator_row(
        terminal_outcome_status="REALIZED_COST_COMPLETE", profitability_supported=True,
        net_pnl_usd=2.0, observed_cost_model_id="cost-v1",
        observed_execution_model="CONSERVATIVE_BBO_DEPTH_TAPE",
    )])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["input_identity_complete"] is True
    assert report["profitability_supported"] is False
    observed = report["profitability_evidence_by_world"]["OBSERVED_PAPER"]
    assert observed["available"] is True
    assert observed["descriptive_leader"]["policy_id"] == "p1"
    assert report["profitability_evidence_by_world"]["CONSERVATIVE_BBO"]["available"] is False
    assert report["winner"] is None


def test_nonfinite_observed_pnl_cannot_populate_a_profitability_leader(tmp_path):
    root, status, baseline = inputs(tmp_path, [evaluator_row(
        terminal_outcome_status="REALIZED_COST_COMPLETE", profitability_supported=True,
        net_pnl_usd=float("nan"), observed_cost_model_id="cost-v1",
        observed_execution_model="CONSERVATIVE_BBO_DEPTH_TAPE",
    )])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
                                                   evaluator_status=status, baseline_report=baseline)
    assert report["profitability_supported"] is False
    assert report["profitability_evidence_by_world"]["OBSERVED_PAPER"]["available"] is False


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
    # Reordering source rows changes its exact input hash, not the findings.
    assert first["input_artifacts"]["baseline"]["canonical_sha256"] != second["input_artifacts"]["baseline"]["canonical_sha256"]
    assert {k: v for k, v in first.items() if k != "input_artifacts"} == {
        k: v for k, v in second.items() if k != "input_artifacts"}


def test_complete_composite_shadow_populates_conservative_leader(tmp_path):
    root, status, _ = inputs(tmp_path)
    baseline, shadow = shadow_inputs(root)
    report = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status,
        baseline_report=baseline, shadow_terminal_report=shadow)
    world = report["profitability_evidence_by_world"]["CONSERVATIVE_BBO"]
    assert world["available"] is True
    assert world["descriptive_leader"]["policy_signature"] == shadow["results"][0]["policy_signature"]
    assert report["input_counts"]["shadow_terminal_rows_added"] == 1
    assert report["shadow_terminal_aggregate"]["complete_replay_count"] == 1
    assert report["shadow_terminal_aggregate"]["unknown_replay_count"] == 0
    from research.policy_evidence_schema import canonical_json
    assert report["input_artifacts"]["baseline"]["canonical_sha256"] == hashlib.sha256(canonical_json(baseline).encode()).hexdigest()
    assert report["input_artifacts"]["shadow_terminal"]["canonical_sha256"] == hashlib.sha256(canonical_json(shadow).encode()).hexdigest()
    provenance = report["shadow_terminal_provenance"][0]
    terminal = shadow["results"][0]["terminal"]
    assert provenance["terminal_receipt_sha256"] == terminal["receipt_sha256"]
    assert provenance["cost_model_signature"] == terminal["cost_model_signature"]
    assert provenance["costs"]["total_cost_usd"] == terminal["total_cost_usd"]
    assert report["winner"] is None
    assert report["live_qualification"] is False


def test_tampered_shadow_terminal_receipt_is_rejected(tmp_path):
    root, status, _ = inputs(tmp_path)
    baseline, shadow = shadow_inputs(root)
    shadow["results"][0]["terminal"]["net_pnl_usd"] += 1
    report = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status,
        baseline_report=baseline, shadow_terminal_report=shadow)
    assert report["input_counts"]["shadow_terminal_rows_added"] == 0
    assert report["unjoinable_counts"]["shadow_terminal:TERMINAL_RECEIPT_SHA256_INVALID"] == 1


def test_shadow_generation_mismatch_fails_whole_publication(tmp_path):
    root, status, _ = inputs(tmp_path)
    baseline, shadow = shadow_inputs(root)
    shadow["generation"] = {**GENERATION, "epoch_id": "foreign"}
    report = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status,
        baseline_report=baseline, shadow_terminal_report=shadow)
    assert report["status"] == "UNKNOWN"
    assert "INPUT_GENERATION_MISMATCH" in report["blockers"]


def test_exact_shadow_duplicates_deduplicate_but_conflicts_block(tmp_path):
    root, status, _ = inputs(tmp_path)
    baseline, shadow = shadow_inputs(root)
    shadow["results"].append(json.loads(json.dumps(shadow["results"][0])))
    shadow["candidate_replay_count"] = 2
    shadow["complete_replay_count"] = 2
    shadow["results_total"] = 2
    report = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status,
        baseline_report=baseline, shadow_terminal_report=shadow)
    assert report["input_counts"]["shadow_terminal_rows_added"] == 1
    assert report["input_counts"]["shadow_terminal_exact_duplicates_deduplicated"] == 1
    shadow["results"][1]["source_candidate_policy_id"] = "same-exit-different-source-candidate"
    shadow["results"][1]["source_candidate_policy_signature"] = "different-source-signature"
    correlated = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status,
        baseline_report=baseline, shadow_terminal_report=shadow)
    assert correlated["input_counts"]["shadow_terminal_rows_added"] == 1
    assert correlated["input_counts"]["shadow_terminal_exact_duplicates_deduplicated"] == 1
    shadow["results"][1]["terminal"]["net_pnl_usd"] += 1
    conflict = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status,
        baseline_report=baseline, shadow_terminal_report=shadow)
    assert conflict["input_counts"]["shadow_terminal_rows_added"] == 0
    assert conflict["input_counts"]["shadow_terminal_conflicting_duplicate_groups"] == 1


def test_shadow_world_does_not_convert_observed_paper_rows(tmp_path):
    root, status, _ = inputs(tmp_path, [evaluator_row(
        terminal_outcome_status="REALIZED_COST_COMPLETE", profitability_supported=True,
        net_pnl_usd=2, observed_cost_model_id="paper-cost",
        observed_execution_model="PAPER_OBSERVED")])
    baseline, shadow = shadow_inputs(root)
    report = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status,
        baseline_report=baseline, shadow_terminal_report=shadow)
    assert report["profitability_evidence_by_world"]["OBSERVED_PAPER"]["available"] is True
    assert report["profitability_evidence_by_world"]["CONSERVATIVE_BBO"]["available"] is True
    assert report["scorecard"]["pnl_sum_across_worlds"] is False


def test_large_truncated_unknown_aggregate_is_preserved_without_double_count(tmp_path):
    root, status, baseline = inputs(tmp_path)
    shadow = {"schema": "generation_bound_conservative_shadow_report_v1",
              "generation": dict(GENERATION), "status": "BUILT_INCOMPLETE",
              "blockers": ["RESEARCH_MODEL_MISSING"], "candidate_replay_count": 1000,
              "complete_replay_count": 0, "unknown_replay_count": 1000,
              "results_total": 1000, "results_truncated": True,
              "reason_counts": {"RESEARCH_MODEL_MISSING": 1000},
              "results": [{"episode_id": "e1", "opportunity_id": "o1", "baseline_id": "b1",
                           "policy_signature": "composite-1", "status": "UNKNOWN",
                           "blockers": ["RESEARCH_MODEL_MISSING"]}]}
    report = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status,
        baseline_report=baseline, shadow_terminal_report=shadow)
    aggregate = report["shadow_terminal_aggregate"]
    assert aggregate["unknown_replay_count"] == 1000
    assert aggregate["reason_counts"] == {"RESEARCH_MODEL_MISSING": 1000}
    assert not any("RESEARCH_MODEL_MISSING" in key for key in report["unjoinable_counts"])
    assert report["input_identity_complete"] is False


def test_unknown_shadow_failure_with_empty_results_preserves_blocker(tmp_path):
    root, status, baseline = inputs(tmp_path)
    shadow = {"schema": "generation_bound_conservative_shadow_report_v1",
              "generation": dict(GENERATION), "status": "UNKNOWN",
              "blockers": ["POLICY_CYCLE_NOT_SUCCESSFUL"], "results": []}
    report = build_discovery_scorecard_publication(
        root, expected_generation=GENERATION, evaluator_status=status,
        baseline_report=baseline, shadow_terminal_report=shadow)
    assert report["shadow_terminal_aggregate"]["counts_available"] is False
    assert report["shadow_terminal_aggregate"]["upstream_blockers"] == ["POLICY_CYCLE_NOT_SUCCESSFUL"]
    assert report["input_identity_complete"] is False


def test_invalid_shadow_aggregate_count_fails_closed(tmp_path):
    root, status, baseline = inputs(tmp_path)
    for invalid in (-1, 1.5, True):
        shadow = {"schema": "generation_bound_conservative_shadow_report_v1",
                  "generation": dict(GENERATION), "status": "BUILT_INCOMPLETE", "blockers": [],
                  "candidate_replay_count": invalid, "complete_replay_count": 0,
                  "unknown_replay_count": 0, "results_total": 0,
                  "results_truncated": False, "reason_counts": {}, "results": []}
        report = build_discovery_scorecard_publication(
            root, expected_generation=GENERATION, evaluator_status=status,
            baseline_report=baseline, shadow_terminal_report=shadow)
        assert report["status"] == "UNKNOWN"
        assert "SHADOW_TERMINAL_AGGREGATE_INVALID:candidate_replay_count" in report["blockers"]


def test_invalid_shadow_reason_bounds_or_blocker_type_fail_closed(tmp_path):
    root, status, _ = inputs(tmp_path)
    baseline, shadow = shadow_inputs(root)
    for changes, expected in (
        ({"reason_counts": {"MISSING": 1}}, "SHADOW_TERMINAL_REASON_COUNT_EXCEEDS_UNKNOWN_REPLAYS"),
        ({"blockers": "ERROR"}, "SHADOW_TERMINAL_AGGREGATE_INVALID:blockers"),
    ):
        report = build_discovery_scorecard_publication(
            root, expected_generation=GENERATION, evaluator_status=status,
            baseline_report=baseline, shadow_terminal_report={**shadow, **changes})
        assert report["status"] == "UNKNOWN"
        assert expected in report["blockers"]
