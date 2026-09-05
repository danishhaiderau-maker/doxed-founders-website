"""Dispatch one reviewed derivative operation; never deploy/restart/arm a bot."""
from __future__ import annotations

import base64
import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import subprocess


def remote_command(source: bytes, revision: str, generation: str, fingerprint: str,
                   inspect_only: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{12}", revision):
        raise ValueError("EXACT_INCUMBENT_REVISION_REQUIRED")
    if not re.fullmatch(r"[0-9a-f]{64}", generation):
        raise ValueError("EXACT_GENERATION_REQUIRED")
    if fingerprint and not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("INVALID_INVENTORY_FINGERPRINT")
    if inspect_only not in {"0", "1"}:
        raise ValueError("EXPLICIT_INSPECTION_MODE_REQUIRED")
    if not 0 < len(source) <= 48 * 1024:
        raise ValueError("HELPER_SOURCE_SIZE_LIMIT")
    args = ["fly_bundle_canary.py", "--expected-revision", revision,
            "--generation-id", generation]
    if fingerprint:
        args += ["--inventory-fingerprint", fingerprint]
    if inspect_only == "1":
        args += ["--inspect-only"]
    # Only reviewed source and strict hex identifiers cross the command line.
    # Authentication is resolved inside the machine, never serialized here.
    source64 = base64.b64encode(gzip.compress(source, mtime=0)).decode("ascii")
    args64 = base64.b64encode(json.dumps(args).encode()).decode("ascii")
    code = ("import base64,gzip,json,sys;sys.path.insert(0,'/app');"
            f"sys.argv=json.loads(base64.b64decode('{args64}'));"
            f"exec(compile(gzip.decompress(base64.b64decode('{source64}')),'<reviewed-bundle-canary>','exec'))")
    if len(code.encode()) > 24 * 1024:
        raise ValueError("REMOTE_COMMAND_PAYLOAD_LIMIT")
    return 'python -c "' + code + '"'


def verified_terminal_receipt(raw: bytes, revision: str, generation: str, inspect_only: str) -> dict:
    if len(raw) > 64 * 1024:
        raise ValueError("REMOTE_OUTPUT_LIMIT")
    receipts = []
    decoded = raw.decode("utf-8", errors="strict")
    try:
        objects = [json.loads(decoded)]
    except ValueError:
        objects = []
        for line in decoded.splitlines():
            try:
                objects.append(json.loads(line))
            except ValueError:
                continue
    for value in objects:
        if not isinstance(value, dict):
            continue
        # Some flyctl releases wrap remote stdout in a transport object.
        if isinstance(value.get("stdout"), str):
            if "exit_code" in value and (type(value["exit_code"]) is not int or value["exit_code"] != 0):
                raise ValueError("REMOTE_EXECUTION_FAILED")
            try:
                value = json.loads(value["stdout"])
            except ValueError:
                continue
        if isinstance(value, dict) and str(value.get("schema", "")).startswith("fly_bundle_canary_"):
            receipts.append(value)
    if len(receipts) != 1:
        raise ValueError("NO_UNIQUE_REMOTE_TERMINAL_RECEIPT")
    receipt = receipts[0]
    if (not re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("runtime_env_revision", "")))
            or receipt["runtime_env_revision"][:12] != revision):
        raise ValueError("REMOTE_RECEIPT_REVISION_MISMATCH")
    if inspect_only == "1":
        if not (receipt.get("schema") == "fly_bundle_canary_inspection_v1"
                and receipt.get("status") == "INSPECTED" and receipt.get("slice_invoked") is False
                and receipt.get("requested_generation_id") == generation):
            raise ValueError("REMOTE_INSPECTION_NOT_PROVEN")
    elif not (receipt.get("schema") == "fly_bundle_canary_receipt_v1"
              and receipt.get("status") == "SLICE_VERIFIED"
              and receipt.get("inventory_generation_id") == generation
              and receipt.get("ack_performed") is False and receipt.get("cleanup_performed") is False):
        raise ValueError("REMOTE_SLICE_NOT_PROVEN")
    return receipt


def main() -> int:
    source = Path(__file__).with_name("fly_bundle_canary.py").read_bytes()
    command = remote_command(source, os.environ.get("EXPECTED_BUNDLE_REVISION", ""),
        os.environ.get("BUNDLE_GENERATION_ID", ""),
        os.environ.get("BUNDLE_INVENTORY_FINGERPRINT", ""),
        os.environ.get("BUNDLE_INSPECT_ONLY", ""))
    listing = subprocess.run(["flyctl", "machines", "list", "--app", "doxed-btc-bot", "--json"],
        check=True, capture_output=True, timeout=30)
    if len(listing.stdout) > 256 * 1024:
        raise ValueError("MACHINE_LIST_SIZE_LIMIT")
    rows = json.loads(listing.stdout)
    if not isinstance(rows, list):
        raise ValueError("MACHINE_LIST_INVALID")
    active = [row for row in rows if str(row.get("state", "")).lower() not in {"destroyed", "stopped"}]
    if len(active) != 1 or active[0].get("state") != "started":
        raise ValueError("ONE_STARTED_OWNER_REQUIRED")
    machine = active[0].get("id")
    if not isinstance(machine, str) or not re.fullmatch(r"[0-9a-f]{10,32}", machine):
        raise ValueError("MACHINE_ID_INVALID")
    print(json.dumps({"helper_sha256": hashlib.sha256(source).hexdigest(),
                      "mode": "inspect" if os.environ["BUNDLE_INSPECT_ONLY"] == "1" else "one_slice",
                      "machine_id": machine}), flush=True)
    # No retry after an ambiguous process result; inspect existing receipt first.
    result = subprocess.run(["flyctl", "machine", "exec", "--app", "doxed-btc-bot",
        "--json", "--timeout", "60", machine, command], timeout=70, check=False, capture_output=True)
    if result.returncode != 0:
        print(json.dumps({"status": "REMOTE_COMMAND_FAILED", "exit_code": result.returncode}))
        return result.returncode
    # flyctl has returned zero for PayloadTooLarge routing failures. A transport
    # exit code alone is never execution proof. No automatic retry follows this.
    receipt = verified_terminal_receipt(result.stdout, os.environ["EXPECTED_BUNDLE_REVISION"],
        os.environ["BUNDLE_GENERATION_ID"], os.environ["BUNDLE_INSPECT_ONLY"])
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
