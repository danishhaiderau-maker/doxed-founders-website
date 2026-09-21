#!/usr/bin/env python3
"""One-shot Fly volume retained-tree inspect (+ optional purge).

Safe defaults: DRY_RUN=1. Never arms Bitfinex. Never deletes the live epoch
research tree for EXPECTED_EPOCH. Targets post-wipe quarantine / superseded
epoch residue that blocks inventory CURRENT and keeps used-MB high.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
DRY_RUN = os.environ.get("DRY_RUN", "1").strip().lower() not in ("0", "false", "no")
EXPECTED_EPOCH = os.environ.get("EXPECTED_EPOCH", "").strip()
# Directories that may retain superseded wipe residue (never live session CWD files).
CANDIDATE_REL = [
    "research/genome/epoch_quarantine",
    "research_epoch_quarantine",
    "v3/authority_identity_quarantine_v1",
    "v3/inventory_spool",
    "v3/transfer_bundles",  # only stale unacked if marked
]


def _dir_stats(path: Path) -> dict:
    files = 0
    bytes_ = 0
    if not path.exists():
        return {"exists": False, "files": 0, "bytes": 0}
    for root, _dirs, names in os.walk(path, followlinks=False):
        for name in names:
            fp = Path(root) / name
            try:
                bytes_ += fp.stat().st_size
                files += 1
            except OSError:
                pass
    return {"exists": True, "files": files, "bytes": bytes_}


def _safe_purge(path: Path) -> dict:
    if DRY_RUN:
        return {"action": "dry_run", "path": str(path)}
    if not path.exists():
        return {"action": "missing", "path": str(path)}
    # Refuse if path string embeds the expected live epoch id.
    if EXPECTED_EPOCH and EXPECTED_EPOCH in str(path):
        return {"action": "refused_live_epoch", "path": str(path)}
    before = _dir_stats(path)
    shutil.rmtree(path, ignore_errors=False)
    return {
        "action": "deleted",
        "path": str(path),
        "files_removed": before["files"],
        "bytes_removed": before["bytes"],
    }


def main() -> int:
    report = {
        "schema": "fly_retained_volume_purge_v1",
        "dry_run": DRY_RUN,
        "expected_epoch": EXPECTED_EPOCH or None,
        "data_root": str(DATA_ROOT),
        "ts": time.time(),
        "live_armed_check": "NOT_PERFORMED_HERE",
        "candidates": [],
        "actions": [],
    }
    # Top-level volume usage snapshot
    try:
        usage = shutil.disk_usage(str(DATA_ROOT))
        report["disk"] = {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "used_pct": round(100.0 * usage.used / usage.total, 2) if usage.total else None,
        }
    except OSError as ex:
        report["disk_error"] = str(ex)

    for rel in CANDIDATE_REL:
        path = (DATA_ROOT / rel).resolve()
        try:
            path.relative_to(DATA_ROOT)
        except ValueError:
            report["actions"].append({"action": "refused_escape", "path": str(path)})
            continue
        stats = _dir_stats(path)
        entry = {"rel": rel, **stats}
        report["candidates"].append(entry)
        # Only purge quarantine trees by default; leave transfer_bundles alone
        # unless empty/stale and explicitly enabled.
        if rel.endswith("transfer_bundles"):
            continue
        if stats.get("exists") and stats.get("files", 0) > 0:
            report["actions"].append(_safe_purge(path))

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
