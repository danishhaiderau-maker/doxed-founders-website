from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "fly-bot-monitor.yml"
RUNNER = ROOT / "scripts" / "fly_monitor_run.py"


def test_monitor_uses_health_only_for_liveness():
    text = RUNNER.read_text(encoding="utf-8")

    # /health is deliberately a small lock-free liveness contract. Runtime
    # readiness, tile roster, and strategy progress belong to /ready.
    assert 'HEALTH_URL = "https://doxed-btc-bot.fly.dev/health"' in text
    assert '"PROCESS_LIVENESS_ONLY"' in text
    assert "strategy_progress" not in text[text.index("health: dict"):text.index("paused_for =")]


def test_monitor_keeps_strict_readiness_separate_from_liveness():
    text = RUNNER.read_text(encoding="utf-8")

    assert 'READY_URL = "https://doxed-btc-bot.fly.dev/ready"' in text
    assert "require_strategy_progress(payload)" in text
    assert "require_tile_registry(" in text


def test_monitor_tracks_master_and_latest_successful_deploy():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    text = RUNNER.read_text(encoding="utf-8")

    assert "ref: master" in workflow
    assert "python scripts/fly_monitor_run.py" in workflow
    assert "resolve_deployed_revision(" in text
    assert "require_deployed_revision(" in text


def test_monitor_failures_are_deduplicated_through_one_incident_issue():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    text = RUNNER.read_text(encoding="utf-8")

    assert "issues: write" in workflow
    assert "actions/cache/restore@v4" in workflow and "actions/cache/save@v4" in workflow
    assert "if: always()" in workflow
    assert 'INCIDENT_LABEL = "fly-monitor-incident"' in text
    assert 'return 1 if any(d["action"] == "alert" for d in decisions) else 0' in text
