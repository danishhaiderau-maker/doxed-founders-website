"""Focused regressions for fresh analyzer-dashboard truthfulness fixes."""
import json

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


def test_dynamic_leader_is_suppressed_when_ev_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: _source({
        "BULL": [{"policy_id": "p-no-ev", "expectancy_lcb_usd": None, "sealed_oos_net_usd": None}],
    }))
    payload = dashboard.app.test_client().get("/api/dynamic-policy-research").get_json()
    assert payload["relative_leader_kind"] == "NONE"
    assert payload["relative_leader_status"] == "UNKNOWN"
    assert payload["regimes"] == []
    assert payload["live_policy_change_allowed"] is False
    assert payload["evidence_source"] == dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE
    assert "INSUFFICIENT_OOS" in payload["blockers"]


def test_completed_manifest_never_revives_legacy_dynamic_leaders(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    published = tmp_path / dashboard.PUBLISHED_REPORTS_DIR
    published.mkdir()
    (published / dashboard.REPORT_MANIFEST_FILE).write_text(json.dumps({
        "generation_id": "completed-current", "fresh_epoch": {"epoch_id": "epoch-current"},
        "reports": [], "text_artifacts": []}), encoding="utf-8")
    stale = _source({"BULL": [{"policy_id": "stale-dynamic-leader",
                               "expectancy_lcb_usd": 4, "sealed_oos_net_usd": 8}]})
    (tmp_path / dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE).write_text(
        json.dumps(stale["report"]), encoding="utf-8")
    # A compatibility adapter cannot override an explicit completed manifest.
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: stale)
    payload = dashboard.app.test_client().get("/api/dynamic-policy-research").get_json()
    assert payload["relative_leader_kind"] == "NONE"
    assert payload["regimes"] == []
    assert payload["live_policy_change_allowed"] is False
    assert payload["evidence_source"] == dashboard.DYNAMIC_POLICY_ANALYSIS_REPORT_FILE


def test_shadow_api_declares_executed_and_counterfactual_as_separate(monkeypatch):
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: _source())
    monkeypatch.setattr(dashboard, "_read_report", lambda _name, default=None: default or {})
    payload = dashboard.app.test_client().get("/api/shadow-policy-research").get_json()
    assert payload["evidence_classes"]["shadow_counterfactual"]["merged_with_executed"] is False
    assert payload["evidence_classes"]["executed_paper"]["merged_with_shadow"] is False


def test_shadow_api_marks_stale_signed_report_unavailable_and_keeps_generic_counts(monkeypatch):
    source = _source()
    source["report"]["generation_revision"] = "revision-current"
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: source)

    def fake_report(name, default=None):
        if name == "shadow_lane_comprehensive_report.json":
            return {
                "generation_revision": "revision-old",
                "epoch_scope": {"selected_epoch_id": "epoch-old"},
                "coverage": {"independent_shared_ai_episodes": 8},
                "cohorts": [{"research_lane": "CONTINUOUS"}],
            }
        if name == "chase_threshold_report.json":
            return {"coverage": {
                "shadow_terminal_outcomes": 14,
                "generic_shadow_counterfactuals": 14,
                "tile_lab_shadow_outcomes": 0,
            }}
        return default or {}

    monkeypatch.setattr(dashboard, "_read_report", fake_report)
    payload = dashboard.app.test_client().get("/api/shadow-policy-research").get_json()
    signed = payload["comprehensive_shadow_lanes"]
    assert signed["available"] is False
    assert "EPOCH_MISMATCH" in signed["reason"]
    assert "GENERATION_REVISION_MISMATCH" in signed["reason"]
    assert signed["coverage"] == {}
    assert signed["cohorts"] == []
    assert payload["generic_shadow_terminals"] == {
        "status": "SEPARATE_GENERIC_COUNTERFACTUAL_COHORT",
        "terminal_outcomes": 14,
        "generic_terminal_outcomes": 14,
        "tile_lab_terminal_outcomes": 0,
        "source": "chase_threshold_report.json",
    }


def test_shadow_api_exposes_matching_signed_report_without_merging_generic(monkeypatch):
    source = _source()
    source["report"]["generation_revision"] = "revision-current"
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: source)

    def fake_report(name, default=None):
        if name == "shadow_lane_comprehensive_report.json":
            return {
                "generation_revision": "revision-current",
                "epoch_scope": {"selected_epoch_id": "epoch-clean"},
                "coverage": {"independent_shared_ai_episodes": 8},
                "cohorts": [{"research_lane": "CONTINUOUS"}],
            }
        if name == "chase_threshold_report.json":
            return {"coverage": {"shadow_terminal_outcomes": 14}}
        return default or {}

    monkeypatch.setattr(dashboard, "_read_report", fake_report)
    payload = dashboard.app.test_client().get("/api/shadow-policy-research").get_json()
    assert payload["comprehensive_shadow_lanes"]["available"] is True
    assert payload["comprehensive_shadow_lanes"]["coverage"]["independent_shared_ai_episodes"] == 8
    assert payload["generic_shadow_terminals"]["terminal_outcomes"] == 14


def test_unsupported_scenario_rows_are_diagnostics_not_execution_leaders():
    raw = {
        "policy_id": "ENTRY|ATR_TP_2.5_SCENARIO_C_ATR_SL_1",
        "policy_family": "FIXED_TARGET",
        "oos_episodes": 9,
        "supported_conservative_episodes": 0,
        "ideal_touch_diagnostic": {
            "touches": 8,
            "no_touches": 1,
            "wins": 5,
            "losses": 3,
            "oos_net_usd": 0.25,
            "max_drawdown_usd": -0.1,
        },
    }
    payload = dashboard._bounded_safe_policy_payload({
        "schema": "safe_policy_genome_v3_1_report_v1",
        "candidate_screen": {"scenario_c_atr_stop_sweep": {
            "qualification": "DESCRIPTIVE_ONLY",
            "leaders_by_stop": {"1": [raw]},
            "best_by_chase_and_stop": {"w345": {"1": raw}},
            "overall_leaders": [raw],
        }},
    })
    sweep = payload["candidate_screen"]["scenario_c_atr_stop_sweep"]
    assert sweep["overall_leaders"] == []
    assert sweep["leaders_by_stop"] == {}
    assert sweep["best_by_chase_and_stop"] == {}
    diagnostic = sweep["diagnostic_hypotheses_by_stop"]["1"][0]
    assert diagnostic["supported_conservative_episodes"] == 0
    assert diagnostic["diagnostic_net_pnl_usd"] == 0.25
    assert diagnostic["evidence_status"] == "IDEAL_TOUCH_DIAGNOSTIC_ONLY"


def test_dashboard_labels_completion_units_and_family_cohorts_separately():
    html = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert "Replay-eligible execution rows" in html
    assert "Completed paths', `${" not in html
    assert "Policy-grid families materialized" in html
    assert "Conservative shortlist families" in html
    assert "Diagnostic families represented" in html
    assert "Policy families searched" not in html
    assert "JSON.stringify(value)" in html
    assert "expected ${c.expected}, found ${c.found}" not in html
    assert "overflow-wrap: anywhere" in html

    safe_html = dashboard.app.test_client().get(
        "/safe-policy-genome-v3.1"
    ).get_data(as_text=True)
    assert "Conservative execution leaders by stop" in safe_html
    assert "Ideal-touch diagnostic hypotheses" in safe_html
    assert "INSUFFICIENT EXECUTION EVIDENCE" in safe_html


def test_status_labels_analyzer_and_mirror_revisions_separately(monkeypatch):
    manifest = {
        "generation_revision": "abc123full",
        "analyzer_sync_id": dashboard.EXPECTED_ANALYZER_SYNC_ID,
        "fresh_epoch": {"epoch_id": "epoch-clean"},
    }
    monkeypatch.setattr(dashboard, "_read_json", lambda name, default=None: manifest if name == dashboard.REPORT_MANIFEST_FILE else (default or {}))
    monkeypatch.setattr(dashboard, "_current_generation_report", lambda _name: {})
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: "abc123")
    monkeypatch.setattr(dashboard, "_mirror_sync_receipt", lambda: {
        "ok": True,
        "pollOk": True,
        "inProgress": False,
        "revisionParity": "MATCH",
        "observedSourceRevision": "abc123",
    })
    payload = dashboard.app.test_client().get("/api/status").get_json()
    assert payload["generation_revision_label"] == "ANALYZER_SOURCE_REVISION"
    assert payload["analyzer_source_revision"] == "abc123full"
    assert payload["mirror_source_revision"] == "abc123"
    assert payload["source_revision_parity"] == "MATCH"


def test_generation_is_stale_while_new_fly_revision_is_syncing(monkeypatch):
    manifest = {
        "generation_revision": "oldrev-full",
        "fresh_epoch": {"epoch_id": "epoch-clean"},
    }
    monkeypatch.setattr(dashboard, "_load_bot_session", lambda: {
        "collector_v22_epoch_id": "epoch-clean",
    })
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: "oldrev")
    monkeypatch.setattr(dashboard, "_mirror_sync_receipt", lambda: {
        "inProgress": True,
        "revisionParity": "MISMATCH",
        "observedSourceRevision": "newrev",
        "mirroredSourceRevision": "oldrev",
    })

    freshness = dashboard._generation_freshness_meta(manifest)

    assert freshness["current"] is False
    assert freshness["stale"] is True
    assert freshness["qualification_allowed"] is False
    assert freshness["mirror_sync_in_progress"] is True
    assert freshness["mirror_sync_revision_parity"] == "MISMATCH"
    assert freshness["observed_source_revision"] == "newrev"
    assert any("synchronization is in progress" in reason for reason in freshness["reasons"])
    assert any("has not been promoted" in reason for reason in freshness["reasons"])


def test_freshness_compares_dataset_source_when_analyzer_code_revision_differs(monkeypatch):
    manifest = {
        "generation_revision": "c2ddb218edd9",
        "source_revision": "577a188d2abc",
        "fresh_epoch": {"epoch_id": "epoch-clean"},
    }
    monkeypatch.setattr(dashboard, "_load_bot_session", lambda: {
        "collector_v22_epoch_id": "epoch-clean",
    })
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: "577a188d2abc")
    monkeypatch.setattr(dashboard, "_mirror_sync_receipt", lambda: {
        "ok": True,
        "pollOk": True,
        "inProgress": False,
        "revisionParity": "MATCH",
        "observedSourceRevision": "577a188d2abc",
    })

    freshness = dashboard._generation_freshness_meta(manifest)

    assert manifest["generation_revision"] != manifest["source_revision"]
    assert freshness["revision_parity"] == "MATCH"
    assert freshness["current"] is True


def test_generation_fails_closed_when_mirror_sync_receipt_failed(monkeypatch):
    manifest = {
        "generation_revision": "abc123full",
        "fresh_epoch": {"epoch_id": "epoch-clean"},
    }
    monkeypatch.setattr(dashboard, "_load_bot_session", lambda: {
        "collector_v22_epoch_id": "epoch-clean",
    })
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: "abc123")
    monkeypatch.setattr(dashboard, "_mirror_sync_receipt", lambda: {
        "ok": False,
        "pollOk": False,
        "revisionParity": "UNKNOWN",
        "consecutiveFailures": 14,
        "mirroredSourceRevision": "abc123",
    })

    freshness = dashboard._generation_freshness_meta(manifest)

    assert freshness["revision_parity"] == "MATCH"
    assert freshness["epoch_parity"] == "MATCH"
    assert freshness["mirror_sync_receipt_ok"] is False
    assert freshness["mirror_sync_poll_ok"] is False
    assert freshness["mirror_sync_revision_parity"] == "UNKNOWN"
    assert freshness["current"] is False
    assert freshness["stale"] is True
    assert freshness["qualification_allowed"] is False
    assert any("receipt is failed" in reason for reason in freshness["reasons"])
    assert any("poll failed" in reason for reason in freshness["reasons"])
    assert any("parity is not confirmed" in reason for reason in freshness["reasons"])


def test_generation_fails_closed_when_mirror_sync_receipt_missing(monkeypatch):
    manifest = {
        "generation_revision": "abc123full",
        "fresh_epoch": {"epoch_id": "epoch-clean"},
    }
    monkeypatch.setattr(dashboard, "_load_bot_session", lambda: {
        "collector_v22_epoch_id": "epoch-clean",
    })
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: "abc123")
    monkeypatch.setattr(dashboard, "_mirror_sync_receipt", lambda: {})

    freshness = dashboard._generation_freshness_meta(manifest)

    assert freshness["current"] is False
    assert freshness["stale"] is True
    assert freshness["qualification_allowed"] is False
    assert freshness["mirror_sync_receipt_ok"] is False
    assert freshness["mirror_sync_revision_parity"] == "UNAVAILABLE"
