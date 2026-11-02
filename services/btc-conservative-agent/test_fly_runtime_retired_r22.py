"""R22 disable-in-place: Fly-era workflows must not spend Actions or poke a dead app.

RK PASS 01:21 AEDT: disable-not-delete. Fly app doxed-btc-bot DELETED 2026-11-03
01:12 AEDT (OBSERVED). R50(e)/R50c — this change must reduce spend (no new paid
services, no Fly wake).

RK HOLD 02:14 AEDT on #969: bitfinex-production-gate.yml must KEEP pull_request
+ push and must NOT gate bitfinex-policy with if: false (policy check for all PRs).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
monitor_run = importlib.import_module("fly_monitor_run")

WORKFLOWS = ROOT / ".github" / "workflows"

# Fly-era Actions only. bitfinex-production-gate is intentionally excluded —
# it enforces Live-OFF / production lock on every PR (RK HOLD 02:14 AEDT).
RETIRED_FLY_WORKFLOWS = (
    "fly-bot-deploy.yml",
    "fly-bot-monitor.yml",
    "fly-monitor-ci.yml",
    "rearm-live-copy.yml",
)


def _on_block(text: str) -> str:
    start = text.index("\non:")
    for marker in ("\npermissions:", "\njobs:", "\nenv:", "\nconcurrency:"):
        if marker in text[start:]:
            end = text.index(marker, start)
            return text[start:end]
    raise AssertionError("could not bound on: block")


def test_retired_fly_workflow_files_still_exist_disable_not_delete():
    for name in RETIRED_FLY_WORKFLOWS:
        path = WORKFLOWS / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        assert "DISABLED IN PLACE" in text
        assert "disable-not-delete" in text
        assert "R22" in text


def test_fly_bot_deploy_has_no_push_and_every_job_is_false_gated():
    text = (WORKFLOWS / "fly-bot-deploy.yml").read_text(encoding="utf-8")
    on_block = _on_block(text)
    assert "\n  push:" not in on_block
    assert "workflow_dispatch:" in on_block
    assert "FORMER push auto-deploy REMOVED" in text
    # 24 top-level jobs — each hard-gated.
    assert text.count("\n    if: false && (") == 24
    assert "flyctl" in text  # body kept; jobs never run


def test_fly_bot_monitor_has_no_cron_and_job_is_false_gated():
    text = (WORKFLOWS / "fly-bot-monitor.yml").read_text(encoding="utf-8")
    on_block = _on_block(text)
    assert "schedule:" not in on_block
    assert "cron:" not in on_block
    assert "workflow_dispatch:" in on_block
    assert "\n    if: false" in text
    assert "python scripts/fly_monitor_run.py" in text


def test_fly_monitor_ci_and_rearm_are_false_gated():
    for name in ("fly-monitor-ci.yml", "rearm-live-copy.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        on_block = _on_block(text)
        assert "\n  push:" not in on_block
        assert "pull_request:" not in on_block
        assert "workflow_dispatch:" in on_block
        assert "\n    if: false" in text


def test_bitfinex_production_gate_still_runs_on_every_pr():
    """RK HOLD 02:14 AEDT preferred fix (1): keep bitfinex-policy on pull_request."""
    text = (WORKFLOWS / "bitfinex-production-gate.yml").read_text(encoding="utf-8")
    on_block = _on_block(text)
    assert "pull_request:" in on_block
    assert "\n  push:" in on_block
    assert "\n    if: false" not in text
    assert "verify-bitfinex-production-lock.mjs" in text
    assert "verify-bitfinex-policy-lock.mjs" in text
    assert "signal_probe.py --full" in text
    # No Fly deploy/monitor poke in this gate.
    assert "flyctl" not in text
    assert "doxed-btc-bot.fly.dev" not in text


def test_fly_monitor_runner_refuses_probes_when_retired(monkeypatch, capsys):
    assert monitor_run.FLY_RUNTIME_RETIRED is True
    # URLs remain for static contract tests; runtime must not hit them.
    assert "doxed-btc-bot.fly.dev" in monitor_run.HEALTH_URL

    called = {"probe": 0}

    def boom(*_a, **_k):
        called["probe"] += 1
        raise AssertionError("probe must not run when FLY_RUNTIME_RETIRED")

    monkeypatch.setattr(monitor_run, "probe", boom)
    assert monitor_run.main() == 0
    out = capsys.readouterr().out
    assert "FLY_RUNTIME_RETIRED" in out
    assert called["probe"] == 0
