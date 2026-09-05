from pathlib import Path
import json
import shlex
import subprocess
import sys
from types import SimpleNamespace
import pytest
import fly_bundle_canary_dispatch as dispatch
from fly_bundle_canary_dispatch import remote_command

ROOT = Path(__file__).resolve().parents[1]


def test_dispatch_accepts_only_hex_identity_and_explicit_mode():
    command = remote_command(b"print('bounded')", "a" * 12, "b" * 64, "c" * 64, "1")
    assert command.startswith('python -c "')
    assert "BOT_ADMIN_TOKEN" not in command
    for args in [("a;exit", "b" * 64, "", "1"), ("a" * 12, "b", "", "1"),
                 ("a" * 12, "b" * 64, "../bad", "1"), ("a" * 12, "b" * 64, "", "")]:
        with pytest.raises(ValueError):
            remote_command(b"print('bounded')", *args)


def test_workflow_has_no_deploy_restart_or_trading_action_in_canary_job():
    workflow = (ROOT / ".github/workflows/fly-bot-deploy.yml").read_text()
    block = workflow.split("  bundle-canary:\n", 1)[1].split("  resume-bootstrap:\n", 1)[0]
    assert "inputs.mode == 'bundle-canary'" in block
    assert "timeout-minutes: 5" in block
    assert "python scripts/fly_bundle_canary_dispatch.py" in block
    for forbidden in ("flyctl deploy", "flyctl machine restart", "/api/pause", "/api/resume", "/api/data-sync/ack"):
        assert forbidden not in block
    assert "default: true" in workflow.split("      bundle_inspect_only:\n", 1)[1].split("      expected_", 1)[0]
    assert "cancel-in-progress: false" in workflow


def test_encoded_remote_command_executes_exact_args_without_shell_injection():
    command = remote_command(b"import json,sys; print(json.dumps(sys.argv))", "a" * 12,
                             "b" * 64, "c" * 64, "1")
    args = shlex.split(command)
    result = subprocess.run([sys.executable, *args[1:]], capture_output=True, text=True,
                            timeout=10, check=True)
    assert json.loads(result.stdout) == ["fly_bundle_canary.py", "--expected-revision", "a" * 12,
        "--generation-id", "b" * 64, "--inventory-fingerprint", "c" * 64, "--inspect-only"]


@pytest.mark.parametrize("rows", [[], [{"id": "a" * 14, "state": "starting"}],
    [{"id": "a" * 14, "state": "started"}, {"id": "b" * 14, "state": "started"}]])
def test_no_remote_execution_without_exactly_one_started_owner(monkeypatch, rows):
    calls = []
    monkeypatch.setenv("EXPECTED_BUNDLE_REVISION", "a" * 12)
    monkeypatch.setenv("BUNDLE_GENERATION_ID", "b" * 64)
    monkeypatch.setenv("BUNDLE_INSPECT_ONLY", "1")
    monkeypatch.delenv("BUNDLE_INVENTORY_FINGERPRINT", raising=False)
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(rows).encode(), returncode=0)
    monkeypatch.setattr(dispatch.subprocess, "run", run)
    with pytest.raises(ValueError, match="ONE_STARTED_OWNER_REQUIRED"):
        dispatch.main()
    assert len(calls) == 1 and calls[0][1:3] == ["machines", "list"]


def test_ambiguous_remote_timeout_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setenv("EXPECTED_BUNDLE_REVISION", "a" * 12)
    monkeypatch.setenv("BUNDLE_GENERATION_ID", "b" * 64)
    monkeypatch.setenv("BUNDLE_INSPECT_ONLY", "1")
    monkeypatch.delenv("BUNDLE_INVENTORY_FINGERPRINT", raising=False)
    def run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return SimpleNamespace(stdout=json.dumps([{"id": "a" * 14, "state": "started"}]).encode())
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(dispatch.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        dispatch.main()
    assert len(calls) == 2 and calls[1][1:3] == ["machine", "exec"]
