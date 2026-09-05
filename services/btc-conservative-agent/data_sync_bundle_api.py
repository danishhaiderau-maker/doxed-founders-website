"""Authenticated reads of already-built transport packages; no HTTP-path work."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from flask import Response, jsonify, request
from data_sync_bundle_transport import MAX_PACKAGE_BYTES, SCHEMA as PACKAGE_SCHEMA
from data_sync_bundle_worker import STATE_SCHEMA

HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_CHUNK_BYTES = 1024 * 1024
IDENTITY_FIELDS = ("inventory_generation_id", "inventory_sha256", "source_git_rev",
                   "collection_epoch_id", "tile_registry_signature", "page_index_sha256")


class BundleReadError(ValueError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


def _regular_child(root, *parts):
    path = root
    for part in parts:
        if not part or part in {".", ".."} or any(c in part for c in "/\\:\x00"):
            raise BundleReadError("INVALID_PACKAGE_PATH", 400)
        path = path / part
        stat = path.lstat()
        if path.is_symlink() or int(getattr(stat, "st_file_attributes", 0) or 0) & 0x400:
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
    root = Path(output_root).resolve()

    def context():
        if authenticated() is not True:
            raise BundleReadError("ADMIN_AUTH_REQUIRED", 401)
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
                    or entry.get("descriptor_path") != f"descriptors/{digest}.json"):
                raise BundleReadError("PACKAGE_INDEX_INVALID")
            if (type(entry.get("member_count")) is not int or not 1 <= entry["member_count"] <= 256
                    or type(entry.get("payload_bytes")) is not int
                    or not 0 <= entry["payload_bytes"] <= 16 * 1024 * 1024):
                raise BundleReadError("PACKAGE_INDEX_COUNTS_INVALID")
            seen.add(digest)
            public.append({k: entry.get(k) for k in (
                "package_sha256", "descriptor_sha256", "member_count", "payload_bytes")})
        return generation_id, directory, expected, state, public

    def guarded(fn):
        def call():
            try:
                response = fn()
            except BundleReadError as exc:
                response = jsonify({"error": exc.code}), exc.status
            except FileNotFoundError:
                response = jsonify({"error": "PACKAGE_NOT_BUILT_OR_RETAINED"}), 404
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                response = jsonify({"error": "PACKAGE_READ_INVALID"}), 409
            return response
        return call

    def index():
        generation_id, _, expected, state, entries = context()
        return jsonify({"schema": "fly_runtime_transport_bundle_index_v1",
                        "generation": expected, "generation_id": generation_id,
                        "status": "COMPLETE" if state.get("completed") is True else "BUILDING",
                        "packages": entries, "ack_authority": "ORIGINAL_MANIFEST_ROWS_ONLY"})

    def package():
        generation_id, directory, expected, _, entries = context()
        package_id = str(request.args.get("package_id") or "")
        if not HEX.fullmatch(package_id):
            raise BundleReadError("INVALID_PACKAGE_ID", 400)
        entry = next((item for item in entries if item["package_sha256"] == package_id), None)
        if entry is None:
            raise BundleReadError("PACKAGE_NOT_IN_GENERATION", 404)
        descriptor = _metadata(_regular_child(root, directory.name, "descriptors", package_id + ".json"),
                               entry["descriptor_sha256"])
        if (descriptor.get("schema") != PACKAGE_SCHEMA or descriptor.get("package_sha256") != package_id
                or any(descriptor.get(k) != v for k, v in expected.items() if k != "page_index_sha256")):
            raise BundleReadError("PACKAGE_DESCRIPTOR_GENERATION_MISMATCH")
        if request.args.get("descriptor") == "1":
            return jsonify({k: v for k, v in descriptor.items() if k != "package_path"})
        offset = int(request.args.get("offset", "0"))
        limit = int(request.args.get("limit", str(MAX_CHUNK_BYTES)))
        size = descriptor.get("package_size")
        if (type(size) is not int or size <= 0 or size > MAX_PACKAGE_BYTES
                or offset < 0 or offset > size or limit < 1 or limit > MAX_CHUNK_BYTES):
            raise BundleReadError("INVALID_PACKAGE_RANGE", 416)
        path = _regular_child(root, directory.name, "packages", package_id + ".tar")
        if path.stat().st_size != size:
            raise BundleReadError("PACKAGE_SIZE_CHANGED")
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(min(limit, size - offset))
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
