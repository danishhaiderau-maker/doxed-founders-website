"""Focused regressions for fresh analyzer-dashboard truthfulness fixes."""
import pytest

from research import research_dashboard as dashboard


@pytest.fixture(autouse=True)
def _clear_dashboard_api_cache():
    dashboard._API_RESPONSE_CACHE.clear()
    yield
    dashboard._API_RESPONSE_CACHE.clear()


def _source(leaders=None):
    report = {
        "epoch_id": "epoch-clean",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "collection": {"decision_outcomes": {"REJECTED": 2}},
        "candidate_screen": {"dynamic_regime_leaders": leaders or {}},
        "blockers": ["INSUFFICIENT_OOS"],
    }
    return {
        "report": report,
        "screen": report["candidate_screen"],
        "qualified": False,
        "epoch_id": "epoch-clean",
        "blockers": report["blockers"],
    }


def test_public_policy_row_normalizes_pass_gates_and_rejects_double_exit_identity():
    row = dashboard._public_policy_evidence_row({
        "policy_id": "ENTRY|EXIT_ONE|EXIT_TWO",
        "gates": {"oos_pass": "true", "risk_pass": True, "fills_pass": False},
    })
    assert row["policy_id"] == "INVALID_CONCATENATED_POLICY_ID"
    assert row["raw_policy_id"] == "ENTRY|EXIT_ONE|EXIT_TWO"
    assert dashboard._failed_gate_names(row["gates"]) == ["fills_pass"]


def test_dynamic_leader_is_suppressed_when_ev_is_unavailable(monkeypatch):
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: _source({
        "BULL": [{"policy_id": "p-no-ev", "expectancy_lcb_usd": None, "sealed_oos_net_usd": None}],
    }))
    payload = dashboard.app.test_client().get("/api/dynamic-policy-research").get_json()
    assert payload["relative_leader_kind"] == "NONE"
    assert payload["relative_leader_status"] == "UNAVAILABLE_EV_NOT_COMPUTABLE"
    assert payload["regimes"] == []


def test_shadow_api_declares_executed_and_counterfactual_as_separate(monkeypatch):
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: _source())
    monkeypatch.setattr(dashboard, "_read_report", lambda _name, default=None: default or {})
    payload = dashboard.app.test_client().get("/api/shadow-policy-research").get_json()
    assert payload["evidence_classes"]["shadow_counterfactual"]["merged_with_executed"] is False
    assert payload["evidence_classes"]["executed_paper"]["merged_with_shadow"] is False


def test_status_labels_analyzer_and_mirror_revisions_separately(monkeypatch):
    manifest = {
        "generation_revision": "abc123full",
        "analyzer_sync_id": dashboard.EXPECTED_ANALYZER_SYNC_ID,
        "fresh_epoch": {"epoch_id": "epoch-clean"},
    }
    monkeypatch.setattr(dashboard, "_read_json", lambda name, default=None: manifest if name == dashboard.REPORT_MANIFEST_FILE else (default or {}))
    monkeypatch.setattr(dashboard, "_current_generation_report", lambda _name: {})
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: "abc123")
    payload = dashboard.app.test_client().get("/api/status").get_json()
    assert payload["generation_revision_label"] == "ANALYZER_SOURCE_REVISION"
    assert payload["analyzer_source_revision"] == "abc123full"
    assert payload["mirror_source_revision"] == "abc123"
    assert payload["source_revision_parity"] == "MATCH"
