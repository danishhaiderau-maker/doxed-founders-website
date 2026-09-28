"""Source contract for opt-in batching in the existing guarded deploy workflow.

These tests verify wiring only, not Actions execution or production recovery.
"""
from pathlib import Path
import itertools
import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")


def test_boolean_opt_in_defaults_off():
    block = WORKFLOW.split("      transport_bundles:\n", 1)[1].split("      expected_", 1)[0]
    assert "        required: false\n" in block
    assert "        default: false\n" in block
    assert "        type: boolean\n" in block


def test_only_normal_dispatch_can_enable_fixed_binary_flag():
    deploy = WORKFLOW.split("      - name: Deploy the exact source revision\n", 1)[1].split(
        "      - name:", 1)[0]
    expression = ("${{ github.event_name == 'workflow_dispatch' && inputs.mode == 'deploy' "
                  "&& inputs.transport_bundles && '1' || '0' }}")
    assert "TRANSPORT_BUNDLES_ENABLED: " + expression in deploy
    assert '--env "DATA_SYNC_TRANSPORT_BUNDLES_ENABLED=${TRANSPORT_BUNDLES_ENABLED}"' in deploy
    assert '--build-arg SOURCE_GIT_REV="${GITHUB_SHA}"' in deploy
    assert "FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}" in deploy
    # No raw workflow expression or arbitrary supplied environment string enters shell.
    run = next(line for line in deploy.splitlines() if "run:" in line)
    assert "inputs." not in run and "${{" not in run


def test_portable_batch_gates_precede_any_maintenance_mutation():
    marker = "      - name: Verify bounded transport package and original acknowledgment contracts"
    gate = WORKFLOW.split(marker, 1)[1].split("      - uses:", 1)[0]
    for name in ("transport", "worker", "runtime", "storage", "api", "client", "bot_integration"):
        assert f"test_data_sync_bundle_{name}.py" in gate
    assert "test_fly_sync_bundle_adapter.py" in gate
    assert "../../scripts/test_fly_transport_bundle_workflow.py" in gate
    assert WORKFLOW.index(marker) < WORKFLOW.index("      - name: Enter durable authenticated paper maintenance boundary")


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
