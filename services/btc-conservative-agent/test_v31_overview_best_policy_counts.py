"""Regression for V3.1 Best Policy Overview compatibility fields."""
from research import research_dashboard as dashboard


def test_best_policy_overview_projects_canonical_v31_counts_and_search(monkeypatch):
    report = {
        "schema": "safe_policy_genome_v3_1_report_v1",
        "epoch_id": "epoch-clean-808",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "blockers": ["NO_SAFE_QUALIFIED_POLICY"],
        "collection": {
            "independent_opportunities": 3,
            "decision_branches": 6,
            "terminal_lifecycles": 3,
            "execution_rows": 1,
            "decision_outcomes": {"CENSORED": 3, "NO_TRADE": 3},
            "effective_paper_execution_identities": [{
                "policy_epoch_id": "paper-epoch-clean",
                "policy_signature": "paper-policy-clean",
            }],
        },
        "candidate_screen": {
            "descriptive_top_100": [],
            "split": {"train": 0, "oos": 0},
        },
        "safe_policy_ranking": {"qualification": "NO_SAFE_QUALIFIED_POLICY"},
        "search": {
            "schema": "hierarchical_policy_search_plan_v3",
            "counts": {
                "entry_cartesian": 2700,
                "nominal_full_cartesian": 8_597_534_400,
            },
        },
    }
    compatibility = {
        "schema": "best_policy_research_v3_1_adapter_v1",
        "epoch_id": "epoch-clean-808",
        "evidence": {"completed_paths": 1},
    }
    monkeypatch.setattr(dashboard, "_current_generation_report", lambda _name: report)
    monkeypatch.setattr(dashboard, "_read_report", lambda name, default=None: (
        compatibility if name == dashboard.BEST_POLICY_RESEARCH_REPORT_FILE else (default or {})
    ))

    # Exercise the adapter directly so this focused test cannot seed the
    # dashboard's short-lived HTTP response cache for a following test cohort.
    payload = dashboard._best_policy_research_v31_payload()

    assert payload["epoch_id"] == "epoch-clean-808"
    assert payload["evidence"]["current_epoch_events"] == 3
    assert payload["evidence"]["completed_paths"] == 1
    assert payload["evidence"]["independent_episode_count"] == 3
    assert payload["evidence"]["decision_branches"] == 6
    assert payload["policy_epoch_id"] == "paper-epoch-clean"
    assert payload["evidence_policy_signature"] == "paper-policy-clean"
    assert payload["research_design"]["counts"]["entry_policy_cartesian"] == 2700
    assert payload["research_design"]["counts"]["naive_full_cartesian"] == 8_597_534_400
    assert payload["research_design"]["static_vs_dynamic"]["required"] is True
    assert payload["status"] == "NO QUALIFIED POLICY"
    assert payload["current_candidate"] is None


def test_best_policy_overview_does_not_count_no_order_terminals_as_paths(monkeypatch):
    report = {
        "schema": "safe_policy_genome_v3_1_report_v1",
        "epoch_id": "epoch-clean-no-orders",
        "collection": {
            "independent_opportunities": 2,
            "terminal_lifecycles": 2,
            "execution_rows": 0,
        },
        "candidate_screen": {"descriptive_top_100": [], "split": {}},
        "safe_policy_ranking": {"qualification": "NO_SAFE_QUALIFIED_POLICY"},
    }
    compatibility = {
        "schema": "best_policy_research_v3_1_adapter_v1",
        "epoch_id": "epoch-clean-no-orders",
        "evidence": {"completed_paths": 0},
    }
    monkeypatch.setattr(dashboard, "_current_generation_report", lambda _name: report)
    monkeypatch.setattr(dashboard, "_read_report", lambda name, default=None: (
        compatibility if name == dashboard.BEST_POLICY_RESEARCH_REPORT_FILE else (default or {})
    ))

    payload = dashboard._best_policy_research_v31_payload()

    assert payload["evidence"]["current_epoch_events"] == 2
    assert payload["evidence"]["terminal_lifecycles"] == 2
    assert payload["evidence"]["completed_paths"] == 0
