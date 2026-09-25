#!/usr/bin/env python3
"""Quarantine closed signal/chase jsonl.N into invent-excluded research_archive.

Never touches unsuffixed active writers. Never arms Bitfinex.
Env: APPLY=true to move; DATA_ROOT=/app/data
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

APPLY = os.environ.get("APPLY", "false").strip().lower() in {"1", "true", "yes"}
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
RUNTIME = DATA_ROOT / "runtime"
CLOSED_RE = re.compile(r"^(signal_replay|chase_offset_touch_grid)\.jsonl\.(\d+)$")


def main() -> int:
    report = {
        "schema": "fly_archive_signal_rotations_v1",
        "ok": False,
        "apply": APPLY,
        "data_root": str(DATA_ROOT),
        "runtime": str(RUNTIME),
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

    actives = {
        "signal_replay.jsonl": (RUNTIME / "signal_replay.jsonl").is_file(),
        "chase_offset_touch_grid.jsonl": (RUNTIME / "chase_offset_touch_grid.jsonl").is_file(),
    }
    report["actives"] = actives

    for path in sorted(RUNTIME.iterdir()):
        if not path.is_file():
            continue
        if not CLOSED_RE.match(path.name):
            continue
        size = path.stat().st_size
        report["candidates"].append({"name": path.name, "bytes": size, "mib": round(size / 1048576, 2)})

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest_root = RUNTIME / "research_archive" / f"acked-rotated-signal-{stamp}"
    report["archive_root"] = str(dest_root)

    if APPLY:
        dest_root.mkdir(parents=True, exist_ok=True)

    for row in report["candidates"]:
        src = RUNTIME / row["name"]
        dest = dest_root / row["name"]
        action = {"from": str(src), "to": str(dest), "bytes": row["bytes"]}
        if APPLY:
            if dest.exists():
                action["error"] = "dest_exists"
                report["actions"].append(action)
                continue
            shutil.move(str(src), str(dest))
            action["moved"] = True
            report["bytes_moved"] += row["bytes"]
        else:
            action["would_move"] = True
        report["actions"].append(action)

    report["bytes_moved_mib"] = round(report["bytes_moved"] / 1048576, 2)
    report["eligible_mib"] = round(
        sum(a.get("bytes") or 0 for a in report["actions"]) / 1048576, 2
    )
    report["candidate_count"] = len(report["candidates"])
    report["ok"] = True
    if APPLY and report["candidate_count"] == 0:
        report["noop_reason"] = "no_closed_signal_rotations"
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
