"""Bounded resumable consumer of immutable disk-pages-v2 inventories.

The worker creates derivative transport packages only.  It never scans the
source tree, serves HTTP, promotes a mirror, acknowledges a generation, or
deletes source data.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid
from typing import Any, Mapping

from data_sync_bundle_transport import build_bundle, is_bundle_eligible_path, MAX_MEMBERS, MAX_PAYLOAD_BYTES

SCHEMA = "fly_runtime_transport_bundle_worker_v1"
STATE_SCHEMA = "fly_runtime_transport_bundle_worker_state_v1"
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_INDEX_ENTRIES = 4096
MAX_INDEX_LINE_BYTES = 16 * 1024
_HEX = frozenset("0123456789abcdef")


class BundleWorkerError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _stat_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns),
            "inode": int(getattr(stat, "st_ino", 0) or 0)}


def _bounded_read(path: Path, limit: int, code: str) -> bytes:
    if limit < 0 or path.stat().st_size > limit:
        raise BundleWorkerError(code)
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise BundleWorkerError(code)
    return raw


@contextlib.contextmanager
def _singleton_lease(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise BundleWorkerError("BUNDLE_WORKER_LEASE_HELD") from exc
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _generation(metadata: Mapping[str, Any]) -> dict[str, Any]:
    generation_id = str(metadata.get("inventory_generation_id") or metadata.get("generation_id") or "").lower()
    inventory_sha = str(metadata.get("inventory_sha256") or generation_id).lower()
    if (len(generation_id) != 64 or any(char not in _HEX for char in generation_id)
            or inventory_sha != generation_id):
        raise BundleWorkerError("INVENTORY_GENERATION_ID_INVALID")
    required = ("source_git_rev", "collection_epoch_id", "tile_registry_signature")
    if metadata.get("storage") != "disk_pages_v2" or metadata.get("ack_eligible") is not True:
        raise BundleWorkerError("INVENTORY_GENERATION_NOT_CURRENT_DISK_PAGES")
    if any(not str(metadata.get(field) or "").strip() for field in required):
        raise BundleWorkerError("INVENTORY_GENERATION_METADATA_INCOMPLETE")
    try:
        page_count = int(metadata["page_count"])
        file_count = int(metadata["file_count"])
        total_bytes = int(metadata["total_bytes"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise BundleWorkerError("INVENTORY_GENERATION_COUNTS_INVALID") from exc
    if any(type(metadata.get(field)) is not int for field in ("page_count", "file_count", "total_bytes")) \
            or page_count < 0 or file_count < 0 or total_bytes < 0:
        raise BundleWorkerError("INVENTORY_GENERATION_COUNTS_INVALID")
    index_sha = str(metadata.get("page_index_sha256") or "").lower()
    if len(index_sha) != 64 or any(char not in _HEX for char in index_sha):
        raise BundleWorkerError("PAGE_INDEX_SHA256_INVALID")
    return {
        "inventory_generation_id": generation_id, "inventory_sha256": inventory_sha,
        "source_git_rev": str(metadata["source_git_rev"]),
        "collection_epoch_id": str(metadata["collection_epoch_id"]),
        "tile_registry_signature": str(metadata["tile_registry_signature"]),
        "ack_eligible": True, "storage": "disk_pages_v2", "page_count": page_count,
        "file_count": file_count, "total_bytes": total_bytes,
        "page_index_sha256": index_sha,
        "generation_dir": str(metadata.get("generation_dir") or ""),
        "page_index_path": str(metadata.get("page_index_path") or ""),
    }


def _initial_state(generation: Mapping[str, Any], index_identity: Mapping[str, int]) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA, "generation": dict(generation),
        "verified_page_index_identity": dict(index_identity),
        "cursor": {"page_index": 0, "page_row_index": 0, "index_offset": 0},
        "package_index": [], "skipped_counts": {}, "completed": False,
        "completed_page_file_count": 0, "completed_page_total_bytes": 0,
    }


def _load_state(path: Path, generation: Mapping[str, Any], index_path: Path,
                max_read_bytes: int) -> tuple[dict[str, Any], int]:
    reads = 0
    if path.is_file():
        raw = _bounded_read(path, min(MAX_INDEX_BYTES, max_read_bytes), "INVOCATION_READ_BYTE_BUDGET_EXCEEDED"); reads += len(raw)
        if reads > max_read_bytes:
            raise BundleWorkerError("INVOCATION_READ_BYTE_BUDGET_EXCEEDED")
        state = json.loads(raw)
        if (not isinstance(state, Mapping) or state.get("schema") != STATE_SCHEMA
                or state.get("generation") != generation):
            raise BundleWorkerError("BUNDLE_WORKER_STATE_GENERATION_MISMATCH")
        if state.get("verified_page_index_identity") != _stat_identity(index_path):
            raise BundleWorkerError("PAGE_INDEX_IDENTITY_CHANGED_AFTER_VERIFICATION")
        if not isinstance(state.get("package_index"), list) or len(state["package_index"]) > MAX_PACKAGE_INDEX_ENTRIES:
            raise BundleWorkerError("PACKAGE_INDEX_LIMIT_EXCEEDED")
        return dict(state), reads
    identity_before = _stat_identity(index_path)
    if identity_before["size"] > MAX_INDEX_BYTES or reads + identity_before["size"] > max_read_bytes:
        raise BundleWorkerError("PAGE_INDEX_READ_BUDGET_EXCEEDED")
    raw = _bounded_read(index_path, min(MAX_INDEX_BYTES, max_read_bytes - reads), "PAGE_INDEX_READ_BUDGET_EXCEEDED"); reads += len(raw)
    if _stat_identity(index_path) != identity_before or _sha(raw) != generation["page_index_sha256"]:
        raise BundleWorkerError("PAGE_INDEX_SHA256_OR_IDENTITY_MISMATCH")
    return _initial_state(generation, identity_before), reads


def _page(index: Any, generation_dir: Path, expected_page: int,
          reads: int, max_read_bytes: int) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(index, Mapping):
        raise BundleWorkerError("PAGE_INDEX_DESCRIPTOR_INVALID")
    try:
        page_index = int(index["page_index"]); count = int(index["file_count"])
        total = int(index["total_bytes"]); digest = str(index["page_sha256"])
        name = str(index["file_name"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise BundleWorkerError("PAGE_INDEX_DESCRIPTOR_INVALID") from exc
    if (any(type(index.get(field)) is not int for field in ("page_index", "file_count", "total_bytes"))
            or page_index != expected_page or count < 0 or total < 0 or len(digest) != 64
            or any(char not in _HEX for char in digest)
            or name != f"p{page_index:08d}-{digest[:24]}.json"):
        raise BundleWorkerError("PAGE_INDEX_DESCRIPTOR_INVALID")
    path = (generation_dir / name).resolve(strict=True)
    try: path.relative_to(generation_dir)
    except ValueError as exc: raise BundleWorkerError("INVENTORY_PAGE_PATH_ESCAPE") from exc
    size = path.stat().st_size
    if size > MAX_PAGE_BYTES or reads + size > max_read_bytes:
        raise BundleWorkerError("INVENTORY_PAGE_READ_BUDGET_EXCEEDED")
    raw = _bounded_read(path, min(MAX_PAGE_BYTES, max_read_bytes - reads), "INVENTORY_PAGE_READ_BUDGET_EXCEEDED"); reads += len(raw)
    if _sha(raw) != digest:
        raise BundleWorkerError("INVENTORY_PAGE_SHA256_MISMATCH")
    payload = json.loads(raw)
    rows = payload.get("rows") if isinstance(payload, Mapping) else None
    if (not isinstance(payload, Mapping) or payload.get("schema") != "fly_runtime_inventory_page_v1"
            or payload.get("page_index") != page_index or not isinstance(rows, list)
            or payload.get("file_count") != count or len(rows) != count
            or payload.get("total_bytes") != total
            or _sha(_canonical(rows)) != payload.get("rows_sha256")):
        raise BundleWorkerError("INVENTORY_PAGE_CONTRACT_INVALID")
    if not all(isinstance(row, Mapping) for row in rows):
        raise BundleWorkerError("INVENTORY_PAGE_ROW_INVALID")
    if any(type(row.get("size")) is not int or row["size"] < 0 for row in rows):
        raise BundleWorkerError("INVENTORY_PAGE_ROW_INVALID")
    try: row_total = sum(int(row["size"]) for row in rows)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise BundleWorkerError("INVENTORY_PAGE_ROW_INVALID") from exc
    if row_total != total:
        raise BundleWorkerError("INVENTORY_PAGE_TOTAL_BYTES_MISMATCH")
    return [dict(row) for row in rows], reads


def run_bundle_worker(
    generation_metadata: Mapping[str, Any], source_root: str | Path,
    output_root: str | Path, *, max_pages: int = 2, max_members: int = 128,
    max_payload_bytes: int = 8 * 1024 * 1024,
    max_read_bytes: int = 32 * 1024 * 1024, max_elapsed_sec: float = 5.0,
) -> dict[str, Any]:
    """Advance one package with bounded reads and cooperative elapsed checks.

    A caller needing a hard wall-clock bound must run this in a timed subprocess:
    filesystem reads/fsyncs cannot be interrupted by a Python deadline check.
    """
    for value, name in ((max_pages, "page"), (max_members, "member"),
                        (max_payload_bytes, "payload"), (max_read_bytes, "read")):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise BundleWorkerError(f"INVALID_{name.upper()}_BUDGET")
    if (isinstance(max_elapsed_sec, bool) or not math.isfinite(float(max_elapsed_sec))
            or float(max_elapsed_sec) <= 0):
        raise BundleWorkerError("INVALID_TIME_BUDGET")
    generation = _generation(generation_metadata)
    if max_members > MAX_MEMBERS or max_payload_bytes > MAX_PAYLOAD_BYTES:
        raise BundleWorkerError("PACKAGE_HARD_BUDGET_EXCEEDED")
    source = Path(source_root).resolve(strict=True)
    output = Path(output_root).resolve()
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise BundleWorkerError("DERIVATIVE_OUTPUT_MUST_BE_OUTSIDE_SOURCE_ROOT")
    generation_dir = Path(generation["generation_dir"]).resolve(strict=True)
    index_path = Path(generation["page_index_path"]).resolve(strict=True)
    try: index_path.relative_to(generation_dir)
    except ValueError as exc: raise BundleWorkerError("PAGE_INDEX_OUTSIDE_GENERATION") from exc
    # Keep content-addressed TAR temporary names below legacy Windows MAX_PATH;
    # the full generation identity remains inside every state/descriptor.
    generation_output = output / f"g-{generation['inventory_generation_id'][:16]}"
    state_path = generation_output / "bundle-worker-state.json"
    started = time.monotonic()
    with _singleton_lease(output / ".bundle-worker.lease"):
        state, reads = _load_state(state_path, generation, index_path, int(max_read_bytes))
        if state.get("completed") is True:
            return {"schema": SCHEMA, "status": "COMPLETE", "generation": generation,
                    "cursor": state["cursor"], "package": None,
                    "package_index_count": len(state["package_index"]),
                    "skipped_counts": state["skipped_counts"], "active_integration": False}
        cursor = dict(state.get("cursor") or {})
        skipped = dict(state.get("skipped_counts") or {})
        selected: list[dict[str, Any]] = []
        selected_bytes = 0; pages_read = 0
        with index_path.open("rb") as index_handle:
            while pages_read < int(max_pages) and cursor["page_index"] < generation["page_count"]:
                if time.monotonic() - started >= float(max_elapsed_sec):
                    break
                index_handle.seek(int(cursor["index_offset"]))
                line_offset = index_handle.tell(); line = index_handle.readline(MAX_INDEX_LINE_BYTES + 1)
                reads += len(line)
                if not line or len(line) > MAX_INDEX_LINE_BYTES or reads > int(max_read_bytes):
                    raise BundleWorkerError("PAGE_INDEX_CURSOR_OR_READ_BUDGET_INVALID")
                descriptor = json.loads(line)
                rows, reads = _page(descriptor, generation_dir, int(cursor["page_index"]),
                                    reads, int(max_read_bytes))
                pages_read += 1
                row_index = int(cursor.get("page_row_index") or 0)
                if row_index < 0 or row_index > len(rows):
                    raise BundleWorkerError("INVENTORY_PAGE_ROW_CURSOR_INVALID")
                stopped = False
                for position in range(row_index, len(rows)):
                    row = rows[position]
                    reason = None
                    if not is_bundle_eligible_path(row.get("path")):
                        reason = "INELIGIBLE_PATH_PER_FILE_FALLBACK"
                    elif row.get("consistency_mode") != "strict_generation_v1":
                        reason = "INELIGIBLE_HOT_ROW_PER_FILE_FALLBACK"
                    try: size = int(row.get("size"))
                    except (TypeError, ValueError, OverflowError): size = -1
                    if isinstance(row.get("size"), bool) or size < 0:
                        raise BundleWorkerError("INVENTORY_PAGE_ROW_INVALID")
                    if reason is None and size > int(max_payload_bytes):
                        reason = "OVERSIZED_ROW_PER_FILE_FALLBACK"
                    if reason:
                        skipped[reason] = int(skipped.get(reason) or 0) + 1
                        cursor["page_row_index"] = position + 1
                        continue
                    if (len(selected) >= int(max_members)
                            or selected_bytes + size > int(max_payload_bytes)
                            or reads + selected_bytes + size > int(max_read_bytes)):
                        cursor["page_row_index"] = position
                        stopped = True; break
                    selected.append(row); selected_bytes += size
                    cursor["page_row_index"] = position + 1
                if cursor["page_row_index"] == len(rows):
                    state["completed_page_file_count"] = int(
                        state.get("completed_page_file_count") or 0) + int(descriptor["file_count"])
                    state["completed_page_total_bytes"] = int(
                        state.get("completed_page_total_bytes") or 0) + int(descriptor["total_bytes"])
                    cursor = {"page_index": int(cursor["page_index"]) + 1,
                              "page_row_index": 0, "index_offset": index_handle.tell()}
                if stopped or selected:
                    break
        package = None
        if selected:
            if time.monotonic() - started >= float(max_elapsed_sec):
                raise BundleWorkerError("INVOCATION_TIME_BUDGET_EXHAUSTED_BEFORE_BUILD")
            package = build_bundle(
                generation, selected, source, generation_output / "packages",
                max_members=int(max_members), max_payload_bytes=int(max_payload_bytes),
            )
            package_receipt_path = generation_output / "descriptors" / f"{package['package_sha256']}.json"
            public_descriptor = {key: value for key, value in package.items() if key != "package_path"}
            _atomic_json(package_receipt_path, public_descriptor)
            entry = {"package_sha256": package["package_sha256"],
                     "descriptor_sha256": _sha(_canonical(public_descriptor)),
                     "descriptor_path": str(package_receipt_path.relative_to(generation_output).as_posix()),
                     "member_count": package["member_count"], "payload_bytes": package["payload_bytes"]}
            if entry not in state["package_index"]:
                if len(state["package_index"]) >= MAX_PACKAGE_INDEX_ENTRIES:
                    raise BundleWorkerError("PACKAGE_INDEX_LIMIT_EXCEEDED")
                state["package_index"].append(entry)
        state["cursor"] = cursor; state["skipped_counts"] = dict(sorted(skipped.items()))
        state["completed"] = cursor["page_index"] == generation["page_count"]
        if state["completed"]:
            if (int(state.get("completed_page_file_count", -1)) != generation["file_count"]
                    or int(state.get("completed_page_total_bytes", -1)) != generation["total_bytes"]):
                raise BundleWorkerError("INVENTORY_GENERATION_PAGE_TOTAL_MISMATCH")
            with index_path.open("rb") as check:
                check.seek(int(cursor["index_offset"]))
                trailing = check.read(1)
            if trailing:
                raise BundleWorkerError("PAGE_INDEX_HAS_UNDECLARED_DESCRIPTORS")
        _atomic_json(state_path, state)
        return {"schema": SCHEMA, "status": "COMPLETE" if state["completed"] else "BUILDING",
                "generation": generation, "cursor": cursor, "package": package,
                "package_index_count": len(state["package_index"]),
                "skipped_counts": state["skipped_counts"], "pages_read": pages_read,
                "inventory_rows_selected": len(selected), "active_integration": False}
