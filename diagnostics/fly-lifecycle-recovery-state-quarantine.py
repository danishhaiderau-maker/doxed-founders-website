#!/usr/bin/env python3
"""Quarantine stale lifecycle recovery-state that blocks receipt_bootstrap.

Env:
  DRY_RUN=true|false (default true)
  DATA_ROOT=/app/data
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

DRY_RUN = str(os.getenv("DRY_RUN", "true")).strip().lower() in {"1", "true", "yes"}
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/app/data")).resolve()
STAMP = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def main() -> int:
    actions = []
    errors = []
    # Common recovery-state locations that previously starved inventory.
    candidates = [
        DATA_ROOT / "runtime" / "v3" / "lifecycle" / "recovery-state",
        DATA_ROOT / "runtime" / "v3" / "receipts" / "lifecycle" / "recovery-state",
        DATA_ROOT / "lifecycle" / "recovery-state",
    ]
    # Also scan shallow for recovery-state directories under runtime/v3.
    runtime_v3 = DATA_ROOT / "runtime" / "v3"
    if runtime_v3.is_dir():
        for dirpath, dirnames, _fns in os.walk(runtime_v3):
            depth = Path(dirpath).relative_to(runtime_v3).parts
            if len(depth) > 4:
                dirnames[:] = []
                continue
            if "recovery-state" in dirnames:
                candidates.append(Path(dirpath) / "recovery-state")

    seen = set()
    qroot = DATA_ROOT / "research_epoch_quarantine" / f"lifecycle-recovery-state-{STAMP}"
    for src in candidates:
        key = str(src.resolve()) if src.exists() else str(src)
        if key in seen:
            continue
        seen.add(key)
        if not src.exists():
            continue
        dest = qroot / src.name
        # uniquify
        n = 0
        while dest.exists():
            n += 1
            dest = qroot / f"{src.name}-{n}"
        actions.append({"action": "MOVE", "src": str(src), "dest": str(dest), "dry_run": DRY_RUN})
        if not DRY_RUN:
            try:
                qroot.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
            except Exception as exc:
                errors.append(f"{src}:{type(exc).__name__}:{exc}")

    out = {
        "ok": len(errors) == 0,
        "schema": "fly_lifecycle_recovery_state_quarantine_v1",
        "dry_run": DRY_RUN,
        "actions": actions,
        "errors": errors,
        "note": "Moves stale recovery-state aside so receipt_bootstrap can COMPLETE. Never arms.",
    }
    print(json.dumps(out, separators=(",", ":")))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
