"""Local-only API adapter. Request JSON is read from stdin, never argv."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from local_fresh_collection import (
    LocalFreshCollectionRejected,
    capability_status,
    execute_operation,
    queue_operation,
    read_operation,
)
from local_fresh_collection_owner_audit import audit_local_research_owners


CANONICAL_ROOT = Path(
    "C:/DoxxedCrypto/btc-v31-current/services/btc-conservative-agent/canonical-research-data"
)
ARCHIVE_ROOT = Path(
    "C:/DoxxedCrypto/btc-v31-current/services/btc-conservative-agent/research_session_archives"
)
STATE_ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / (
    "DoxxedCrypto/local-research-reset"
)
RUNTIME_AGENT_ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capability", "queue", "status", "run"))
    parser.add_argument("--operation-id")
    args = parser.parse_args()
    try:
        if args.command == "capability":
            result = capability_status(
                canonical_root=CANONICAL_ROOT,
                archive_root=ARCHIVE_ROOT,
                expected_canonical_root=CANONICAL_ROOT,
                expected_archive_root=ARCHIVE_ROOT,
                runtime_agent_root=RUNTIME_AGENT_ROOT,
            )
        elif args.command == "queue":
            request = json.load(sys.stdin)
            result, replay = queue_operation(
                canonical_root=CANONICAL_ROOT,
                archive_root=ARCHIVE_ROOT,
                state_root=STATE_ROOT,
                request=request,
                expected_canonical_root=CANONICAL_ROOT,
                expected_archive_root=ARCHIVE_ROOT,
                runtime_agent_root=RUNTIME_AGENT_ROOT,
            )
            result = dict(result, replay=replay)
        elif args.command == "status":
            result = read_operation(state_root=STATE_ROOT, operation_id=args.operation_id)
        else:
            result = execute_operation(
                state_root=STATE_ROOT,
                operation_id=args.operation_id,
                owner_auditor=audit_local_research_owners,
                expected_canonical_root=CANONICAL_ROOT,
                expected_archive_root=ARCHIVE_ROOT,
                runtime_agent_root=RUNTIME_AGENT_ROOT,
            )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (LocalFreshCollectionRejected, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
