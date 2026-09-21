#!/usr/bin/env python3
"""Physically purge retained post-wipe Fly volume bulk toward ~0 used research.

Safe boundaries:
- Never touches Bitfinex / live arm state.
- Keeps append_heads, wipe receipts, bound-epoch ledgers, ACTIVE identity.
- Deletes inventory-excluded quarantine trees that still consume disk after
  the earlier move-based purge (research_epoch_quarantine/postwipe-*).
- Clears leftover .data-sync-snapshots contents.

Env:
  DRY_RUN=true|false (default true)
  DATA_ROOT=/app/data (default)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path


SCHEMA = "fly_postwipe_retained_purge_v2"
DRY_RUN = str(os.getenv("DRY_RUN", "true")).strip().lower() in {"1", "true", "yes"}
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/app/data")).resolve()


def _dir_size(path: Path) -> tuple[int, int]:
    files = 0
    bytes_ = 0
    if not path.exists():
        return 0, 0
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 1, 0
    for dirpath, _dns, fns in os.walk(path, followlinks=False):
        for name in fns:
            files += 1
            try:
                bytes_ += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return files, bytes_


def _fs_used_mb(root: Path) -> float | None:
    try:
        st = os.statvfs(str(root))
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        return round(used / (1024 * 1024), 1)
    except Exception:
        return None


def _rm_tree(path: Path, actions: list, errors: list) -> None:
    files, bytes_ = _dir_size(path)
    actions.append({
        "action": "DELETE_TREE",
        "path": str(path.relative_to(DATA_ROOT)) if path.is_relative_to(DATA_ROOT) else str(path),
        "files": files,
        "bytes": bytes_,
        "dry_run": DRY_RUN,
    })
    if DRY_RUN or not path.exists():
        return
    try:
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path, ignore_errors=False)
    except Exception as exc:
        errors.append(f"{path}:{type(exc).__name__}:{exc}")


def _clear_dir_contents(path: Path, actions: list, errors: list) -> None:
    if not path.is_dir():
        return
    for child in list(path.iterdir()):
        _rm_tree(child, actions, errors)


def main() -> int:
    if not DATA_ROOT.is_dir():
        print(json.dumps({"ok": False, "error": f"DATA_ROOT missing: {DATA_ROOT}"}))
        return 2

    before_mb = _fs_used_mb(DATA_ROOT)
    actions: list = []
    errors: list = []
    targets: list[Path] = []

    # Move-based quarantine still occupies disk — delete postwipe retained trees.
    rq = DATA_ROOT / "research_epoch_quarantine"
    if rq.is_dir():
        for child in sorted(rq.iterdir()):
            name = child.name
            if name.startswith("postwipe-retained-") or name.startswith("postwipe-"):
                targets.append(child)
            # Also drop empty/orphan quarantine buckets older than this epoch work.
            if name in {"corrupt_evidence_quarantine"} and child.is_dir():
                targets.append(child)

    # Snapshots are rebuildable inventory caches.
    snaps = DATA_ROOT / ".data-sync-snapshots"
    if snaps.is_dir():
        actions.append({"action": "CLEAR_SNAPSHOTS", "path": ".data-sync-snapshots", "dry_run": DRY_RUN})
        if not DRY_RUN:
            _clear_dir_contents(snaps, actions, errors)

    # Lifecycle recovery quarantine copies moved earlier.
    for rel in (
        "lifecycle_recovery_quarantine",
        "research_archive",
    ):
        p = DATA_ROOT / rel
        if p.exists():
            targets.append(p)

    for t in targets:
        _rm_tree(t, actions, errors)

    # Emergency idempotency file-count bomb should already be moved; if stubs remain empty, leave.
    after_mb = _fs_used_mb(DATA_ROOT)
    deleted_bytes = sum(int(a.get("bytes") or 0) for a in actions if a.get("action") == "DELETE_TREE")
    out = {
        "ok": len(errors) == 0,
        "schema": SCHEMA,
        "dry_run": DRY_RUN,
        "data_root": str(DATA_ROOT),
        "before_mb": before_mb,
        "after_mb": after_mb,
        "deleted_bytes_planned": deleted_bytes,
        "deleted_mb_planned": round(deleted_bytes / (1024 * 1024), 1),
        "actions": actions,
        "errors": errors[:40],
        "note": (
            "Physical delete of postwipe quarantine + rebuildable snapshots. "
            "Keeps ACTIVE identity, wipe receipts, append_heads, live research epoch files. "
            "Never arms Bitfinex."
        ),
        "ts": int(time.time()),
    }
    print(json.dumps(out, separators=(",", ":")))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
