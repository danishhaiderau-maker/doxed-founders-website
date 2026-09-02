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


def test_monitor_splits_fast_liveness_from_full_readiness_fail_closed():
    assert MONITOR.count('"https://doxed-btc-bot.fly.dev/health"') == 2
    assert MONITOR.count('"https://doxed-btc-bot.fly.dev/ready"') == 2
    assert '"https://doxed-btc-bot.fly.dev/api/status"' not in MONITOR
    assert MONITOR.count("require_health(") == 2
    assert MONITOR.count("require_ready(") == 2
    assert "require_strategy_progress(ready)" in MONITOR
    assert "require_tile_registry(" in MONITOR


def test_stalled_runtime_recovery_is_bound_to_guarded_receipts_and_durable_flatness():
    assert "recover-stalled-runtime" in DEPLOY
    assert "inputs.mode == 'recover-stalled-runtime'" in DEPLOY
    assert "Stalled runtime recovery anchored to guarded deployment" in DEPLOY
    assert 'run.get("conclusion") != "success"' in DEPLOY
    assert '"Deploy the exact source revision"' in DEPLOY
    assert '"Prove liveness, execution safety, and exact revision"' in DEPLOY
    assert 'DURABLE_RELAYS_ONLY_RECOVERY: "YES"' in DEPLOY
    assert 'REQUIRE_CANONICAL_FLY_OWNER: "NO"' in DEPLOY


def test_unready_recovery_uses_guest_agent_flatness_when_http_owner_is_unavailable():
    maintenance = DEPLOY[
        DEPLOY.index("- name: Enter durable authenticated paper maintenance boundary"):
        DEPLOY.index("- name: Prove the current Fly owner and every relay account are flat")
    ]
    assert "inputs.mode != 'recover-unready'" in maintenance
    recovery = DEPLOY[
        DEPLOY.index("- name: Prove exact unready Fly revision and every durable relay account is flat"):
        DEPLOY.index("- name: Prove stalled runtime recovery from the last guarded deployment")
    ]
    assert 'flyctl machine exec --app doxed-btc-bot' in recovery
    assert 're.fullmatch(r"[0-9a-f]{12}", expected)' in recovery
    assert '[[ "$EXPECTED_UNREADY_REVISION" =~ ^[0-9a-f]{12}$ ]]' in recovery
    assert 'FAILED_DEPLOY_COMPLETED_AT="$(cat "$RUNNER_TEMP/failed-deploy-completed-at.txt")"' in recovery
    assert 'GITHUB_ENV' not in recovery
    assert 'os.getenv(\\"SOURCE_GIT_REV\\",\\"\\")[:12]==expected' in recovery
    assert 'p.stat().st_mtime>=threshold' in recovery
    assert 'd.get(\\"paper_only\\") is True and d.get(\\"live_armed\\") is False' in recovery
    assert 'd.get(\\"positions\\")==[] and d.get(\\"pending_orders\\")==[]' in recovery
    assert 'c.get(\\"manual_admin_pause\\") is True' in recovery
    assert 'os.getenv(\\"FORCE_PAPER_MODE\\",\\"\\").lower()==\\"true\\"' in recovery
    assert 'b\\"ModuleNotFoundError\\" in log' in recovery
    assert 'b\\"research.mirror_generation_lease\\" in log' in recovery
    assert 'run.get("name") != "Deploy Fly BTC bot"' in recovery
    assert 'run.get("path") or "").split("@", 1)[0] != ".github/workflows/fly-bot-deploy.yml"' in recovery
    assert 'deploy_jobs = [job for job in jobs if job.get("name") == "test-and-deploy"]' in recovery
    assert 'int(deployed[0]["number"]) < int(failed_acceptance[0]["number"]) < int(preserved[0]["number"])' in recovery
    assert '"Re-enter maintenance and flatten the exact deployed revision"' in recovery
    assert '"Prove liveness, execution safety, and exact revision"' in recovery
    assert '"Best-effort preserve safe paper maintenance after failed guarded deploy"' in recovery
    predeploy = DEPLOY[
        DEPLOY.index("- name: Recheck maintenance boundary immediately before deploy"):
        DEPLOY.index("- name: Deploy the exact source revision")
    ]
    assert "inputs.mode != 'recover-unready'" in predeploy


def test_all_bitfinex_instances_must_be_paused_disarmed_and_reconciled_flat():
    helper = (ROOT / "scripts/check-relay-flat.mjs").read_text(encoding="utf-8")
    assert "const cheetahRows" not in helper
    assert "const cheetah = rows.filter" not in helper
    assert "const relayPausedAndDisarmed = rows.length > 0" in helper
    assert "const reconciledFlat = rows.length > 0" in helper
    assert "&& rows.every(isRelayPausedAndDisarmed)" in helper


def test_deploy_uses_durable_pause_flat_deploy_accept_resume_boundary():
    pause = DEPLOY.index("- name: Enter durable authenticated paper maintenance boundary")
    flat = DEPLOY.index("- name: Prove the current Fly owner and every relay account are flat")
    predeploy = DEPLOY.index("- name: Recheck maintenance boundary immediately before deploy")
    deploy = DEPLOY.index("- name: Deploy the exact source revision")
    postdeploy = DEPLOY.index("- name: Re-enter maintenance and flatten the exact deployed revision")
    accept = DEPLOY.index("- name: Prove liveness, execution safety, and exact revision")
    resume = DEPLOY.index("- name: Resume paper execution after exact-revision acceptance")
    assert pause < flat < predeploy < deploy < postdeploy < accept < resume
    assert 'status.get("manual_admin_pause") is True' in DEPLOY
    assert 'payload.get("manual_admin_pause") is True' in DEPLOY
    assert 'str(status.get("source_git_rev") or "").startswith(expected)' in DEPLOY
    assert 'status.get("manual_admin_pause") is False' in DEPLOY
    assert 'status.get("live_armed") is False' in DEPLOY
    assert 'status.get("bitfinex_live_enabled") is False' in DEPLOY
    assert 'status.get("force_paper_mode") is True' in DEPLOY
    assert 'request_json("/api/orders/cancel", {"trade_id": trade_id})' in DEPLOY
    assert 'request_json("/api/reconcile/phantom-cancel"' in DEPLOY
    assert "pause mutation attempt={attempt}" in DEPLOY
    assert "checking durable state" in DEPLOY


def test_postdeploy_restart_boundary_is_exact_generation_fenced_and_flat():
    block = DEPLOY[
        DEPLOY.index("- name: Re-enter maintenance and flatten the exact deployed revision"):
        DEPLOY.index("- name: Prove liveness, execution safety, and exact revision")
    ]
    exact = block.index('str(status.get("source_git_rev") or "") == expected')
    pause = block.index('request_json("/api/pause", {}, timeout=30)')
    assert exact < pause
    assert 'status.get("execution_paused") is True' in block
    assert 'status.get("manual_admin_pause") is True' in block
    assert 'request_json("/api/orders/cancel", {"trade_id": trade_id})' in block
    assert 'request_json("/api/positions/close", {"trade_id": trade_id})' in block
    assert 'generation <= round_generation' in block
    assert 'fresh_state(required_generation)' in block
    assert 'require_paused=True, require_flat=True' in block
    assert 'if not final["orders"] and not final["positions"]:' in block


def test_generationless_bootstrap_is_bound_to_one_exact_safe_revision():
    maintenance = DEPLOY[
        DEPLOY.index("- name: Enter durable authenticated paper maintenance boundary"):
        DEPLOY.index("- name: Prove the current Fly owner and every relay account are flat")
    ]
    assert 'legacy_bootstrap_revision = "e5e61229871744a062ae75651d3c442bae910b5d"' in maintenance
    assert "legacy_status_revision = legacy_bootstrap_revision[:12]" in maintenance
    assert 'str(legacy_status.get("source_git_rev") or "") == legacy_status_revision' in maintenance
    assert 'str(legacy_status.get("source_git_rev") or "").startswith' not in maintenance
    assert 'legacy_status.get("execution_paused") is True' in maintenance
    assert 'legacy_status.get("manual_admin_pause") is True' in maintenance
    assert 'legacy_status.get("force_paper_mode") is True' in maintenance
    assert 'legacy_status.get("live_armed") is False' in maintenance
    assert 'legacy_status.get("bitfinex_live_enabled") is False' in maintenance
    assert 'raise SystemExit("generation-less relay authority is not the exact safe legacy bootstrap revision")' in maintenance


def test_generationless_bootstrap_requires_repeated_relay_and_status_flatness():
    maintenance = DEPLOY[
        DEPLOY.index("- name: Enter durable authenticated paper maintenance boundary"):
        DEPLOY.index("- name: Prove the current Fly owner and every relay account are flat")
    ]
    assert "def prove_legacy_bootstrap_flat():" in maintenance
    assert "confirmations >= 3" in maintenance
    assert "sequence > last_snapshot_seq" in maintenance
    assert "not orders and not positions and status_flat and sequence_advanced" in maintenance
    assert "open_positions != 0 or pending_orders != 0" in maintenance
    assert maintenance.count("prove_legacy_bootstrap_flat()") == 3


def test_legacy_missing_order_is_accepted_only_before_mandatory_flat_reproof():
    maintenance = DEPLOY[
        DEPLOY.index("- name: Enter durable authenticated paper maintenance boundary"):
        DEPLOY.index("- name: Prove the current Fly owner and every relay account are flat")
    ]
    missing = maintenance.index('if exc.code != 404 or exposure.get("_legacy_exact_revision_bootstrap") is not True:')
    marker = maintenance.index('cancelled = {"status": "not_found"}', missing)
    skip = maintenance.index('if cancelled.get("status") == "not_found":', marker)
    reproof = maintenance.rindex("prove_legacy_bootstrap_flat()")
    assert missing < marker < skip < reproof
    assert "if exc.code != 404 or" in maintenance
    assert "raise" in maintenance[missing:marker]


def test_legacy_flat_proof_ignores_transport_failures_but_not_confirmations():
    maintenance = DEPLOY[
        DEPLOY.index("- name: Enter durable authenticated paper maintenance boundary"):
        DEPLOY.index("- name: Prove the current Fly owner and every relay account are flat")
    ]
    proof = maintenance[
        maintenance.index("def prove_legacy_bootstrap_flat():"):
        maintenance.index("def fresh_exposure", maintenance.index("def prove_legacy_bootstrap_flat():"))
    ]
    assert "for attempt in range(1, 21):" in proof
    assert "except Exception as exc:" in proof
    assert "confirmations = 0" in proof
    assert "continue" in proof
    assert "confirmations >= 3" in proof


def test_generation_remains_mandatory_outside_the_exact_bootstrap():
    maintenance = DEPLOY[
        DEPLOY.index("- name: Enter durable authenticated paper maintenance boundary"):
        DEPLOY.index("- name: Prove the current Fly owner and every relay account are flat")
    ]
    assert 'candidate["_legacy_exact_revision_bootstrap"] = True' in maintenance
    assert 'if minimum_generation is not None:' in maintenance
    assert 'raise SystemExit("relay execution generation disappeared after a fenced mutation")' in maintenance
    assert 'raise SystemExit("relay generation appeared during legacy flat proof; restart with the normal fence")' in maintenance
    assert 'raise SystemExit("maintenance cancel generation is missing")' in maintenance
    assert 'raise SystemExit("maintenance reconciliation generation is missing")' in maintenance


def test_failed_deploy_preserves_pause_and_never_resumes_unaccepted_revision():
    failure_pause = DEPLOY.index("- name: Best-effort preserve safe paper maintenance after failed guarded deploy")
    assert failure_pause > DEPLOY.index("- name: Resume paper execution after exact-revision acceptance")
    block = DEPLOY[failure_pause:]
    assert "if: failure()" in block
    assert "continue-on-error: true" in block
    assert 'base + "/api/pause"' in block
    assert 'base + "/api/resume"' not in block
    assert 'status.get("force_paper_mode") is True' in block
    assert 'status.get("live_armed") is False' in block
    assert 'status.get("bitfinex_live_enabled") is False' in block
    assert 'status.get("execution_paused") is True' in block
    assert 'status.get("manual_admin_pause") is True' in block


def test_admin_pause_is_persisted_before_guarded_deploy_can_continue():
    bot = (ROOT / "services/btc-conservative-agent/bot.py").read_text(encoding="utf-8")
    pause_body = bot[bot.index("def api_pause():"):bot.index("_RESUMABLE_PAUSE_REASONS")]
    assert pause_body.index('state["manual_admin_pause"] = True') < pause_body.index('_disarm_live_control("ADMIN_MANUAL")')
    disarm = bot[bot.index("def _disarm_live_control"):bot.index("@app.route('/api/exchange_exposure_audit'")]
    assert "save_persistent_config()" in disarm
