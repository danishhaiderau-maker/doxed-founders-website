"""Validate and inject an explicit lifecycle reset proof into one Fly deploy."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence


MAX_PROOF_BYTES = 2048
PROOF_KEYS = frozenset({"operation_path", "operation_sha256", "trigger"})
LEDGER_NAMES = frozenset({
    "opportunity",
    "pre_entry_features",
    "evidence_failure",
    "decision",
    "order_intent",
    "execution",
    "market_segment",
    "lifecycle",
})
ENV_KEYS = (
    "LIFECYCLE_RESET_OPERATION_PATH",
    "LIFECYCLE_RESET_OPERATION_SHA256",
    "LIFECYCLE_RESET_RECOVERY_TRIGGER",
)
_OPERATION_PATH = re.compile(
    r"/app/data/runtime/research_reset_receipts/[0-9a-f]{24}/operation\.json"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TRIGGER = re.compile(
    r"SOURCE_LEDGER_(?:TRUNCATED|DELETED_BY_RESET):([A-Za-z0-9_.-]+)\.jsonl"
)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("LIFECYCLE_RESET_PROOF_DUPLICATE_KEY")
        result[key] = value
    return result


def validate_proof(raw: str, *, event_name: str, mode: str) -> dict[str, str] | None:
    """Return the exact validated proof, or None for an unconfigured deploy."""
    if not isinstance(raw, str):
        raise ValueError("LIFECYCLE_RESET_PROOF_INPUT_INVALID")
    if raw == "":
        return None
    if len(raw.encode("utf-8")) > MAX_PROOF_BYTES:
        raise ValueError("LIFECYCLE_RESET_PROOF_TOO_LARGE")
    if event_name != "workflow_dispatch" or mode != "deploy":
        raise ValueError("LIFECYCLE_RESET_PROOF_MODE_INVALID")
    try:
        proof = json.loads(raw, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("LIFECYCLE_RESET_PROOF_JSON_INVALID") from exc
    if not isinstance(proof, dict) or set(proof) != PROOF_KEYS:
        raise ValueError("LIFECYCLE_RESET_PROOF_KEYS_INVALID")
    if not all(isinstance(proof[key], str) for key in PROOF_KEYS):
        raise ValueError("LIFECYCLE_RESET_PROOF_VALUES_INVALID")
    if _OPERATION_PATH.fullmatch(proof["operation_path"]) is None:
        raise ValueError("LIFECYCLE_RESET_PROOF_PATH_INVALID")
    if _SHA256.fullmatch(proof["operation_sha256"]) is None:
        raise ValueError("LIFECYCLE_RESET_PROOF_SHA256_INVALID")
    trigger_match = _TRIGGER.fullmatch(proof["trigger"])
    if trigger_match is None or trigger_match.group(1) not in LEDGER_NAMES:
        raise ValueError("LIFECYCLE_RESET_PROOF_TRIGGER_INVALID")
    return proof


def deploy_argv(*, source_git_rev: str, transport_bundles_enabled: str,
                proof: Mapping[str, str] | None) -> list[str]:
    """Build the exact Fly argv, explicitly clearing absent one-shot authority."""
    if re.fullmatch(r"[0-9a-f]{40}", source_git_rev) is None:
        raise ValueError("SOURCE_GIT_REV_INVALID")
    if transport_bundles_enabled not in {"0", "1"}:
        raise ValueError("TRANSPORT_BUNDLES_ENABLED_INVALID")
    values = {
        ENV_KEYS[0]: proof["operation_path"] if proof is not None else "",
        ENV_KEYS[1]: proof["operation_sha256"] if proof is not None else "",
        ENV_KEYS[2]: proof["trigger"] if proof is not None else "",
    }
    argv = [
        "flyctl", "deploy", "--remote-only", "--strategy", "immediate",
        "--build-arg", f"SOURCE_GIT_REV={source_git_rev}",
        "--env", f"DATA_SYNC_TRANSPORT_BUNDLES_ENABLED={transport_bundles_enabled}",
    ]
    for key in ENV_KEYS:
        argv.extend(("--env", f"{key}={values[key]}"))
    return argv


def run(argv: Sequence[str], *, environ: Mapping[str, str] = os.environ,
        runner: Callable[..., object] = subprocess.run) -> int:
    if list(argv) not in (["validate"], ["deploy"]):
        raise ValueError("usage: fly_deploy_reset_proof.py validate|deploy")
    proof = validate_proof(
        environ.get("LIFECYCLE_RESET_PROOF", ""),
        event_name=environ.get("GITHUB_EVENT_NAME", ""),
        mode=environ.get("DEPLOY_MODE", ""),
    )
    if list(argv) == ["validate"]:
        print("Lifecycle reset startup proof validated" if proof else
              "Lifecycle reset startup proof is not configured")
        return 0
    command = deploy_argv(
        source_git_rev=environ.get("GITHUB_SHA", ""),
        transport_bundles_enabled=environ.get("TRANSPORT_BUNDLES_ENABLED", ""),
        proof=proof,
    )
    runner(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
