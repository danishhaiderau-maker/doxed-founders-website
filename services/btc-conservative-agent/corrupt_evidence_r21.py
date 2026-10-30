"""R21 expiry for corrupt_evidence_quarantine/ (HM audit 30 Oct BOT-36).

Lists aged quarantine dirs with sha256 fingerprints. Delete only via
``confirm_delete(plan_sha256=...)`` after RK verifies the dry-run list.
Default is dry-run. Never touches laptop gs-export temp or ``.puller-backup-*``.

Admin surface: ``POST /api/admin/corrupt-evidence-r21`` (dry_run default True).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

SCHEMA = "corrupt_evidence_r21_list_v1"
# Short until RK verifies expiry behaviour (open R5); raise only after verification.
DEFAULT_MAX_AGE_SEC = 2 * 86400
QUARANTINE_DIRNAME = "corrupt_evidence_quarantine"


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _disk_free(path: Path) -> int | None:
    try:
        return int(shutil.disk_usage(path if path.exists() else path.parent).free)
    except OSError:
        return None


def _dir_inventory(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Map child-dir name → sorted file sha rows (for remaining-dir invariance)."""
    out: dict[str, list[dict[str, Any]]] = {}
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.is_symlink():
            continue
        files = []
        for f in sorted(child.rglob("*")):
            if f.is_symlink():
                continue
            if f.is_file():
                files.append({
                    "rel": f.relative_to(root).as_posix(),
                    "sha256": _sha_file(f),
                    "size": f.stat().st_size,
                })
        out[child.name] = files
    return out


def plan_expired(
    data_dir: str | os.PathLike,
    *,
    now: float | None = None,
    max_age_sec: float = DEFAULT_MAX_AGE_SEC,
) -> dict[str, Any]:
    """Return a sha-listed dry-run plan of expired corrupt_evidence quarantine dirs."""
    root = Path(data_dir) / QUARANTINE_DIRNAME
    now = float(time.time() if now is None else now)
    max_age_sec = float(max_age_sec)
    entries: list[dict[str, Any]] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_symlink():
                continue
            if not child.is_dir():
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            age = now - mtime
            if age < max_age_sec:
                continue
            files = []
            symlink_refused = False
            for f in sorted(child.rglob("*")):
                if f.is_symlink():
                    symlink_refused = True
                    break
                if f.is_file():
                    files.append({
                        "rel": f.relative_to(root).as_posix(),
                        "sha256": _sha_file(f),
                        "size": f.stat().st_size,
                    })
            if symlink_refused:
                continue  # never plan a tree that contains a symlink
            entries.append({
                "dir": child.name,
                "mtime": mtime,
                "age_sec": round(age, 3),
                "files": files,
            })
    # Fingerprint stable fields only (age_sec moves with wall clock and must not invalidate the plan).
    stable = [{"dir": e["dir"], "mtime": e["mtime"], "files": e["files"]} for e in entries]
    blob = json.dumps({"entries": stable, "max_age_sec": max_age_sec}, sort_keys=True).encode()
    plan_sha = hashlib.sha256(blob).hexdigest()
    return {
        "schema": SCHEMA,
        "mode": "DRY_RUN",
        "execute": False,
        "quarantine_root": str(root),
        "max_age_sec": max_age_sec,
        "count": len(entries),
        "entries": entries,
        "plan_sha256": plan_sha,
        "free_bytes_before": _disk_free(root if root.exists() else Path(data_dir)),
        "note": "ids/paths + sha only; confirm with confirm_delete(plan_sha256=...) to delete",
    }


def confirm_delete(
    data_dir: str | os.PathLike,
    *,
    plan_sha256: str,
    now: float | None = None,
    max_age_sec: float = DEFAULT_MAX_AGE_SEC,
) -> dict[str, Any]:
    """Delete exactly the dry-run list when sha matches; refuse on mismatch.

    R21: before_count, after = before − list, read-back each deleted dir gone,
    remaining dirs unchanged by sha, free_bytes before/after.
    """
    root = Path(data_dir) / QUARANTINE_DIRNAME
    free_before = _disk_free(root if root.exists() else Path(data_dir))
    before_inventory = _dir_inventory(root)
    before_count = len(before_inventory)
    plan = plan_expired(data_dir, now=now, max_age_sec=max_age_sec)
    if plan["plan_sha256"] != plan_sha256:
        return {
            "ok": False,
            "error": "PLAN_SHA_MISMATCH",
            "expected": plan_sha256,
            "actual": plan["plan_sha256"],
            "deleted": [],
            "before_count": before_count,
            "after_count": before_count,
            "free_bytes_before": free_before,
            "free_bytes_after": free_before,
        }
    deleted = []
    for entry in plan["entries"]:
        target = root / entry["dir"]
        if target.is_symlink():
            return {
                "ok": False, "error": "SYMLINK_REFUSED", "dir": entry["dir"],
                "deleted": deleted, "before_count": before_count,
                "after_count": len(_dir_inventory(root)),
                "free_bytes_before": free_before,
                "free_bytes_after": _disk_free(root if root.exists() else Path(data_dir)),
            }
        if not target.is_dir():
            return {
                "ok": False, "error": "MISSING_DIR", "dir": entry["dir"],
                "deleted": deleted, "before_count": before_count,
                "after_count": len(_dir_inventory(root)),
                "free_bytes_before": free_before,
                "free_bytes_after": _disk_free(root if root.exists() else Path(data_dir)),
            }
        # Per-file sha re-verify (B3): plan sha alone is not enough if a file swapped
        # under a sticky/monkeypatched plan.
        for fmeta in entry["files"]:
            fpath = root / fmeta["rel"]
            if fpath.is_symlink():
                return {
                    "ok": False, "error": "SYMLINK_REFUSED", "path": fmeta["rel"],
                    "deleted": deleted, "before_count": before_count,
                    "after_count": len(_dir_inventory(root)),
                    "free_bytes_before": free_before,
                    "free_bytes_after": _disk_free(root if root.exists() else Path(data_dir)),
                }
            if not fpath.is_file() or _sha_file(fpath) != fmeta["sha256"]:
                return {
                    "ok": False, "error": "FILE_SHA_MISMATCH", "path": fmeta["rel"],
                    "deleted": deleted, "before_count": before_count,
                    "after_count": len(_dir_inventory(root)),
                    "free_bytes_before": free_before,
                    "free_bytes_after": _disk_free(root if root.exists() else Path(data_dir)),
                }
        shutil.rmtree(target)
        if target.exists():
            return {
                "ok": False, "error": "READBACK_STILL_PRESENT", "dir": entry["dir"],
                "deleted": deleted, "before_count": before_count,
                "after_count": len(_dir_inventory(root)),
                "free_bytes_before": free_before,
                "free_bytes_after": _disk_free(root if root.exists() else Path(data_dir)),
            }
        deleted.append(entry["dir"])
    after_inventory = _dir_inventory(root)
    after_count = len(after_inventory)
    # Remaining dirs must be byte-identical by sha to the pre-delete inventory.
    for name, files in before_inventory.items():
        if name in deleted:
            continue
        if after_inventory.get(name) != files:
            return {
                "ok": False, "error": "REMAINING_DIR_SHA_CHANGED", "dir": name,
                "deleted": deleted, "before_count": before_count, "after_count": after_count,
                "free_bytes_before": free_before,
                "free_bytes_after": _disk_free(root if root.exists() else Path(data_dir)),
            }
    expected_after = before_count - len(deleted)
    if after_count != expected_after:
        return {
            "ok": False, "error": "AFTER_COUNT_MISMATCH",
            "expected_after": expected_after, "after_count": after_count,
            "deleted": deleted, "before_count": before_count,
            "free_bytes_before": free_before,
            "free_bytes_after": _disk_free(root if root.exists() else Path(data_dir)),
        }
    free_after = _disk_free(root if root.exists() else Path(data_dir))
    return {
        "ok": True,
        "schema": SCHEMA,
        "mode": "CONFIRM_DELETE",
        "plan_sha256": plan_sha256,
        "deleted": deleted,
        "count": len(deleted),
        "before_count": before_count,
        "after_count": after_count,
        "free_bytes_before": free_before,
        "free_bytes_after": free_after,
        "readback_gone": list(deleted),
    }
