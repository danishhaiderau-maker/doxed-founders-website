"""Static parity checks for Fly deploy and revision-monitor path semantics."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")
MONITOR = (ROOT / ".github/workflows/fly-bot-monitor.yml").read_text(encoding="utf-8")
TEST_PATH = "services/btc-conservative-agent/test*.py"


def test_test_only_changes_are_excluded_from_deploy_and_expected_revision():
    assert f'- "!{TEST_PATH}"' in DEPLOY
    assert f"':(exclude,glob){TEST_PATH}'" in MONITOR


def test_runtime_and_deploy_contract_changes_remain_revision_relevant():
    for path in (
        "services/btc-conservative-agent/**",
        "scripts/check-relay-flat.mjs",
        ".github/workflows/fly-bot-deploy.yml",
    ):
        assert path in DEPLOY
        assert path.replace("/**", "") in MONITOR


def test_stalled_runtime_recovery_is_bound_to_guarded_receipts_and_durable_flatness():
    assert "recover-stalled-runtime" in DEPLOY
    assert "inputs.mode == 'recover-stalled-runtime'" in DEPLOY
    assert "Stalled runtime recovery anchored to guarded deployment" in DEPLOY
    assert 'run.get("conclusion") != "success"' in DEPLOY
    assert '"Deploy the exact source revision"' in DEPLOY
    assert '"Prove liveness, execution safety, and exact revision"' in DEPLOY
    assert 'DURABLE_RELAYS_ONLY_RECOVERY: "YES"' in DEPLOY
    assert 'REQUIRE_CANONICAL_FLY_OWNER: "NO"' in DEPLOY


def test_deploy_uses_durable_pause_flat_deploy_accept_resume_boundary():
    pause = DEPLOY.index("- name: Enter durable authenticated paper maintenance boundary")
    flat = DEPLOY.index("- name: Prove the current Fly owner and every relay account are flat")
    predeploy = DEPLOY.index("- name: Recheck maintenance boundary immediately before deploy")
    deploy = DEPLOY.index("- name: Deploy the exact source revision")
    accept = DEPLOY.index("- name: Prove liveness, execution safety, and exact revision")
    resume = DEPLOY.index("- name: Resume paper execution after exact-revision acceptance")
    assert pause < flat < predeploy < deploy < accept < resume
    assert 'status.get("manual_admin_pause") is True' in DEPLOY
    assert 'payload.get("manual_admin_pause") is True' in DEPLOY
    assert 'str(status.get("source_git_rev") or "").startswith(expected)' in DEPLOY
    assert 'status.get("manual_admin_pause") is False' in DEPLOY
    assert 'status.get("live_armed") is False' in DEPLOY
    assert 'status.get("bitfinex_live_enabled") is False' in DEPLOY
    assert 'request_json("/api/orders/cancel", {"trade_id": trade_id})' in DEPLOY
    assert 'request_json("/api/reconcile/phantom-cancel"' in DEPLOY


def test_failed_deploy_has_bounded_safe_paper_resume_without_weakening_live_flags():
    failure_resume = DEPLOY.index("- name: Best-effort safe paper resume after failed guarded deploy")
    assert failure_resume > DEPLOY.index("- name: Resume paper execution after exact-revision acceptance")
    block = DEPLOY[failure_resume:]
    assert "if: failure()" in block
    assert "continue-on-error: true" in block
    assert 'status.get("force_paper_mode") is True' in block
    assert 'status.get("live_armed") is False' in block
    assert 'status.get("bitfinex_live_enabled") is False' in block
    assert 'status.get("manual_admin_pause") is True' in block


def test_admin_pause_is_persisted_before_guarded_deploy_can_continue():
    bot = (ROOT / "services/btc-conservative-agent/bot.py").read_text(encoding="utf-8")
    pause_body = bot[bot.index("def api_pause():"):bot.index("_RESUMABLE_PAUSE_REASONS")]
    assert pause_body.index('state["manual_admin_pause"] = True') < pause_body.index('_disarm_live_control("ADMIN_MANUAL")')
    disarm = bot[bot.index("def _disarm_live_control"):bot.index("@app.route('/api/exchange_exposure_audit'")]
    assert "save_persistent_config()" in disarm
