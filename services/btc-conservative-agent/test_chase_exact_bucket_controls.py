from pathlib import Path


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def test_exact_chase_controls_are_rendered():
    expected = (
        "0_chases",
        "1_chase",
        "2_chases",
        "3_chases",
        "4_chases",
        "5+_chases",
    )
    for bucket in expected:
        assert f"'{bucket}'" in BOT_SOURCE or f'"{bucket}"' in BOT_SOURCE
    assert "select 3 and 4 only" in BOT_SOURCE


def test_grouped_chase_controls_are_migration_only():
    assert "def _normalize_chase_execution_buckets(raw)" in BOT_SOURCE
    assert 'if "3-5_chases" in raw:' in BOT_SOURCE
    assert 'if "6+_chases" in raw:' in BOT_SOURCE
    assert "state.get(\"chase_execution_buckets\")" in BOT_SOURCE
    assert "const order = ['0_chases','1_chase','2_chases','3_chases','4_chases','5+_chases'];" in BOT_SOURCE


def test_virtual_wait_and_cancel_paths_remain_wired():
    assert "dashboard_virtual_chase_submit_ready(signal)" in BOT_SOURCE
    assert "_cancel_pending_for_chase_gate(order" in BOT_SOURCE
    assert "process_awaiting_dashboard_virtual_chase_entries()" in BOT_SOURCE
    assert "next_chase_count = int(order.get(\"limit_chase_count\") or 0) + 1" in BOT_SOURCE
    assert "if not chase_bucket_allowed(next_chase_count):" in BOT_SOURCE
    assert "def _virtual_limit_would_fill(signal: dict, market_price: float)" in BOT_SOURCE
    assert "VIRTUAL_FILL_SKIPPED_CHASE_" in BOT_SOURCE


def test_obsolete_confidence_controls_are_not_rendered():
    assert '<div id="aiBandControls"' not in BOT_SOURCE
    assert "<strong>AI execution bands:</strong>" not in BOT_SOURCE
    assert "<h3>Directional gap analytics</h3>" in BOT_SOURCE


def test_gap_analytics_matches_analyzer_matrix_schema():
    assert 'parts[0] not in ("0", "1", "2", "3", "4", "5+")' in BOT_SOURCE
    assert '"normalized directional score gap = abs(LONG score - SHORT score) // 10"' in BOT_SOURCE
    assert "raw gap 30 → execution bucket 3" in BOT_SOURCE
    assert "'3':'bucket 3 (raw gap 30 to 39)'" in BOT_SOURCE
    assert "<th>Raw gap (0–100)</th><th>Execution gap bucket</th>" in BOT_SOURCE
    assert "Math.floor(rawGap / 10)" in BOT_SOURCE
    assert '"""Compatibility wrapper over the one canonical dashboard gap gate."""' in BOT_SOURCE
    assert "return not spread_gate_allows(spread), bucket" in BOT_SOURCE
    assert "updates the same config-7002.json gate as the dashboard" in BOT_SOURCE


def test_market_bid_ask_spread_is_collected_separately():
    assert '"market_bid_ask_spread_usd_at_entry"' in BOT_SOURCE
    assert '"market_bid_ask_spread_bps_at_entry"' in BOT_SOURCE
    assert "the older “conviction spread” is this same normalized AI gap" in BOT_SOURCE
