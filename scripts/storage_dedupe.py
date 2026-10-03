"""Laptop storage inventory and verified de-duplication for C:\\DoxxedCrypto.

    python scripts/storage_dedupe.py inventory --db <inv.sqlite3> --root C:\\DoxxedCrypto
    python scripts/storage_dedupe.py report --db <inv.sqlite3>
    python scripts/storage_dedupe.py reclaim --db <inv.sqlite3> --junk <dir> [--junk <dir>] \\
        --keep <dir> [--keep <dir>] --retain-dir <dir> [--apply]

``inventory`` records every file (size, mtime, NTFS file id, link count) and
sha256 for files that could be duplicates (same size, different inode). Hashes
are cached by (file id, size, mtime) so re-runs only hash new bytes.

``reclaim`` removes files under ``--junk`` roots only when their exact bytes
are proven to exist elsewhere:

* COVERED_IDENTICAL: a file under a ``--keep`` root (or another junk file
  already chosen as the retained copy) has the same size and sha256;
* COVERED_PREFIX: the same relative path under a keep root is an append-only
  superset (its first N bytes hash to the junk file's sha256);
* REGENERABLE: caches (``__pycache__``, ``.pytest_cache``, ``pytest-tmp``).

Anything else is UNIQUE and is moved (once, verified) into ``--retain-dir``
instead of deleted, so the only copy of a byte sequence is never lost.
Without ``--apply`` nothing changes and the plan is written as JSON.
Protected classes (relay/Bitfinex evidence, recovery state, paper ledgers,
<14 day 1s tape) are never deleted from keep roots; junk roots are only
reclaimed under the rules above. Never prints secrets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_CHUNK = 4 * 1024 * 1024
REGENERABLE_PARTS = ("__pycache__", ".pytest_cache", "pytest-tmp", "pytest-of-", ".mypy_cache", ".ruff_cache")
SKIP_DIR_NAMES = {".git", "node_modules"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY, top TEXT, size INTEGER, mtime_ns INTEGER,
  dev INTEGER, ino INTEGER, nlink INTEGER, sha256 TEXT, scanned_at REAL);
CREATE INDEX IF NOT EXISTS files_size ON files(size);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha256);
CREATE INDEX IF NOT EXISTS files_top ON files(top);
CREATE TABLE IF NOT EXISTS hash_cache (
  dev INTEGER, ino INTEGER, size INTEGER, mtime_ns INTEGER, sha256 TEXT,
  PRIMARY KEY (dev, ino, size, mtime_ns));
CREATE TABLE IF NOT EXISTS prefix_cache (
  path TEXT, size INTEGER, mtime_ns INTEGER, prefix INTEGER, sha256 TEXT,
  PRIMARY KEY (path, size, mtime_ns, prefix));
"""


def sha256_file(path: str, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    remaining = limit
    with open(path, "rb") as handle:
        while remaining is None or remaining > 0:
            chunk = handle.read(_CHUNK if remaining is None else min(_CHUNK, remaining))
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return digest.hexdigest()


def connect(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    return conn


def _top(root: Path, path: str) -> str:
    rel = os.path.relpath(path, root)
    return rel.split(os.sep, 1)[0]


def is_reparse_point(path: str) -> bool:
    """Junctions and symlinks: walking them would visit (and could delete) another tree's files."""
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400) or os.path.islink(path)


def real_under(path: str, root: str) -> bool:
    real = os.path.normcase(os.path.realpath(path))
    base = os.path.normcase(os.path.realpath(root)).rstrip(os.sep)
    return real.startswith(base + os.sep)


def walk(root: Path, skip_git: bool = True):
    for directory, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not is_reparse_point(os.path.join(directory, d))
                   and not (skip_git and d in SKIP_DIR_NAMES)]
        for name in names:
            full = os.path.join(directory, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            yield full, st


def inventory(db: str, roots: list[Path], *, min_hash_size: int, workers: int, hash_all_under: list[Path]) -> dict:
    conn = connect(db)
    started = time.time()
    rows = []
    for root in roots:
        for full, st in walk(root):
            rows.append((full, _top(root, full), st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino, st.st_nlink,
                         None, started))
    conn.execute("DELETE FROM files")
    conn.executemany("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    # Candidate duplicates: same size, more than one distinct inode.
    sizes = {size for (size,) in conn.execute(
        "SELECT size FROM files WHERE size >= ? GROUP BY size HAVING COUNT(DISTINCT dev || ':' || ino) > 1",
        (min_hash_size,))}
    forced = [str(p).lower() for p in hash_all_under]
    todo: dict[tuple, str] = {}
    for path, size, mtime_ns, dev, ino in conn.execute("SELECT path, size, mtime_ns, dev, ino FROM files"):
        if size in sizes or any(path.lower().startswith(prefix) for prefix in forced):
            todo.setdefault((dev, ino, size, mtime_ns), path)
    cached = {}
    for dev, ino, size, mtime_ns, sha in conn.execute("SELECT * FROM hash_cache"):
        cached[(dev, ino, size, mtime_ns)] = sha
    pending = {key: path for key, path in todo.items() if key not in cached}

    def job(item):
        key, path = item
        try:
            return key, sha256_file(path)
        except OSError:
            return key, None

    hashed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, sha in pool.map(job, pending.items(), chunksize=64):
            if sha:
                cached[key] = sha
                conn.execute("INSERT OR REPLACE INTO hash_cache VALUES (?,?,?,?,?)", (*key, sha))
                hashed += 1
                if hashed % 5000 == 0:
                    conn.commit()
    conn.commit()
    updates = []
    for path, size, mtime_ns, dev, ino in conn.execute("SELECT path, size, mtime_ns, dev, ino FROM files"):
        sha = cached.get((dev, ino, size, mtime_ns))
        if sha:
            updates.append((sha, path))
    conn.executemany("UPDATE files SET sha256=? WHERE path=?", updates)
    conn.commit()
    return {"files": len(rows), "hash_candidates": len(todo), "hashed_now": hashed,
            "elapsed_sec": round(time.time() - started, 1)}


def report(db: str, top_n: int = 40) -> dict:
    conn = connect(db)
    by_top = []
    for top, files, logical in conn.execute(
            "SELECT top, COUNT(*), SUM(size) FROM files GROUP BY top ORDER BY SUM(size) DESC"):
        physical = conn.execute(
            "SELECT COALESCE(SUM(size),0) FROM (SELECT DISTINCT dev, ino, size FROM files WHERE top=?)", (top,)
        ).fetchone()[0]
        by_top.append({"top": top, "files": files, "logical_gb": round(logical / 1e9, 3),
                       "physical_gb": round(physical / 1e9, 3)})
    total_logical = conn.execute("SELECT COALESCE(SUM(size),0) FROM files").fetchone()[0]
    total_physical = conn.execute(
        "SELECT COALESCE(SUM(size),0) FROM (SELECT DISTINCT dev, ino, size FROM files)").fetchone()[0]
    # Bytes held in more than one physical copy (same sha, distinct inodes).
    dup = conn.execute("""
        SELECT COALESCE(SUM(extra),0) FROM (
          SELECT (COUNT(DISTINCT dev || ':' || ino) - 1) * size AS extra
          FROM files WHERE sha256 IS NOT NULL GROUP BY sha256, size)""").fetchone()[0]
    pairs = {}
    for sha, size, tops in conn.execute("""
        SELECT sha256, size, GROUP_CONCAT(DISTINCT top) FROM files WHERE sha256 IS NOT NULL
        GROUP BY sha256, size HAVING COUNT(DISTINCT dev || ':' || ino) > 1"""):
        key = ",".join(sorted(tops.split(",")))
        pairs[key] = pairs.get(key, 0) + size
    top_pairs = sorted(pairs.items(), key=lambda kv: -kv[1])[:top_n]
    return {"logical_gb": round(total_logical / 1e9, 3), "physical_gb": round(total_physical / 1e9, 3),
            "duplicate_physical_copies_gb": round(dup / 1e9, 3), "by_top": by_top[:top_n],
            "duplicate_groups_by_tops_gb": [{"tops": k, "gb_per_copy": round(v / 1e9, 3)} for k, v in top_pairs]}


def _under(path: str, roots: list[str]) -> str | None:
    low = path.lower()
    for root in roots:
        if low == root or low.startswith(root + os.sep):
            return root
    return None


def _prefix_sha(conn, path: str, prefix: int) -> str | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    row = conn.execute("SELECT sha256 FROM prefix_cache WHERE path=? AND size=? AND mtime_ns=? AND prefix=?",
                       (path, st.st_size, st.st_mtime_ns, prefix)).fetchone()
    if row:
        return row[0]
    sha = sha256_file(path, prefix)
    conn.execute("INSERT OR REPLACE INTO prefix_cache VALUES (?,?,?,?,?)", (path, st.st_size, st.st_mtime_ns, prefix, sha))
    return sha


def plan_reclaim(db: str, junk: list[Path], keep: list[Path], *, prefix_matching: bool = True) -> dict:
    conn = connect(db)
    junk_roots = [os.path.normcase(os.path.abspath(p)).rstrip(os.sep) for p in junk]
    keep_roots = [os.path.normcase(os.path.abspath(p)).rstrip(os.sep) for p in keep]
    keep_by_sha: dict[tuple, str] = {}
    keep_by_rel: dict[str, list[tuple[str, int]]] = {}
    junk_rows = []
    for path, size, sha, dev, ino in conn.execute("SELECT path, size, sha256, dev, ino FROM files"):
        k = _under(path, keep_roots)
        if k:
            if sha:
                keep_by_sha.setdefault((sha, size), path)
            keep_by_rel.setdefault(os.path.basename(os.path.normcase(path)), []).append((path, size))
            continue
        j = _under(path, junk_roots)
        if j and real_under(path, j):
            junk_rows.append((path, size, sha, dev, ino, j))
    retained_sha: dict[tuple, str] = {}
    plan = {"COVERED_IDENTICAL": [], "COVERED_PREFIX": [], "REGENERABLE": [], "DUPLICATE_WITHIN_JUNK": [],
            "UNIQUE_RETAIN": [], "UNHASHED": []}
    for path, size, sha, dev, ino, root in sorted(junk_rows):
        parts = path.lower().split(os.sep)
        if any(part.startswith(REGENERABLE_PARTS) or part in REGENERABLE_PARTS for part in parts):
            plan["REGENERABLE"].append({"path": path, "bytes": size})
            continue
        if sha is None:
            try:
                sha = sha256_file(path)
            except OSError:
                plan["UNHASHED"].append({"path": path, "bytes": size})
                continue
        if (sha, size) in keep_by_sha:
            plan["COVERED_IDENTICAL"].append({"path": path, "bytes": size, "sha256": sha,
                                              "copy": keep_by_sha[(sha, size)]})
            continue
        covered = None
        if prefix_matching and size > 0:
            # Any same-named larger keep file is a candidate; the prefix hash is the proof.
            candidates = sorted((c for c in keep_by_rel.get(os.path.basename(os.path.normcase(path)), [])
                                 if c[1] > size), key=lambda c: c[1])
            for candidate, _csize in candidates[:6]:
                if _prefix_sha(conn, candidate, size) == sha:
                    covered = candidate
                    break
        if covered:
            plan["COVERED_PREFIX"].append({"path": path, "bytes": size, "sha256": sha, "superset": covered})
            continue
        if (sha, size) in retained_sha:
            plan["DUPLICATE_WITHIN_JUNK"].append({"path": path, "bytes": size, "sha256": sha,
                                                  "copy": retained_sha[(sha, size)]})
            continue
        retained_sha[(sha, size)] = path
        plan["UNIQUE_RETAIN"].append({"path": path, "bytes": size, "sha256": sha, "root": root})
    conn.commit()
    summary = {k: {"files": len(v), "gb": round(sum(r["bytes"] for r in v) / 1e9, 3)} for k, v in plan.items()}
    return {"summary": summary, "plan": plan, "junk_roots": junk_roots, "keep_roots": keep_roots}


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, 0o666)
        os.unlink(path)


def apply_reclaim(result: dict, retain_dir: Path, ledger: Path) -> dict:
    retain_dir.mkdir(parents=True, exist_ok=True)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    moved_map: dict[str, str] = {}
    counts = {"deleted": 0, "deleted_bytes": 0, "retained": 0, "retained_bytes": 0, "skipped": 0}
    with ledger.open("a", encoding="utf-8") as log:
        def write(row):
            log.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}) + "\n")
        # Unique bytes first: move into the retained store, verified, before any deletion.
        for row in result["plan"]["UNIQUE_RETAIN"]:
            src = row["path"]
            root = row["root"]
            if not real_under(src, root):
                counts["skipped"] += 1
                continue
            rel = os.path.relpath(src, os.path.dirname(root))
            dst = retain_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(src, dst)
            except OSError as exc:
                write({"action": "RETAIN_FAILED", "path": src, "error": str(exc)})
                counts["skipped"] += 1
                continue
            if sha256_file(str(dst)) != row["sha256"]:
                raise RuntimeError(f"retained copy hash mismatch: {dst}")
            moved_map[src] = str(dst)
            counts["retained"] += 1
            counts["retained_bytes"] += row["bytes"]
            write({"action": "RETAIN", "path": src, "to": str(dst), "sha256": row["sha256"], "bytes": row["bytes"]})
        for kind in ("COVERED_IDENTICAL", "COVERED_PREFIX", "DUPLICATE_WITHIN_JUNK"):
            for row in result["plan"][kind]:
                copy = row.get("copy") or row.get("superset")
                copy = moved_map.get(copy, copy)
                try:
                    if kind == "COVERED_PREFIX":
                        ok = sha256_file(copy, row["bytes"]) == row["sha256"]
                    else:
                        ok = os.path.getsize(copy) == row["bytes"] and sha256_file(copy) == row["sha256"]
                    ok = ok and sha256_file(row["path"]) == row["sha256"]
                    ok = ok and any(real_under(row["path"], root) for root in result["junk_roots"])
                    ok = ok and os.path.normcase(os.path.realpath(copy)) != os.path.normcase(os.path.realpath(row["path"]))
                except OSError:
                    ok = False
                if not ok:
                    write({"action": "SKIP_UNVERIFIED", "kind": kind, "path": row["path"], "copy": copy})
                    counts["skipped"] += 1
                    continue
                _unlink(row["path"])
                counts["deleted"] += 1
                counts["deleted_bytes"] += row["bytes"]
                write({"action": "DELETE", "kind": kind, "path": row["path"], "sha256": row["sha256"],
                       "bytes": row["bytes"], "verified_copy": copy})
        for row in result["plan"]["REGENERABLE"]:
            if not any(real_under(row["path"], root) for root in result["junk_roots"]):
                counts["skipped"] += 1
                continue
            try:
                _unlink(row["path"])
            except OSError:
                counts["skipped"] += 1
                continue
            counts["deleted"] += 1
            counts["deleted_bytes"] += row["bytes"]
            write({"action": "DELETE", "kind": "REGENERABLE", "path": row["path"], "bytes": row["bytes"]})
    for root in result["junk_roots"]:
        for directory, dirs, names in os.walk(root, topdown=False):
            if not names and not dirs:
                try:
                    os.rmdir(directory)
                except OSError:
                    pass
            else:
                try:
                    if not os.listdir(directory):
                        os.rmdir(directory)
                except OSError:
                    pass
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--db", required=True)
    inv.add_argument("--root", action="append", required=True)
    inv.add_argument("--min-hash-size", type=int, default=1)
    inv.add_argument("--workers", type=int, default=8)
    inv.add_argument("--hash-all-under", action="append", default=[])
    rep = sub.add_parser("report")
    rep.add_argument("--db", required=True)
    rec = sub.add_parser("reclaim")
    rec.add_argument("--db", required=True)
    rec.add_argument("--junk", action="append", required=True)
    rec.add_argument("--keep", action="append", default=[])
    rec.add_argument("--retain-dir", required=True)
    rec.add_argument("--plan-out", required=True)
    rec.add_argument("--ledger", default=r"C:\DoxxedCrypto\bot-data-retention\storage-dedupe-ledger.jsonl")
    rec.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    for value in [getattr(args, "db", "")] + list(getattr(args, "root", []) or []) + list(getattr(args, "junk", []) or []):
        if "\\onedrive\\" in os.path.abspath(str(value)).lower():
            raise SystemExit("STORAGE_DEDUPE_ONEDRIVE_REFUSED")
    if args.cmd == "inventory":
        print(json.dumps(inventory(args.db, [Path(r) for r in args.root], min_hash_size=args.min_hash_size,
                                   workers=args.workers, hash_all_under=[Path(p) for p in args.hash_all_under]),
                         indent=2))
    elif args.cmd == "report":
        print(json.dumps(report(args.db), indent=2))
    else:
        result = plan_reclaim(args.db, [Path(p) for p in args.junk], [Path(p) for p in args.keep])
        Path(args.plan_out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        out = {"summary": result["summary"], "plan_out": args.plan_out, "applied": False}
        if args.apply:
            out["applied"] = apply_reclaim(result, Path(args.retain_dir), Path(args.ledger))
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
