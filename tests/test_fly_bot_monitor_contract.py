from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "fly-bot-monitor.yml"


def test_monitor_uses_health_only_for_liveness_and_status_for_diagnostics():
    text = WORKFLOW.read_text(encoding="utf-8")

    # /health is deliberately a small lock-free liveness contract. Runtime
    # readiness, tile roster, and strategy progress belong to /api/status.
    assert text.count('"https://doxed-btc-bot.fly.dev/health"') == 1
    assert text.count('"https://doxed-btc-bot.fly.dev/api/status"') == 2
    assert 'liveness.get("process_alive") is not True' in text
    assert 'health.get("strategy_progress") or {}' in text


def test_monitor_keeps_strict_readiness_separate_from_liveness():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '"https://doxed-btc-bot.fly.dev/ready"' in text
    assert 'ready.get("ok") is not True' in text
    assert 'ready.get("tile_registry_signature")' in text
