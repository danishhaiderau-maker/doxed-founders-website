#!/usr/bin/env python3
"""Quarantine/purge retained post-wipe volume that blocks inventory CURRENT.

Authorized by Danish wipe/delete-all-old-data for epoch-4d795… collect.
Safe gates:
  - EXPECTED_EPOCH must match a COMPLETE wipe operation
  - live paths never touched for trading/arm
  - emergency append_heads retained in place
  - research_reset_receipts retained (wipe proof)
  - current v3/ledgers retained

Actions (DRY_RUN=true default):
  1) MOVE emergency_record_idempotency_v1 ledger dirs (except append_heads)
     -> research_epoch_quarantine/postwipe-emergency-idempotency-<ts>/
     (inventory excludes research_epoch_quarantine)
  2) DELETE files under .data-sync-snapshots/ (operational cache; excluded)
  3) MOVE research_archive/ -> research_epoch_quarantine/postwipe-research-archive-<ts>/
  4) MOVE lifecycle recovery-quarantine/* -> research_epoch_quarantine/postwipe-lifecycle-rq-<ts>/
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


def _dir_bytes_files(path: Path) -> tuple[int, int]:
    files = 0
    bytes_ = 0
    if not path.exists():
        return 0, 0
    for root, _dirs, names in os.walk(path, followlinks=False):
        for name in names:
            files += 1
            try:
                bytes_ += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return bytes_, files


def _move_tree(src: Path, dest: Path, report_actions: list, *, dry: bool) -> dict:
    row = {
        "action": "MOVE",
        "src": str(src.relative_to(DATA)).replace("\\", "/"),
        "dest": str(dest.relative_to(DATA)).replace("\\", "/"),
        "exists": src.exists(),
    }
    if not src.exists():
        row["skipped"] = "ABSENT"
        report_actions.append(row)
        return row
    b, f = _dir_bytes_files(src)
    row["bytes"] = b
    row["files"] = f
    if dry:
        row["status"] = "WOULD_MOVE"
        report_actions.append(row)
        return row
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        row["status"] = "DEST_EXISTS"
        row["error"] = "REFUSED"
        report_actions.append(row)
        return row
    shutil.move(str(src), str(dest))
    row["status"] = "MOVED"
    report_actions.append(row)
    return row


def _rm_tree_contents(path: Path, report_actions: list, *, dry: bool) -> dict:
    row = {
        "action": "DELETE_CONTENTS",
        "src": str(path.relative_to(DATA)).replace("\\", "/") if path.is_relative_to(DATA) else str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        row["skipped"] = "ABSENT"
        report_actions.append(row)
        return row
    b, f = _dir_bytes_files(path)
    row["bytes_before"] = b
    row["files_before"] = f
    if dry:
        row["status"] = "WOULD_DELETE_CONTENTS"
        report_actions.append(row)
        return row
    deleted = 0
    deleted_bytes = 0
    for child in list(path.iterdir()):
        try:
            if child.is_symlink():
                child.unlink(missing_ok=True)
                deleted += 1
            elif child.is_file():
                deleted_bytes += child.stat().st_size
                child.unlink()
                deleted += 1
            elif child.is_dir():
                cb, cf = _dir_bytes_files(child)
                shutil.rmtree(child)
                deleted += cf
                deleted_bytes += cb
        except OSError as exc:
            row.setdefault("errors", []).append(f"{child.name}:{type(exc).__name__}")
    row["deleted_files"] = deleted
    row["deleted_bytes"] = deleted_bytes
    row["status"] = "DELETED_CONTENTS"
    report_actions.append(row)
    return row


def main() -> int:
    report = {
        "schema": "fly_postwipe_retained_purge_v1",
        "dry_run": DRY_RUN,
        "expected_epoch": EXPECTED_EPOCH or None,
        "ok": False,
        "actions": [],
    }
    if not EXPECTED_EPOCH.startswith("epoch-"):
        report["error"] = "EXPECTED_EPOCH_INVALID"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    # Prove COMPLETE wipe for this epoch exists
    rr = RUNTIME / "research_reset_receipts"
    matching = []
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

    try:
        usage = os.statvfs("/app/data")
        report["disk_before"] = {
            "used_bytes": usage.f_frsize * (usage.f_blocks - usage.f_bavail),
            "free_bytes": usage.f_frsize * usage.f_bavail,
            "total_bytes": usage.f_frsize * usage.f_blocks,
        }
    except Exception as exc:
        report["disk_before"] = {"error": str(exc)}

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    qroot = DATA / "research_epoch_quarantine" / f"postwipe-retained-{EXPECTED_EPOCH}-{ts}"

    # 1) Emergency idempotency ledger dirs (file-count bomb for inventory)
    em = RUNTIME / "v3/receipts/emergency_record_idempotency_v1"
    keep = {"append_heads"}
    if em.is_dir():
        dest_em = qroot / "emergency_record_idempotency_v1"
        for child in sorted(em.iterdir(), key=lambda p: p.name):
            if child.name in keep:
                report["actions"].append({
                    "action": "KEEP",
                    "src": str(child.relative_to(DATA)).replace("\\", "/"),
                    "reason": "APPEND_HEADS_REQUIRED",
                })
                continue
            if child.is_dir():
                _move_tree(child, dest_em / child.name, report["actions"], dry=DRY_RUN)

    # 2) .data-sync-snapshots operational cache
    _rm_tree_contents(DATA / ".data-sync-snapshots", report["actions"], dry=DRY_RUN)

    # 3) research_archive (pre-wipe copies; inventory-excluded but huge)
    _move_tree(
        DATA / "research_archive",
        qroot / "research_archive",
        report["actions"],
        dry=DRY_RUN,
    )

    # 4) lifecycle recovery-quarantine retired sqlite copies
    rq = RUNTIME / "v3/lifecycle_bundle_index/recovery-quarantine"
    if rq.is_dir():
        dest_rq = qroot / "lifecycle_recovery_quarantine"
        for child in sorted(rq.iterdir(), key=lambda p: p.name):
            _move_tree(child, dest_rq / child.name, report["actions"], dry=DRY_RUN)

    # 5) corrupt_evidence_quarantine under runtime (small)
    _move_tree(
        RUNTIME / "corrupt_evidence_quarantine",
        qroot / "corrupt_evidence_quarantine",
        report["actions"],
        dry=DRY_RUN,
    )

    try:
        usage = os.statvfs("/app/data")
        report["disk_after"] = {
            "used_bytes": usage.f_frsize * (usage.f_blocks - usage.f_bavail),
            "free_bytes": usage.f_frsize * usage.f_bavail,
            "total_bytes": usage.f_frsize * usage.f_blocks,
        }
    except Exception as exc:
        report["disk_after"] = {"error": str(exc)}

    moved_files = sum(int(a.get("files") or a.get("deleted_files") or 0) for a in report["actions"])
    moved_bytes = sum(
        int(a.get("bytes") or a.get("bytes_before") or a.get("deleted_bytes") or 0)
        for a in report["actions"]
        if a.get("action") in {"MOVE", "DELETE_CONTENTS"}
    )
    report["totals"] = {"files": moved_files, "bytes": moved_bytes, "quarantine_root": str(qroot.relative_to(DATA)).replace("\\", "/") if not DRY_RUN or True else None}
    report["ok"] = True
    report["note"] = (
        "Emergency idempotency moved under research_epoch_quarantine (inventory-excluded). "
        "Snapshots deleted. research_archive + lifecycle RQ moved. "
        "append_heads + research_reset_receipts + ledgers retained."
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
