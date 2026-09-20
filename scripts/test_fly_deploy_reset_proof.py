import json
from pathlib import Path

import pytest

from fly_deploy_reset_proof import ENV_KEYS, deploy_argv, run, validate_proof


VALID = {
    "operation_path": "/app/data/runtime/research_reset_receipts/0123456789abcdef01234567/operation.json",
    "operation_sha256": "a" * 64,
    "trigger": "SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl",
}


def encoded(value=VALID):
    return json.dumps(value, separators=(",", ":"))


def test_valid_proof_and_exact_deploy_command():
    proof = validate_proof(encoded(), event_name="workflow_dispatch", mode="deploy")
    argv = deploy_argv(
        source_git_rev="b" * 40,
        transport_bundles_enabled="1",
        proof=proof,
    )
    assert argv == [
        "flyctl", "deploy", "--remote-only", "--strategy", "immediate",
        "--build-arg", "SOURCE_GIT_REV=" + "b" * 40,
        "--env", "DATA_SYNC_TRANSPORT_BUNDLES_ENABLED=1",
        "--env", f"{ENV_KEYS[0]}={VALID['operation_path']}",
        "--env", f"{ENV_KEYS[1]}={VALID['operation_sha256']}",
        "--env", f"{ENV_KEYS[2]}={VALID['trigger']}",
    ]


@pytest.mark.parametrize("value", [
    {"operation_path": VALID["operation_path"], "operation_sha256": "a" * 64},
    {**VALID, "unexpected": "value"},
])
def test_rejects_missing_or_extra_keys(value):
    with pytest.raises(ValueError, match="KEYS_INVALID"):
        validate_proof(encoded(value), event_name="workflow_dispatch", mode="deploy")


def test_rejects_duplicate_keys():
    raw = encoded()[:-1] + ',"trigger":"SOURCE_LEDGER_DELETED_BY_RESET:decision.jsonl"}'
    with pytest.raises(ValueError, match="DUPLICATE_KEY"):
        validate_proof(raw, event_name="workflow_dispatch", mode="deploy")


@pytest.mark.parametrize(("field", "value", "error"), [
    ("operation_path", "/app/data/runtime/research_reset_receipts/../operation.json", "PATH_INVALID"),
    ("operation_path", "/app/data/runtime/research_reset_receipts/ABCDEF0123456789ABCDEF01/operation.json", "PATH_INVALID"),
    ("operation_sha256", "A" * 64, "SHA256_INVALID"),
    ("operation_sha256", "a" * 63, "SHA256_INVALID"),
    ("trigger", "SOURCE_LEDGER_DELETED_BY_RESET:unknown.jsonl", "TRIGGER_INVALID"),
    ("trigger", "SOURCE_LEDGER_ROTATED:opportunity.jsonl", "TRIGGER_INVALID"),
])
def test_rejects_invalid_path_hash_or_ledger(field, value, error):
    candidate = dict(VALID)
    candidate[field] = value
    with pytest.raises(ValueError, match=error):
        validate_proof(encoded(candidate), event_name="workflow_dispatch", mode="deploy")


@pytest.mark.parametrize(("event_name", "mode"), [
    ("push", ""),
    ("workflow_dispatch", "recover-unready"),
    ("workflow_dispatch", "recover-stalled-runtime"),
])
def test_nonempty_proof_is_rejected_outside_explicit_dispatch_deploy(event_name, mode):
    with pytest.raises(ValueError, match="MODE_INVALID"):
        validate_proof(encoded(), event_name=event_name, mode=mode)


def test_empty_proof_explicitly_clears_all_recovery_environment():
    assert validate_proof("", event_name="push", mode="") is None
    argv = deploy_argv(
        source_git_rev="b" * 40,
        transport_bundles_enabled="0",
        proof=None,
    )
    for key in ENV_KEYS:
        index = argv.index(f"{key}=")
        assert argv[index - 1:index + 1] == ["--env", f"{key}="]


def test_deploy_uses_subprocess_argv_without_shell():
    calls = []
    environment = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "DEPLOY_MODE": "deploy",
        "LIFECYCLE_RESET_PROOF": encoded(),
        "GITHUB_SHA": "b" * 40,
        "TRANSPORT_BUNDLES_ENABLED": "0",
    }
    assert run(
        ["deploy"],
        environ=environment,
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    ) == 0
    assert len(calls) == 1
    assert calls[0][1] == {"check": True}
    assert isinstance(calls[0][0][0], list)


def test_proof_is_bounded_by_utf8_bytes():
    with pytest.raises(ValueError, match="TOO_LARGE"):
        validate_proof("x" * 2049, event_name="workflow_dispatch", mode="deploy")


def test_workflow_validates_before_maintenance_and_deploys_through_helper():
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "fly-bot-deploy.yml"
    ).read_text(encoding="utf-8")
    guarded = workflow[workflow.index("  test-and-deploy:"):]
    validate_at = guarded.index("Validate optional lifecycle reset startup proof")
    maintenance_at = guarded.index("Enter durable authenticated paper maintenance boundary")
    deploy_at = guarded.index("Deploy the exact source revision", maintenance_at)
    assert validate_at < maintenance_at < deploy_at
    assert "python scripts/fly_deploy_reset_proof.py validate" in workflow
    assert "python ../../scripts/fly_deploy_reset_proof.py deploy" in workflow
    assert "LIFECYCLE_RESET_PROOF: " + "$" + "{{ inputs.lifecycle_reset_proof }}" in workflow
