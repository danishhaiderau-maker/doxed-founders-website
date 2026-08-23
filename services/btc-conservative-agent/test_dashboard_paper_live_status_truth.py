from pathlib import Path


BOT_SOURCE = Path(__file__).with_name("bot.py")


def test_dashboard_separates_paper_entry_and_bitfinex_live_status():
    source = BOT_SOURCE.read_text(encoding="utf-8")

    assert "function renderUltimateGatePanel(gates, runtimeState)" in source
    assert "d.execution_paused !== true" in source
    assert "d.signal_generation_ready === true" in source
    assert "<strong>PAPER ENTRIES:</strong>" in source
    assert "<strong>BITFINEX LIVE:</strong>" in source
    assert "BLOCKED — DISARMED" in source
    assert "renderUltimateGatePanel(d.dashboard_execution_gates || {}, d)" in source
