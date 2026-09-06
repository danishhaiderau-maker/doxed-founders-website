"""Authenticated bounded package reads with durable download exclusion."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

from flask import Response, jsonify, request
from data_sync_bundle_transport import MAX_PACKAGE_BYTES, SCHEMA as PACKAGE_SCHEMA
from data_sync_bundle_worker import STATE_SCHEMA, BundleWorkerError
from data_sync_bundle_download_pins import DownloadProtection

HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_CHUNK_BYTES = 1024 * 1024
IDENTITY_FIELDS = ("inventory_generation_id", "inventory_sha256", "source_git_rev",
                   "collection_epoch_id", "tile_registry_signature", "page_index_sha256")


class BundleReadError(ValueError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


def _is_link_or_reparse(path):
    stat = path.lstat()
    if int(getattr(stat, "st_file_attributes", 0) or 0) & 0x400:
        return True
    return path.is_symlink()


def _validated_root(raw):
    lexical = Path(os.path.abspath(raw))
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current = current / part
        if _is_link_or_reparse(current):
            raise BundleReadError("PACKAGE_ROOT_LINK_REJECTED")
    resolved = lexical.resolve(strict=True)
    if resolved != lexical or not resolved.is_dir():
        raise BundleReadError("PACKAGE_ROOT_INVALID")
    return resolved


def _regular_child(root, *parts):
    path = root
    for part in parts:
        if not part or part in {".", ".."} or any(c in part for c in "/\\:\x00"):
            raise BundleReadError("INVALID_PACKAGE_PATH", 400)
        path = path / part
        stat = path.lstat()
        if int(getattr(stat, "st_file_attributes", 0) or 0) & 0x400 or path.is_symlink():
            raise BundleReadError("PACKAGE_PATH_LINK_REJECTED")
    if not path.is_file():
        raise BundleReadError("PACKAGE_FILE_UNAVAILABLE", 404)
    return path


def _metadata(path, expected_sha=None):
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise BundleReadError("PACKAGE_METADATA_LIMIT")
    with path.open("rb") as handle:
        raw = handle.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise BundleReadError("PACKAGE_METADATA_LIMIT")
    if expected_sha is not None and hashlib.sha256(raw).hexdigest() != expected_sha:
        raise BundleReadError("PACKAGE_DESCRIPTOR_HASH_MISMATCH")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise BundleReadError("PACKAGE_METADATA_INVALID")
    return value


def register_bundle_routes(app, *, authenticated, generation_lookup, output_root):
    """Inject strict authorization and retained-generation lookup from the owner.

    Registration is explicit. This module starts no workers, triggers no
    inventories, acknowledges no files, and never hashes a complete TAR in HTTP.
    """
    # Production registration occurs before derivative artifacts exist. A lazy
    # path provider must be read-only; validate it only after authentication.
    fixed_root = None if callable(output_root) else _validated_root(output_root)

    def authority():
        if authenticated() is not True:
            raise BundleReadError("ADMIN_AUTH_REQUIRED", 401)
        root = _validated_root(output_root() if callable(output_root) else fixed_root)
        generation_id = str(request.args.get("generation_id") or "")
        if not HEX.fullmatch(generation_id):
            raise BundleReadError("INVALID_GENERATION_ID", 400)
        current = generation_lookup(generation_id)
        if (not isinstance(current, dict) or current.get("ack_eligible") is not True
                or current.get("storage") != "disk_pages_v2"):
            raise BundleReadError("GENERATION_NOT_RETAINED_OR_ACK_ELIGIBLE", 409)
        expected = {key: current.get(key) for key in IDENTITY_FIELDS}
        expected["inventory_generation_id"] = generation_id
        expected["inventory_sha256"] = generation_id
        if any(not isinstance(value, str) or not value for value in expected.values()):
            raise BundleReadError("GENERATION_IDENTITY_INCOMPLETE")
        return root, generation_id, expected

    def context(access):
        root, generation_id, expected = access
        directory = root / f"g-{generation_id[:16]}"
        state = _metadata(_regular_child(root, directory.name, "bundle-worker-state.json"))
        generation = state.get("generation")
        if (state.get("schema") != STATE_SCHEMA or not isinstance(generation, dict)
                or any(generation.get(k) != v for k, v in expected.items())):
            raise BundleReadError("PACKAGE_STATE_GENERATION_MISMATCH")
        index = state.get("package_index")
        if not isinstance(index, list) or len(index) > 4096:
            raise BundleReadError("PACKAGE_INDEX_INVALID")
        public = []
        seen = set()
        for entry in index:
            if not isinstance(entry, dict):
                raise BundleReadError("PACKAGE_INDEX_INVALID")
            digest, descriptor_sha = entry.get("package_sha256"), entry.get("descriptor_sha256")
            if (not isinstance(digest, str) or not HEX.fullmatch(digest) or digest in seen
                    or not isinstance(descriptor_sha, str) or not HEX.fullmatch(descriptor_sha)
                    or entry.get("descriptor_path") != f"descriptors/d-{digest[:20]}.json"):
                raise BundleReadError("PACKAGE_INDEX_INVALID")
            if (type(entry.get("member_count")) is not int or not 1 <= entry["member_count"] <= 256
                    or type(entry.get("payload_bytes")) is not int
                    or not 0 <= entry["payload_bytes"] <= 16 * 1024 * 1024):
                raise BundleReadError("PACKAGE_INDEX_COUNTS_INVALID")
            seen.add(digest)
            public.append({k: entry.get(k) for k in (
                "package_sha256", "descriptor_sha256", "descriptor_path",
                "member_count", "payload_bytes")})
        return generation_id, directory, expected, state, public

    def guarded(fn):
        def call():
            try:
                access = authority()  # No metadata mutation before authorization.
                root, generation_id, _ = access
                session = request.headers.get("X-Bundle-Download-Session")
                if session is not None and not HEX.fullmatch(session):
                    raise BundleReadError("INVALID_DOWNLOAD_SESSION", 400)
                # Legacy clients require no new parameter/cookie. One shared
                # generation pin protects their idle intervals between chunks.
                session = session or hashlib.sha256(
                    ("legacy-bundle-download:" + generation_id).encode()).hexdigest()
                pin_root = root.parent / "transport-download-pins"
                pin_root.mkdir(exist_ok=True)  # One bounded sibling, never raw evidence.
                protection = DownloadProtection(pin_root, root / ".bundle-worker.lease")
                protection.pin(generation_id, session, ttl_seconds=300)
                with protection.read_chunk(generation_id, session):
                    # Recheck retention under exclusion before any artifact read.
                    if authority() != access:
                        raise BundleReadError("GENERATION_AUTHORITY_CHANGED")
                    response = fn(access)  # Fully materialized; no streaming handles.
            except BundleReadError as exc:
                response = jsonify({"error": exc.code}), exc.status
            except BundleWorkerError as exc:
                if str(exc) == "BUNDLE_WORKER_LEASE_HELD":
                    response = jsonify({"error": "BUNDLE_DOWNLOAD_BUSY"}), 503, {"Retry-After": "1"}
                else:
                    response = jsonify({"error": "PACKAGE_READ_INVALID"}), 409
            except FileNotFoundError:
                response = jsonify({"error": "PACKAGE_NOT_BUILT_OR_RETAINED"}), 404
            except ValueError as exc:
                code = "BUNDLE_DOWNLOAD_RETIRING" if str(exc) == "BUNDLE_DOWNLOAD_RETIRING" else "PACKAGE_READ_INVALID"
                response = jsonify({"error": code}), 409
            except (OSError, TypeError, KeyError, OverflowError):
                response = jsonify({"error": "PACKAGE_READ_INVALID"}), 409
            return response
        return call

    def index(access):
        generation_id, _, expected, state, entries = context(access)
        packages = [
            {k: entry[k] for k in (
                "package_sha256", "descriptor_sha256", "member_count", "payload_bytes"
            )}
            for entry in entries
        ]
        return jsonify({"schema": "fly_runtime_transport_bundle_index_v1",
                        "generation": expected, "generation_id": generation_id,
                        "status": "COMPLETE" if state.get("completed") is True else "BUILDING",
                        "packages": packages, "ack_authority": "ORIGINAL_MANIFEST_ROWS_ONLY"})

    def package(access):
        generation_id, directory, expected, _, entries = context(access)
        root = directory.parent
        package_id = str(request.args.get("package_id") or "")
        if not HEX.fullmatch(package_id):
            raise BundleReadError("INVALID_PACKAGE_ID", 400)
        entry = next((item for item in entries if item["package_sha256"] == package_id), None)
        if entry is None:
            raise BundleReadError("PACKAGE_NOT_IN_GENERATION", 404)
        descriptor_parts = PurePosixPath(entry["descriptor_path"]).parts
        descriptor = _metadata(_regular_child(
            root, directory.name, *descriptor_parts),
                               entry["descriptor_sha256"])
        if (descriptor.get("schema") != PACKAGE_SCHEMA or descriptor.get("package_sha256") != package_id
                or any(descriptor.get(k) != v for k, v in expected.items() if k != "page_index_sha256")):
            raise BundleReadError("PACKAGE_DESCRIPTOR_GENERATION_MISMATCH")
        if (type(descriptor.get("member_count")) is not int
                or descriptor["member_count"] != entry["member_count"]
                or type(descriptor.get("payload_bytes")) is not int
                or descriptor["payload_bytes"] != entry["payload_bytes"]):
            raise BundleReadError("PACKAGE_DESCRIPTOR_INDEX_MISMATCH")
        if request.args.get("descriptor") == "1":
            return jsonify({k: v for k, v in descriptor.items() if k != "package_path"})
        offset = int(request.args.get("offset", "0"))
        limit = int(request.args.get("limit", str(MAX_CHUNK_BYTES)))
        size = descriptor.get("package_size")
        if (type(size) is not int or size <= 0 or size > MAX_PACKAGE_BYTES
                or offset < 0 or offset > size or limit < 1 or limit > MAX_CHUNK_BYTES):
            raise BundleReadError("INVALID_PACKAGE_RANGE", 416)
        path = _regular_child(root, directory.name, "packages", package_id + ".tar")
        lexical_before = path.stat()
        if lexical_before.st_size != size:
            raise BundleReadError("PACKAGE_SIZE_CHANGED")
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if (int(before.st_size), int(before.st_mtime_ns), int(before.st_ino)) != (
                    int(lexical_before.st_size), int(lexical_before.st_mtime_ns),
                    int(getattr(lexical_before, "st_ino", 0) or 0)):
                raise BundleReadError("PACKAGE_GENERATION_CHANGED")
            handle.seek(offset)
            chunk = handle.read(min(limit, size - offset))
            after = os.fstat(handle.fileno())
        lexical_after = path.stat()
        before_identity = (int(before.st_size), int(before.st_mtime_ns), int(before.st_ino))
        after_identity = (int(after.st_size), int(after.st_mtime_ns), int(after.st_ino))
        lexical_after_identity = (
            int(lexical_after.st_size), int(lexical_after.st_mtime_ns),
            int(getattr(lexical_after, "st_ino", 0) or 0),
        )
        if before_identity != after_identity or after_identity != lexical_after_identity:
            raise BundleReadError("PACKAGE_GENERATION_CHANGED")
        if len(chunk) != min(limit, size - offset):
            raise BundleReadError("PACKAGE_SHORT_READ")
        return Response(chunk, mimetype="application/octet-stream", headers={
            "Cache-Control": "no-store", "X-Inventory-Generation": generation_id,
            "X-Package-Sha256": package_id, "X-Chunk-Sha256": hashlib.sha256(chunk).hexdigest(),
            "X-Chunk-Offset": str(offset), "X-Package-Size": str(size),
            "X-Chunk-EOF": "true" if offset + len(chunk) == size else "false",
        })

    app.add_url_rule("/api/data-sync/bundles", "data_sync_bundles", guarded(index), methods=["GET"])
    app.add_url_rule("/api/data-sync/bundle", "data_sync_bundle", guarded(package), methods=["GET"])
