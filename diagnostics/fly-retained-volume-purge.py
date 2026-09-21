#!/usr/bin/env python3
"""Shallow Fly /app/data volume breakdown for post-wipe residual risk."""
from __future__ import annotations
import json, os, time
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
EXPECTED_EPOCH = os.environ.get("EXPECTED_EPOCH", "").strip()

def dir_stats(path: Path, max_depth: int = 2):
    files = 0
    bytes_ = 0
    if not path.exists():
        return {"exists": False, "files": 0, "bytes": 0, "mb": 0.0}
    def walk(p: Path, depth: int):
        nonlocal files, bytes_
        try:
            for child in p.iterdir():
                if child.is_symlink():
                    continue
                if child.is_file():
                    files += 1
                    try: bytes_ += child.stat().st_size
                    except OSError: pass
                elif child.is_dir() and depth < max_depth:
                    walk(child, depth + 1)
        except OSError:
            return
    if path.is_file():
        try:
            sz = path.stat().st_size
            return {"exists": True, "files": 1, "bytes": sz, "mb": round(sz/1048576, 2)}
        except OSError:
            return {"exists": True, "files": 0, "bytes": 0, "mb": 0.0}
    walk(path, 0)
    return {"exists": True, "files": files, "bytes": bytes_, "mb": round(bytes_/1048576, 2)}

def deep_stats(path: Path):
    files = 0
    bytes_ = 0
    if not path.exists():
        return {"exists": False, "files": 0, "bytes": 0, "mb": 0.0}
    for root, _dns, fns in os.walk(path, followlinks=False):
        for name in fns:
            files += 1
            try: bytes_ += (Path(root)/name).stat().st_size
            except OSError: pass
    return {"exists": True, "files": files, "bytes": bytes_, "mb": round(bytes_/1048576, 2)}

report = {
    "schema": "fly_volume_shallow_inspect_v1",
    "expected_epoch": EXPECTED_EPOCH or None,
    "data_root": str(DATA_ROOT),
    "ts": time.time(),
    "disk": None,
    "top": [],
    "hotspots": [],
}
try:
    usage = os.statvfs(str(DATA_ROOT))
    used = (usage.f_blocks - usage.f_bfree) * usage.f_frsize
    report["disk"] = {
        "total_mb": round(usage.f_blocks * usage.f_frsize / 1048576, 1),
        "used_mb": round(used / 1048576, 1),
        "free_mb": round(usage.f_bfree * usage.f_frsize / 1048576, 1),
    }
except Exception as exc:
    report["disk_error"] = str(exc)

if DATA_ROOT.is_dir():
    for child in sorted(DATA_ROOT.iterdir(), key=lambda p: p.name):
        if child.name in {"runtime", "research", "research_epoch_quarantine", "research_archive", "v3", "logs", "debug", "exports", ".data-sync-snapshots", "lifecycle_recovery_quarantine"}:
            st = deep_stats(child)
        else:
            st = dir_stats(child, max_depth=1)
        entry = {"path": child.name, "is_dir": child.is_dir(), **st}
        report["top"].append(entry)
    report["top"].sort(key=lambda r: -int(r.get("bytes") or 0))

    runtime = DATA_ROOT / "runtime"
    if runtime.is_dir():
        kids = []
        for child in runtime.iterdir():
            st = deep_stats(child) if child.is_dir() else dir_stats(child, 0)
            kids.append({"path": f"runtime/{child.name}", **st})
        kids.sort(key=lambda r: -int(r.get("bytes") or 0))
        report["hotspots"] = kids[:30]

print(json.dumps(report, indent=2, sort_keys=True))
