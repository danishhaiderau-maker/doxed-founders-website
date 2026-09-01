"""Isolated, stdlib-only Fly runtime inventory builder.

The trading owner launches this helper with one nonce-bound request and result
path.  It never imports the engine, opens exchange clients, or promotes the
canonical snapshot; the parent validates and performs that promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


REQUEST_SCHEMA = "fly_runtime_inventory_worker_request_v1"
RESULT_SCHEMA = "fly_runtime_inventory_worker_result_v2"
CHECKPOINT_SCHEMA = "fly_runtime_inventory_worker_checkpoint_v2"
PROGRESS_SCHEMA = "fly_runtime_inventory_worker_progress_v2"

DEFAULT_FILE_BUDGET = 5000
DEFAULT_DIRECTORY_BUDGET = 1000
DEFAULT_ELAPSED_BUDGET_SECONDS = 240.0
MAX_ELAPSED_BUDGET_SECONDS = 270.0
MAX_FILE_BUDGET = 5000
MAX_DIRECTORY_BUDGET = 1000
MAX_DIRECTORY_ENTRIES = 10000
DEFAULT_PAGE_ROWS = 250
MAX_PAGE_ROWS = 1000


class CheckpointError(ValueError):
    """The durable traversal state cannot be trusted."""


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


def _row(path: Path, request: dict) -> dict | None:
    try:
        resolved = path.resolve(strict=True)
        if not _allowed(resolved, request):
            return None
        stat = resolved.stat()
        return {
            "path": _relpath(resolved, request),
            "size": _complete_record_size(resolved, int(stat.st_size)),
            "physical_size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "inode": int(getattr(stat, "st_ino", 0) or 0),
            "consistency_mode": _consistency_mode(resolved, request),
        }
    except (OSError, ValueError):
        return None


def _stable_request(request: dict) -> dict:
    """Return only fields that define one resumable inventory generation."""
    keys = (
        "source_revision", "top_level_receipt_names", "extensions",
        "excluded_names", "excluded_dir_names", "append_prefix_names",
        "serialized_append_targets", "rewrite_targets",
    )
    stable = {key: request.get(key) for key in keys}
    stable["inventory_page_rows"] = min(MAX_PAGE_ROWS, max(
        1, int(request.get("inventory_page_rows") or DEFAULT_PAGE_ROWS),
    ))
    stable["volume_root"] = str(request["_volume"])
    stable["runtime_root"] = str(request["_runtime"])
    stable["allowed_roots"] = [str(path) for path in request["_roots"]]
    return stable


def _request_fingerprint(request: dict) -> str:
    raw = json.dumps(
        _stable_request(request), separators=(",", ":"), sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _checkpoint_digest(payload: dict) -> str:
    unsigned = dict(payload)
    unsigned.pop("checkpoint_sha256", None)
    raw = json.dumps(
        unsigned, separators=(",", ":"), sort_keys=True, ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _bounded_roots(request: dict) -> list[Path]:
    """Remove nested roots so a path is traversed once across resumptions."""
    roots = sorted(set(request["_roots"]), key=lambda path: (len(path.parts), str(path)))
    bounded = []
    for root in roots:
        if any(_within(root, parent) for parent in bounded):
            continue
        bounded.append(root)
    return bounded


def _state_paths(work_root: Path, fingerprint: str) -> tuple[Path, Path, Path, Path]:
    stem = f"inventory-worker-v2-{fingerprint[:32]}"
    return (
        work_root / f"{stem}.checkpoint.json",
        work_root / f"{stem}.progress.json",
        work_root / f"{stem}.sqlite3",
        work_root / "inventory-generations" / f".building-{fingerprint[:32]}",
    )


def _remove_database(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        path.with_name(f"{path.name}{suffix}").unlink(missing_ok=True)


def _quarantine(paths: tuple[Path, ...]) -> None:
    token = uuid.uuid4().hex
    for path in paths:
        if not path.exists():
            continue
        try:
            os.replace(path, path.with_name(f"{path.name}.corrupt-{token}"))
        except OSError:
            pass


def _initial_checkpoint(request: dict, fingerprint: str, database_name: str) -> dict:
    roots = _bounded_roots(request)
    payload = {
        "schema": CHECKPOINT_SCHEMA,
        "request_fingerprint": fingerprint,
        "source_revision": str(request.get("source_revision") or ""),
        "roots": [str(path) for path in roots],
        "phase": "SCAN",
        "pending_dirs": [
            {"root_index": index, "relative": ""}
            for index in range(len(roots) - 1, -1, -1)
        ],
        "current_dir": None,
        "top_level_complete": False,
        "top_level_after": None,
        "files_seen": 0,
        "dirs_seen": 0,
        "rows_written": 0,
        "database_name": database_name,
        "elapsed_seconds": 0.0,
        "invocations": 0,
    }
    payload["checkpoint_sha256"] = _checkpoint_digest(payload)
    return payload


def _load_checkpoint(path: Path, request: dict, fingerprint: str, database_path: Path) -> dict:
    if not path.exists():
        _remove_database(database_path)
        return _initial_checkpoint(request, fingerprint, database_path.name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != CHECKPOINT_SCHEMA:
            raise CheckpointError("checkpoint schema mismatch")
        if payload.get("request_fingerprint") != fingerprint:
            raise CheckpointError("checkpoint request identity mismatch")
        if payload.get("source_revision") != str(request.get("source_revision") or ""):
            raise CheckpointError("checkpoint revision mismatch")
        if payload.get("database_name") != database_path.name:
            raise CheckpointError("checkpoint database identity mismatch")
        if payload.get("checkpoint_sha256") != _checkpoint_digest(payload):
            raise CheckpointError("checkpoint digest mismatch")
        roots = [str(path) for path in _bounded_roots(request)]
        if payload.get("roots") != roots:
            raise CheckpointError("checkpoint roots mismatch")
        if payload.get("phase") not in {"SCAN", "FINALIZE"}:
            raise CheckpointError("checkpoint phase is invalid")
        for key in ("files_seen", "dirs_seen", "rows_written", "invocations"):
            if not isinstance(payload.get(key), int) or payload[key] < 0:
                raise CheckpointError(f"checkpoint {key} is invalid")
        if not isinstance(payload.get("pending_dirs"), list):
            raise CheckpointError("checkpoint directory cursor is invalid")
        if not isinstance(payload.get("top_level_complete"), bool):
            raise CheckpointError("checkpoint top-level cursor is invalid")
        if payload.get("top_level_after") is not None and not isinstance(payload["top_level_after"], str):
            raise CheckpointError("checkpoint top-level position is invalid")
        root_count = len(roots)

        def validate_directory_cursor(cursor: object) -> None:
            if not isinstance(cursor, dict):
                raise CheckpointError("checkpoint directory entry is invalid")
            root_index = cursor.get("root_index")
            relative = cursor.get("relative")
            if not isinstance(root_index, int) or root_index < 0 or root_index >= root_count:
                raise CheckpointError("checkpoint directory root is invalid")
            if not isinstance(relative, str):
                raise CheckpointError("checkpoint directory path is invalid")
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise CheckpointError("checkpoint directory path escapes root")

        for cursor in payload["pending_dirs"]:
            validate_directory_cursor(cursor)
        current = payload.get("current_dir")
        if current is not None:
            validate_directory_cursor(current)
            if not isinstance(current.get("children_enqueued"), bool):
                raise CheckpointError("checkpoint child state is invalid")
            if current.get("after_file") is not None and not isinstance(current["after_file"], str):
                raise CheckpointError("checkpoint file cursor is invalid")
        return payload
    except CheckpointError:
        raise
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"checkpoint cannot be decoded: {type(exc).__name__}") from exc


def _open_database(path: Path, fingerprint: str) -> sqlite3.Connection:
    connection = None
    try:
        connection = sqlite3.connect(path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rows ("
            "path TEXT PRIMARY KEY, row_json TEXT NOT NULL, transferable_bytes INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pages (page_index INTEGER PRIMARY KEY, descriptor_json TEXT NOT NULL)"
        )
        existing = connection.execute(
            "SELECT value FROM meta WHERE key = 'request_fingerprint'"
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('request_fingerprint', ?)",
                (fingerprint,),
            )
            connection.commit()
        elif existing[0] != fingerprint:
            raise CheckpointError("inventory database identity mismatch")
        return connection
    except CheckpointError:
        if connection is not None:
            connection.close()
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        if connection is not None:
            connection.close()
        raise CheckpointError(f"inventory database is corrupt: {type(exc).__name__}") from exc


def _store_rows(connection: sqlite3.Connection, rows: list[dict]) -> int:
    try:
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            raw = json.dumps(row, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
            previous = connection.execute(
                "SELECT row_json FROM rows WHERE path = ?", (row["path"],)
            ).fetchone()
            if previous is not None and previous[0] != raw:
                raise CheckpointError(f"conflicting inventory row: {row['path']}")
            if previous is None:
                connection.execute(
                    "INSERT INTO rows(path, row_json, transferable_bytes) VALUES(?, ?, ?)",
                    (row["path"], raw, int(row["size"])),
                )
        connection.commit()
        return int(connection.execute("SELECT COUNT(*) FROM rows").fetchone()[0])
    except BaseException:
        connection.rollback()
        raise


def _atomic_bytes(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _rss_bytes() -> int | None:
    try:
        import resource
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux reports KiB; macOS reports bytes.
        linux = hasattr(os, "uname") and os.uname().sysname == "Linux"
        return value * 1024 if linux else value
    except (ImportError, OSError, ValueError):
        return None


def _budgets(request: dict) -> tuple[int, int, float]:
    file_budget = min(MAX_FILE_BUDGET, max(1, int(
        request.get("inventory_file_budget") or request.get("max_rows") or DEFAULT_FILE_BUDGET
    )))
    directory_budget = min(MAX_DIRECTORY_BUDGET, max(1, int(
        request.get("inventory_directory_budget") or DEFAULT_DIRECTORY_BUDGET
    )))
    elapsed_budget = min(MAX_ELAPSED_BUDGET_SECONDS, max(
        0.01, float(request.get("inventory_elapsed_budget_seconds") or DEFAULT_ELAPSED_BUDGET_SECONDS),
    ))
    return file_budget, directory_budget, elapsed_budget


def _bounded_directory_entries(directory: Path) -> list[tuple[str, str, bool, bool]]:
    entries = []
    try:
        with os.scandir(directory) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_DIRECTORY_ENTRIES:
                    raise CheckpointError(
                        f"directory entry hard limit exceeded: {directory}"
                    )
                try:
                    entries.append((
                        entry.name,
                        entry.path,
                        entry.is_dir(follow_symlinks=False),
                        entry.is_file(follow_symlinks=False),
                    ))
                except OSError:
                    continue
    except CheckpointError:
        raise
    except OSError:
        return []
    entries.sort(key=lambda item: item[0])
    return entries


def _write_page(staging: Path, page_index: int, rows: list[dict]) -> dict:
    rows_sha256 = _rows_sha256(rows)
    total_bytes = sum(int(row["size"]) for row in rows)
    payload = {
        "schema": "fly_runtime_inventory_page_v1",
        "page_index": page_index,
        "file_count": len(rows),
        "total_bytes": total_bytes,
        "rows_sha256": rows_sha256,
        "rows": rows,
    }
    raw = json.dumps(
        payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True,
    ).encode("utf-8")
    page_sha256 = hashlib.sha256(raw).hexdigest()
    # The descriptor retains the full digest; the shorter content prefix keeps
    # Windows diagnostic/test paths below legacy MAX_PATH.
    file_name = f"p{page_index:08d}-{page_sha256[:24]}.json"
    destination = staging / file_name
    if destination.exists():
        existing = destination.read_bytes()
        if hashlib.sha256(existing).hexdigest() != page_sha256:
            raise CheckpointError("staged inventory page hash mismatch")
    else:
        _atomic_bytes(destination, raw)
    return {
        "page_index": page_index,
        "file_count": len(rows),
        "total_bytes": total_bytes,
        "page_sha256": page_sha256,
        "file_name": file_name,
        "last_path": rows[-1]["path"] if rows else "",
    }


def _store_page(connection: sqlite3.Connection, descriptor: dict) -> None:
    raw = json.dumps(descriptor, separators=(",", ":"), sort_keys=True)
    previous = connection.execute(
        "SELECT descriptor_json FROM pages WHERE page_index = ?",
        (descriptor["page_index"],),
    ).fetchone()
    if previous is not None and previous[0] != raw:
        raise CheckpointError("conflicting inventory page descriptor")
    if previous is None:
        connection.execute(
            "INSERT INTO pages(page_index, descriptor_json) VALUES(?, ?)",
            (descriptor["page_index"], raw),
        )
        connection.commit()


def _publish_generation(
    request: dict,
    work_root: Path,
    fingerprint: str,
    staging: Path,
    connection: sqlite3.Connection,
) -> dict:
    descriptors = connection.execute(
        "SELECT descriptor_json FROM pages ORDER BY page_index"
    )
    generation_hasher = hashlib.sha256(b"fly_runtime_inventory_generation_v2\n")
    index_hasher = hashlib.sha256()
    index_temporary = staging / f"page-index.{uuid.uuid4().hex[:8]}.tmp"
    page_count = 0
    indexed_files = 0
    indexed_bytes = 0
    try:
        with index_temporary.open("wb") as handle:
            for (raw_descriptor,) in descriptors:
                descriptor = json.loads(raw_descriptor)
                public = {
                    key: descriptor[key]
                    for key in ("page_index", "file_count", "total_bytes", "page_sha256", "file_name")
                }
                line = json.dumps(
                    public, separators=(",", ":"), sort_keys=True, ensure_ascii=True,
                ).encode("utf-8") + b"\n"
                handle.write(line)
                index_hasher.update(line)
                generation_hasher.update(
                    f"{public['page_index']}:{public['file_count']}:{public['total_bytes']}:"
                    f"{public['page_sha256']}\n".encode("ascii")
                )
                page_count += 1
                indexed_files += int(public["file_count"])
                indexed_bytes += int(public["total_bytes"])
            handle.flush()
            os.fsync(handle.fileno())
        database_files, database_bytes = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(transferable_bytes), 0) FROM rows"
        ).fetchone()
        if indexed_files != int(database_files) or indexed_bytes != int(database_bytes):
            raise CheckpointError("inventory page index does not cover the complete database")
        generation_hasher.update(f"files:{indexed_files}\nbytes:{indexed_bytes}\n".encode("ascii"))
        generation_id = generation_hasher.hexdigest()
        generation_root = work_root / "inventory-generations"
        generation_root.mkdir(parents=True, exist_ok=True)
        final_directory = generation_root / generation_id
        index_name = "page-index.jsonl"
        index_sha256 = index_hasher.hexdigest()
        index_destination = staging / index_name
        os.replace(index_temporary, index_destination)
        if final_directory.exists():
            existing_index = final_directory / index_name
            existing_valid = bool(
                existing_index.is_file()
                and _file_sha256(existing_index) == index_sha256
            )
            if existing_valid:
                for (raw_descriptor,) in connection.execute(
                    "SELECT descriptor_json FROM pages ORDER BY page_index"
                ):
                    descriptor = json.loads(raw_descriptor)
                    page_path = final_directory / str(descriptor["file_name"])
                    try:
                        raw_page = page_path.read_bytes()
                        page = json.loads(raw_page)
                        rows = page.get("rows")
                        existing_valid = bool(
                            page_path.is_file()
                            and len(raw_page) <= 8 * 1024 * 1024
                            and hashlib.sha256(raw_page).hexdigest() == descriptor["page_sha256"]
                            and page.get("schema") == "fly_runtime_inventory_page_v1"
                            and int(page.get("page_index", -1)) == int(descriptor["page_index"])
                            and isinstance(rows, list)
                            and int(page.get("file_count", -1)) == len(rows) == int(descriptor["file_count"])
                            and int(page.get("total_bytes", -1)) == int(descriptor["total_bytes"])
                            and hmac.compare_digest(
                                str(page.get("rows_sha256") or ""), _rows_sha256(rows)
                            )
                        )
                    except (OSError, TypeError, ValueError, json.JSONDecodeError):
                        existing_valid = False
                    if not existing_valid:
                        break
            if existing_valid:
                shutil.rmtree(staging)
            else:
                # Retain the corrupt artifact for forensic diagnosis, then
                # atomically replace its canonical name with the verified build.
                _quarantine((final_directory,))
                os.replace(staging, final_directory)
        else:
            os.replace(staging, final_directory)
        return {
            "generation_id": generation_id,
            "generation_dir": str(final_directory.resolve()),
            "page_index_path": str((final_directory / index_name).resolve()),
            "page_index_sha256": index_sha256,
            "page_count": page_count,
            "file_count": indexed_files,
            "total_bytes": indexed_bytes,
        }
    finally:
        index_temporary.unlink(missing_ok=True)


def _build_resumable(request: dict, work_root: Path) -> tuple[dict | None, dict]:
    fingerprint = _request_fingerprint(request)
    checkpoint_path, progress_path, database_path, staging = _state_paths(work_root, fingerprint)
    try:
        checkpoint = _load_checkpoint(checkpoint_path, request, fingerprint, database_path)
        if checkpoint["invocations"] == 0 and staging.exists():
            shutil.rmtree(staging)
        connection = _open_database(database_path, fingerprint)
        database_rows = int(connection.execute("SELECT COUNT(*) FROM rows").fetchone()[0])
        if database_rows < checkpoint["rows_written"]:
            raise CheckpointError("inventory database lost checkpointed rows")
        checkpoint["rows_written"] = database_rows
        page_count = int(connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
        if checkpoint["phase"] == "SCAN" and page_count:
            raise CheckpointError("inventory database has premature pages")
    except CheckpointError:
        _quarantine((checkpoint_path, database_path))
        raise
    file_budget, directory_budget, elapsed_budget = _budgets(request)
    page_rows = min(MAX_PAGE_ROWS, max(1, int(
        request.get("inventory_page_rows") or DEFAULT_PAGE_ROWS
    )))
    started = time.monotonic()
    cpu_started = time.process_time()
    invocation_files = 0
    invocation_dirs = 0
    batch_rows: list[dict] = []
    generation = None

    def budget_exhausted() -> bool:
        return (
            invocation_files >= file_budget
            or invocation_dirs >= directory_budget
            or (time.monotonic() - started) >= elapsed_budget
        )

    try:
        if checkpoint["phase"] == "SCAN":
            if not checkpoint["top_level_complete"]:
                for name in sorted(request.get("top_level_receipt_names") or []):
                    if checkpoint.get("top_level_after") is not None and name <= checkpoint["top_level_after"]:
                        continue
                    if budget_exhausted():
                        break
                    row = _row(request["_volume"] / name, request)
                    invocation_files += 1
                    checkpoint["files_seen"] += 1
                    checkpoint["top_level_after"] = name
                    if row is not None:
                        batch_rows.append(row)
                else:
                    checkpoint["top_level_complete"] = True

            roots = [Path(path) for path in checkpoint["roots"]]
            excluded_dirs = set(request.get("excluded_dir_names") or [])
            while checkpoint["top_level_complete"] and not budget_exhausted():
                current = checkpoint.get("current_dir")
                if current is None:
                    if not checkpoint["pending_dirs"]:
                        break
                    current = checkpoint["pending_dirs"].pop()
                    current = {
                        "root_index": int(current["root_index"]),
                        "relative": str(current["relative"]),
                        "after_file": None,
                        "children_enqueued": False,
                    }
                    checkpoint["current_dir"] = current
                root_index = int(current["root_index"])
                directory = roots[root_index] / current["relative"]
                entries = _bounded_directory_entries(directory)
                if not current["children_enqueued"]:
                    children = []
                    for name, raw_path, is_directory, _ in entries:
                        path = Path(raw_path)
                        if (
                            is_directory
                            and name.lower() not in excluded_dirs
                            and not _linked_directory(path)
                        ):
                            children.append({
                                "root_index": root_index,
                                "relative": path.relative_to(roots[root_index]).as_posix(),
                            })
                    if len(checkpoint["pending_dirs"]) + len(children) > 50000:
                        raise CheckpointError("pending directory hard limit exceeded")
                    checkpoint["pending_dirs"].extend(reversed(children))
                    current["children_enqueued"] = True
                    checkpoint["dirs_seen"] += 1
                    invocation_dirs += 1
                after_file = current.get("after_file")
                files = [
                    (name, raw_path) for name, raw_path, _, is_file in entries
                    if is_file and (after_file is None or name > after_file)
                ]
                for name, raw_path in files:
                    if budget_exhausted():
                        break
                    row = _row(Path(raw_path), request)
                    invocation_files += 1
                    checkpoint["files_seen"] += 1
                    current["after_file"] = name
                    if row is not None:
                        batch_rows.append(row)
                else:
                    checkpoint["current_dir"] = None

            checkpoint["rows_written"] = _store_rows(connection, batch_rows)
            scan_complete = bool(
                checkpoint["top_level_complete"]
                and checkpoint.get("current_dir") is None
                and not checkpoint["pending_dirs"]
            )
            if scan_complete:
                checkpoint["phase"] = "FINALIZE"

        if checkpoint["phase"] == "FINALIZE" and not budget_exhausted():
            staging.mkdir(parents=True, exist_ok=True)
            previous = connection.execute(
                "SELECT descriptor_json FROM pages ORDER BY page_index DESC LIMIT 1"
            ).fetchone()
            if previous is None:
                page_index = 0
                after_path = ""
            else:
                descriptor = json.loads(previous[0])
                page_index = int(descriptor["page_index"]) + 1
                after_path = str(descriptor["last_path"])
            while not budget_exhausted():
                limit = min(page_rows, file_budget - invocation_files)
                if limit <= 0:
                    break
                records = connection.execute(
                    "SELECT row_json FROM rows WHERE path > ? ORDER BY path LIMIT ?",
                    (after_path, limit),
                ).fetchall()
                if not records:
                    if page_index == 0:
                        descriptor = _write_page(staging, 0, [])
                        _store_page(connection, descriptor)
                    generation = _publish_generation(
                        request, work_root, fingerprint, staging, connection,
                    )
                    break
                rows = [json.loads(record[0]) for record in records]
                descriptor = _write_page(staging, page_index, rows)
                _store_page(connection, descriptor)
                invocation_files += len(rows)
                page_index += 1
                after_path = descriptor["last_path"]

        elapsed = time.monotonic() - started
        checkpoint["elapsed_seconds"] = float(checkpoint.get("elapsed_seconds") or 0.0) + elapsed
        checkpoint["invocations"] += 1
        receipt = {
            "schema": PROGRESS_SCHEMA,
            "request_fingerprint": fingerprint,
            "source_revision": str(request.get("source_revision") or ""),
            "complete": generation is not None,
            "phase": "COMPLETE" if generation is not None else checkpoint["phase"],
            "files_seen": checkpoint["files_seen"],
            "dirs_seen": checkpoint["dirs_seen"],
            "rows_written": checkpoint["rows_written"],
            "invocations": checkpoint["invocations"],
            "invocation_files_seen": invocation_files,
            "invocation_dirs_seen": invocation_dirs,
            "invocation_elapsed_seconds": elapsed,
            "total_elapsed_seconds": checkpoint["elapsed_seconds"],
            "cpu_seconds": time.process_time() - cpu_started,
            "peak_rss_bytes": _rss_bytes(),
            "file_budget": file_budget,
            "directory_budget": directory_budget,
            "elapsed_budget_seconds": elapsed_budget,
            "page_rows": page_rows,
            "checkpoint_path": str(checkpoint_path.resolve()),
            "database_path": str(database_path.resolve()),
        }
        _atomic_json(progress_path, receipt)
        if generation is None:
            checkpoint["checkpoint_sha256"] = _checkpoint_digest(checkpoint)
            _atomic_json(checkpoint_path, checkpoint)
        return generation, receipt
    except (CheckpointError, sqlite3.DatabaseError):
        connection.close()
        _quarantine((checkpoint_path, database_path, staging))
        raise
    finally:
        try:
            connection.close()
        except BaseException:
            pass


def _cleanup_completed_state(request: dict, work_root: Path) -> None:
    fingerprint = _request_fingerprint(request)
    checkpoint, _, database, _ = _state_paths(work_root, fingerprint)
    checkpoint.unlink(missing_ok=True)
    _remove_database(database)


def _rows_sha256(rows: list[dict]) -> str:
    raw = json.dumps(rows, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run(request_path: Path, result_path: Path, nonce: str) -> int:
    request = None
    try:
        if hasattr(os, "nice"):
            try:
                os.nice(10)
            except OSError:
                pass
        request = _load_request(request_path, result_path, nonce)
        generation, worker_receipt = _build_resumable(request, request_path.parent)
        generated_unix = time.time()
        base = {
            "schema": RESULT_SCHEMA,
            "nonce": nonce,
            "source_revision": str(request.get("source_revision") or ""),
            "launched_unix": float(request.get("launched_unix") or 0.0),
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "generated_unix": generated_unix,
            "worker_receipt": worker_receipt,
        }
        if generation is None:
            payload = {
                **base,
                "status": "BUILDING",
                "generation_id": None,
                "resume_token": worker_receipt["request_fingerprint"],
                "checkpoint_path": worker_receipt["checkpoint_path"],
                "spool_path": worker_receipt["database_path"],
                "files_seen": worker_receipt["files_seen"],
                "dirs_seen": worker_receipt["dirs_seen"],
                "rows_discovered": worker_receipt["rows_written"],
                "phase": worker_receipt["phase"],
                "retry_after_seconds": 5,
            }
            _atomic_json(result_path, payload)
            return 75
        payload = {
            **base,
            "status": "COMPLETE",
            "generation_id": generation["generation_id"],
            "file_count": generation["file_count"],
            "total_bytes": generation["total_bytes"],
            "page_size": worker_receipt["page_rows"],
            "page_count": generation["page_count"],
            "generation_dir": generation["generation_dir"],
            "page_index_path": generation["page_index_path"],
            "page_index_sha256": generation["page_index_sha256"],
        }
        _atomic_json(result_path, payload)
        _cleanup_completed_state(request, request_path.parent)
        return 0
    except BaseException as exc:
        if request is not None:
            try:
                _atomic_json(result_path, {
                    "schema": RESULT_SCHEMA,
                    "status": "FAILED",
                    "nonce": nonce,
                    "source_revision": str(request.get("source_revision") or ""),
                    "launched_unix": float(request.get("launched_unix") or 0.0),
                    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "generated_unix": time.time(),
                    "failure_kind": type(exc).__name__,
                    "failure_reason": str(exc)[:500],
                    "retry_after_seconds": 30,
                })
            except BaseException:
                pass
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    return run(Path(args.request), Path(args.result), str(args.nonce).lower())


if __name__ == "__main__":
    raise SystemExit(main())
