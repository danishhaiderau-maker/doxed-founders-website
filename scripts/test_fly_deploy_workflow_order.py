"""Source contract for the guarded deploy workflow's safety ordering.

These tests verify wiring only, not Actions execution or production recovery.
"""
from pathlib import Path
import itertools
import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")


def test_deploy_step_has_no_transfer_bundle_configuration():
    for retired in ("transport_bundles:", "bundle-canary", "TRANSPORT_BUNDLES", "data_sync_bundle", "data_sync_inventory_worker"):
        assert retired not in WORKFLOW
    deploy = WORKFLOW.split("      - name: Deploy the exact source revision\n", 1)[1].split(
        "      - name:", 1)[0]
    assert "FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}" in deploy
    # No raw workflow expression or arbitrary supplied environment string enters shell.
    run = next(line for line in deploy.splitlines() if "run:" in line)
    assert "inputs." not in run and "${{" not in run


def test_existing_safety_and_exact_revision_gates_remain_in_order():
    steps = [
        "Verify canonical signal-engine parity",
        "Enter durable authenticated paper maintenance boundary",
        "Prove the current Fly owner and every relay account are flat",
        "Recheck maintenance boundary immediately before deploy",
        "Deploy the exact source revision",
        "Re-enter maintenance and flatten the exact deployed revision",
    ]
    offsets = [WORKFLOW.index("      - name: " + step) for step in steps]
    assert offsets == sorted(offsets)
    assert 'and status.get("live_armed") is False' in WORKFLOW
    assert 'and status.get("bitfinex_live_enabled") is False' in WORKFLOW
    assert 'and status.get("force_paper_mode") is True' in WORKFLOW


@pytest.mark.parametrize("maintenance,deploy", list(itertools.product(("", "skipped", "success", "failure"), repeat=2)))
def test_failure_cleanup_only_runs_after_mutation_started(maintenance, deploy):
    block = WORKFLOW.split("      - name: Best-effort preserve safe paper maintenance after failed guarded deploy\n", 1)[1]
    expression = next(line.strip()[4:] for line in block.splitlines() if line.strip().startswith("if: "))
    expected = ("failure() && (steps.paper_maintenance.outcome == 'success' || "
                "steps.paper_maintenance.outcome == 'failure' || "
                "steps.deploy_source.outcome == 'success' || steps.deploy_source.outcome == 'failure')")
    assert expression == expected
    evaluated = expression.replace("failure()", "True").replace("&&", "and").replace("||", "or")
    evaluated = evaluated.replace("steps.paper_maintenance.outcome", repr(maintenance))
    evaluated = evaluated.replace("steps.deploy_source.outcome", repr(deploy))
    assert eval(evaluated, {"__builtins__": {}}) == (maintenance in {"success", "failure"} or deploy in {"success", "failure"})
    assert "id: paper_maintenance" in WORKFLOW.split("      - name: Enter durable authenticated paper maintenance boundary\n", 1)[1].split("      - name:", 1)[0]
    assert "id: deploy_source" in WORKFLOW.split("      - name: Deploy the exact source revision\n", 1)[1].split("      - name:", 1)[0]


def test_offline_seal_repair_proves_hold_then_sleeps_then_restores_in_order():
    job = WORKFLOW.split("  repair-v22-seals-offline:\n", 1)[1].split("\n  clean-epoch-boundary-reset:\n", 1)[0]
    steps = [
        "Bind the failed deploy and the exact crash-looping revision",
        "Prove every relay is PAUSED, disarmed and flat from durable Railway state",
        "Prove the seal crash loop, a flat paper lifecycle and the exact orphan plan",
        "Require the confirm token",
        "Hold the machine on sleep so bot.py is not running",
        "Quarantine exactly the proven receipts with no bot process running",
        "Restore the image entrypoint and prove a held-down boot",
        "Re-prove every relay is PAUSED, disarmed and flat",
    ]
    offsets = [job.index("      - name: " + step) for step in steps]
    assert offsets == sorted(offsets)
    assert 'EXPECTED_GENERATIONS: "1,2"' in job
    assert job.count('DURABLE_RELAYS_ONLY_RECOVERY: "YES"') == 2
    assert '--command "sleep infinity"' in job and '--command "/fly-entrypoint.sh"' in job
    assert "offline-execute" in job and "--confirm ${CONFIRM}" in job
    for mutating in steps[3:]:
        step = job.split("      - name: " + mutating + "\n", 1)[1].split("      - name:", 1)[0]
        assert "if: ${{ inputs.mode == 'repair-v22-seals-offline-execute' }}" in step
    for forbidden in ("rm -", "unlink", "resume", "/api/arm", "flyctl deploy"):
        assert forbidden not in job
