"""Static and executable checks for tile accounting presentation truth."""

import json
import shutil
import subprocess
from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _run_pathway_helper(expression: str):
    helpers = SOURCE[
        SOURCE.index("function pathwayMetricNumber("):
        SOURCE.index("function renderPathwayLab(")
    ]
    node = shutil.which("node")
    assert node, "Node is required for executable tile presentation QA"
    result = subprocess.run(
        [node, "-e", helpers + "\nconsole.log(JSON.stringify(" + expression + "));"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _render_chunk() -> str:
    start = SOURCE.index("function renderPathwayLab")
    end = SOURCE.index("function renderPathwayScorecard", start)
    return SOURCE[start:end]


def test_tile_headlines_use_one_identical_six_metric_contract():
    chunk = _render_chunk()
    for label in ("Status", "Pending", "Open", "Closed", "PnL", "EV/appr"):
        assert f"statRow('{label}'" in chunk
    assert "statRow('Executed'" not in chunk
    assert "statRow('Win%'" not in chunk
    assert "Counterfactual closes" not in chunk
    assert "activeGrid" not in chunk


def test_tile_headlines_always_use_executed_fresh_collection_metrics():
    chunk = _render_chunk()
    assert "currentSettingsPeriod" in chunk
    assert "headlineClosed" in chunk
    assert "headlinePnl" in chunk
    assert "headlineEv" in chunk
    assert "active execution-settings period; earlier rows remain separate" in chunk
    assert "labPrimaryTrades" not in chunk
    assert "v2ChkPass" not in chunk


def test_tile_ev_is_unavailable_when_there_are_no_approvals():
    chunk = _render_chunk()
    assert "const headlineEv = headlineApprovals > 0 && headlineEvAvailable" in chunk
    assert "const headlineEvLabel = headlineEv == null ? 'Unavailable'" in chunk
    assert "statRow('EV/appr', headlineEvLabel)" in chunk
    assert "· EV ' + headlineEvLabel + '/approve" in chunk
    assert "headlineApprovals ? headlinePnl / headlineApprovals : 0" not in chunk


def test_trade_rows_distinguish_observed_loss_from_stop_trigger_reference():
    assert "function tradeStopEvidence" in SOURCE
    assert "Observed PnL % of margin" in SOURCE
    assert "Observed Net USD" in SOURCE
    assert "STOP OVERSHOOT" in SOURCE
    assert "trigger-level reference $" in SOURCE
    assert "not reconstructed execution" in SOURCE
    assert "inferredMargin" in SOURCE
    assert "PRE-FIX PNL ACCOUNTING CONTAMINATED" in SOURCE
    assert "terminal_single_count_v1" in SOURCE


def test_settings_periods_are_durable_and_attached_to_both_payload_paths():
    assert 'EXECUTION_SETTINGS_HISTORY_FILE = "execution_settings_history.jsonl"' in SOURCE
    assert '_record_execution_settings_epoch("CHASE_CHANGED")' in SOURCE
    assert '_record_execution_settings_epoch("GAP_CHANGED")' in SOURCE
    assert '_record_execution_settings_epoch("TRACKING_STARTED")' in SOURCE
    assert '_record_execution_settings_epoch("FRESH_COLLECTION_STARTED", force=True)' in SOURCE
    # Current signed-epoch normalization plus the disk and analyzer-backed
    # payload paths must all reconcile their period rows to the same headline.
    assert SOURCE.count('["settings_periods"] = _reconcile_settings_periods_to_headline(') == 3
    assert "def _reconcile_settings_periods_to_headline" in SOURCE
    chunk = _render_chunk()
    assert "Settings-period breakdown" in chunk
    assert "LEGACY SETTINGS BASELINE" in chunk
    assert "Not recorded" in chunk


def test_active_settings_period_is_never_presented_as_evidence_freshness():
    chunk = _render_chunk()
    assert "ACTIVE SETTINGS PERIOD" in chunk
    assert "ACTIVE SETTINGS PERIOD METRICS" in chunk
    assert "DATA FRESHNESS IS SEPARATE" in chunk
    assert "Active settings period:" in chunk
    assert "not an evidence-freshness claim" in chunk
    assert " · CURRENT'" not in chunk
    assert "CURRENT PERIOD METRICS" not in chunk


def test_runtime_metrics_and_research_facts_are_separate_and_fail_unverified():
    expression = "pathwayTileFacts(" + json.dumps({
        "server_ts": "2026-09-14T12:00:00Z",
        "price_ts": "2026-09-14T11:59:58Z",
        "fresh_epoch_id": "epoch-live",
    }) + "," + json.dumps({"session_stats_source": "analyzer"}) + "," + json.dumps({
        "scope": "SIGNED_FRESH_EPOCH",
    }) + "," + json.dumps({"current": True, "start": 1789000000}) + ",{})"
    facts = _run_pathway_helper(expression)
    assert facts["runtime_response"] != "UNAVAILABLE"
    assert facts["runtime_market"] != "UNAVAILABLE"
    assert facts["metrics_source"] == "SIGNED PAPER LEDGER"
    assert facts["metrics_period"] == "ACTIVE SETTINGS PERIOD"
    assert facts["metrics_epoch"] == "epoch-live"
    assert facts["metrics_source_observation"] == "UNAVAILABLE"
    assert facts["metrics_freshness"] == "FRESHNESS UNVERIFIED"
    assert facts["report_publication"] == "UNAVAILABLE"
    assert facts["report_identity"] == "UNAVAILABLE"
    assert facts["report_qualification"] == "UNAVAILABLE"
    assert facts["report_freshness"] == "FRESHNESS UNVERIFIED"


def test_legacy_and_malformed_timestamps_never_gain_freshness():
    facts = _run_pathway_helper(
        "pathwayTileFacts({server_ts:'bad',price_ts:null},"
        "{session_scope:'ALL_HISTORY'},"
        "{metrics_source:'LEGACY LEDGER',metrics_freshness_status:'HISTORICAL'},"
        "null,{status:'PROFITABLE_IN_ANALYZER_HYPOTHESIS_MODEL'})"
    )
    assert facts["runtime_response"] == "UNAVAILABLE"
    assert facts["runtime_market"] == "UNAVAILABLE"
    assert facts["metrics_period"] == "ALL_HISTORY"
    assert facts["metrics_freshness"] == "HISTORICAL/STALE"
    assert facts["report_freshness"] == (
        "HISTORICAL/STALE · DIAGNOSTIC · FRESHNESS UNVERIFIED"
    )


def test_tile_timestamp_contract_rejects_each_bad_type_and_invalid_iso():
    bad_values = [
        None, True, False, {}, [], 1789000000,
        "2026-09-14T12:00:00",
        "2026-02-30T12:00:00Z",
        "2026-09-14T25:00:00Z",
        "2026-09-14T12:00:00+15:00",
    ]
    expression = (
        json.dumps(bad_values)
        + ".map(value => pathwayIsoTimestamp(value))"
    )
    assert _run_pathway_helper(expression) == [None] * len(bad_values)
    assert _run_pathway_helper(
        "[pathwayIsoTimestamp('2026-09-14T12:00:00Z'),"
        "pathwayIsoTimestamp('2026-09-14T12:00:00+10:00'),"
        "pathwayTimestamp(1789000000,false).valid,"
        "pathwayTimestamp(1789000000,true).valid]"
    ) == ["2026-09-14T12:00:00Z", "2026-09-14T12:00:00+10:00", False, True]


def test_tile_revision_and_epoch_contract_rejects_bad_identifiers():
    assert _run_pathway_helper(
        "[pathwayFullRevision('" + "a" * 40 + "'),"
        "pathwayFullRevision('short'),pathwayFullRevision('" + "g" * 40 + "'),"
        "pathwayFullRevision(true),pathwayFullRevision({}),"
        "pathwayEpochId('epoch-current'),pathwayEpochId('current'),"
        "pathwayEpochId(true),pathwayEpochId({})]"
    ) == ["a" * 40, None, None, None, None, "epoch-current", None, None, None]


def test_tile_parity_and_complete_provenance_control_current_badge():
    report = {
        "freshness_status": "CURRENT",
        "report_published_at": "2026-09-14T11:30:00Z",
        "dataset_source_revision": "b" * 40,
        "epoch_id": "epoch-current",
        "qualification": "NOT_QUALIFIED",
        "source_revision_parity": "MATCH",
    }
    expression = "pathwayTileFacts({}, {}, {}, null, " + json.dumps(report) + ")"
    facts = _run_pathway_helper(expression)
    assert facts["report_freshness"] == "CURRENT"
    assert facts["report_qualification"] == "NOT_QUALIFIED"

    for parity in (None, "UNKNOWN", "MATCH "):
        report["source_revision_parity"] = parity
        facts = _run_pathway_helper(
            "pathwayTileFacts({}, {}, {}, null, " + json.dumps(report) + ")"
        )
        assert facts["report_freshness"] == "FRESHNESS UNVERIFIED"
    for parity in ("MISMATCH", "CONFLICT"):
        report["source_revision_parity"] = parity
        facts = _run_pathway_helper(
            "pathwayTileFacts({}, {}, {}, null, " + json.dumps(report) + ")"
        )
        assert facts["report_freshness"] == "HISTORICAL/STALE"

    report["source_revision_parity"] = "MATCH"
    for field, value in (
        ("report_published_at", "2026-09-14T11:30:00"),
        ("dataset_source_revision", "b" * 12),
        ("epoch_id", 7),
    ):
        malformed = {**report, field: value}
        facts = _run_pathway_helper(
            "pathwayTileFacts({}, {}, {}, null, " + json.dumps(malformed) + ")"
        )
        assert facts["report_freshness"] == "FRESHNESS UNVERIFIED"


def test_legacy_hypothesis_cannot_promote_itself_with_current_flags():
    diagnostic = {
        "status": "PROFITABLE_IN_ANALYZER_HYPOTHESIS_MODEL",
        "freshness_status": "CURRENT",
        "report_published_at": "2026-09-14T11:30:00Z",
        "dataset_source_revision": "c" * 40,
        "epoch_id": "epoch-current",
        "qualification": "QUALIFIED",
        "source_revision_parity": "MATCH",
    }
    facts = _run_pathway_helper(
        "pathwayTileFacts({}, {}, {}, null, " + json.dumps(diagnostic) + ")"
    )
    assert facts["report_freshness"] == (
        "HISTORICAL/STALE · DIAGNOSTIC · FRESHNESS UNVERIFIED"
    )
    assert facts["report_qualification"] == "DIAGNOSTIC ONLY · NOT QUALIFIED"


def test_true_zero_is_distinct_from_unavailable_for_tile_money():
    assert _run_pathway_helper(
        "[pathwayMetricAvailable({},'pnl',0),"
        "pathwayMetricAvailable({pnl:false},'pnl',0),"
        "pathwayMetricNumber(0),pathwayMetricNumber(null)]"
    ) == [True, False, 0, None]
    chunk = _render_chunk()
    assert "result.oos_net_usd || 0" not in chunk
    assert "headlinePnlRaw" in chunk
    assert "headlinePnlLabel" in chunk
    assert "diagnostic net ' + (resultNet == null ? 'Unavailable'" in chunk


def test_server_is_authoritative_for_execution_gate_controls():
    assert "navigator.sendBeacon('/api/set_chase_buckets'" not in SOURCE
    assert "navigator.sendBeacon('/api/set_spread_gate'" not in SOURCE
    assert "await post('/api/set_chase_buckets', {buckets: prefs.chase_execution_buckets})" not in SOURCE
    assert "await post('/api/set_spread_gate', {gate: prefs.spread_gate})" not in SOURCE
    assert "Execution settings are server-owned" in SOURCE
    assert "_patch_api_state_cache_fields(\n        chase_execution_buckets=out" in SOURCE
    assert "_patch_api_state_cache_fields(\n        spread_gate=out" in SOURCE


def test_paper_tile_banner_reports_each_relay_blocker_truthfully():
    assert '"BLOCKED_UNQUALIFIED": "strategy is not qualified"' in SOURCE
    assert '"BLOCKED_PARTIAL_REDUCTION_UNPROVEN": (' in SOURCE
    assert '"BLOCKED_INITIAL_STOP_SWEEP_REQUIRED": (' in SOURCE
    assert "live copy blocked until partial-close relay support is verified" not in SOURCE


def test_virtual_chase_candidates_are_separate_from_pending_orders():
    assert '<tbody id="virtualChaseTable"></tbody>' in SOURCE
    # Danish decision 6 (2026-08-01) — virtual candidates expose the full
    # 12-field transparency set and never appear as exchange pending orders.
    assert "These are <strong>not pending orders</strong>" in SOURCE
    assert "WAITING_VIRTUAL_CHASE" in SOURCE
    assert "REAL_LIMIT_PENDING" in SOURCE
    assert "VIRTUAL_TOUCH_BEFORE_SELECTED_ENTRY" in SOURCE
    assert "deterministic 0.1% offset" in SOURCE
    assert "next_enabled_chase" in SOURCE
    assert "No live virtual-chase candidate right now" in SOURCE
    # Exchange order id column only appears AFTER a real order exists.
    assert "<th>Exchange order ID</th>" in SOURCE
    # The old ambiguous "VIRTUAL ONLY" cell text was replaced with explicit
    # state + no-order reason columns.
    assert "VIRTUAL ONLY" not in SOURCE


def test_settings_period_approvals_reconcile_to_analyzer_headline():
    namespace = {}
    start = SOURCE.index("def _reconcile_settings_periods_to_headline")
    end = SOURCE.index("\ndef spread_gate_allows", start)
    exec("import copy\n" + SOURCE[start:end], namespace)
    reconcile = namespace["_reconcile_settings_periods_to_headline"]
    rows = reconcile(
        {"approves": 1375},
        [
            {
                "settings_recorded": False,
                "approvals": 1377,
                "pnl_usd": 30.39,
            },
            {
                "settings_recorded": True,
                "approvals": 0,
                "pnl_usd": 0,
            },
        ],
    )
    assert sum(row["approvals"] for row in rows) == 1375
    assert rows[0]["approvals"] == 1375
    assert rows[0]["ev_per_approval"] == 0.02


if __name__ == "__main__":
    test_tile_headlines_use_one_identical_six_metric_contract()
    test_tile_headlines_always_use_executed_fresh_collection_metrics()
    test_tile_ev_is_unavailable_when_there_are_no_approvals()
    test_trade_rows_distinguish_observed_loss_from_stop_trigger_reference()
    test_settings_periods_are_durable_and_attached_to_both_payload_paths()
    test_server_is_authoritative_for_execution_gate_controls()
    test_virtual_chase_candidates_are_separate_from_pending_orders()
    test_settings_period_approvals_reconcile_to_analyzer_headline()
    print("tile summary/settings-period regression checks passed")
