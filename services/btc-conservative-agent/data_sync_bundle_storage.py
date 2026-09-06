"""Read-only total derivative admission. Not retention, cleanup, or source GC.

The caller must serialize this check with derivative creation. Inspect only the
dedicated output root and its fixed-depth package/descriptor directories; never
walk source data. Unknown/orphan artifacts require operator diagnosis, not
deletion or an optimistic zero-byte estimate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_PACKAGES = 4096
MAX_PACKAGE_BYTES = 16 * 1024 * 1024 + 256 * 1024 + 10240
STATE_SCHEMA = "fly_runtime_transport_bundle_worker_state_v1"
HEX = re.compile(r"^[0-9a-f]{64}$")
GEN = re.compile(r"^g-[0-9a-f]{16}$")
DIAGNOSTIC_FILE = "bundle-coordinator-status.json"
EARLY_DIAGNOSTIC_FILE = "bundle-coordinator-early-status.json"


def _diagnostic_usage(path, identity=None):
    """Only the bounded diagnostic schema; never arbitrary extra artifacts."""
    before = _stat(path)
    if not 0 < before.st_size <= 16384:
        _fail("BUNDLE_DERIVATIVE_DIAGNOSTIC_INVALID")
    with path.open("rb") as stream:
        raw = stream.read(16385)
    after = _stat(path)
    if len(raw) != before.st_size or (before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
        _fail("BUNDLE_DERIVATIVE_DIAGNOSTIC_INVALID")
    value = json.loads(raw, object_pairs_hook=_pairs)
    fields = {"generation_id", "page_index_sha256", "source_git_rev", "collection_epoch_id", "tile_registry_signature"}
    bound = value.get("identity")
    if (value.get("schema") != "fly_transport_bundle_coordinator_status_v1"
            or value.get("authority") != "DIAGNOSTIC_ONLY_NO_ACK_OR_LIVENESS_AUTHORITY"
            or not isinstance(bound, dict) or set(bound) != fields
            or any(not isinstance(v, str) or not 1 <= len(v) <= 256 for v in bound.values())
            or not HEX.fullmatch(bound["generation_id"])
            or not HEX.fullmatch(bound["page_index_sha256"])
            or value.get("status") not in {"STARTING", "BUILDING", "COMPLETE", "FAILED", "DEFERRED", "STOPPED"}
            or type(value.get("terminal")) is not bool):
        _fail("BUNDLE_DERIVATIVE_DIAGNOSTIC_INVALID")
    if identity is not None and any(bound[key] != identity.get("inventory_generation_id" if key == "generation_id" else key) for key in fields):
        _fail("BUNDLE_DERIVATIVE_DIAGNOSTIC_IDENTITY")
    return len(raw)


class DerivativeAdmissionError(ValueError):
    pass


def _fail(code):
    raise DerivativeAdmissionError(code)


def _stat(path, *, directory=False):
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode) or int(getattr(value, "st_file_attributes", 0) or 0) & 0x400:
        _fail("BUNDLE_DERIVATIVE_LINK_REJECTED")
    if not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)):
        _fail("BUNDLE_DERIVATIVE_TYPE_INVALID")
    return value


def _entries(path, limit):
    result = []
    with os.scandir(path) as entries:
        for entry in entries:
            if len(result) >= limit:
                _fail("BUNDLE_DERIVATIVE_ENTRY_LIMIT")
            result.append(Path(entry.path))
    return result


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("BUNDLE_DERIVATIVE_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _generation_usage(directory, current):
    children = {path.name: path for path in _entries(directory, 4)}
    if "bundle-worker-state.json" not in children or set(children) - {
            "bundle-worker-state.json", "packages", "descriptors", DIAGNOSTIC_FILE}:
        _fail("BUNDLE_DERIVATIVE_ORPHAN_ARTIFACT")
    state_path = children["bundle-worker-state.json"]
    before = _stat(state_path)
    if before.st_size > MAX_STATE_BYTES:
        _fail("BUNDLE_DERIVATIVE_STATE_LIMIT")
    with state_path.open("rb") as handle:
        raw = handle.read(MAX_STATE_BYTES + 1)
    after = _stat(state_path)
    if (len(raw) > MAX_STATE_BYTES or len(raw) != before.st_size
            or (before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_ino, after.st_size, after.st_mtime_ns)):
        _fail("BUNDLE_DERIVATIVE_STATE_CHANGED")
    value = json.loads(raw, object_pairs_hook=_pairs,
                       parse_constant=lambda _: _fail("BUNDLE_DERIVATIVE_JSON_INVALID"))
    identity = value.get("generation") if isinstance(value, dict) else None
    generation_id = identity.get("inventory_generation_id") if isinstance(identity, dict) else None
    if (value.get("schema") != STATE_SCHEMA or not isinstance(generation_id, str)
            or not HEX.fullmatch(generation_id)
            or identity.get("inventory_sha256") != generation_id
            or directory.name != "g-" + generation_id[:16]
            or (directory.name == "g-" + current[:16] and generation_id != current)):
        _fail("BUNDLE_DERIVATIVE_GENERATION_INVALID")
    index = value.get("package_index")
    if not isinstance(index, list) or len(index) > MAX_PACKAGES:
        _fail("BUNDLE_DERIVATIVE_INDEX_LIMIT")
    expected_packages, expected_descriptors = {}, {}
    conservative = 0
    for entry in index:
        if not isinstance(entry, dict):
            _fail("BUNDLE_DERIVATIVE_INDEX_INVALID")
        digest, descriptor = entry.get("package_sha256"), entry.get("descriptor_sha256")
        if (not isinstance(digest, str) or not HEX.fullmatch(digest)
                or not isinstance(descriptor, str) or not HEX.fullmatch(descriptor)
                or not _integer(entry.get("member_count"), 1, 256)
                or not _integer(entry.get("payload_bytes"), 0, 16 * 1024 * 1024)
                or entry.get("descriptor_path") != f"descriptors/d-{digest[:20]}.json"
                or digest + ".tar" in expected_packages
                or f"d-{digest[:20]}.json" in expected_descriptors):
            _fail("BUNDLE_DERIVATIVE_INDEX_INVALID")
        expected_packages[digest + ".tar"] = MAX_PACKAGE_BYTES
        expected_descriptors[f"d-{digest[:20]}.json"] = MAX_STATE_BYTES
        conservative += entry["payload_bytes"] + entry["member_count"] * 4096 + 32768
    physical = 0
    for name, expected in (("packages", expected_packages), ("descriptors", expected_descriptors)):
        if name not in children:
            if expected:
                _fail("BUNDLE_DERIVATIVE_INDEXED_ARTIFACT_MISSING")
            continue
        _stat(children[name], directory=True)
        found = set()
        for artifact in _entries(children[name], MAX_PACKAGES):
            if artifact.name not in expected:
                _fail("BUNDLE_DERIVATIVE_ORPHAN_ARTIFACT")
            info = _stat(artifact)
            if info.st_size > expected[artifact.name]:
                _fail("BUNDLE_DERIVATIVE_ARTIFACT_SIZE_LIMIT")
            physical += info.st_size
            found.add(artifact.name)
        if found != set(expected):
            _fail("BUNDLE_DERIVATIVE_INDEXED_ARTIFACT_MISSING")
    if physical > conservative:
        _fail("BUNDLE_DERIVATIVE_ESTIMATE_UNDERSTATES_FILES")
    diagnostic_bytes = _diagnostic_usage(children[DIAGNOSTIC_FILE], identity) if DIAGNOSTIC_FILE in children else 0
    return conservative + len(raw) + diagnostic_bytes + 4 * 4096, generation_id


def check_derivative_admission(outputroot, currentgeneration, reservepackagebytes,
                               budget=512 * 1024 * 1024, max_generations=4):
    """Return admission receipt or raise a sanitized deterministic error.

    ``currentgeneration`` is the full 64-hex inventory generation. Existing
    current generation consumes one slot, not an additional resume slot.
    No directory is created and no bytes are deleted by this function.
    """
    if (not isinstance(currentgeneration, str) or not HEX.fullmatch(currentgeneration)
            or not _integer(budget, 1, 2**63 - 1)
            or not _integer(reservepackagebytes, 0, budget)
            or not _integer(max_generations, 1, 16)):
        _fail("BUNDLE_DERIVATIVE_ADMISSION_ARGUMENTS")
    try:
        root = Path(os.path.abspath(outputroot))
        for component in (*reversed(root.parents), root):
            try:
                _stat(component, directory=True)
            except FileNotFoundError:
                # Missing root/parents contain no derivatives. Never mkdir.
                break
        entries = _entries(root, max_generations + 2) if root.exists() else []
        generations, estimate, found_current = 0, 0, False
        for path in entries:
            if path.name == EARLY_DIAGNOSTIC_FILE:
                estimate += _diagnostic_usage(path)
                continue
            if path.name == ".bundle-worker.lease":
                if _stat(path).st_size > 4096:
                    _fail("BUNDLE_DERIVATIVE_LEASE_LIMIT")
                estimate += 4096
                continue
            if not GEN.fullmatch(path.name):
                _fail("BUNDLE_DERIVATIVE_ORPHAN_ARTIFACT")
            _stat(path, directory=True)
            generations += 1
            if generations > max_generations:
                _fail("BUNDLE_DERIVATIVE_GENERATION_LIMIT")
            size, generation = _generation_usage(path, currentgeneration)
            estimate += size
            found_current = found_current or generation == currentgeneration
        projected_generations = generations + (not found_current)
        if projected_generations > max_generations:
            _fail("BUNDLE_DERIVATIVE_GENERATION_LIMIT")
        if estimate + reservepackagebytes > budget:
            _fail("BUNDLE_DERIVATIVE_TOTAL_BUDGET")
        return {"status": "ADMITTED", "estimated_bytes": estimate,
                "reserved_bytes": reservepackagebytes, "budget_bytes": budget,
                "existing_generations": generations,
                "projected_generations": projected_generations,
                "current_generation_present": found_current, "cleanup_performed": False}
    except DerivativeAdmissionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        _fail("BUNDLE_DERIVATIVE_ADMISSION_UNAVAILABLE")
