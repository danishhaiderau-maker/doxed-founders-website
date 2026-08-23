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


def test_legacy_chase_and_exit_surfaces_are_machine_readably_nonqualifying(monkeypatch):
    reports = {
        "chase_threshold_report.json": {
            "generated_at": "2026-08-20T00:00:00+00:00",
            "thresholds": {"4": {"trades": 17, "ev_usd": 1.12}},
        },
        "chase_attribution_report.json": {"trades": []},
        "exit_combinations_report.json": {"top": [{"type": "CONTINUOUS"}]},
        "exit_leakage_by_reason_report.json": {"reasons": []},
        "exit_ladder_simulator_report.json": {"profiles": []},
    }
    monkeypatch.setattr(dashboard, "_read_report", lambda name: reports.get(name, {}))

    payloads = [
        dashboard._chase_threshold_payload(),
        dashboard._exit_combos_payload(),
        dashboard._exit_reason_leak_payload(),
        dashboard._ladder_sim_payload(),
    ]
    for payload in payloads:
        assert payload["qualified_v3_1"] is False
        assert payload["ranking_eligible"] is False
        assert payload["evidence_scope"].startswith("LEGACY")
        assert payload["warning"]


def test_top_combos_includes_decoded_current_epoch_oos_policy_grid(monkeypatch):
    monkeypatch.setattr(dashboard, "_read_report", lambda name: {
        "top": [{
            "adx_bucket": "30+", "spread_bucket": "5+", "entry_mode": "DIRECT",
            "lane": "CONTINUOUS", "trades": 3, "wr_pct": 33.3,
            "pnl_usd": -8.68, "ev_usd": -2.89,
        }],
        "dimensions": ["adx_bucket"],
    })
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: {
        "epoch_id": "epoch-clean", "qualified": False,
        "blockers": ["QUALIFICATION_GATE_FAILED:conservative_execution"],
        "ranking": {},
        "report": {
            "collection": {"oos_episodes": 40},
            "search_progress": {
                "entry_policy_cartesian": 2700,
                "naive_full_cartesian": 8597534400,
            },
        },
        "screen": {"descriptive_top_100": [{
            "policy_id": "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
            "episodes_total": 133, "oos_episodes": 40, "oos_fills": 40,
            "oos_wins": 30, "oos_losses": 10,
            "sealed_oos_net_usd": 101.7442,
            "expectancy_lcb_usd": 2.543605,
            "max_drawdown_usd": -100.5837,
        }]},
    })

    payload = dashboard._combos_payload()

    assert len(payload["top"]) == 1
    grid = payload["policy_grid"]
    assert grid["live_policy_change_allowed"] is False
    assert grid["evidence"]["oos_episodes"] == 40
    assert grid["search_counts"]["entry_policy_cartesian"] == 2700
    assert grid["search_counts"]["naive_full_cartesian"] == 8597534400
    assert grid["rows_limit"] == 100
    row = grid["rows"][0]
    assert row["entry_offset_pct"] == 0.29
    assert row["chase_windows"] == "2, 3, 4"
    assert row["chase_window_ages"] == "10–25 min"
    assert row["chase_remaining_gap_step_pct"] == 25.0
    assert row["reprice_interval_sec"] == 60
    assert row["exit_policy"] == "atr_tp_k2.5"
    assert row["atr_take_profit_multiple"] == 2.5
    assert row["fill_model"] == "IDEAL_TOUCH_REPLAY"
    assert row["protection_model"] == "NO_LADDER_NO_THESIS_NO_HARD_STOP"
    assert row["oos_win_probability_pct"] == 75.0
    assert row["oos_win_probability_ci95_low_pct"] < 75 < row["oos_win_probability_ci95_high_pct"]
    assert row["oos_expectancy_usd"] == 2.543605


def test_main_dashboard_labels_current_policy_grid_and_legacy_scopes():
    client = dashboard.app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Current-epoch counterfactual policy grid" in html
    assert "Win probability (95% CI)" in html
    assert "MIXED — CURRENT V2.2 POLICY GRID + LEGACY EXECUTED" in html
    assert "LEGACY EXECUTED" in html
    assert "SOURCE UNAVAILABLE" in html
    assert "Top 100 Policy Combinations" in html
    assert "Top 100 Policy Combos" in html
    assert "Entry configurations" in html
    assert "Distinct policies tested" in html
    assert "Profitable in train + OOS" in html
    assert "Theoretical search space" in html
    assert "Hierarchical search space" in html
    assert "pg.policy_rows || pg.rows || []" in html


def test_current_policy_grid_exposes_at_most_top_100_rows(monkeypatch):
    policies = []
    for index in range(120):
        policies.append({
            "policy_id": f"OFFSET_0.0_CHASE_none|atr_tp_k{index + 1}",
            "qualification": "DESCRIPTIVE_ONLY",
            "train": {"independent_episodes": 70},
            "oos": {
                "independent_episodes": 30,
                "fills": 30,
                "wins": 20,
                "losses": 10,
                "net_pnl_usd": 10.0,
                "expectancy_usd": 0.333333,
                "max_drawdown_usd": -5.0,
            },
        })
    v31_policies = [{
        "policy_id": row["policy_id"], "episodes_total": 100,
        "oos_episodes": 30, "oos_fills": 30, "oos_wins": 20,
        "oos_losses": 10, "sealed_oos_net_usd": 10.0,
        "expectancy_lcb_usd": 0.333333, "max_drawdown_usd": -5.0,
    } for row in policies]
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: {
        "epoch_id": "epoch-clean", "qualified": False, "blockers": [],
        "ranking": {},
        "report": {
            "collection": {},
            "search_progress": {"entry_policy_cartesian": 2700},
            "safe_policy_ranking": {
                "distinct_policies_tested": 12601,
                "train_and_oos_profitable_policies": 1449,
            },
        },
        "screen": {"descriptive_top_100": v31_policies},
    })

    grid = dashboard._current_policy_grid_rows()

    assert len(grid["rows"]) == 100
    assert grid["rows_available"] == 120
    assert grid["policy_search_statistics"]["distinct_policies_tested"] == 12601
    assert grid["rows_limit"] == 100


def test_genome_blocks_preserved_report_when_current_source_is_unavailable(monkeypatch):
    def fake_read(name):
        text = str(name).replace("\\", "/")
        if text.endswith("genome_source_status.json"):
            return {
                "schema": "genome_source_status_v1",
                "status": "GENOME_SOURCE_UNAVAILABLE",
                "reason": "REQUIRED_SOURCE_TABLES_MISSING",
                "missing_tables": ["decision_genome", "market_genome"],
            }
        if text.endswith("genome_analysis_report.json"):
            return {"schema": "genome_analysis_v1", "generated_at": "old"}
        return {}
    monkeypatch.setattr(dashboard, "_read_json", fake_read)

    payload = dashboard._genome_payload()

    assert payload["available"] is False
    assert payload["status"] == "GENOME_SOURCE_UNAVAILABLE"
    assert payload["preserved_report_available"] is True
    assert "not rendered as current evidence" in payload["warning"]


def test_genome_prefers_current_safe_v31_over_retired_missing_source(monkeypatch):
    def fake_read(name, *args):
        text = str(name).replace("\\", "/")
        if text.endswith("safe_policy_genome_v3_report.json"):
            return {
                "schema": "safe_policy_genome_v3_1_report_v1",
                "status": "COLLECTING",
                "qualification": "NO_SAFE_QUALIFIED_POLICY",
                "generated_at": "2026-08-23T04:30:00Z",
                "epoch_id": "epoch-clean",
                "collection": {"independent_opportunities": 12},
                "candidate_screen": {"descriptive_top_100": []},
                "blockers": ["INSUFFICIENT_OOS_EPISODES"],
                "live_policy_change_allowed": False,
            }
        if text.endswith("genome_source_status.json"):
            return {"status": "GENOME_SOURCE_UNAVAILABLE"}
        return {}
    monkeypatch.setattr(dashboard, "_read_json", fake_read)

    payload = dashboard._genome_payload()

    assert payload["available"] is True
    assert payload["collector_generation"] == "V3.1"
    assert payload["epoch_id"] == "epoch-clean"
    assert payload["collection"]["independent_opportunities"] == 12
    assert payload["live_policy_change_allowed"] is False
    assert payload["legacy_genome"]["status"].startswith("RETIRED_RESEARCH_DB")


def test_api_cache_key_changes_with_analyzer_report_generation(tmp_path, monkeypatch):
    manifest = tmp_path / dashboard.REPORT_MANIFEST_FILE
    safe = tmp_path / dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE
    manifest.write_text('{"generated_at":"one"}', encoding="utf-8")
    safe.write_text('{"schema":"safe_policy_genome_v3_1_report_v1"}', encoding="utf-8")

    monkeypatch.setattr(
        dashboard,
        "_data_file_candidates",
        lambda name: [manifest if name == dashboard.REPORT_MANIFEST_FILE else safe],
    )
    with dashboard.app.test_request_context("/api/genome"):
        first = dashboard._read_api_cache_key()
        manifest.write_text('{"generated_at":"generation-two"}', encoding="utf-8")
        second = dashboard._read_api_cache_key()

    assert first != second


def test_integrity_api_is_fail_closed_and_exposes_valid_receipt(monkeypatch):
    dashboard._API_RESPONSE_CACHE.clear()
    monkeypatch.setattr(
        dashboard,
        "_read_json",
        lambda name, *args: (
            {"schema": "analyzer_integrity_v1", "valid": True, "failed_checks": []}
            if name == dashboard.ANALYZER_INTEGRITY_FILE else {}
        ),
    )
    with dashboard.app.test_client() as client:
        response = client.get("/api/integrity")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True

    monkeypatch.setattr(dashboard, "_read_json", lambda name, *args: {})
    with dashboard.app.test_client() as client:
        missing = client.get("/api/integrity")
    assert missing.status_code == 503
    assert missing.get_json()["valid"] is False


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


def test_static_and_dynamic_routes_use_v31_genome_not_retired_v22(monkeypatch):
    report = {
        "schema": "safe_policy_genome_v3_1_report_v1",
        "status": "V3_COLLECTING",
        "epoch_id": "epoch-v31-clean",
        "live_policy_change_allowed": False,
        "blockers": ["NO_SAFE_QUALIFIED_POLICY"],
        "collection": {"independent_opportunities": 42},
        "search": {"nominal_full_cartesian": 8597534400},
        "candidate_screen": {
            "unique_policies_evaluated": 2700,
            "training_episodes": 28,
            "oos_episodes": 14,
            "descriptive_top_100": [
                {"policy_id": "profitable", "sealed_oos_net_usd": 12.5},
                {"policy_id": "losing", "sealed_oos_net_usd": -1.0},
            ],
            "dynamic_regime_leaders": {
                "BULL": [{"policy_id": "bull-policy", "sealed_oos_net_usd": 3.0}],
            },
        },
        "safe_policy_ranking": {"qualification": "NO_SAFE_QUALIFIED_POLICY"},
        "number_one_strategy": None,
    }

    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: {
        "report": report,
        "screen": report["candidate_screen"],
        "ranking": report["safe_policy_ranking"],
        "epoch_id": report["epoch_id"],
        "qualified": False,
        "blockers": report["blockers"],
    })
    client = dashboard.app.test_client()

    static = client.get("/api/static-policy-research").get_json()
    dynamic = client.get("/api/dynamic-policy-research").get_json()

    assert static["schema"] == "static_policy_dashboard_v3_1"
    assert static["collector_generation"] == "V3.1"
    assert static["epoch_id"] == "epoch-v31-clean"
    assert static["independent_episodes"] == 42
    assert [row["policy_id"] for row in static["profitable_policies"]] == ["profitable"]
    assert "NO_CURRENT_V22_EPOCH" not in static["blockers"]
    assert dynamic["schema"] == "dynamic_policy_dashboard_v3_1"
    assert dynamic["collector_generation"] == "V3.1"
    assert dynamic["regimes"][0]["regime"] == "BULL"
    assert dynamic["live_policy_change_allowed"] is False
    assert "NO_CURRENT_V22_EPOCH" not in dynamic["blockers"]


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
            "winner_kind": "NONE",
            "winner_status": "NO_PROFITABLE_OOS_WINNER",
            "relative_leader_kind": "DYNAMIC",
            "comparison_delta": {
                "dynamic_minus_static_expectancy_usd": 3.0467,
                "dynamic_minus_static_net_pnl_usd": 45.701,
            },
            "static_oos": {"independent_episodes": 15, "net_pnl_usd": -56.4694, "expectancy_usd": -3.7646},
            "dynamic_oos": {"independent_episodes": 15, "net_pnl_usd": -10.7684, "expectancy_usd": -0.7179},
            "profitable_static_policies": [{"policy_id": "policy-a"}],
            "dynamic_regimes": [{
                "regime": "BULL", "selected_policy_id": "CONTROL_OR_NO_TRADE",
                "research_candidate_policy_id": "policy-a", "fallback": True,
                "fallback_reason": "INSUFFICIENT_REGIME_OOS_EPISODES",
            }],
        },
        "shadow_research": {"independent_episodes": 1, "profitable_policies": []},
    }
    (tmp_path / "policy_candidate_oos_report.json").write_text(json.dumps(detail), encoding="utf-8")
    (tmp_path / "paused_shadow_research_report.json").write_text(json.dumps({"overall": {"closed": 3}}), encoding="utf-8")
    (tmp_path / "shadow_lane_comprehensive_report.json").write_text(json.dumps({
        "coverage": {"independent_shared_ai_episodes": 4, "deduped_lane_records": 7},
        "cohorts": [{"research_lane": "TYPE_B_HUNTER_V1", "classification": "POLICY_ENTERED_ACCEPTED"}],
        "safety": {"executed_pnl_merged": False},
    }), encoding="utf-8")
    (tmp_path / "real_edge_summary.json").write_text(json.dumps({"executed_pnl_usd": -2.5}), encoding="utf-8")
    (tmp_path / dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE).write_text(
        json.dumps({
            "schema": "safe_policy_genome_v3_1_report_v1",
            "epoch_id": "epoch-clean",
            "candidate_screen": {},
            "blockers": ["INSUFFICIENT_V3_1_EVIDENCE"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: {
        "report": {
            "schema": "safe_policy_genome_v3_1_report_v1",
            "epoch_id": "epoch-clean",
            "candidate_screen": {},
            "collection": {"decision_outcomes": {"REJECTED": 2}},
            "blockers": ["INSUFFICIENT_V3_1_EVIDENCE"],
        },
        "screen": {}, "ranking": {}, "epoch_id": "epoch-clean",
        "qualified": False, "blockers": ["INSUFFICIENT_V3_1_EVIDENCE"],
    })
    dashboard._API_RESPONSE_CACHE.clear()

    client = dashboard.app.test_client()
    static = client.get("/api/static-policy-research").get_json()
    dynamic = client.get("/api/dynamic-policy-research").get_json()
    shadow = client.get("/api/shadow-policy-research").get_json()

    # Retired V2.2 policy detail must never repopulate V3.1 policy pages.
    assert static["profitable_policies"] == []
    assert static["evidence_source"] == "safe_policy_genome_v3_report.json"
    assert static["live_policy_change_allowed"] is False
    assert dynamic["winner_kind"] == "NONE"
    assert dynamic["winner_status"] == "NO_PROFITABLE_OOS_WINNER"
    assert dynamic["relative_leader_kind"] == "NONE"
    assert dynamic["regimes"] == []
    assert "No profitable OOS winner" in dynamic["warning"]
    assert dynamic["fallback"] == "CONTROL_OR_NO_TRADE"
    assert shadow["v22_shadow"] == {}
    assert shadow["legacy_v22_excluded"]["shadow_research"]["independent_episodes"] == 1
    assert shadow["paused_shadow"]["overall"]["closed"] == 3
    assert shadow["comprehensive_shadow_lanes"]["coverage"]["independent_shared_ai_episodes"] == 4
    assert shadow["comprehensive_shadow_lanes"]["safety"]["executed_pnl_merged"] is False
    assert shadow["live_policy_change_allowed"] is False
    assert client.get("/static-policies").status_code == 200
    assert client.get("/dynamic-policies").status_code == 200
    assert client.get("/shadow-research").status_code == 200


def test_main_dashboard_links_to_all_policy_research_pages():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    assert 'href="/safe-policy-genome-v3.1"' in source
    assert 'href="/static-policies"' in source
    assert 'href="/dynamic-policies"' in source
    assert 'href="/shadow-research"' in source
    assert "Profitable OOS winner" in source
    assert "NONE — both candidates unprofitable" in source
    assert "Relative leader only" in source
    assert "Descriptive winner" not in source


def test_safe_policy_genome_v31_routes_are_canonical_aliases(monkeypatch):
    payload = {
        "schema": "safe_policy_genome_v3_1_report_v1",
        "status": "V3_COLLECTING",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "collection": {"independent_opportunities": 12},
    }
    monkeypatch.setattr(dashboard, "_read_json", lambda *_args, **_kwargs: payload)
    client = dashboard.app.test_client()

    assert client.get("/safe-policy-genome-v3.1").status_code == 200
    assert client.get("/api/safe-policy-genome-v3.1").get_json() == payload
    assert client.get("/safe-policy-genome-v3").status_code == 200
    assert client.get("/api/safe-policy-genome-v3").get_json() == payload


def test_analyzer_policy_reports_use_configured_data_and_report_roots():
    analyzer = Path(__file__).with_name("analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    assert 'policy_data_dir = os.getenv("BTC_AGENT_DATA_DIR") or "."' in analyzer
    assert 'policy_report_dir = os.getenv("BTC_AGENT_REPORT_DIR") or "."' in analyzer
    assert "data_dir=policy_data_dir" in analyzer
    assert "report_dir=policy_report_dir" in analyzer
    assert "build_shadow_lane_comprehensive_report" in analyzer
