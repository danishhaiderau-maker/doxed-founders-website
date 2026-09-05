from copy import deepcopy

from research.discovery_scorecard_publication import build_discovery_scorecard_publication
from research.shadow_result_stream import digest
from test_discovery_scorecard_publication import GENERATION, inputs, shadow_inputs


def fixture(tmp_path):
    root, evaluator, _ = inputs(tmp_path)
    baseline, shadow = shadow_inputs(root)
    episode = baseline["episode_receipts"][0]
    episode.update(signal_ts=9.0, market="BITFINEX", symbol="BTCUSD",
        pre_entry_features={"regime": {"value": "BULL", "observed_ts": 8.0}},
        bucket_definition_signature="predeclared-regime-taxonomy-v1")
    terminal = shadow["results"][0]["terminal"]
    terminal.update(economics_evidence_basis="DECLARED_SIMULATION", declared_contract_sha256="d" * 64)
    terminal["receipt_sha256"] = digest({k: v for k, v in terminal.items() if k != "receipt_sha256"})
    return root, evaluator, baseline, shadow


def build(values, **options):
    root, evaluator, baseline, shadow = values
    return build_discovery_scorecard_publication(root, expected_generation=GENERATION,
        evaluator_status=evaluator, baseline_report=baseline, shadow_terminal_report=shadow,
        dynamic_feature_names=("regime",), dynamic_protocol={"purge_sec": 7200}, **options)


def test_complete_shadow_same_publication_reaches_cohort_with_no_fly_input_json(tmp_path):
    values = fixture(tmp_path)
    report = build(values)
    cohort = report["dynamic_cohorts"]
    assert cohort["counts"]["supported_outcomes"] == 1
    assert cohort["status"] == "BUILT_INCOMPLETE_RESEARCH_ONLY"
    assert cohort["comparison_complete"] is False and cohort["live_qualification"] is False
    assert "INPUT_ROWS_REJECTED_CANDIDATE_UNIVERSE_INCOMPLETE" in cohort["blockers"]
    episodes = [e for g in cohort["groups"] for e in g["episodes"] if e["policy_outcomes"]]
    assert episodes[0]["signal_ts"] == 9
    assert episodes[0]["required_end_ts"] == 13
    assert episodes[0]["pre_entry_features"]["regime"]["observed_ts"] == 8
    assert not list(values[0].rglob("dynamic_policy_analysis_input.json"))


def test_missing_taxonomy_is_explicit_unavailable_not_fabricated(tmp_path):
    values = fixture(tmp_path)
    del values[2]["episode_receipts"][0]["bucket_definition_signature"]
    cohort = build(values)["dynamic_cohorts"]
    assert cohort["status"] == "UNAVAILABLE"
    assert cohort["counts"]["supported_outcomes"] == 0
    assert cohort["rejections"]["CAUSAL_IDENTITY_INCOMPLETE"] > 0


def test_unknown_candidate_universe_and_aggregate_are_not_survivor_filtered(tmp_path):
    values = fixture(tmp_path)
    shadow = values[3]
    unknown = deepcopy(shadow["results"][0])
    unknown.update(status="UNKNOWN", policy_signature="unknown-policy", composite_policy_signature="unknown-policy")
    unknown["terminal"] = {"status": "UNKNOWN", "reason_codes": ["DEPTH_GAP"]}
    shadow["results"].append(unknown)
    shadow.update(candidate_replay_count=2, results_total=2, unknown_replay_count=1,
                  reason_counts={"DEPTH_GAP": 1}, status="BUILT_INCOMPLETE")
    cohort = build(values)["dynamic_cohorts"]
    assert {c["policy_id"] for c in cohort["candidate_universe"]} >= {"unknown-policy"}
    assert cohort["shadow_terminal_aggregate"]["unknown_replay_count"] == 1
    assert cohort["counts"]["unknown_outcome_rows"] >= 1
    assert cohort["counts"]["supported_outcomes"] == 1
    assert cohort["comparison_complete"] is False
    assert "INPUT_ROWS_REJECTED_CANDIDATE_UNIVERSE_INCOMPLETE" in cohort["blockers"]


def test_adapter_budget_failure_does_not_destroy_independent_static_scorecard(tmp_path):
    report = build(fixture(tmp_path), dynamic_limits={"max_rows": 1})
    assert report["scorecard"] is not None
    assert report["dynamic_cohorts"]["status"] == "UNAVAILABLE"
    assert report["dynamic_cohorts"]["blockers"] == ["DYNAMIC_ADAPTER_ROW_LIMIT"]


def test_postsignal_feature_does_not_use_latest_regime_as_replacement(tmp_path):
    values = fixture(tmp_path)
    values[2]["episode_receipts"][0]["pre_entry_features"]["regime"]["observed_ts"] = 10
    cohort = build(values)["dynamic_cohorts"]
    assert cohort["status"] == "UNAVAILABLE"
    assert cohort["rejections"]["POST_SIGNAL_FEATURE"] == 1


def test_untrusted_terminal_horizon_change_stays_unknown(tmp_path):
    values = fixture(tmp_path)
    values[3]["results"][0]["terminal"]["required_horizon_end_ts"] = 1
    report = build(values)
    assert report["dynamic_cohorts"]["counts"]["supported_outcomes"] == 0
    assert report["unjoinable_counts"]["shadow_terminal:TERMINAL_RECEIPT_SHA256_INVALID"] == 1
