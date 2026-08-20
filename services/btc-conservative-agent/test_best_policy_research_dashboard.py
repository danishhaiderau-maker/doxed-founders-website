import json
from pathlib import Path

from research import research_dashboard as dashboard
from research.best_policy_research import (
    QUALIFICATION_GATE_SCHEMA,
    REQUIRED_QUALIFICATION_GATES,
    build_best_policy_research_report,
    candidate_contract_blockers,
)

POLICY_EPOCH = "policy-epoch-control-off"
EVIDENCE_SIGNATURE = "policy-control-off"


def _gates(value=True):
    return {gate: value for gate in REQUIRED_QUALIFICATION_GATES}


def _static_candidate():
    return {
        "kind": "STATIC",
        "policy_id": "policy-oos-1",
        "policy_signature": "candidate-static-1",
        "policy_spec": {"entry_offset_pct": 0.08, "invert_on": False},
    }


def _event(event_id, outcome, episode_id, *, complete=True,
           policy_epoch=POLICY_EPOCH, policy_signature=EVIDENCE_SIGNATURE):
    signal_ts = 1_800_000_000.0
    candle_count = 60 if complete else 10
    path = [
        [(signal_ts + minute * 60) * 1000, 100, 101, 99, 100, 1]
        for minute in range(candle_count)
    ]
    return {
        "event_id": event_id,
        "epoch_id": "epoch-clean",
        "event_episode_id": episode_id,
        "policy_epoch_id": policy_epoch,
        "policy_signature": policy_signature,
        "collector_version": "collector_v2.2",
        "primary_outcome": outcome,
        "observation_status": "PATH_COMPLETE",
        "envelope": {
            "signal_ts": signal_ts,
            "epoch_id": "epoch-clean",
            "policy_epoch_id": policy_epoch,
            "policy_signature": policy_signature,
        },
        "canonical_tape": {"path_1m": path},
        "entry_children": [],
    }


def _write_fixture(tmp_path: Path, events, report):
    (tmp_path / dashboard.RESEARCH_EVENTS_FILE).write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    (tmp_path / dashboard.BEST_POLICY_RESEARCH_REPORT_FILE).write_text(
        json.dumps(report), encoding="utf-8"
    )
    (tmp_path / dashboard.REPORT_MANIFEST_FILE).write_text(
        json.dumps({"generated_at": "2026-08-20T00:00:00+00:00"}), encoding="utf-8"
    )


def test_best_policy_is_hidden_until_current_epoch_oos_is_qualified(tmp_path, monkeypatch):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1"),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    _write_fixture(tmp_path, events, {
        "epoch_id": "epoch-clean",
        "policy_epoch_id": POLICY_EPOCH,
        "evidence_policy_signature": EVIDENCE_SIGNATURE,
        "status": "PROVISIONAL",
        "candidate": _static_candidate(),
        "evidence": {"qualified_oos_episodes": 9},
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gates": _gates(False),
    })
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])

    payload = dashboard._best_policy_research_payload()

    assert payload["status"] == "NO QUALIFIED POLICY"
    assert payload["current_candidate"] is None
    assert payload["live_policy_change_allowed"] is False
    assert "INDEPENDENT_OOS_EVIDENCE_MISSING" in payload["blockers"]
    assert payload["evidence"]["completed_paths"] == 3
    assert payload["evidence"]["independent_episode_count"] == 3
    assert payload["evidence"]["outcome_coverage"] == {
        "ACCEPTED_FILLED": 1, "ACCEPTED_UNFILLED": 1, "REJECTED": 1,
    }


def test_best_policy_requires_complete_paths_and_exact_epoch(tmp_path, monkeypatch):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1", complete=False),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    _write_fixture(tmp_path, events, {
        "epoch_id": "epoch-old",
        "policy_epoch_id": POLICY_EPOCH,
        "evidence_policy_signature": EVIDENCE_SIGNATURE,
        "status": "QUALIFIED",
        "candidate": _static_candidate(),
        "independent_oos_qualified": True,
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gates": _gates(),
    })
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])

    payload = dashboard._best_policy_research_payload()

    assert payload["status"] == "NO QUALIFIED POLICY"
    assert payload["current_candidate"] is None
    assert payload["evidence"]["replay_ineligible_events"] == 1
    assert "REPLAY_INELIGIBLE_PATHS_PRESENT" in payload["blockers"]
    assert "BEST_POLICY_REPORT_EPOCH_MISMATCH" in payload["blockers"]


def test_exact_epoch_qualified_oos_report_can_show_candidate(tmp_path, monkeypatch):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1"),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    _write_fixture(tmp_path, events, {
        "epoch_id": "epoch-clean",
        "policy_epoch_id": POLICY_EPOCH,
        "evidence_policy_signature": EVIDENCE_SIGNATURE,
        "status": "QUALIFIED",
        "candidate": _static_candidate(),
        "independent_oos_qualified": True,
        "evidence": {"qualified_oos_episodes": 3},
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gates": _gates(),
    })
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])

    payload = dashboard._best_policy_research_payload()
    compatibility = dashboard._decision_readiness_payload()

    assert payload["status"] == "QUALIFIED"
    assert payload["current_candidate"] == _static_candidate()
    assert payload["live_policy_change_allowed"] is True
    assert compatibility["questions"][0]["key"] == "best_policy_research"
    assert len(compatibility["questions"]) == 1


def test_dashboard_retires_five_question_cards():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    assert "<h2>Best Policy Research</h2>" in source
    assert "Live-policy question readiness" not in source
    assert "Cluster distance" not in source
    assert "Thesis fast-cut" not in source
    assert "Chase timing and limits" not in source
    assert "fetch('/api/best-policy-research')" in source


def test_analyzer_adapter_emits_fail_closed_current_epoch_artifact(tmp_path):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1"),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    (tmp_path / dashboard.RESEARCH_EVENTS_FILE).write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    (tmp_path / "policy_candidate_oos_report.json").write_text(json.dumps({
        "epoch_id": "epoch-clean",
        "policy_epoch_id": POLICY_EPOCH,
        "evidence_policy_signature": EVIDENCE_SIGNATURE,
        "status": "PROVISIONAL",
        "candidate": _static_candidate(),
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gates": _gates(False),
    }), encoding="utf-8")

    report = build_best_policy_research_report(tmp_path, tmp_path)
    written = json.loads((tmp_path / dashboard.BEST_POLICY_RESEARCH_REPORT_FILE).read_text(encoding="utf-8"))

    assert report["status"] == "NO QUALIFIED POLICY"
    assert report["current_candidate"] is None
    assert report["evidence"]["completed_paths"] == 3
    assert written == report


def test_same_collection_epoch_is_stratified_by_policy_epoch(tmp_path, monkeypatch):
    events = [
        _event("old-invert", "ACCEPTED_FILLED", "episode-old",
               policy_epoch="policy-epoch-invert-on", policy_signature="policy-invert-on"),
        _event("filled", "ACCEPTED_FILLED", "episode-1"),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    # Ensure the latest event selects the OFF policy epoch.
    events[0]["envelope"]["signal_ts"] -= 60
    _write_fixture(tmp_path, events, {
        "epoch_id": "epoch-clean",
        "policy_epoch_id": POLICY_EPOCH,
        "evidence_policy_signature": EVIDENCE_SIGNATURE,
        "status": "QUALIFIED",
        "candidate": _static_candidate(),
        "independent_oos_qualified": True,
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gates": _gates(),
    })
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])

    payload = dashboard._best_policy_research_payload()

    assert payload["status"] == "QUALIFIED"
    assert payload["policy_epoch_id"] == POLICY_EPOCH
    assert payload["evidence"]["current_epoch_events"] == 3


def test_matching_collection_epoch_but_wrong_policy_signature_blocks(tmp_path, monkeypatch):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1"),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    _write_fixture(tmp_path, events, {
        "epoch_id": "epoch-clean",
        "policy_epoch_id": POLICY_EPOCH,
        "evidence_policy_signature": "wrong-signature",
        "status": "QUALIFIED",
        "candidate": _static_candidate(),
        "independent_oos_qualified": True,
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gates": _gates(),
    })
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])

    payload = dashboard._best_policy_research_payload()
    assert payload["status"] == "NO QUALIFIED POLICY"
    assert "BEST_POLICY_REPORT_POLICY_SIGNATURE_MISMATCH" in payload["blockers"]


def test_arbitrary_true_gate_cannot_qualify():
    from research.best_policy_research import qualification_gate_blockers

    blockers = qualification_gate_blockers({"anything": True})
    assert "QUALIFICATION_GATE_FAILED:chronological_untouched_oos" in blockers


def test_dynamic_candidate_requires_frozen_mapping_fallback_and_regime_oos():
    valid = {
        "kind": "DYNAMIC",
        "policy_id": "dynamic-1",
        "policy_signature": "candidate-dynamic-1",
        "regime_classifier": {"id": "regime-v1", "version": "1", "feature_schema": "causal-v1"},
        "regime_policy_map": {"TREND": "static-a", "RANGE": "static-b"},
        "fallback": "CONTROL",
        "drift_action": "NO_TRADE",
        "training_cutoff": "2026-08-01T00:00:00Z",
        "supported_domain": "BTCUSD",
        "per_regime_oos": {
            "TREND": {"independent_episodes": 10},
            "RANGE": {"independent_episodes": 8},
        },
    }
    assert candidate_contract_blockers(valid) == []
    broken = dict(valid)
    broken["fallback"] = "BEST_AVAILABLE"
    broken["per_regime_oos"] = {"TREND": {"independent_episodes": 10}}
    blockers = candidate_contract_blockers(broken)
    assert "DYNAMIC_FALLBACK_INVALID" in blockers
    assert "DYNAMIC_REGIME_OOS_MISSING:RANGE" in blockers


def test_static_dynamic_and_shadow_apis_fail_closed_but_expose_current_detail(tmp_path, monkeypatch):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1"),
        _event("rejected", "REJECTED", "episode-2"),
    ]
    _write_fixture(tmp_path, events, {
        "epoch_id": "epoch-clean",
        "policy_epoch_id": POLICY_EPOCH,
        "evidence_policy_signature": EVIDENCE_SIGNATURE,
        "status": "NO QUALIFIED POLICY",
        "candidate": None,
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gates": _gates(False),
    })
    detail = {
        "epoch_id": "epoch-clean",
        "policy_epoch_id": POLICY_EPOCH,
        "evidence_policy_signature": EVIDENCE_SIGNATURE,
        "evidence": {"independent_episodes": 2, "training_episodes": 1, "oos_episodes": 1},
        "descriptive_challenger": {
            "winner_kind": "STATIC",
            "profitable_static_policies": [{"policy_id": "policy-a"}],
            "dynamic_regimes": [{"regime": "BULL", "selected_policy_id": "policy-a"}],
        },
        "shadow_research": {"independent_episodes": 1, "profitable_policies": []},
    }
    (tmp_path / "policy_candidate_oos_report.json").write_text(json.dumps(detail), encoding="utf-8")
    (tmp_path / "paused_shadow_research_report.json").write_text(json.dumps({"overall": {"closed": 3}}), encoding="utf-8")
    (tmp_path / "real_edge_summary.json").write_text(json.dumps({"executed_pnl_usd": -2.5}), encoding="utf-8")
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])

    client = dashboard.app.test_client()
    static = client.get("/api/static-policy-research").get_json()
    dynamic = client.get("/api/dynamic-policy-research").get_json()
    shadow = client.get("/api/shadow-policy-research").get_json()

    assert static["profitable_policies"][0]["policy_id"] == "policy-a"
    assert static["live_policy_change_allowed"] is False
    assert dynamic["regimes"][0]["regime"] == "BULL"
    assert dynamic["fallback"] == "CONTROL_OR_NO_TRADE"
    assert shadow["v22_shadow"]["independent_episodes"] == 1
    assert shadow["paused_shadow"]["overall"]["closed"] == 3
    assert shadow["live_policy_change_allowed"] is False
    assert client.get("/static-policies").status_code == 200
    assert client.get("/dynamic-policies").status_code == 200
    assert client.get("/shadow-research").status_code == 200


def test_main_dashboard_links_to_all_policy_research_pages():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    assert 'href="/static-policies"' in source
    assert 'href="/dynamic-policies"' in source
    assert 'href="/shadow-research"' in source


def test_analyzer_policy_reports_use_configured_data_and_report_roots():
    analyzer = Path(__file__).with_name("analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    assert 'policy_data_dir = os.getenv("BTC_AGENT_DATA_DIR") or "."' in analyzer
    assert 'policy_report_dir = os.getenv("BTC_AGENT_REPORT_DIR") or "."' in analyzer
    assert "data_dir=policy_data_dir" in analyzer
    assert "report_dir=policy_report_dir" in analyzer
