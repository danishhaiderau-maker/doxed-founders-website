"""Static regression checks for the two-tile accounting contract."""

from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


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
    assert "current execution-settings period; earlier rows remain separate" in chunk
    assert "labPrimaryTrades" not in chunk
    assert "v2ChkPass" not in chunk


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
    assert "Legacy baseline" in chunk
    assert "Not recorded" in chunk


def test_server_is_authoritative_for_execution_gate_controls():
    assert "navigator.sendBeacon('/api/set_chase_buckets'" not in SOURCE
    assert "navigator.sendBeacon('/api/set_spread_gate'" not in SOURCE
    assert "await post('/api/set_chase_buckets', {buckets: prefs.chase_execution_buckets})" not in SOURCE
    assert "await post('/api/set_spread_gate', {gate: prefs.spread_gate})" not in SOURCE
    assert "Execution settings are server-owned" in SOURCE
    assert "_patch_api_state_cache_fields(\n        chase_execution_buckets=out" in SOURCE
    assert "_patch_api_state_cache_fields(\n        spread_gate=out" in SOURCE


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
    test_trade_rows_distinguish_observed_loss_from_stop_trigger_reference()
    test_settings_periods_are_durable_and_attached_to_both_payload_paths()
    test_server_is_authoritative_for_execution_gate_controls()
    test_virtual_chase_candidates_are_separate_from_pending_orders()
    test_settings_period_approvals_reconcile_to_analyzer_headline()
    print("tile summary/settings-period regression checks passed")
