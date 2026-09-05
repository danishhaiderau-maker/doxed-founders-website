"""Dispatch one reviewed derivative operation; never deploy/restart/arm a bot."""
from __future__ import annotations

import base64
import hashlib
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
    source64 = base64.b64encode(source).decode("ascii")
    args64 = base64.b64encode(json.dumps(args).encode()).decode("ascii")
    code = ("import base64,json,sys;sys.path.insert(0,'/app');"
            f"sys.argv=json.loads(base64.b64decode('{args64}'));"
            f"exec(compile(base64.b64decode('{source64}'),'<reviewed-bundle-canary>','exec'))")
    return 'python -c "' + code + '"'


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
        "--timeout", "60", machine, command], timeout=70, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
