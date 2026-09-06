"""Explicit, lease-serialized retirement of reproducible transport derivatives.

Never called by HTTP or admission automatically. Caller owns the coordinator
boundary and supplies its protected generations. No raw research is removed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from data_sync_bundle_storage import (_generation_usage, _stat, _pairs, _entries,
    HEX, MAX_STATE_BYTES, DIAGNOSTIC_FILE)
from data_sync_bundle_worker import _singleton_lease, _validate_output_root

MAX_RECEIPT = 4 * 1024 * 1024


def _stable_read(path, limit):
    before = _stat(path)
    if before.st_size > limit:
        raise ValueError("DERIVATIVE_RETIRE_READ_LIMIT")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    after = _stat(path)
    if len(raw) != before.st_size or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("DERIVATIVE_RETIRE_READ_CHANGED")
    return raw


def _seal(payload):
    value = {k: v for k, v in payload.items() if k != "receipt_sha256"}
    value["receipt_sha256"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def _directory_identity(path):
    info = _stat(path, directory=True)
    return [info.st_dev, info.st_ino]


def retire_derivative_generation(source_root, output_root, generation_id, *,
        current_generation, expected_state_sha256, protected_generations,
        receipt_path, timeout_seconds=120, maximum_bytes=256 * 1024 * 1024):
    """Remove one exact reviewed generation; caller must park coordinator first.

    protected_generations is evaluated inside the OS worker lease. It must
    return all active/coordinator/download generation IDs, or fail closed.
    """
    if (any(not isinstance(v, str) or not HEX.fullmatch(v) for v in
            (generation_id, current_generation, expected_state_sha256))
            or generation_id == current_generation or not callable(protected_generations)
            or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120
            or type(maximum_bytes) is not int or not 0 < maximum_bytes <= 512 * 1024 * 1024):
        raise ValueError("DERIVATIVE_RETIRE_ARGUMENTS")
    source = Path(os.path.abspath(source_root))
    output = _validate_output_root(source.resolve(strict=True), output_root)
    target = output / ("g-" + generation_id[:16])
    receipt = Path(os.path.abspath(receipt_path))
    for root in (source, output):
        if receipt == root or root in receipt.parents:
            raise ValueError("DERIVATIVE_RETIRE_RECEIPT_MUST_BE_EXTERNAL")
    for component in (*reversed(output.parents), output):
        _stat(component, directory=True)
    for component in (*reversed(receipt.parent.parents), receipt.parent):
        _stat(component, directory=True)
    root_identities = {str(p): _directory_identity(p) for p in (*reversed(output.parents), output)}
    lease = output / ".bundle-worker.lease"
    if lease.exists() or lease.is_symlink():
        _stat(lease)
    deadline = time.monotonic() + timeout_seconds
    def protected():
        ids = protected_generations()
        if not isinstance(ids, (set, frozenset, list, tuple)) or any(
                not isinstance(v, str) or not HEX.fullmatch(v) for v in ids):
            raise ValueError("DERIVATIVE_RETIRE_PROTECTION_UNAVAILABLE")
        if generation_id in ids:
            raise ValueError("DERIVATIVE_RETIRE_PROTECTED")
    with _singleton_lease(lease):
        protected()
        resume = receipt.exists() or receipt.is_symlink()
        if resume:
            payload = json.loads(_stable_read(receipt, MAX_RECEIPT), object_pairs_hook=_pairs)
            if (payload.get("receipt_sha256") != _seal(payload)["receipt_sha256"]
                    or payload.get("schema") != "transport_derivative_retirement_v1"
                    or payload.get("status") not in {"VERIFIED_RETIREMENT_INTENT", "COMPLETE"}
                    or payload.get("generation_id") != generation_id
                    or payload.get("state_sha256") != expected_state_sha256
                    or payload.get("source_root") != str(source) or payload.get("output_root") != str(output)
                    or payload.get("raw_source_deleted") is not False):
                raise ValueError("DERIVATIVE_RETIRE_RESUME_IDENTITY")
            rows = payload.get("artifacts")
            if not isinstance(rows, list) or not 1 <= len(rows) <= 8194:
                raise ValueError("DERIVATIVE_RETIRE_RESUME_INVALID")
            expected = {}
            for row in rows:
                rel = row.get("path")
                if (not isinstance(rel, str) or not re.fullmatch(r"(?:bundle-worker-state\.json|bundle-coordinator-status\.json|packages/[0-9a-f]{64}\.tar|descriptors/d-[0-9a-f]{20}\.json)", rel)
                        or rel in expected or not isinstance(row.get("sha256"), str) or not HEX.fullmatch(row["sha256"])
                        or type(row.get("bytes")) is not int or row["bytes"] < 0):
                    raise ValueError("DERIVATIVE_RETIRE_RESUME_INVALID")
                expected[rel] = row["sha256"]
            if expected.get("bundle-worker-state.json") != expected_state_sha256:
                raise ValueError("DERIVATIVE_RETIRE_RESUME_INVALID")
            directories = payload.get("directory_identities")
            if not isinstance(directories, dict) or set(directories) - {".", "packages", "descriptors"} or "." not in directories:
                raise ValueError("DERIVATIVE_RETIRE_RESUME_INVALID")
        else:
            _stat(target, directory=True)
            _, actual_id = _generation_usage(target, current_generation)
            if actual_id != generation_id:
                raise ValueError("DERIVATIVE_RETIRE_IDENTITY")
            raw = _stable_read(target / "bundle-worker-state.json", MAX_STATE_BYTES)
            if hashlib.sha256(raw).hexdigest() != expected_state_sha256:
                raise ValueError("DERIVATIVE_RETIRE_STATE_CHANGED")
            state = json.loads(raw, object_pairs_hook=_pairs)
            expected = {"bundle-worker-state.json": expected_state_sha256}
            for row in state["package_index"]:
                expected["packages/" + row["package_sha256"] + ".tar"] = row["package_sha256"]
                expected[row["descriptor_path"]] = row["descriptor_sha256"]
            if (target / DIAGNOSTIC_FILE).exists():
                expected[DIAGNOSTIC_FILE] = None
            directories = {name: _directory_identity(target / name) for name in (".", "packages", "descriptors") if (target / name).exists()}
        def guard():
            for p, identity in root_identities.items():
                if _directory_identity(Path(p)) != identity:
                    raise ValueError("DERIVATIVE_RETIRE_DIRECTORY_CHANGED")
            for name, identity in directories.items():
                p = target / name
                if p.exists() or p.is_symlink():
                    if _directory_identity(p) != identity:
                        raise ValueError("DERIVATIVE_RETIRE_DIRECTORY_CHANGED")
                elif not resume:
                    raise ValueError("DERIVATIVE_RETIRE_DIRECTORY_CHANGED")
        guard()
        # On resume only previously recorded artifacts may have disappeared.
        if target.exists():
            for child in _entries(target, 4):
                if child.name in {"packages", "descriptors"}:
                    _stat(child, directory=True)
                    for item in _entries(child, 4096):
                        if str(item.relative_to(target)).replace("\\", "/") not in expected:
                            raise ValueError("DERIVATIVE_RETIRE_ORPHAN")
                elif child.name not in expected:
                    raise ValueError("DERIVATIVE_RETIRE_ORPHAN")
        verified, total = [], 0
        for relative, digest in expected.items():
            path = target / relative
            guard()
            if resume and not path.exists() and not path.is_symlink():
                continue
            before = _stat(path)
            total += before.st_size
            if total > maximum_bytes:
                raise ValueError("DERIVATIVE_RETIRE_BYTE_BUDGET")
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                while True:
                    if time.monotonic() >= deadline:
                        raise ValueError("DERIVATIVE_RETIRE_DEADLINE")
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    hasher.update(block)
            after = _stat(path)
            identity = (before.st_ino, before.st_size, before.st_mtime_ns)
            if identity != (after.st_ino, after.st_size, after.st_mtime_ns) or (digest and hasher.hexdigest() != digest):
                raise ValueError("DERIVATIVE_RETIRE_ARTIFACT_CHANGED")
            verified.append({"path": relative, "sha256": hasher.hexdigest(), "bytes": before.st_size, "identity": identity})
        protected()
        # Revalidate every artifact before the durable deletion-intent receipt.
        if not resume:
            _generation_usage(target, current_generation)
        for row in verified:
            info = _stat(target / row["path"])
            if (info.st_ino, info.st_size, info.st_mtime_ns) != row["identity"]:
                raise ValueError("DERIVATIVE_RETIRE_ARTIFACT_CHANGED")
        if not resume:
            payload = {"schema": "transport_derivative_retirement_v1", "status": "VERIFIED_RETIREMENT_INTENT",
            "generation_id": generation_id, "current_generation": current_generation,
            "state_sha256": expected_state_sha256, "artifacts": verified, "bytes": total,
            "source_root": str(source), "output_root": str(output), "directory_identities": directories,
            "raw_source_deleted": False, "reproducible_transport_only": True}
        payload = _seal(payload)
        if len(json.dumps(payload).encode()) > MAX_RECEIPT:
            raise ValueError("DERIVATIVE_RETIRE_RECEIPT_LIMIT")
        # Exclusive creation retains evidence even if unlinking is interrupted.
        if not resume:
            with receipt.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
        # Missing directories become expected only after recorded deletion starts.
        resume = True
        for row in verified:
            guard()
            info = _stat(target / row["path"])
            if (info.st_ino, info.st_size, info.st_mtime_ns) != row["identity"]:
                raise ValueError("DERIVATIVE_RETIRE_ARTIFACT_CHANGED")
            (target / row["path"]).unlink()
        for name in ("packages", "descriptors"):
            guard()
            directory = target / name
            if directory.exists():
                directory.rmdir()
        guard()
        if target.exists():
            target.rmdir()
        payload["status"] = "COMPLETE"
        payload = _seal(payload)
        temporary = receipt.with_name(receipt.name + "." + uuid.uuid4().hex + ".tmp")
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, receipt)
        return payload
