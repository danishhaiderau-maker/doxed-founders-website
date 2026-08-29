"""Isolated, stdlib-only Fly runtime inventory builder.

The trading owner launches this helper with one nonce-bound request and result
path.  It never imports the engine, opens exchange clients, or promotes the
canonical snapshot; the parent validates and performs that promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


REQUEST_SCHEMA = "fly_runtime_inventory_worker_request_v1"
RESULT_SCHEMA = "fly_runtime_inventory_worker_result_v1"


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _rotation_parts(name: str, extensions: set[str]):
    base_name, separator, generation = Path(name).name.rpartition(".")
    if not separator or not generation.isdigit():
        return None
    return (base_name, int(generation)) if Path(base_name).suffix.lower() in extensions else None


def _complete_record_size(path: Path, size: int) -> int:
    if size <= 0 or path.suffix.lower() not in {".jsonl", ".csv"}:
        return max(0, int(size))
    cursor = int(size)
    with path.open("rb") as handle:
        while cursor > 0:
            start = max(0, cursor - (64 * 1024))
            handle.seek(start)
            block = handle.read(cursor - start)
            newline = block.rfind(b"\n")
            if newline >= 0:
                return start + newline + 1
            cursor = start
    return 0


def _load_request(request_path: Path, result_path: Path, nonce: str) -> dict:
    if not nonce or any(char not in "0123456789abcdef" for char in nonce) or len(nonce) != 32:
        raise ValueError("invalid worker nonce")
    request_parent_lexical = Path(os.path.abspath(request_path.parent))
    result_parent_lexical = Path(os.path.abspath(result_path.parent))
    request_path = request_path.resolve(strict=True)
    result_parent = result_path.parent.resolve(strict=True)
    if request_path.parent != result_parent:
        raise ValueError("worker request/result roots differ")
    expected_request = f"inventory-request-{nonce}.json"
    expected_result = f"inventory-result-{nonce}.json"
    if request_path.name != expected_request or result_path.name != expected_result:
        raise ValueError("worker path is not nonce-bound")
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    if payload.get("schema") != REQUEST_SCHEMA or payload.get("nonce") != nonce:
        raise ValueError("worker request identity mismatch")
    work_root = Path(str(payload.get("work_root") or "")).resolve(strict=True)
    if request_path.parent != work_root or result_parent != work_root:
        raise ValueError("worker path escapes declared work root")
    volume = Path(str(payload.get("volume_root") or "")).resolve(strict=True)
    work_root.relative_to(volume)
    if (
        request_parent_lexical != work_root
        or result_parent_lexical != work_root
        or Path(str(payload.get("work_root") or "")).is_symlink()
    ):
        raise ValueError("worker work root must not be linked")
    runtime = Path(str(payload.get("runtime_root") or "")).resolve(strict=True)
    runtime.relative_to(volume)
    roots = []
    for raw in payload.get("allowed_roots") or []:
        root = Path(str(raw)).resolve(strict=True)
        root.relative_to(volume)
        if not root.is_dir():
            raise ValueError("worker allowed root is not a directory")
        roots.append(root)
    if not roots:
        raise ValueError("worker has no allowed roots")
    payload["_volume"] = volume
    payload["_runtime"] = runtime
    payload["_roots"] = roots
    return payload


def _relpath(path: Path, request: dict) -> str:
    resolved = path.resolve()
    volume = request["_volume"]
    runtime = request["_runtime"]
    top_names = set(request.get("top_level_receipt_names") or [])
    if resolved.parent == volume and resolved.name in top_names:
        return resolved.name
    try:
        return resolved.relative_to(runtime).as_posix()
    except ValueError:
        for name in ("research", "research_accumulator", "research_archive"):
            link = runtime / name
            try:
                return f"{name}/{resolved.relative_to(link.resolve()).as_posix()}"
            except (OSError, ValueError):
                continue
    raise ValueError("inventory path is outside runtime roots")


def _allowed(path: Path, request: dict) -> bool:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(request["_volume"])
    except (OSError, ValueError):
        return False
    excluded_dirs = set(request.get("excluded_dir_names") or [])
    excluded_names = set(request.get("excluded_names") or [])
    extensions = set(request.get("extensions") or [])
    name_lower = resolved.name.lower()
    if {part.lower() for part in resolved.parts}.intersection(excluded_dirs):
        return False
    if resolved.name in excluded_names:
        return False
    if name_lower.startswith(".env") or "secret" in name_lower or "credential" in name_lower:
        return False
    supported = resolved.suffix.lower() in extensions or _rotation_parts(resolved.name, extensions)
    return bool(resolved.is_file() and supported)


def _linked_directory(path: Path) -> bool:
    try:
        lexical = Path(os.path.abspath(path))
        return path.is_symlink() or path.resolve(strict=True) != lexical
    except OSError:
        return True


def _consistency_mode(path: Path, request: dict) -> str:
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return "sqlite_snapshot_v1"
    resolved = str(path.resolve())
    parts = tuple(part.lower() for part in path.resolve().parts)
    v3_append = len(parts) >= 3 and parts[-3:-1] == ("v3", "ledgers") and path.suffix.lower() == ".jsonl"
    append = resolved not in set(request.get("rewrite_targets") or []) and (
        path.suffix.lower() == ".log"
        or path.name in set(request.get("append_prefix_names") or [])
        or resolved in set(request.get("serialized_append_targets") or [])
        or v3_append
    )
    return "append_prefix_v1" if append else "strict_generation_v1"


def build_rows(request: dict) -> list[dict]:
    rows = []
    seen_paths = set()
    seen_relpaths = set()
    max_rows = min(5000, max(1, int(request.get("max_rows") or 5000)))

    def append(path: Path) -> None:
        try:
            resolved = path.resolve(strict=True)
            relpath = _relpath(resolved, request)
            if resolved in seen_paths or relpath in seen_relpaths or not _allowed(resolved, request):
                return
            stat = resolved.stat()
            rows.append({
                "path": relpath,
                "size": _complete_record_size(resolved, int(stat.st_size)),
                "physical_size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
                "inode": int(getattr(stat, "st_ino", 0) or 0),
                "consistency_mode": _consistency_mode(resolved, request),
            })
            seen_paths.add(resolved)
            seen_relpaths.add(relpath)
        except (OSError, ValueError):
            return

    for name in sorted(request.get("top_level_receipt_names") or []):
        append(request["_volume"] / name)
    scanned = 0
    excluded_dirs = set(request.get("excluded_dir_names") or [])
    for root in request["_roots"]:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [
                name for name in dirnames
                if name.lower() not in excluded_dirs and not _linked_directory(Path(dirpath) / name)
            ]
            for filename in filenames:
                append(Path(dirpath) / filename)
                scanned += 1
                if scanned % 25 == 0:
                    time.sleep(0.005)
                if len(rows) >= max_rows:
                    break
            if len(rows) >= max_rows:
                break
        if len(rows) >= max_rows:
            break
    rows.sort(key=lambda row: row["path"])
    return rows


def _rows_sha256(rows: list[dict]) -> str:
    raw = json.dumps(rows, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run(request_path: Path, result_path: Path, nonce: str) -> int:
    temporary = None
    try:
        if hasattr(os, "nice"):
            try:
                os.nice(10)
            except OSError:
                pass
        request = _load_request(request_path, result_path, nonce)
        rows = build_rows(request)
        generated_unix = time.time()
        payload = {
            "schema": RESULT_SCHEMA,
            "nonce": nonce,
            "source_revision": str(request.get("source_revision") or ""),
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "generated_unix": generated_unix,
            "rows_sha256": _rows_sha256(rows),
            "file_count": len(rows),
            "rows": rows,
        }
        temporary = result_path.with_name(f"{result_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        os.replace(temporary, result_path)
        return 0
    except BaseException:
        return 1
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    return run(Path(args.request), Path(args.result), str(args.nonce).lower())


if __name__ == "__main__":
    raise SystemExit(main())
