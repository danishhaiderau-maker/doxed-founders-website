"""Regression checks for the isolated Fly bot startup contract."""

from pathlib import Path


ROOT = Path(__file__).parent
WRAPPER = (ROOT / "btc_conservative_agent.py").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "fly-entrypoint.sh").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")
FLY_CONFIG = (ROOT / "fly.toml").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_container_revision_does_not_require_git_checkout_depth():
    assert 'os.getenv("SOURCE_GIT_REV")' in WRAPPER
    assert "for candidate in (service_dir, *service_dir.parents)" in WRAPPER
    assert "ARG SOURCE_GIT_REV=unknown" in DOCKERFILE
    assert "ENV SOURCE_GIT_REV=${SOURCE_GIT_REV}" in DOCKERFILE


def test_restart_loop_declares_the_shell_features_it_uses():
    assert ENTRYPOINT.startswith("#!/bin/bash\n")
    assert "PIPESTATUS[0]" in ENTRYPOINT


def test_fly_standby_has_no_implicit_production_relay():
    assert 'if not os.getenv("FLY_APP_NAME"):' in WRAPPER
    assert "SHOWCASE_RELAY_WEBHOOK_URL" in WRAPPER


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
    assert 'path = "/ready"' in FLY_CONFIG
    assert 'policy = "always"' in FLY_CONFIG


if __name__ == "__main__":
    test_container_revision_does_not_require_git_checkout_depth()
    test_restart_loop_declares_the_shell_features_it_uses()
    test_fly_standby_has_no_implicit_production_relay()
    test_fly_image_does_not_bake_laptop_runtime_json()
    test_fly_has_strict_readiness_and_restart_contract()
    print("Fly startup contract checks passed")
