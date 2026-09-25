#!/usr/bin/env python3
"""Quarantine invent-eligible ops/index bulk into research_archive (excluded).

Targets (never unique un-ACKed research ledgers):
  - closed bot_runtime.log.N rotations
  - active bot_runtime.log when oversized (rename-rotate; bot recreates)
  - v3/qualification_horizon_index.sqlite3 (+ -wal/-shm) rebuildable index

Never arms Bitfinex. Never deletes. Soft-cap unchanged.
Env: APPLY=true to move; DATA_ROOT=/app/data
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

APPLY = os.environ.get("APPLY", "false").strip().lower() in {"1", "true", "yes"}
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
RUNTIME = DATA_ROOT / "runtime"
MIN_ACTIVE_LOG_BYTES = int(os.environ.get("MIN_ACTIVE_LOG_BYTES", str(8 * 1048576)))


def _size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def main() -> int:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest_root = RUNTIME / "research_archive" / f"ops-bulk-under-softcap-{stamp}"
    report: dict = {
        "schema": "fly_archive_ops_bulk_under_softcap_v1",
        "ok": False,
        "apply": APPLY,
        "data_root": str(DATA_ROOT),
        "runtime": str(RUNTIME),
        "archive_root": str(dest_root),
        "candidates": [],
        "actions": [],
        "bytes_moved": 0,
        "NO_SAFE": True,
        "never_arm": True,
    }
    if not RUNTIME.is_dir():
        report["error"] = "runtime_missing"
        print(json.dumps(report, sort_keys=True))
        return 2

    candidates: list[tuple[Path, str]] = []

    # Closed rotated runtime logs.
    for path in sorted(RUNTIME.glob("bot_runtime.log.*")):
        if path.is_file() and path.name != "bot_runtime.log":
            candidates.append((path, "closed_runtime_log"))

    # Oversized active log: rename-rotate into archive (bot recreates).
    active_log = RUNTIME / "bot_runtime.log"
    if active_log.is_file() and _size(active_log) >= MIN_ACTIVE_LOG_BYTES:
        candidates.append((active_log, "active_runtime_log_rotate"))

    # Rebuildable qualification horizon index (not analyzer cohort evidence).
    qh = RUNTIME / "v3" / "qualification_horizon_index.sqlite3"
    for path in (qh, Path(str(qh) + "-wal"), Path(str(qh) + "-shm")):
        if path.is_file():
            candidates.append((path, "qualification_horizon_index"))

    for path, kind in candidates:
        sz = _size(path)
        report["candidates"].append({
            "kind": kind,
            "name": path.name,
            "rel": str(path.relative_to(RUNTIME)).replace("\\", "/"),
            "bytes": sz,
            "mib": round(sz / 1048576, 2),
        })

    report["candidate_count"] = len(report["candidates"])
    report["eligible_mib"] = round(
        sum(int(c["bytes"]) for c in report["candidates"]) / 1048576, 2
    )

    if APPLY:
        dest_root.mkdir(parents=True, exist_ok=True)

    for path, kind in candidates:
        rel = str(path.relative_to(RUNTIME)).replace("\\", "/")
        dest = dest_root / rel.replace("/", "__")
        action = {
            "kind": kind,
            "from": str(path),
            "to": str(dest),
            "bytes": _size(path),
        }
        if APPLY:
            if dest.exists():
                action["error"] = "dest_exists"
                report["actions"].append(action)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            action["moved"] = True
            report["bytes_moved"] += int(action["bytes"] or 0)
        else:
            action["would_move"] = True
        report["actions"].append(action)

    report["bytes_moved_mib"] = round(report["bytes_moved"] / 1048576, 2)
    report["ok"] = True
    if not report["candidates"]:
        report["noop_reason"] = "no_ops_bulk_candidates"
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
