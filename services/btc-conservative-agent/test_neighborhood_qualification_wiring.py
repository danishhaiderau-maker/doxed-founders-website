from research.v3_policy_report_adapter import candidate_from_genome


CELL_SUPPORT = {
    "phase7_support_qualification": {
        "schema": "phase7_regime_support_qualification_v2",
        "qualification_allowed": True,
        "raw_independent_decision_n": 100,
        "cluster_adjusted_effective_n": 100,
        "eligible_regime_direction_cells": ["RANGE|LONG"],
        "gates": {"every_eligible_regime_direction_cell_supported": True},
        "reason_codes": [],
    }
}


def _genome(neighborhood_value):
    winner_gates = {
        "chronological_untouched_oos": True,
        "cost_adjusted_positive_expectancy": True,
        "acceptable_drawdown": True,
        "minimum_independent_episodes": True,
        "conservative_execution": True,
        "regime_diversity": True,
        "no_data_integrity_defects": True,
        "control_benchmark_comparison": True,
        "purged_walk_forward_pass": True,
        "sealed_holdout_pass": True,
        "measured_costs_pass": True,
        "liquidation_buffer_pass": True,
        "regime_coverage_pass": True,
        "baseline_replay_coverage_pass": True,
    }
    if neighborhood_value is not None:
        winner_gates["neighborhood_stability_pass"] = neighborhood_value
    winner = {
        "kind": "STATIC",
        "policy_id": "policy-1",
        "policy_signature": "signature-1",
        "policy_spec": {"entry": {"offset_pct": 0.09}},
        "gates": winner_gates,
    }
    return {
        "epoch_id": "epoch-1",
        "qualification": "QUALIFIED",
        "number_one_strategy": winner,
        "collection": {
            "independent_opportunities": 100,
            "execution_rows": 20,
            "market_segments": 20,
            "effective_paper_execution_identities": [{
                "policy_epoch_id": "policy-epoch-1",
                "policy_signature": "source-signature-1",
            }],
        },
        "integrity": {"passed": True},
        "candidate_screen": {},
        "safe_policy_ranking": {},
        "blockers": [],
    }


def test_adapter_maps_supported_neighborhood_evidence_to_mandatory_gate():
    report = candidate_from_genome(_genome(True), {"snapshot_id": "snapshot-1"}, CELL_SUPPORT)

    assert report["qualification_gates"]["parameter_neighborhood_stability"] is True
    assert "QUALIFICATION_GATE_FAILED:parameter_neighborhood_stability" not in report["blockers"]
    assert report["status"] == "QUALIFIED"
    assert report["candidate"] is not None


def test_adapter_fails_closed_when_neighborhood_evidence_is_missing():
    report = candidate_from_genome(_genome(None), {"snapshot_id": "snapshot-1"}, CELL_SUPPORT)

    assert report["qualification_gates"]["parameter_neighborhood_stability"] is False
    assert "QUALIFICATION_GATE_FAILED:parameter_neighborhood_stability" in report["blockers"]
    assert report["status"] == "BLOCKED"
    assert report["candidate"] is None


def test_adapter_fails_closed_without_canonical_independent_cell_receipt():
    report = candidate_from_genome(_genome(True), {"snapshot_id": "snapshot-1"})

    assert report["qualification_gates"]["detailed_regime_support"] is False
    assert "QUALIFICATION_GATE_FAILED:detailed_regime_support" in report["blockers"]
    assert report["qualification_gate_evidence"]["detailed_regime_support"]["blocker"] == (
        "CANONICAL_CELL_SUPPORT_RECEIPT_MISSING"
    )
    assert report["status"] == "BLOCKED"
