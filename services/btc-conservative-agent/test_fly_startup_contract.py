"""Regression checks for the isolated Fly bot startup contract."""

from pathlib import Path


ROOT = Path(__file__).parent
WRAPPER = (ROOT / "btc_conservative_agent.py").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "fly-entrypoint.sh").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")
FLY_CONFIG = (ROOT / "fly.toml").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
PUSHER = (ROOT / "fly_relay_state_pusher.py").read_text(encoding="utf-8")


def test_container_revision_does_not_require_git_checkout_depth():
    assert 'os.getenv("SOURCE_GIT_REV")' in WRAPPER
    assert "for candidate in (service_dir, *service_dir.parents)" in WRAPPER
    assert "ARG SOURCE_GIT_REV=unknown" in DOCKERFILE
    assert "ENV SOURCE_GIT_REV=${SOURCE_GIT_REV}" in DOCKERFILE


def test_restart_loop_declares_the_shell_features_it_uses():
    assert ENTRYPOINT.startswith("#!/bin/bash\n")
    assert "PIPESTATUS[0]" in ENTRYPOINT


def test_fly_standby_has_no_implicit_production_relay():
    assert '_fly_app != "doxed-btc-bot"' in WRAPPER
    assert 'os.getenv("FLY_MACHINE_ID")' in WRAPPER
    assert 'os.getenv("FLY_REGION")' in WRAPPER
    assert "REFUSED_NON_FLY_RUNTIME" in WRAPPER
    assert 'os.environ["HOME_BOT_LOCAL"] = "0"' in WRAPPER
    assert 'os.environ["HOME_RESEARCH_FULL"] = "0"' in WRAPPER
    assert 'os.environ.setdefault("BLOCK_RESEARCH_WAREHOUSE", "1")' in WRAPPER
    assert 'os.environ["DASHBOARD_PUBLIC_URL"] = "https://doxed-btc-bot.fly.dev/"' in WRAPPER
    assert '"https://doxed-btc-bot.fly.dev/analysis"' in WRAPPER
    assert "raise SystemExit(78)" in WRAPPER


def test_fly_refuses_missing_control_secrets_or_direct_live_mode():
    for required in (
        "BOT_ADMIN_TOKEN",
        "BOT_CONTROL_SECRET",
        "SHOWCASE_WEBHOOK_SECRET",
        "SHOWCASE_RELAY_WEBHOOK_URL",
    ):
        assert f'"{required}"' in WRAPPER
    assert "REFUSED_MISSING_FLY_CONTROL" in WRAPPER
    assert 'os.getenv("FORCE_PAPER_MODE")' in WRAPPER
    assert "REFUSED_DIRECT_FLY_LIVE" in WRAPPER
    assert "Railway is the isolated Bitfinex live executor" in WRAPPER
    assert "return _is_direct_local_control(_client_ip())" in BOT
    assert "return True  # gate disabled" not in BOT
    assert 'os.environ.setdefault(' in WRAPPER
    assert '"SHOWCASE_INFERENCE_USAGE_URL"' in WRAPPER
    assert (
        '"https://doxxedcrypto.digital/api/internal/showcase-inference-usage"'
        in WRAPPER
    )


def test_fly_image_does_not_bake_laptop_runtime_json():
    assert "!*.json" not in DOCKERIGNORE
    assert "!manifest.json" in DOCKERIGNORE
    assert "!genome_cluster_library.json" in DOCKERIGNORE
    for runtime_name in (
        "config-7002.json",
        "research_session.json",
        "lane_pnl_ledger.json",
        "open_positions.json",
    ):
        assert f"!{runtime_name}" not in DOCKERIGNORE


def test_fly_has_strict_readiness_and_restart_contract():
    assert "@app.route('/ready')" in BOT
    assert '"force_paper_mode": force_paper_mode' in BOT
    assert '"relay_configured": relay_configured' in BOT
    # Fly routing proves process liveness. The separate /ready route remains
    # strict strategy readiness and may intentionally return 503 while the
    # market is quiet or execution is gated.
    assert 'path = "/health"' in FLY_CONFIG
    assert 'path = "/ready"' not in FLY_CONFIG
    assert 'policy = "always"' in FLY_CONFIG
    assert "k: state.get(k)" in BOT


def test_fly_publishes_one_authenticated_canonical_snapshot():
    assert "python /app/fly_relay_state_pusher.py" in ENTRYPOINT
    assert "BOT_CONTROL_SECRET" in ENTRYPOINT
    assert "/api/relay-state" in PUSHER
    assert "/api/internal/showcase-snapshot" in PUSHER
    assert '"X-Bot-Control-Secret": CONTROL_SECRET' in PUSHER
    assert '"snapshot_seq": seq' in PUSHER


if __name__ == "__main__":
    test_container_revision_does_not_require_git_checkout_depth()
    test_restart_loop_declares_the_shell_features_it_uses()
    test_fly_standby_has_no_implicit_production_relay()
    test_fly_refuses_missing_control_secrets_or_direct_live_mode()
    test_fly_image_does_not_bake_laptop_runtime_json()
    test_fly_has_strict_readiness_and_restart_contract()
    test_fly_publishes_one_authenticated_canonical_snapshot()
    print("Fly startup contract checks passed")
