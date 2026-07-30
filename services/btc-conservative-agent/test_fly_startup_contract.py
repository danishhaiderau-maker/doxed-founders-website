"""Regression checks for the isolated Fly bot startup contract."""

from pathlib import Path


ROOT = Path(__file__).parent
WRAPPER = (ROOT / "btc_conservative_agent.py").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "fly-entrypoint.sh").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


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


if __name__ == "__main__":
    test_container_revision_does_not_require_git_checkout_depth()
    test_restart_loop_declares_the_shell_features_it_uses()
    test_fly_standby_has_no_implicit_production_relay()
    print("Fly startup contract checks passed")
