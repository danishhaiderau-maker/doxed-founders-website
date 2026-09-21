#!/usr/bin/env python3
"""Fast post-wipe retained purge/quarantine. Rename-first, no deep walks.

Moves inventory file-bombs under research_epoch_quarantine (excluded from
data-sync inventory). Deletes .data-sync-snapshots contents. Keeps
append_heads, research_reset_receipts, and v3/ledgers.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

DATA = Path("/app/data")
RUNTIME = DATA / "runtime"
EXPECTED_EPOCH = str(os.environ.get("EXPECTED_EPOCH") or "").strip()
DRY_RUN = str(os.environ.get("DRY_RUN", "true")).strip().lower() in {"1", "true", "yes"}


def disk() -> dict:
    try:
        u = os.statvfs("/app/data")
        return {
            "used_bytes": u.f_frsize * (u.f_blocks - u.f_bavail),
            "free_bytes": u.f_frsize * u.f_bavail,
            "total_bytes": u.f_frsize * u.f_blocks,
        }
    except Exception as exc:
        return {"error": str(exc)}


def child_names(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    try:
        return sorted(p.name for p in path.iterdir())
    except OSError:
        return []


def move_path(src: Path, dest: Path, actions: list) -> None:
    rel_src = str(src.relative_to(DATA)).replace("\\", "/")
    rel_dest = str(dest.relative_to(DATA)).replace("\\", "/")
    row = {"action": "MOVE", "src": rel_src, "dest": rel_dest, "exists": src.exists()}
    if not src.exists():
        row["skipped"] = "ABSENT"
        actions.append(row)
        return
    if dest.exists():
        row["status"] = "DEST_EXISTS_REFUSED"
        actions.append(row)
        return
    if DRY_RUN:
        row["status"] = "WOULD_MOVE"
        row["children"] = child_names(src)[:30]
        actions.append(row)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    row["status"] = "MOVED"
    actions.append(row)


def delete_contents(path: Path, actions: list) -> None:
    rel = str(path.relative_to(DATA)).replace("\\", "/")
    row = {"action": "DELETE_CONTENTS", "src": rel, "exists": path.exists()}
    if not path.exists():
        row["skipped"] = "ABSENT"
        actions.append(row)
        return
    names = child_names(path)
    row["child_count_before"] = len(names)
    row["children_sample"] = names[:20]
    if DRY_RUN:
        row["status"] = "WOULD_DELETE_CONTENTS"
        actions.append(row)
        return
    deleted = 0
    errors = []
    for child in list(path.iterdir()):
        try:
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
                deleted += 1
            elif child.is_dir():
                shutil.rmtree(child)
                deleted += 1
        except OSError as exc:
            errors.append(f"{child.name}:{type(exc).__name__}")
    row["deleted_entries"] = deleted
    if errors:
        row["errors"] = errors[:20]
    row["status"] = "DELETED_CONTENTS"
    actions.append(row)


def main() -> int:
    report = {
        "schema": "fly_postwipe_retained_purge_v1",
        "dry_run": DRY_RUN,
        "expected_epoch": EXPECTED_EPOCH or None,
        "ok": False,
        "actions": [],
        "disk_before": disk(),
    }
    if not EXPECTED_EPOCH.startswith("epoch-"):
        report["error"] = "EXPECTED_EPOCH_INVALID"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    matching = []
    rr = RUNTIME / "research_reset_receipts"
    for op in sorted(rr.glob("*/operation.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
        try:
            j = json.loads(op.read_text("utf-8"))
        except Exception:
            continue
        proof = j.get("proof") or {}
        new_epoch = j.get("new_epoch_id") or proof.get("new_epoch_id")
        if new_epoch == EXPECTED_EPOCH and j.get("stage") == "COMPLETE":
            matching.append(str(op.relative_to(RUNTIME)).replace("\\", "/"))
    report["matching_wipe_ops"] = matching
    if not matching:
        report["error"] = "NO_COMPLETE_WIPE_OPERATION_FOR_EXPECTED_EPOCH"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 3

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    qroot = DATA / "research_epoch_quarantine" / f"postwipe-retained-{EXPECTED_EPOCH}-{ts}"
    report["quarantine_root"] = str(qroot.relative_to(DATA)).replace("\\", "/")

    # 1) Emergency idempotency ledgers (inventory file-count bomb)
    em = RUNTIME / "v3/receipts/emergency_record_idempotency_v1"
    keep = {"append_heads"}
    if em.is_dir():
        for name in child_names(em):
            child = em / name
            if name in keep:
                report["actions"].append({
                    "action": "KEEP",
                    "src": str(child.relative_to(DATA)).replace("\\", "/"),
                    "reason": "APPEND_HEADS_REQUIRED",
                })
                continue
            if child.is_dir():
                move_path(child, qroot / "emergency_record_idempotency_v1" / name, report["actions"])

    # 2) Operational snapshot cache
    delete_contents(DATA / ".data-sync-snapshots", report["actions"])

    # 3) Pre-wipe research_archive
    move_path(DATA / "research_archive", qroot / "research_archive", report["actions"])

    # 4) Lifecycle recovery-quarantine copies
    rq = RUNTIME / "v3/lifecycle_bundle_index/recovery-quarantine"
    if rq.is_dir():
        for name in child_names(rq):
            move_path(rq / name, qroot / "lifecycle_recovery_quarantine" / name, report["actions"])

    # 5) corrupt evidence quarantine
    move_path(
        RUNTIME / "corrupt_evidence_quarantine",
        qroot / "corrupt_evidence_quarantine",
        report["actions"],
    )

    report["disk_after"] = disk()
    report["ok"] = True
    report["note"] = (
        "Moved emergency idempotency ledgers + research_archive + lifecycle RQ "
        "under research_epoch_quarantine (inventory-excluded). Deleted "
        ".data-sync-snapshots contents. Kept append_heads, wipe receipts, ledgers."
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
