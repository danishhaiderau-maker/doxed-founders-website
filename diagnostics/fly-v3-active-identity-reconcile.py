#!/usr/bin/env python3
"""Quarantine stale lifecycle recovery-state after a completed fresh-epoch wipe.

Preserves the file by rename (never unlink). Does not arm live trading.
Safe when:
  - current session epoch matches EXPECTED_EPOCH
  - recovery-state.reset_proof.epoch_id != EXPECTED_EPOCH
  - a COMPLETE wipe operation exists for EXPECTED_EPOCH
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

RUNTIME = Path("/app/data/runtime")
EXPECTED_EPOCH = str(os.environ.get("EXPECTED_EPOCH") or "").strip()
DRY_RUN = str(os.environ.get("DRY_RUN", "true")).strip().lower() in {"1", "true", "yes"}


def main() -> int:
    report = {
        "schema": "lifecycle_recovery_state_quarantine_v1",
        "dry_run": DRY_RUN,
        "expected_epoch": EXPECTED_EPOCH or None,
        "ok": False,
    }
    if not EXPECTED_EPOCH.startswith("epoch-"):
        report["error"] = "EXPECTED_EPOCH_INVALID"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    # Prove current wipe receipt exists for this epoch
    ops = []
    rr = RUNTIME / "research_reset_receipts"
    for op in sorted(rr.glob("*/operation.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]:
        try:
            j = json.loads(op.read_text("utf-8"))
        except Exception as exc:
            continue
        proof = j.get("proof") or {}
        new_epoch = j.get("new_epoch_id") or proof.get("new_epoch_id")
        ops.append({
            "path": str(op.relative_to(RUNTIME)).replace("\\", "/"),
            "stage": j.get("stage"),
            "new_epoch_id": new_epoch,
        })
    report["recent_operations"] = ops
    matching = [o for o in ops if o.get("new_epoch_id") == EXPECTED_EPOCH and o.get("stage") == "COMPLETE"]
    if not matching:
        report["error"] = "NO_COMPLETE_WIPE_OPERATION_FOR_EXPECTED_EPOCH"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 3

    state_path = RUNTIME / "v3" / "lifecycle_bundle_index" / "recovery-state.json"
    report["state_exists"] = state_path.exists()
    if not state_path.exists():
        report["ok"] = True
        report["action"] = "ALREADY_ABSENT"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    state = json.loads(state_path.read_text("utf-8"))
    proof = state.get("reset_proof") if isinstance(state.get("reset_proof"), dict) else {}
    stale_epoch = proof.get("epoch_id")
    report["state_phase"] = state.get("phase")
    report["state_trigger"] = state.get("trigger")
    report["stale_epoch"] = stale_epoch
    if stale_epoch == EXPECTED_EPOCH:
        report["ok"] = True
        report["action"] = "ALREADY_CURRENT_EPOCH"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if not stale_epoch:
        report["error"] = "STATE_MISSING_RESET_PROOF_EPOCH"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 4

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = state_path.with_name(
        f"recovery-state.quarantine-stale-{stale_epoch}-before-{EXPECTED_EPOCH}-{ts}.json"
    )
    meta = {
        "schema": "lifecycle_recovery_state_quarantine_receipt_v1",
        "quarantined_at": ts,
        "reason": "STALE_RESET_PROOF_EPOCH_AFTER_FRESH_WIPE",
        "expected_epoch": EXPECTED_EPOCH,
        "stale_epoch": stale_epoch,
        "original_name": state_path.name,
        "matching_wipe_operation": matching[0],
        "dry_run": DRY_RUN,
    }
    report["dest"] = str(dest.relative_to(RUNTIME)).replace("\\", "/")
    report["meta"] = meta
    if DRY_RUN:
        report["action"] = "WOULD_QUARANTINE"
        report["ok"] = True
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    state_path.replace(dest)
    (dest.parent / (dest.name + ".quarantine.json")).write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    report["action"] = "QUARANTINED"
    report["state_exists_after"] = state_path.exists()
    report["ok"] = not state_path.exists() and dest.exists()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
