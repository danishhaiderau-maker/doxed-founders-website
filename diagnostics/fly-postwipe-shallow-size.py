#!/usr/bin/env python3
"""Shallow Fly volume size probe (no full walk of huge trees)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/app/data")).resolve()


def _size(path: Path, max_depth: int = 2) -> dict:
    if not path.exists():
        return {"path": str(path), "absent": True, "files": 0, "mb": 0.0}
    files = 0
    bytes_ = 0

    def walk(p: Path, depth: int) -> None:
        nonlocal files, bytes_
        if depth > max_depth:
            return
        try:
            for child in p.iterdir():
                if child.is_symlink():
                    continue
                if child.is_file():
                    files += 1
                    try:
                        bytes_ += child.stat().st_size
                    except OSError:
                        pass
                elif child.is_dir():
                    walk(child, depth + 1)
        except OSError:
            return

    if path.is_file():
        try:
            return {"path": str(path.relative_to(DATA_ROOT)), "files": 1, "mb": round(path.stat().st_size / (1024 * 1024), 2)}
        except OSError:
            return {"path": str(path), "files": 0, "mb": 0.0}
    walk(path, 0)
    rel = str(path.relative_to(DATA_ROOT)) if path != DATA_ROOT else "."
    return {"path": rel, "files": files, "mb": round(bytes_ / (1024 * 1024), 2)}


def main() -> int:
    try:
        st = os.statvfs(str(DATA_ROOT))
        used_mb = round((st.f_blocks - st.f_bfree) * st.f_frsize / (1024 * 1024), 1)
        free_mb = round(st.f_bfree * st.f_frsize / (1024 * 1024), 1)
        total_mb = round(st.f_blocks * st.f_frsize / (1024 * 1024), 1)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    top = []
    if DATA_ROOT.is_dir():
        for child in sorted(DATA_ROOT.iterdir(), key=lambda p: p.name):
            top.append(_size(child, max_depth=1 if child.name in {"research_epoch_quarantine", "runtime", ".data-sync-snapshots"} else 0))
    top.sort(key=lambda r: -float(r.get("mb") or 0))
    print(json.dumps({
        "ok": True,
        "used_mb": used_mb,
        "free_mb": free_mb,
        "total_mb": total_mb,
        "top": top[:25],
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
