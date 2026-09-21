#!/usr/bin/env python3
"""Read-only post-wipe volume breakdown for doxed-btc-bot.

Does not delete. Does not arm. Prints JSON sizes by top-level and known heavy trees.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOTS = [Path("/app/data"), Path("/app/data/runtime")]
EXPECTED_EPOCH = str(os.environ.get("EXPECTED_EPOCH") or "").strip()

HEAVY = [
    "runtime/v3/lifecycle_bundle_index",
    "runtime/v3/receipts",
    "runtime/research_reset_receipts",
    "runtime/emergency_evidence_wal_v2",
    "runtime/emergency_wal_release_acks",
    "runtime/epoch_quarantine",
    "runtime/research_epoch_quarantine",
    "runtime/v3/ledgers",
    "runtime/v3/generations",
    "runtime/data_sync",
    "runtime/.data_sync",
    "data_sync",
    ".data_sync",
    "sqlite_snapshots",
    "forensic",
    "analyzer",
    "transfer",
    "bundles",
]


def dir_stats(path: Path, *, max_files: int = 2_000_000) -> dict:
    files = 0
    bytes_ = 0
    dirs = 0
    if not path.exists():
        return {"exists": False, "files": 0, "dirs": 0, "bytes": 0}
    try:
        for root, dirnames, filenames in os.walk(path, followlinks=False):
            dirs += len(dirnames)
            for name in filenames:
                files += 1
                if files > max_files:
                    return {
                        "exists": True,
                        "files": files,
                        "dirs": dirs,
                        "bytes": bytes_,
                        "truncated": True,
                    }
                try:
                    bytes_ += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError as exc:
        return {"exists": True, "error": f"{type(exc).__name__}:{exc}"}
    return {"exists": True, "files": files, "dirs": dirs, "bytes": bytes_}


def top_children(path: Path, limit: int = 40) -> list:
    if not path.is_dir():
        return []
    rows = []
    try:
        kids = list(path.iterdir())
    except OSError as exc:
        return [{"error": str(exc)}]
    for child in kids:
        try:
            if child.is_symlink():
                rows.append({"name": child.name, "kind": "symlink", "bytes": 0, "files": 0})
                continue
            if child.is_file():
                rows.append({
                    "name": child.name,
                    "kind": "file",
                    "bytes": child.stat().st_size,
                    "files": 1,
                })
            elif child.is_dir():
                st = dir_stats(child)
                rows.append({
                    "name": child.name,
                    "kind": "dir",
                    "bytes": st.get("bytes", 0),
                    "files": st.get("files", 0),
                    "dirs": st.get("dirs", 0),
                    "truncated": st.get("truncated"),
                })
        except OSError as exc:
            rows.append({"name": child.name, "error": str(exc)})
    rows.sort(key=lambda r: int(r.get("bytes") or 0), reverse=True)
    return rows[:limit]


def main() -> int:
    report = {
        "schema": "fly_postwipe_volume_breakdown_v1",
        "expected_epoch": EXPECTED_EPOCH or None,
        "ok": True,
    }
    # Session epoch if present
    for cand in [
        Path("/app/data/runtime/research_session_meta.json"),
        Path("/app/data/runtime/collector_session.json"),
        Path("/app/data/runtime/v3/session.json"),
    ]:
        if cand.exists():
            try:
                report[f"meta:{cand.name}"] = json.loads(cand.read_text("utf-8"))
            except Exception as exc:
                report[f"meta:{cand.name}"] = f"{type(exc).__name__}:{exc}"

    report["roots"] = {}
    for root in ROOTS:
        report["roots"][str(root)] = {
            "exists": root.exists(),
            "top": top_children(root, 50) if root.exists() else [],
            "total": dir_stats(root) if root.exists() else {},
        }

    report["heavy"] = {}
    data = Path("/app/data")
    for rel in HEAVY:
        p = data / rel
        report["heavy"][rel] = dir_stats(p)
        if p.exists() and p.is_dir():
            report["heavy"][rel]["top"] = top_children(p, 20)

    # lifecycle quarantine detail
    lq = data / "runtime/v3/lifecycle_bundle_index"
    if lq.exists():
        report["lifecycle_index_listing"] = sorted(p.name for p in lq.iterdir())[:60]
        rq = lq / "recovery-quarantine"
        if rq.exists():
            report["recovery_quarantine_top"] = top_children(rq, 30)

    # wipe receipt retained sample
    rr = data / "runtime/research_reset_receipts"
    if rr.exists():
        ops = sorted(rr.glob("*/operation.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        report["recent_wipe_ops"] = []
        for op in ops:
            try:
                j = json.loads(op.read_text("utf-8"))
                proof = j.get("proof") or {}
                deletion = (j.get("deletion") or {}).get("deletion_receipt") or {}
                report["recent_wipe_ops"].append({
                    "path": str(op.relative_to(data / "runtime")).replace("\\", "/"),
                    "stage": j.get("stage"),
                    "new_epoch_id": j.get("new_epoch_id") or proof.get("new_epoch_id"),
                    "deleted_count": len(j.get("deleted") or []),
                    "retained_count": len(j.get("retained") or deletion.get("retained") or []),
                    "deletion_status": deletion.get("status"),
                })
            except Exception as exc:
                report["recent_wipe_ops"].append({"path": str(op), "error": str(exc)})

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
