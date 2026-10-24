"""Delete closed trades from the trades CSV by explicit id list (RK PASS 24 Oct, R17 995 delete).

Root cause: no sanctioned delete-by-id mechanism existed for trades_3factor.csv.  This module is the
only writer for that operation; bot.py wraps it in admin-token routes and holds the trade-file lock.

Contract
* ``list_sha256`` = sha256 of ``json.dumps(ids)`` (Python default separators, ids in the given order).
  That equals the sha256 of an RK id file written with ``json.dump(ids, f)``.
* Refusals (nothing written): empty list, duplicates, sha mismatch, any id not present in the file,
  duplicate ids already in the file for a requested id, unreadable/headerless file.
* dry_run defaults to True and never writes, never snapshots.
* Real run: temporary rollback snapshot (copy + fsync), then tmp + fsync + os.replace + dir fsync.
* Invariant: hash of the remaining (non-removed) rows before == after; otherwise the original is
  restored from the snapshot and the call fails.
* Snapshots are temporary: ``delete_rollback_snapshot`` removes one and reads back that it is gone.
  They are never archived.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import time
from typing import Any, Iterable

SNAPSHOT_DIRNAME = ".trade_delete_rollback"
ID_FIELD = "trade_id"
_SNAPSHOT_RE = re.compile(r"^[A-Za-z0-9_.-]+\.rollback\.csv$")


class DeleteRefused(ValueError):
    def __init__(self, code: str, detail: Any = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def list_sha256(ids: list) -> str:
    return hashlib.sha256(json.dumps(ids).encode("utf-8")).hexdigest()


def _ids_hash(ids: Iterable[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(ids)).encode("utf-8")).hexdigest()


def _rows_hash(rows: list[list[str]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(r).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _read(path: str) -> tuple[list[str], list[list[str]], int]:
    if not os.path.isfile(path):
        raise DeleteRefused("FILE_MISSING")
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise DeleteRefused("FILE_EMPTY")
        rows = [r for r in reader]
    if ID_FIELD not in header:
        raise DeleteRefused("NO_TRADE_ID_COLUMN")
    return header, rows, header.index(ID_FIELD)


def _fsync_dir(d: str) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate(ids: Any, sha: Any) -> list[str]:
    if not isinstance(ids, list) or not ids:
        raise DeleteRefused("EMPTY_LIST")
    if not all(isinstance(i, str) and i for i in ids):
        raise DeleteRefused("BAD_ID_TYPE")
    seen, dups = set(), []
    for i in ids:
        if i in seen:
            dups.append(i)
        seen.add(i)
    if dups:
        raise DeleteRefused("DUPLICATE_IDS", sorted(set(dups))[:20])
    if not isinstance(sha, str) or list_sha256(ids) != sha.strip().lower():
        raise DeleteRefused("SHA_MISMATCH", {"computed": list_sha256(ids)})
    return ids


def delete_trades_by_id(path: str, ids: Any, sha: Any, *, dry_run: bool = True,
                        lock=None, now=time.time, _replace=os.replace) -> dict:
    """Validate, then (unless dry_run) remove exactly ``ids`` from the CSV at ``path``."""
    ids = _validate(ids, sha)
    path = os.path.abspath(path)
    ctx = lock if lock is not None else _NullLock()
    with ctx:
        header, rows, k = _read(path)
        want = set(ids)
        present: dict[str, int] = {}
        for r in rows:
            tid = r[k] if k < len(r) else ""
            if tid in want:
                present[tid] = present.get(tid, 0) + 1
        missing = [i for i in ids if i not in present]
        if missing:
            raise DeleteRefused("IDS_NOT_PRESENT", {"count": len(missing), "first": missing[:20]})
        multi = sorted(i for i, n in present.items() if n > 1)
        if multi:
            raise DeleteRefused("ID_MULTIPLE_ROWS_IN_FILE", multi[:20])
        keep = [r for r in rows if (r[k] if k < len(r) else "") not in want]
        result = {
            "dry_run": bool(dry_run),
            "path": path,
            "list_sha256": list_sha256(ids),
            "before_count": len(rows),
            "removed_count": len(rows) - len(keep),
            "after_count": len(keep),
            "removed_ids_sha256": _ids_hash(ids),
            "remaining_rows_sha256_before": _rows_hash(keep),
            "remaining_rows_sha256_after": None,
            "snapshot_path": None,
        }
        if dry_run:
            return result
        d = os.path.dirname(path)
        snap_dir = os.path.join(d, SNAPSHOT_DIRNAME)
        os.makedirs(snap_dir, exist_ok=True)
        snap = os.path.join(snap_dir, f"{os.path.basename(path)}.{int(now() * 1000)}.rollback.csv")
        shutil.copyfile(path, snap)
        with open(snap, "rb") as f:
            os.fsync(f.fileno())
        _fsync_dir(snap_dir)
        result["snapshot_path"] = snap
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        w.writerows(keep)
        tmp = f"{path}.delete-{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(buf.getvalue())
                f.flush()
                os.fsync(f.fileno())
            _replace(tmp, path)
            _fsync_dir(d)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        h2, rows2, k2 = _read(path)
        after_hash = _rows_hash(rows2)
        result["remaining_rows_sha256_after"] = after_hash
        result["after_count"] = len(rows2)
        left = sum(1 for r in rows2 if (r[k2] if k2 < len(r) else "") in want)
        if h2 != header or after_hash != result["remaining_rows_sha256_before"] or left:
            shutil.copyfile(snap, tmp)
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            os.replace(tmp, path)
            raise DeleteRefused("INVARIANT_FAILED_RESTORED", {"snapshot_path": snap})
        return result


def delete_rollback_snapshot(trades_path: str, snapshot_path: str, *, lock=None) -> dict:
    """Remove one rollback snapshot (only inside the snapshot dir) and read back that it is gone."""
    snap_dir = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(trades_path)), SNAPSHOT_DIRNAME))
    p = os.path.realpath(str(snapshot_path or ""))
    if os.path.dirname(p) != snap_dir or not _SNAPSHOT_RE.match(os.path.basename(p)):
        raise DeleteRefused("SNAPSHOT_PATH_NOT_ALLOWED")
    with (lock if lock is not None else _NullLock()):
        if not os.path.isfile(p):
            raise DeleteRefused("SNAPSHOT_MISSING")
        os.unlink(p)
        _fsync_dir(snap_dir)
        exists = os.path.exists(p)
    return {"snapshot_path": p, "deleted": not exists, "exists_after": exists}


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
