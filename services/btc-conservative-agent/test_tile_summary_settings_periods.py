"""Static regression checks for the two-tile accounting contract."""

from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _render_chunk() -> str:
    start = SOURCE.index("function renderPathwayLab")
    end = SOURCE.index("function renderPathwayScorecard", start)
    return SOURCE[start:end]


def test_tile_headlines_use_one_identical_four_metric_contract():
    chunk = _render_chunk()
    for label in ("Status", "Executed", "PnL", "EV/appr"):
        assert f"statRow('{label}'" in chunk
    assert "statRow('Win%'" not in chunk
    assert "Counterfactual closes" not in chunk
    assert "activeGrid" not in chunk


def test_tile_headlines_always_use_executed_fresh_collection_metrics():
    chunk = _render_chunk()
    assert "stats.real_fills" in chunk
    assert "stats.net_pnl_real" in chunk
    assert "stats.per_approve_ev" in chunk
    assert "labPrimaryTrades" not in chunk
    assert "v2ChkPass" not in chunk


def test_settings_periods_are_durable_and_attached_to_both_payload_paths():
    assert 'EXECUTION_SETTINGS_HISTORY_FILE = "execution_settings_history.jsonl"' in SOURCE
    assert '_record_execution_settings_epoch("CHASE_CHANGED")' in SOURCE
    assert '_record_execution_settings_epoch("GAP_CHANGED")' in SOURCE
    assert '_record_execution_settings_epoch("TRACKING_STARTED")' in SOURCE
    assert '_record_execution_settings_epoch("FRESH_COLLECTION_STARTED", force=True)' in SOURCE
    assert SOURCE.count('["settings_periods"] = copy.deepcopy(') == 2
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


if __name__ == "__main__":
    test_tile_headlines_use_one_identical_four_metric_contract()
    test_tile_headlines_always_use_executed_fresh_collection_metrics()
    test_settings_periods_are_durable_and_attached_to_both_payload_paths()
    test_server_is_authoritative_for_execution_gate_controls()
    print("tile summary/settings-period regression checks passed")
