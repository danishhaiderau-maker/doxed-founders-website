from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "fly-bot-monitor.yml"


def test_monitor_uses_health_only_for_liveness():
    text = WORKFLOW.read_text(encoding="utf-8")

    # /health is deliberately a small lock-free liveness contract. Runtime
    # readiness, tile roster, and strategy progress belong to /ready.
    assert text.count('"https://doxed-btc-bot.fly.dev/health"') == 2
    assert text.count("require_health(") == 2


def test_monitor_keeps_strict_readiness_separate_from_liveness():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '"https://doxed-btc-bot.fly.dev/ready"' in text
    assert "require_ready(" in text
    assert "require_tile_registry(" in text


def test_monitor_tracks_master_and_latest_successful_deploy():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "ref: master" in text
    assert "resolve_deployed_revision(" in text
    assert "require_deployed_revision(" in text
