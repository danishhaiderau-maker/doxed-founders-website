"""Static regression checks for the registry-tile accounting contract."""

from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _render_chunk() -> str:
    start = SOURCE.index("function renderPathwayLab")
    end = SOURCE.index("function renderPathwayScorecard", start)
    return SOURCE[start:end]


def test_tile_headlines_use_one_identical_seven_metric_contract():
    chunk = _render_chunk()
    for label in ("Status", "Pending", "Open", "Closed", "PnL", "EV/appr", "Win %"):
        assert f"statRow('{label}'" in chunk
    assert "grid-template-columns:repeat(7,1fr)" in chunk
    assert "statRow('Executed'" not in chunk
    assert "statRow('Win%'" not in chunk
    assert "Counterfactual closes" not in chunk
    assert "activeGrid" not in chunk


def test_tile_headlines_always_use_executed_fresh_collection_metrics():
    chunk = _render_chunk()
    assert "const headlineClosed = Number(stats.real_fills || 0)" in chunk
    assert "const headlineApprovals = Number(stats.approves || 0)" in chunk
    assert "headlinePnl" in chunk
    assert "headlineEv" in chunk
    assert "Headline scope: " in chunk
    assert "Clean-epoch headline: n=" in chunk
    assert "labPrimaryTrades" not in chunk
    assert "v2ChkPass" not in chunk


def test_tile_ev_is_unavailable_when_there_are_no_approvals():
    chunk = _render_chunk()
    assert "const headlineEv = headlineApprovals > 0" in chunk
    assert "(headlineEv == null ? '\u2014'" in chunk
    # Unknown approvals (no report / counters younger than the epoch) read n/a.
    assert "const headlineEvLabel = stats.approvals_known === false" in chunk
    assert "(stats.approvals_known === false ? 'n/a' : headlineApprovals)" in chunk
    assert "statRow('EV/appr', headlineEvLabel)" in chunk
    assert "EV ' + headlineEvLabel + '/approve" in chunk
    assert "headlineApprovals ? headlinePnl / headlineApprovals : 0" not in chunk


def test_win_pct_counts_net_wins_over_closed_trades():
    chunk = _render_chunk()
    assert "const headlineWinLabel = winPctLabel(stats.wins, stats.losses, headlineClosed)" in chunk
    assert "statRow('Win %', headlineWinLabel)" in chunk


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


def test_legacy_settings_period_breakdown_is_retired():
    # The audit trail stays on disk; the synthetic "Legacy baseline" table and
    # its approvals back-fill are gone from the API and the tile card.
    assert 'EXECUTION_SETTINGS_HISTORY_FILE = "execution_settings_history.jsonl"' in SOURCE
    assert '_record_execution_settings_epoch("FRESH_COLLECTION_STARTED", force=True)' in SOURCE
    for retired in ("_settings_period_breakdown", "_reconcile_settings_periods_to_headline",
                    '["settings_periods"]', "_settings_breakdown_cache"):
        assert retired not in SOURCE
    chunk = _render_chunk()
    for retired in ("Settings-period breakdown", "Legacy baseline", "Not recorded",
                    "settings_periods", "currentSettingsPeriod"):
        assert retired not in chunk


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


if __name__ == "__main__":
    test_tile_headlines_use_one_identical_seven_metric_contract()
    test_tile_headlines_always_use_executed_fresh_collection_metrics()
    test_tile_ev_is_unavailable_when_there_are_no_approvals()
    test_win_pct_counts_net_wins_over_closed_trades()
    test_trade_rows_distinguish_observed_loss_from_stop_trigger_reference()
    test_legacy_settings_period_breakdown_is_retired()
    test_server_is_authoritative_for_execution_gate_controls()
    test_paper_tile_banner_reports_each_relay_blocker_truthfully()
    test_virtual_chase_candidates_are_separate_from_pending_orders()
    print("tile summary regression checks passed")
