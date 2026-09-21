#!/usr/bin/env python3
"""Clear stale ACTIVE_RESET pointer left after residual purge (paper restore).

Keeps research_reset_receipts dir. Removes ACTIVE_RESET.json and any retired
ACTIVE_RESET.* stubs so /api/resume is not blocked by RESET_RECEIPT_UNREADABLE.
Never arms Bitfinex.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
RR = DATA_ROOT / "runtime" / "research_reset_receipts"

actions = []
errors = []
if RR.is_dir():
    for child in list(RR.iterdir()):
        name = child.name
        if name == "ACTIVE_RESET.json" or name.startswith("ACTIVE_RESET."):
            actions.append({"action": "DELETE", "path": f"runtime/research_reset_receipts/{name}", "dry_run": DRY_RUN})
            if not DRY_RUN:
                try:
                    child.unlink(missing_ok=True)
                except Exception as exc:
                    errors.append(f"{name}:{type(exc).__name__}:{exc}")

print(json.dumps({
    "schema": "fly_clear_stale_active_reset_v1",
    "dry_run": DRY_RUN,
    "actions": actions,
    "errors": errors,
    "ts": time.time(),
}, separators=(",", ":")))
raise SystemExit(0 if not errors else 1)
