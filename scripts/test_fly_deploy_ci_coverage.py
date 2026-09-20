"""Pin the focused safety suites required by the guarded Fly deploy job."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "fly-bot-deploy.yml"


def _focused_gate() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index(
        "      - name: Verify transactional and local research safety regressions"
    )
    end = text.index("      - name: Verify canonical signal-engine parity", start)
    return text[start:end]


def test_guarded_deploy_runs_complete_focused_safety_matrix():
    gate = _focused_gate()
    required = (
        "test_transactional_receipt_store.py",
        "test_execution_admission_pause_truth.py",
        "test_reset_inventory_invalidation.py",
        "test_relay_owner_delivery_plan.py",
        "test_chase_analytics_truth.py",
        "test_local_fresh_collection.py",
        "test_local_fresh_collection_integration.py",
        "test_local_research_reset.py",
        "test_local_research_reset_cli.py",
        "test_local_research_reset_audit.py",
        "test_local_reset_dashboard.py",
        "test_fresh_collection_signals.py",
        "test_local_reset_http_listener.py",
    )
    for test_file in required:
        assert gate.count(test_file) == 1, test_file


def test_analyzer_fixture_is_isolated_and_windows_receipt_remains_explicit():
    gate = _focused_gate()
    assert "BTC_AGENT_DATA_DIR: ${{ runner.temp }}/btc-agent-data-ci" in gate
    assert 'mkdir -p "$BTC_AGENT_DATA_DIR"' in gate
    assert "separate Windows HttpListener receipt" in gate
    assert "actions/setup-node" not in gate
