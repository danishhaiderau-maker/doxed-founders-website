"""Injected-HTTP, verify-then-stage package client. Never promotes or ACKs.

``fetch(relative_url, timeout=seconds)`` returns ``(status, headers, body_bytes)``.
The adapter must enforce the timeout and bound response reads (2 MiB metadata,
1 MiB chunks). This client additionally validates lengths after every fetch.
No credential, HTTP transport, subprocess or canonical mirror is owned here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid
from collections.abc import Mapping

from data_sync_bundle_transport import (
    MAX_MEMBERS, MAX_PACKAGE_BYTES, MAX_PAYLOAD_BYTES, SCHEMA,
    extract_verified_bundle, is_bundle_eligible_path,
)

MAX_CHUNK_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_ROWS = 1_000_000
IDENTITY_FIELDS = ("inventory_generation_id", "inventory_sha256", "source_git_rev",
                   "collection_epoch_id", "tile_registry_signature")
ROW_FIELDS = ("path", "size", "inode", "mtime_ns", "consistency_mode")
HEX = re.compile(r"^[0-9a-f]{64}$")


class BundleClientError(ValueError):
    """Sanitized, deterministic client failure; no incomplete result is returned."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleClientError("DESCRIPTOR_DUPLICATE_KEY")
        result[key] = value
    return result


def _root(raw):
    root = Path(os.path.abspath(raw))
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current /= part
        if current.exists() or current.is_symlink():
            stat = current.lstat()
            if int(getattr(stat, "st_file_attributes", 0) or 0) & 0x400 or current.is_symlink():
                raise BundleClientError("STAGING_LINK_REJECTED")
    root.mkdir(parents=True, exist_ok=True)
    if root.resolve(strict=True) != root or not root.is_dir():
        raise BundleClientError("STAGING_ROOT_INVALID")
    return root


def fetch_verified_package(index_entry, generation, original_manifest_rows, staging_root,
                           fetch, *, max_attempts=3, timeout_sec=15.0,
                           deadline_sec=120.0, clock=time.monotonic, sleep=time.sleep):
    """Download one bounded package and return verified, isolated staged members.

    Original manifest rows, not the transport descriptor, remain ACK authority.
    ``generation`` must be the verified manifest generation. Retry only timeout,
    connection errors, 429/502/503/504; corruption and identity drift fail once.
    Deadline is cooperative around adapter calls; its timeout must be enforced
    by the injected adapter. A successful package file is retained for the owner.
    """
    if (not _integer(max_attempts, 1, 5)
            or type(timeout_sec) not in (float, int) or not math.isfinite(timeout_sec)
            or not 0 < timeout_sec <= 30
            or type(deadline_sec) not in (float, int) or not math.isfinite(deadline_sec)
            or not 0 < deadline_sec <= 300):
        raise BundleClientError("CLIENT_BUDGET_INVALID")
    if (not isinstance(generation, dict) or generation.get("ack_eligible") is not True
            or any(not isinstance(generation.get(k), str) or not generation[k] for k in IDENTITY_FIELDS)
            or not _digest(generation["inventory_generation_id"])
            or generation["inventory_sha256"] != generation["inventory_generation_id"]):
        raise BundleClientError("MANIFEST_GENERATION_INVALID")
    if (not isinstance(index_entry, dict)
            or not _digest(index_entry.get("package_sha256"))
            or not _digest(index_entry.get("descriptor_sha256"))
            or not _integer(index_entry.get("member_count"), 1, MAX_MEMBERS)
            or not _integer(index_entry.get("payload_bytes"), 0, MAX_PAYLOAD_BYTES)):
        raise BundleClientError("PACKAGE_INDEX_INVALID")
    expires = clock() + deadline_sec

    def check_deadline():
        remaining = expires - clock()
        if remaining <= 0:
            raise BundleClientError("PACKAGE_DEADLINE_EXCEEDED")
        return remaining

    def request(url, max_bytes):
        for attempt in range(max_attempts):
            remaining = check_deadline()
            try:
                status, headers, body = fetch(url, timeout=min(timeout_sec, remaining))
            except (TimeoutError, ConnectionError):
                status, headers, body = 503, {}, b""
            check_deadline()
            if type(status) is not int or not isinstance(headers, dict) and not hasattr(headers, "items"):
                raise BundleClientError("HTTP_RESPONSE_INVALID")
            if not isinstance(body, bytes) or len(body) > max_bytes:
                raise BundleClientError("HTTP_BODY_LIMIT_OR_TYPE")
            if status == 200:
                return {str(k).lower(): str(v) for k, v in headers.items()}, body
            if status not in (429, 502, 503, 504):
                raise BundleClientError(f"PACKAGE_HTTP_{status}")
            if attempt + 1 == max_attempts:
                raise BundleClientError("PACKAGE_RETRY_EXHAUSTED")
            delay = min(0.25 * (2 ** attempt), check_deadline())
            sleep(delay)
        raise BundleClientError("PACKAGE_RETRY_EXHAUSTED")

    generation_id = generation["inventory_generation_id"]
    package_id = index_entry["package_sha256"]
    url = f"/api/data-sync/bundle?generation_id={generation_id}&package_id={package_id}"
    _, body = request(url + "&descriptor=1", MAX_METADATA_BYTES)
    try:
        descriptor = json.loads(body, object_pairs_hook=_object_pairs,
                                parse_constant=lambda _x: (_ for _ in ()).throw(ValueError()))
        descriptor_hash = hashlib.sha256(_canonical(descriptor)).hexdigest()
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BundleClientError("DESCRIPTOR_JSON_INVALID") from exc
    if descriptor_hash != index_entry["descriptor_sha256"]:
        raise BundleClientError("DESCRIPTOR_HASH_MISMATCH")
    if (not isinstance(descriptor, dict) or descriptor.get("schema") != SCHEMA
            or descriptor.get("package_sha256") != package_id
            or any(descriptor.get(k) != generation[k] for k in IDENTITY_FIELDS)):
        raise BundleClientError("DESCRIPTOR_GENERATION_MISMATCH")
    for field in ("member_count", "payload_bytes"):
        if type(descriptor.get(field)) is not int or descriptor[field] != index_entry[field]:
            raise BundleClientError("DESCRIPTOR_INDEX_MISMATCH")
    size = descriptor.get("package_size")
    members = descriptor.get("members")
    if (not _integer(size, 1, MAX_PACKAGE_BYTES) or not isinstance(members, list)
            or len(members) != index_entry["member_count"]):
        raise BundleClientError("DESCRIPTOR_LIMIT_INVALID")
    selected = {}
    for member in members:
        if (not isinstance(member, dict) or not is_bundle_eligible_path(member.get("path"))
                or member["path"] in selected or not _digest(member.get("sha256"))
                or any(not _integer(member.get(k), 0, 2**64-1) for k in ("size", "inode", "mtime_ns"))
                or member.get("consistency_mode") != "strict_generation_v1"):
            raise BundleClientError("DESCRIPTOR_MEMBER_INVALID")
        selected[member["path"]] = member
    if (sum(item["size"] for item in members) != descriptor["payload_bytes"]
            or hashlib.sha256(_canonical(members)).hexdigest() != descriptor.get("member_tree_sha256")):
        raise BundleClientError("DESCRIPTOR_MEMBER_TREE_MISMATCH")
    # A caller may construct and duplicate-check the complete manifest index
    # once. Verify both key and embedded path, then touch only selected members.
    # This avoids O(packages * manifest_rows) work on the normal sync path.
    if isinstance(original_manifest_rows, Mapping):
        if len(original_manifest_rows) > MAX_MANIFEST_ROWS:
            raise BundleClientError("MANIFEST_ROW_LIMIT")
        candidate_rows = []
        for path in selected:
            row = original_manifest_rows.get(path)
            if not isinstance(row, dict) or row.get("path") != path:
                raise BundleClientError("MANIFEST_MEMBER_MISSING_OR_KEY_MISMATCH")
            candidate_rows.append(row)
    else:
        candidate_rows = original_manifest_rows
    matched = set()
    for count, row in enumerate(candidate_rows):
        if count >= MAX_MANIFEST_ROWS:
            raise BundleClientError("MANIFEST_ROW_LIMIT")
        if count % 256 == 0:
            check_deadline()
        if not isinstance(row, dict):
            raise BundleClientError("MANIFEST_ROW_INVALID")
        path = row.get("path")
        if not isinstance(path, str):
            raise BundleClientError("MANIFEST_ROW_INVALID")
        if path not in selected:
            continue
        if path in matched:
            raise BundleClientError("MANIFEST_MEMBER_DUPLICATE")
        if (any(row.get(k) != selected[path][k] for k in ROW_FIELDS)
                or any(type(row.get(k)) is not int for k in ("size", "inode", "mtime_ns"))):
            raise BundleClientError("MANIFEST_MEMBER_MISMATCH")
        matched.add(path)
    if matched != set(selected):
        raise BundleClientError("MANIFEST_MEMBER_MISSING")
    check_deadline()
    root = _root(staging_root)
    package = root / f"p-{uuid.uuid4().hex[:12]}.tar"
    try:
        offset = 0
        checksum = hashlib.sha256()
        with package.open("xb") as handle:
            while offset < size:
                limit = min(MAX_CHUNK_BYTES, size - offset)
                headers, chunk = request(url + f"&offset={offset}&limit={limit}", limit)
                expected = {
                    "x-inventory-generation": generation_id, "x-package-sha256": package_id,
                    "x-chunk-offset": str(offset), "x-package-size": str(size),
                    "x-chunk-eof": "true" if offset + limit == size else "false",
                    "x-chunk-sha256": hashlib.sha256(chunk).hexdigest(),
                }
                if len(chunk) != limit or any(headers.get(k) != v for k, v in expected.items()):
                    raise BundleClientError("PACKAGE_CHUNK_FENCE_MISMATCH")
                handle.write(chunk)
                checksum.update(chunk)
                offset += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if checksum.hexdigest() != package_id:
            raise BundleClientError("PACKAGE_SHA256_MISMATCH")
        check_deadline()
        result = extract_verified_bundle(package, descriptor, generation_id, root)
        return {**result, "package_path": str(package), "descriptor": descriptor,
                "ack_authority": "ORIGINAL_MANIFEST_ROWS_ONLY"}
    except Exception:
        package.unlink(missing_ok=True)
        raise
