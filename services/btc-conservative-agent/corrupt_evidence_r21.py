"""R21 expiry for corrupt_evidence_quarantine/ (HM audit 30 Oct BOT-36).

Lists aged quarantine dirs with sha256 fingerprints. Delete only via
``confirm_list_sha`` after RK verifies the dry-run list. Default is dry-run.
Never touches laptop gs-export temp or ``.puller-backup-*``.
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
DEFAULT_MAX_AGE_SEC = 14 * 86400  # 14 days
QUARANTINE_DIRNAME = "corrupt_evidence_quarantine"


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_expired(
    data_dir: str | os.PathLike,
    *,
    now: float | None = None,
    max_age_sec: float = DEFAULT_MAX_AGE_SEC,
) -> dict[str, Any]:
    """Return a sha-listed dry-run plan of expired corrupt_evidence quarantine dirs."""
    root = Path(data_dir) / QUARANTINE_DIRNAME
    now = float(time.time() if now is None else now)
    entries: list[dict[str, Any]] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            age = now - mtime
            if age < float(max_age_sec):
                continue
            files = []
            for f in sorted(child.rglob("*")):
                if f.is_file():
                    files.append({
                        "rel": f.relative_to(root).as_posix(),
                        "sha256": _sha_file(f),
                        "size": f.stat().st_size,
                    })
            entries.append({
                "dir": child.name,
                "mtime": mtime,
                "age_sec": round(age, 3),
                "files": files,
            })
    blob = json.dumps({"entries": entries, "max_age_sec": max_age_sec}, sort_keys=True).encode()
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
        "note": "ids/paths + sha only; confirm with confirm_list_sha to delete",
    }


def confirm_delete(
    data_dir: str | os.PathLike,
    *,
    plan_sha256: str,
    now: float | None = None,
    max_age_sec: float = DEFAULT_MAX_AGE_SEC,
) -> dict[str, Any]:
    """Delete exactly the dry-run list when sha matches; refuse on mismatch."""
    plan = plan_expired(data_dir, now=now, max_age_sec=max_age_sec)
    if plan["plan_sha256"] != plan_sha256:
        return {
            "ok": False,
            "error": "PLAN_SHA_MISMATCH",
            "expected": plan_sha256,
            "actual": plan["plan_sha256"],
            "deleted": [],
        }
    root = Path(data_dir) / QUARANTINE_DIRNAME
    deleted = []
    for entry in plan["entries"]:
        target = root / entry["dir"]
        if not target.is_dir():
            return {"ok": False, "error": "MISSING_DIR", "dir": entry["dir"], "deleted": deleted}
        # verify file shas still match
        for fmeta in entry["files"]:
            fpath = root / fmeta["rel"]
            if not fpath.is_file() or _sha_file(fpath) != fmeta["sha256"]:
                return {"ok": False, "error": "FILE_SHA_MISMATCH", "path": fmeta["rel"], "deleted": deleted}
        shutil.rmtree(target)
        deleted.append(entry["dir"])
    return {
        "ok": True,
        "schema": SCHEMA,
        "mode": "CONFIRM_DELETE",
        "plan_sha256": plan_sha256,
        "deleted": deleted,
        "count": len(deleted),
    }
