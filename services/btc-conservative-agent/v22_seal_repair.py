"""Quarantine research_events_v22 seal receipts whose sealed generation file is gone.

    python v22_seal_repair.py plan    --runtime-root /app/data/runtime
    python v22_seal_repair.py execute --runtime-root /app/data/runtime --confirm QUARANTINE-V22-SEALS:<sha12>

Receipts are moved under ``research_events_v22.seals/quarantine/<stamp>/`` with a
manifest; nothing is deleted. Execute requires the paper maintenance hold.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Callable

import clean_epoch_wipe
from collector_v22 import plan_orphan_event_seals, quarantine_orphan_event_seals

CONFIRM_PREFIX = "QUARANTINE-V22-SEALS"


def confirm_token(plan_sha256: str) -> str:
    return f"{CONFIRM_PREFIX}:{plan_sha256[:12]}"


def main(argv: list[str] | None = None, fetch: Callable[[str], dict] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "execute"))
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--health-url", default=clean_epoch_wipe.HELD_DOWN_URLS["fly"])
    args = parser.parse_args(argv)
    plan = plan_orphan_event_seals(args.runtime_root)
    plan["confirm_token"] = confirm_token(plan["plan_sha256"])
    if args.mode == "plan":
        print(json.dumps(plan, sort_keys=True))
        return 0
    if args.confirm != plan["confirm_token"]:
        print(json.dumps({"error": "CONFIRM_TOKEN_MISMATCH", "expected_prefix": CONFIRM_PREFIX}))
        return 5
    try:
        violations = clean_epoch_wipe.held_down_violations((fetch or clean_epoch_wipe._fetch_health)(args.health_url))
    except Exception as exc:  # noqa: BLE001 - any doubt fails closed
        violations = [f"health unavailable: {type(exc).__name__}"]
    if violations:
        print(json.dumps({"error": "BOT_NOT_HELD_DOWN", "violations": violations}))
        return 7
    result = quarantine_orphan_event_seals(args.runtime_root, expected_plan_sha256=plan["plan_sha256"])
    remaining = plan_orphan_event_seals(args.runtime_root)["orphans"]
    print(json.dumps({**result, "remaining_orphans": remaining}, sort_keys=True))
    return 0 if not remaining else 8


if __name__ == "__main__":
    sys.exit(main())
