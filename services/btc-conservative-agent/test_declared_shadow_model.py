import copy
import hashlib
import json

import pytest

from research.declared_shadow_model import (
    SCHEMA, MODE, validate_contract, calculate_declared_costs, load_declared_shadow_model,
)
from research.policy_evidence_schema import stable_hash, canonical_json
from research.conservative_shadow_report import build_conservative_shadow_report
from research.conservative_shadow_terminal import evaluate_shadow_terminal
from test_conservative_shadow_report import _fixture, GEN
from test_conservative_shadow_terminal import _inputs, _rebind


def contract(gen=GEN):
    body = {"schema": SCHEMA, "evidence_basis": "DECLARED_SIMULATION", "generation": gen,
            "model_id": "explicit-test-scenario", "provenance": "TEST_CONFIG_NOT_EXCHANGE_OBSERVATION",
            "source_config_sha256": "a"*64, "fee_rates": {"entry": .001, "exit": .002},
            "funding": {"treatment": "ZERO_SCENARIO"},
            "latency": {"treatment": "PRESERVE_BASELINE_TIMING", "additional_latency_sec": 0}}
    return sign(body)


def sign(body):
    body = {k: v for k, v in body.items() if k != "signature"}
    return {**body, "signature": stable_hash("declared-shadow-model", body)}


def baseline_context(baseline, model):
    entry = baseline["episode_receipts"][0]["results"][0]
    receipt = entry["conservative_receipt"]
    body = {**model["contexts"][0], "schema": "baseline_execution_model_context_v1",
            "generation": GEN,
            "entry_receipt_sha256": hashlib.sha256(canonical_json(receipt).encode()).hexdigest(),
            "source_evidence_sha256": ["b"*64], "latency_provenance": "SIGNED_BASELINE_SCHEDULE",
            "timing_basis": "BASELINE_EXECUTION_TIMESTAMPS_UNCHANGED"}
    entry["execution_model_context"] = {**body, "signature": stable_hash("baseline-execution-model-context", body)}
    return entry


def test_declared_report_uses_actual_exit_not_fixed_cost_totals(tmp_path):
    baseline, candidates, artifact, old_model = _fixture(tmp_path)
    baseline_context(baseline, old_model)
    before = copy.deepcopy(baseline)
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=contract())
    assert baseline == before
    assert report["complete_replay_count"] == 1
    terminal = report["results"][0]["terminal"]
    assert terminal["trading_fees_usd"] == pytest.approx(40*.001 + 41.2*.002)
    assert terminal["funding_usd"] == 0
    assert terminal["measured_funding_usd"] is None
    assert terminal["measured_ack_latency_sec"] is None
    assert terminal["economics_evidence_basis"] == "DECLARED_SIMULATION"
    assert terminal["ranking_eligible"] is False and report["live_qualification"] is False


def test_missing_baseline_position_is_unknown_not_filled_from_configuration(tmp_path):
    baseline, candidates, artifact, _ = _fixture(tmp_path)
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=contract())
    assert report["complete_replay_count"] == 0 and report["unknown_replay_count"] == 1
    assert report["reason_counts"]["BASELINE_EXECUTION_MODEL_CONTEXT_MISSING"] == 1


@pytest.mark.parametrize("defect", ["hash", "generation", "provenance", "funding-missing", "latency", "fee-missing"])
def test_invalid_contract_never_creates_economics(defect):
    model = contract()
    if defect == "hash": model["fee_rates"]["entry"] = .9
    elif defect == "generation": model = sign({**model, "generation": {**GEN, "epoch_id": "other"}})
    elif defect == "provenance": model = sign({**model, "provenance": ""})
    elif defect == "funding-missing": model = sign({**model, "funding": {}})
    elif defect == "latency": model = sign({**model, "latency": {"treatment": "PRESERVE_BASELINE_TIMING", "additional_latency_sec": 1}})
    elif defect == "fee-missing": model = sign({**model, "fee_rates": {"exit": 0}})
    with pytest.raises(ValueError): validate_contract(model, GEN)


def test_partial_exit_fees_and_funding_depend_on_holding_schedule():
    model = contract()
    model = sign({**model, "funding": {"treatment": "CONSTANT_ENTRY_NOTIONAL_RATE", "rate_per_hour": .01}})
    short = calculate_declared_costs(model, generation=GEN, entry_events=[(0,100,1)],
                                   exit_events=[(1800,110,.5),(3600,120,.5)], direction="LONG")
    longer = calculate_declared_costs(model, generation=GEN, entry_events=[(0,100,1)],
                                    exit_events=[(3600,110,.5),(7200,120,.5)], direction="LONG")
    assert short["trading_fees_usd"] == pytest.approx(.1 + .11 + .12)
    assert short["funding_usd"] == pytest.approx(.75)
    assert longer["funding_usd"] == pytest.approx(1.5)
    assert longer["trading_fees_usd"] == short["trading_fees_usd"]
    short_direction = calculate_declared_costs(model, generation=GEN, entry_events=[(0,100,1)],
                                   exit_events=[(1800,110,.5),(3600,120,.5)], direction="SHORT")
    assert short_direction["funding_usd"] == -short["funding_usd"]


def test_partial_entry_quantity_time_uses_each_accepted_fill():
    model = sign({**contract(), "funding": {"treatment": "CONSTANT_ENTRY_NOTIONAL_RATE", "rate_per_hour": .01}})
    values = calculate_declared_costs(model, generation=GEN,
        entry_events=[(0,100,.5),(1800,120,.5)], exit_events=[(3600,130,1)], direction="LONG")
    assert values["funding_usd"] == pytest.approx(.8)
    assert values["trading_fees_usd"] == pytest.approx(.11+.26)


def test_same_paper_shadow_evidence_same_scenario_same_costs():
    kwargs = dict(generation=GEN, entry_events=[(0,100,1)], exit_events=[(10,101,1)], direction="LONG")
    assert calculate_declared_costs(contract(), **kwargs) == calculate_declared_costs(contract(), **copy.deepcopy(kwargs))


def test_terminal_rate_path_accounts_replay_partials():
    values = _inputs()
    values["policy_spec"]["profit_protection"]["partial_take_profits"] = [[1,.5]]
    from research_v3_contract import canonical_hash
    values["policy_signature"] = canonical_hash("v3-policy", values["policy_spec"])
    values["cost_model"].update({"calculation_mode": MODE,
                                  "declared_contract": contract(values["generation"]),
                                  "cost_provenance": "DECLARED_SIMULATION"})
    _rebind(values)
    terminal = evaluate_shadow_terminal(**values)
    assert terminal["status"] == "COMPLETE"
    assert terminal["partial_exit_count"] == 1
    assert terminal["trading_fees_usd"] == pytest.approx(40*.001 + (.2*101+.2*103)*.002)


def test_loader_requires_exact_file_hash_and_generation(tmp_path):
    path = tmp_path / "scenario.json"
    raw = json.dumps(contract()).encode(); path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    assert load_declared_shadow_model(path, expected_sha256=digest, expected_generation=GEN)["model_id"]
    with pytest.raises(ValueError, match="FILE_HASH_MISMATCH"):
        load_declared_shadow_model(path, expected_sha256="0"*64, expected_generation=GEN)


@pytest.mark.parametrize("field,value", [("atr_basis", "SIGNAL_ATR"), ("latency_provenance", ""),
                                         ("leverage", None), ("source_evidence_sha256", [])])
def test_no_invented_atr_latency_sizing_or_source_context(tmp_path, field, value):
    baseline, candidates, artifact, old_model = _fixture(tmp_path)
    entry = baseline_context(baseline, old_model)
    body = {k:v for k,v in entry["execution_model_context"].items() if k != "signature"}
    body[field] = value
    entry["execution_model_context"] = {**body, "signature": stable_hash("baseline-execution-model-context", body)}
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=contract())
    assert report["complete_replay_count"] == 0 and report["unknown_replay_count"] == 1


def test_large_missing_context_cohort_is_exact_without_cartesian_materialization(tmp_path, monkeypatch):
    from research_v3_contract import canonical_hash
    import research.conservative_shadow_report as reporter
    baseline, candidates, artifact, _ = _fixture(tmp_path)
    template = baseline["episode_receipts"][0]
    baseline["episode_receipts"] = [
        {**template, "episode_id": f"e-{i}", "opportunity_id": f"o-{i}",
         "results": [{**template["results"][0], "baseline_id": f"b-{j}"} for j in range(11)]}
        for i in range(1000)]
    prototype = candidates[0]
    candidates = []
    for i in range(21070):
        policy = {**prototype["policy_spec"], "entry": {"entry_policy_id": f"e-{i}"}}
        candidates.append({"policy_id": f"p-{i}", "policy_spec": policy,
                           "policy_signature": canonical_hash("v3-policy", policy)})
    candidates.sort(key=lambda row: (row["policy_signature"], row["policy_id"]))
    artifact["candidate_count"] = len(candidates)
    artifact["candidates_sha256"] = hashlib.sha256(canonical_json(candidates).encode()).hexdigest()
    monkeypatch.setattr(reporter, "build_composite_policy_identity", lambda *a, **k: pytest.fail("Cartesian hash"))
    monkeypatch.setattr(reporter, "_load_paths", lambda *a, **k: pytest.fail("unusable path IO"))
    report = reporter.build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=contract())
    expected = 1000 * 11 * 21070
    assert report["candidate_replay_count"] == report["unknown_replay_count"] == expected
    assert report["complete_replay_count"] == report["evaluated_composite_policy_count"] == 0
    assert report["terminal_evaluated_count"] == 0
    assert report["reason_counts"] == {"BASELINE_EXECUTION_MODEL_CONTEXT_MISSING": expected}
    assert len(report["results"]) == 100 and report["results_truncated"] is True


@pytest.mark.parametrize("remaining", ["0.00000001", "0.000000001", "0.000000000000000001"])
def test_whole_tiny_lot_residual_is_not_forgiven(remaining):
    from decimal import Decimal
    quantity = Decimal(remaining)
    with pytest.raises(ValueError, match="QUANTITY_INVALID"):
        calculate_declared_costs(contract(), generation=GEN,
            entry_events=[(0,100,quantity*2)], exit_events=[(1,100,quantity)], direction="LONG")


def test_complete_results_streamed_even_when_display_limit_zero(tmp_path):
    baseline, candidates, artifact, old_model = _fixture(tmp_path)
    baseline_context(baseline, old_model)
    streamed = []
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates, policy_artifact_receipt=artifact,
        research_model=contract(), result_sink=streamed.append, max_diagnostic_results=0)
    assert report["complete_replay_count"] == len(streamed) == 1
    assert report["results"] == [] and report["results_truncated"] is True
    assert report["results_total"] == report["individual_results_streamed"] == 1
    assert streamed[0]["terminal"]["economics_evidence_basis"] == "DECLARED_SIMULATION"
    assert report["status"] == "BUILT_INCOMPLETE" and report["profitability_supported"] is False
    assert report["ranking_eligible"] is False and report["live_qualification"] is False
    assert "RESULT_STREAM_CONSUMER_NOT_BOUND" in report["blockers"]
    from research.discovery_scorecard_publication import _shadow_aggregate
    _, defects = _shadow_aggregate(report)
    assert "SHADOW_TERMINAL_COMPLETE_RESULTS_OMITTED_OR_MISMATCHED" in defects


@pytest.mark.parametrize("fractions", [[.1,.2], [.1,.2,.3], [.33333333,.33333333]])
def test_realistic_point_three_fill_partial_fractions_reconcile_exactly(fractions):
    from research_v3_contract import canonical_hash
    values = _inputs()
    entry = values["entry_receipt"]
    entry["filled_qty"] = .3
    entry["quantity_attempts"][0]["rounded_executable_quantity"] = .3
    values["position_context"]["margin_usd"] = 3
    values["policy_spec"]["profit_protection"]["partial_take_profits"] = [
        [1 + i*.1, fraction] for i, fraction in enumerate(fractions)]
    values["policy_signature"] = canonical_hash("v3-policy", values["policy_spec"])
    values["cost_model"].update({"calculation_mode": MODE,
        "declared_contract": contract(values["generation"]), "cost_provenance": "DECLARED_SIMULATION"})
    _rebind(values)
    terminal = evaluate_shadow_terminal(**values)
    assert terminal["status"] == "COMPLETE", terminal["blockers"]
    assert terminal["partial_exit_count"] == len(fractions)
    assert terminal["entry_notional_usd"] == pytest.approx(30)


def test_exact_replay_accounting_rejects_forged_terminal_remainder():
    from research.declared_shadow_model import exact_replay_exit_quantities
    policy = {"profit_protection": {"partial_take_profits": [[1,.1]]}}
    replay = {"trace": [{"ts": 1, "partial_exits": [{"trigger_atr_k": 1, "fraction": .1}]}],
              "remaining_fraction_at_terminal": .89, "exit_ts": 2}
    with pytest.raises(ValueError, match="TERMINAL_FRACTION_MISMATCH"):
        exact_replay_exit_quantities(replay, policy, .3)
