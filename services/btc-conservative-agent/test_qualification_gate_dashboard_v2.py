from research import research_dashboard as dashboard
from research.best_policy_research import (
    QUALIFICATION_GATE_SCHEMA,
    REQUIRED_QUALIFICATION_GATES,
    qualification_gate_details,
)
from research.v3_policy_report_adapter import candidate_from_genome


MANDATORY_NEW_GATES = {
    "purged_walk_forward_validation",
    "sealed_holdout_receipt",
    "parameter_neighborhood_stability",
    "measured_execution_costs",
    "liquidation_buffer",
    "detailed_regime_support",
    "baseline_replay_coverage",
}


def test_mandatory_gate_projection_distinguishes_pass_fail_and_unknown():
    values = {
        "purged_walk_forward_validation": True,
        "sealed_holdout_receipt": False,
    }
    details = qualification_gate_details(values, {
        "purged_walk_forward_validation": {
            "receipt_id": "walk-forward-sha256-1",
            "evidence": "5 embargoed folds",
        },
        "sealed_holdout_receipt": {
            "blocker": "SEALED_HOLDOUT_RECEIPT_SIGNATURE_INVALID",
        },
    })
    indexed = {row["gate"]: row for row in details}

    assert QUALIFICATION_GATE_SCHEMA == "best_policy_qualification_gates_v2"
    assert MANDATORY_NEW_GATES <= set(REQUIRED_QUALIFICATION_GATES)
    assert indexed["purged_walk_forward_validation"]["status"] == "PASS"
    assert indexed["purged_walk_forward_validation"]["receipt_id"] == "walk-forward-sha256-1"
    assert indexed["sealed_holdout_receipt"]["status"] == "FAIL"
    assert indexed["sealed_holdout_receipt"]["blocker"] == "SEALED_HOLDOUT_RECEIPT_SIGNATURE_INVALID"
    assert indexed["measured_execution_costs"]["status"] == "UNKNOWN"
    assert indexed["measured_execution_costs"]["blocker"] == (
        "QUALIFICATION_GATE_EVIDENCE_MISSING:measured_execution_costs"
    )


def test_missing_current_generation_makes_every_gate_unavailable():
    details = qualification_gate_details(
        {gate: True for gate in REQUIRED_QUALIFICATION_GATES},
        current_generation_available=False,
    )

    assert len(details) == len(REQUIRED_QUALIFICATION_GATES)
    assert {row["status"] for row in details} == {"UNAVAILABLE"}
    assert {row["blocker"] for row in details} == {"CURRENT_GENERATION_UNAVAILABLE"}


def test_v31_dashboard_api_never_reuses_old_generation_gate_passes(monkeypatch):
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: {
        "report": {
            "epoch_id": "epoch-current",
            "collection": {},
            "search": {},
            "candidate_screen": {},
            "safe_policy_ranking": {},
        },
        "screen": {},
        "ranking": {},
        "epoch_id": "epoch-current",
        "qualified": False,
        "blockers": ["STALE_ANALYZER_GENERATION"],
        "generation_freshness": {
            "current": False,
            "stale": True,
            "revision_parity": "MISMATCH",
            "epoch_parity": "MATCH",
            "reasons": ["SOURCE_REVISION_MISMATCH"],
        },
    })
    monkeypatch.setattr(dashboard, "_read_report", lambda *_args, **_kwargs: {
        "schema": "best_policy_research_v3_1_adapter_v1",
        "epoch_id": "epoch-current",
        "qualification_gates": {gate: True for gate in REQUIRED_QUALIFICATION_GATES},
    })

    payload = dashboard._best_policy_research_v31_payload()

    assert payload["status"] == "STALE GENERATION — QUALIFICATION BLOCKED"
    assert payload["live_policy_change_allowed"] is False
    assert {row["status"] for row in payload["qualification_gate_details"]} == {"UNAVAILABLE"}


def test_main_dashboard_renders_mandatory_gate_status_table():
    html = dashboard.app.test_client().get("/").get_data(as_text=True)

    assert "Mandatory Bitfinex qualification gates" in html
    assert "qualification-gate-body" in html
    assert "PASS requires current-generation evidence" in html
    assert "No current evidence receipt" in html


def test_v31_adapter_maps_validation_receipts_to_precise_gate_blockers():
    genome = {
        "epoch_id": "epoch-current",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "collection": {
            "independent_opportunities": 101,
            "execution_rows": 1,
            "market_segments": 1,
        },
        "integrity": {"passed": True},
        "number_one_strategy": {
            "gates": {
                "purged_walk_forward_pass": False,
                "sealed_holdout_pass": False,
                "measured_costs_pass": False,
                "liquidation_buffer_pass": False,
                "regime_coverage_pass": False,
                "baseline_replay_coverage_pass": False,
            },
            "validation": {
                "purged_walk_forward": {"schema": "purged-v1", "blockers": ["EMBARGO_MISSING"]},
                "sealed_holdout": {"schema": "sealed-v1", "blockers": ["HOLDOUT_SIGNATURE_INVALID"]},
                "measured_cost_evidence": {"schema": "cost-v1", "defects": ["FEES_MISSING"]},
                "liquidation_buffer": {"schema": "liquidation-v1", "blockers": ["DISTANCE_MISSING"]},
                "regimes": ["BULL"],
            },
        },
        "candidate_screen": {},
        "blockers": [],
    }

    report = candidate_from_genome(genome, {"snapshot_id": "snapshot-1"})
    indexed = {row["gate"]: row for row in report["qualification_gate_details"]}

    assert indexed["purged_walk_forward_validation"]["blocker"] == "EMBARGO_MISSING"
    assert indexed["sealed_holdout_receipt"]["blocker"] == "HOLDOUT_SIGNATURE_INVALID"
    assert indexed["measured_execution_costs"]["blocker"] == "FEES_MISSING"
    assert indexed["liquidation_buffer"]["blocker"] == "DISTANCE_MISSING"
    assert indexed["baseline_replay_coverage"]["blocker"] == "BASELINE_REPLAY_COVERAGE_NOT_PROVEN"
    assert report["status"] == "BLOCKED"
