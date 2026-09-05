"""Bounded read-only append/rotation recovery audit; never opens a V3 store.

Caller must hold writer/reader/reset barriers. This audits journal closure, not
WAL, paper exposure or full ledger content integrity. It never runs recovery.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from research_v3_contract import LEDGER_NAMES, canonical_json


def _unsafe(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _signed(row):
    if not isinstance(row, dict):
        return False
    material = dict(row)
    supplied = material.pop("binding_sha256", None)
    return supplied == hashlib.sha256(canonical_json(material).encode()).hexdigest()


def audit_research_reset_recovery(runtime_root, *, expected_identity,
                                 max_entries=10_000, max_metadata_bytes=16 * 1024 * 1024):
    """Return exact retained paths and blockers. Cap exhaustion never means empty.

    The SHA-bound PREPARED/SEALED/COMMITTED chain is checked without bulk-ledger
    hashing. Retained recovery files are never deletion candidates from this API.
    """
    if (type(max_entries) is not int or not 0 < max_entries <= 100_000
            or type(max_metadata_bytes) is not int or not 0 < max_metadata_bytes <= 64 * 1024 * 1024):
        raise ValueError("INVALID_AUDIT_BOUNDS")
    root = Path(os.path.abspath(os.fspath(runtime_root)))
    if not Path(runtime_root).is_absolute() or root == Path(root.anchor) or root == Path.home():
        raise ValueError("EXPLICIT_RUNTIME_ROOT_REQUIRED")
    for path in (root, *root.parents):
        if _unsafe(path.lstat()):
            raise ValueError("UNSAFE_RUNTIME_ROOT")
    if not root.is_dir():
        raise ValueError("RUNTIME_ROOT_NOT_DIRECTORY")
    blockers, retained, completed, retired = [], [], [], []
    seen, consumed, complete = 0, 0, True

    def block(code, path=None):
        blockers.append({"code": code, "path": path.relative_to(root).as_posix() if path else None})

    keys = {"epoch_id", "source_revision", "deployed_revision", "tile_config_signature"}
    identity_ok = isinstance(expected_identity, dict) and set(expected_identity) == keys and all(
        isinstance(v, str) and v.strip() and v.upper() not in {"UNKNOWN", "UNAVAILABLE", "NOT_DEPLOYED_LOCAL"}
        for v in expected_identity.values())
    if not identity_ok:
        block("CURRENT_IDENTITY_UNAVAILABLE")

    def check(path, *, directory=False):
        for ancestor in (path, *path.parents):
            if ancestor == root:
                break
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                continue
            if _unsafe(info):
                raise ValueError("UNSAFE_LINK_OR_REPARSE")
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ValueError("UNEXPECTED_FILE_TYPE")
        return info

    def entries(directory):
        nonlocal seen, complete
        try:
            check(directory, directory=True)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            complete = False
            block(str(exc) if isinstance(exc, ValueError) else "DIRECTORY_IO_ERROR", directory)
            return []
        rows = []
        try:
            with os.scandir(directory) as stream:
                for item in stream:
                    seen += 1
                    if seen > max_entries:
                        complete = False
                        block("ENTRY_BUDGET_EXCEEDED", directory)
                        break
                    rows.append(Path(item.path))
        except OSError:
            complete = False
            block("DIRECTORY_IO_ERROR", directory)
        return sorted(rows)

    def read(path):
        nonlocal consumed, complete
        try:
            before = check(path)
            if before.st_size > max_metadata_bytes - consumed:
                complete = False
                raise ValueError("METADATA_BUDGET_EXCEEDED")
            with path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev):
                    raise ValueError("RECOVERY_FILE_CHANGED")
                raw = handle.read(max_metadata_bytes - consumed + 1)
            consumed += len(raw)
            after = check(path)
            if consumed > max_metadata_bytes:
                complete = False
                raise ValueError("METADATA_BUDGET_EXCEEDED")
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("RECOVERY_FILE_CHANGED")
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("RECOVERY_JSON_NOT_OBJECT")
            retained.append({"path": path.relative_to(root).as_posix(), "size_bytes": len(raw),
                             "sha256": hashlib.sha256(raw).hexdigest(), "reason": "RECOVERY_JOURNAL_RETAINED"})
            return row, raw
        except (OSError, ValueError, UnicodeError) as exc:
            code = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "RECOVERY_FILE_UNREADABLE_OR_CORRUPT"
            block(code, path)
            retained.append({"path": path.relative_to(root).as_posix(), "reason": "UNKNOWN_RECOVERY_RETAINED"})
            return None, None

    # Retired metadata is not active evidence. Exact completed relocation plans
    # permit its continued retention without accepting old identity in ACTIVE
    # rotation namespaces. An unfinished relocation is still pending recovery.
    retirement_root = root / "v3/receipts/epoch_authority_retirements_v1"
    retirement_groups, retirement_dirs = {}, {}
    for path in entries(retirement_root):
        match = re.fullmatch(r"([0-9a-f]{64})\.(PREPARED|COMPLETED)\.json", path.name)
        if match:
            retirement_groups.setdefault(match[1], {})[match[2]] = path
        elif re.fullmatch(r"[0-9a-f]{64}", path.name):
            retirement_dirs[path.name] = path
        else:
            block("UNKNOWN_EPOCH_RETIREMENT_PATH", path)
            retained.append({"path": path.relative_to(root).as_posix(), "reason": "UNKNOWN_RECOVERY_RETAINED"})
    for pointer_sha in sorted(set(retirement_groups) | set(retirement_dirs)):
        paths = retirement_groups.get(pointer_sha, {})
        rows = {state: read(path)[0] for state, path in paths.items()}
        archive_paths = entries(retirement_dirs[pointer_sha]) if pointer_sha in retirement_dirs else []
        if set(paths) != {"PREPARED", "COMPLETED"}:
            block("EPOCH_RETIREMENT_INCOMPLETE_OR_ORPHANED", next(iter(paths.values()), retirement_dirs.get(pointer_sha)))
            retained.extend({"path": path.relative_to(root).as_posix(), "reason": "INCOMPLETE_RETIREMENT_METADATA_RETAINED"}
                            for path in archive_paths)
            continue
        prepared, final = rows["PREPARED"], rows["COMPLETED"]
        if prepared is None or final is None:
            continue
        plan = {k: v for k, v in prepared.items() if k not in {"binding_sha256", "state"}}
        valid = (_signed(prepared) and _signed(final) and prepared.get("state") == "PREPARED"
                 and final.get("state") == "COMPLETED" and plan == {
                     k: v for k, v in final.items() if k not in {"binding_sha256", "state"}})
        old = plan.get("old_identity")
        ledger = plan.get("ledger")
        new_epoch = plan.get("new_epoch_id")
        valid = valid and plan.get("schema") == "empty_epoch_authority_retirement_v2"
        valid = valid and ledger in LEDGER_NAMES and plan.get("pointer_sha256") == pointer_sha
        valid = valid and isinstance(old, dict) and set(old) == keys and all(
            isinstance(v, str) and v.strip() and v.upper() not in {"UNKNOWN", "UNAVAILABLE", "NOT_DEPLOYED_LOCAL"}
            for v in old.values())
        valid = valid and isinstance(new_epoch, str) and new_epoch.strip() and new_epoch.upper() not in {"UNKNOWN", "UNAVAILABLE"}
        valid = valid and new_epoch != (old or {}).get("epoch_id")
        valid = valid and plan.get("relative_pointer") == f"v3/receipts/ledger_generations_v1/{ledger}/ACTIVE.json"
        valid = valid and isinstance(plan.get("boundary_proof_sha256"), str) and bool(re.fullmatch(r"[0-9a-f]{64}", plan["boundary_proof_sha256"]))
        metadata = plan.get("metadata")
        valid = valid and isinstance(metadata, list) and len(metadata) <= max_entries
        expected_destinations = set()
        expected_sources = set()
        archived_rotations = {}
        if valid:
            for item in metadata:
                if not isinstance(item, dict) or set(item) != {"source_relative", "destination_relative", "sha256"}:
                    valid = False
                    break
                source, destination, digest = (item.get(k) for k in ("source_relative", "destination_relative", "sha256"))
                if not all(isinstance(v, str) for v in (source, destination, digest)):
                    valid = False
                    break
                basename = source.rsplit("/", 1)[-1]
                permitted_source = (source == f"v3/receipts/ledger_generations_v1/{ledger}/LEGACY-0-TO-1.json"
                    or bool(re.fullmatch(re.escape(f"v3/receipts/ledger_generations_v1/{ledger}/rotations/")
                        + r"[0-9]{20}\.(PREPARED|SEALED|COMMITTED)\.json", source)))
                permitted_destination = retirement_root.relative_to(root).as_posix() + f"/{pointer_sha}/{basename}"
                if (not permitted_source or destination != permitted_destination or not re.fullmatch(r"[0-9a-f]{64}", digest)
                        or destination in expected_destinations or source in expected_sources):
                    valid = False
                    break
                expected_destinations.add(destination)
                expected_sources.add(source)
                archived, raw = read(root / destination)
                if (archived is None or hashlib.sha256(raw).hexdigest() != digest or not _signed(archived)
                        or archived.get("identity") != old or archived.get("ledger") != ledger):
                    valid = False
                    break
                schema = archived.get("schema")
                if basename == "LEGACY-0-TO-1.json":
                    valid = schema == "v3_ledger_legacy_generation_migration_v1"
                elif basename.endswith(".SEALED.json"):
                    valid = schema == "v3_ledger_rotation_seal_v1"
                else:
                    valid = schema == "v3_ledger_rotation_transaction_v1" and archived.get("state") == basename.split(".")[1]
                if basename != "LEGACY-0-TO-1.json":
                    generation_text, stage, _ = basename.split(".")
                    generation = int(generation_text)
                    valid = valid and generation > 0 and type(archived.get("generation")) is int and archived["generation"] == generation
                    archived_rotations.setdefault(generation, {})[stage] = (archived, raw)
                if not valid:
                    break
        if valid:
            for stages in archived_rotations.values():
                if set(stages) != {"PREPARED", "SEALED", "COMMITTED"}:
                    valid = False
                    break
                first, seal, last = (stages[s][0] for s in ("PREPARED", "SEALED", "COMMITTED"))
                reconstructed = {k: v for k, v in last.items() if k not in {"binding_sha256", "seal_sha256"}}
                reconstructed["state"] = "PREPARED"
                valid = reconstructed == {k: v for k, v in first.items() if k != "binding_sha256"}
                valid = valid and last.get("seal_sha256") == hashlib.sha256(stages["SEALED"][1]).hexdigest()
                valid = valid and all(first.get(k) == seal.get(k) for k in ("active_ref", "sealed_ref", "successor_ref"))
                if not valid:
                    break
        actual_destinations = {path.relative_to(root).as_posix() for path in archive_paths}
        valid = valid and actual_destinations == expected_destinations
        if valid:
            retired.append({"ledger": ledger, "pointer_sha256": pointer_sha,
                            "old_epoch_id": old["epoch_id"], "new_epoch_id": new_epoch,
                            "status": "EXACT_COMPLETED_RETIREMENT_METADATA_VERIFIED"})
        else:
            block("EPOCH_RETIREMENT_RECEIPT_OR_METADATA_INVALID", paths["COMPLETED"])

    heads = root / "v3/receipts/emergency_record_idempotency_v1/append_heads"
    for path in entries(heads):
        if path.name not in {n + ".json" for n in LEDGER_NAMES}:
            block("UNKNOWN_APPEND_HEAD_PATH", path)
            retained.append({"path": path.relative_to(root).as_posix(), "reason": "UNKNOWN_RECOVERY_RETAINED"})
            continue
        row, _ = read(path)
        if row is None:
            continue
        ledger = path.stem
        valid = (_signed(row) and row.get("schema") == "v3_ledger_append_head_v1"
                 and row.get("state") == "PREPARED" and row.get("ledger") == ledger
                 and row.get("identity") == expected_identity and identity_ok)
        payload = row.get("row_payload_utf8")
        if not isinstance(payload, str):
            valid = False
        else:
            data = payload.encode("utf-8")
            valid = valid and data.endswith(b"\n") and row.get("length") == len(data) and row.get("row_sha256") == hashlib.sha256(data).hexdigest()
        block("APPEND_HEAD_PENDING" if valid else "APPEND_HEAD_INVALID_OR_IDENTITY_MISMATCH", path)

    generations = root / "v3/receipts/ledger_generations_v1"
    for ledger_dir in entries(generations):
        ledger = ledger_dir.name
        if ledger not in LEDGER_NAMES:
            block("UNKNOWN_LEDGER_GENERATION_PATH", ledger_dir)
            retained.append({"path": ledger_dir.relative_to(root).as_posix(), "reason": "UNKNOWN_RECOVERY_RETAINED"})
            continue
        # Enumerating the ledger container detects unexpected receipt shapes;
        # ACTIVE/migration are retained here, not reinterpreted as rotation work.
        for child in entries(ledger_dir):
            if child.name in {"ACTIVE.json", "LEGACY-0-TO-1.json"}:
                try:
                    check(child)
                    retained.append({"path": child.relative_to(root).as_posix(), "reason": "GENERATION_AUTHORITY_RETAINED"})
                except (OSError, ValueError):
                    block("INVALID_GENERATION_AUTHORITY_PATH", child)
                continue
            if child.name != "rotations":
                block("UNKNOWN_GENERATION_RECOVERY_PATH", child)
                retained.append({"path": child.relative_to(root).as_posix(), "reason": "UNKNOWN_RECOVERY_RETAINED"})
                continue
            groups = {}
            for path in entries(child):
                match = re.fullmatch(r"([0-9]{20})\.(PREPARED|SEALED|COMMITTED)\.json", path.name)
                if not match or int(match[1]) < 1:
                    block("UNKNOWN_ROTATION_JOURNAL_PATH", path)
                    retained.append({"path": path.relative_to(root).as_posix(), "reason": "UNKNOWN_RECOVERY_RETAINED"})
                    continue
                groups.setdefault(int(match[1]), {})[match[2]] = path
            for generation, paths in sorted(groups.items()):
                rows = {state: read(path) for state, path in paths.items()}
                if set(paths) != {"PREPARED", "SEALED", "COMMITTED"}:
                    block("ROTATION_UNFINISHED_OR_ORPHANED", next(iter(paths.values())))
                    continue
                if any(value[0] is None for value in rows.values()):
                    continue
                prepared, seal, committed = (rows[name][0] for name in ("PREPARED", "SEALED", "COMMITTED"))
                valid = identity_ok and all(_signed(row) and row.get("identity") == expected_identity
                    and row.get("ledger") == ledger and type(row.get("generation")) is int
                    and row["generation"] == generation for row in (prepared, seal, committed))
                valid = valid and prepared.get("schema") == committed.get("schema") == "v3_ledger_rotation_transaction_v1"
                valid = valid and prepared.get("state") == "PREPARED" and committed.get("state") == "COMMITTED"
                valid = valid and seal.get("schema") == "v3_ledger_rotation_seal_v1"
                material = {k: v for k, v in committed.items() if k not in {"binding_sha256", "seal_sha256"}}
                material["state"] = "PREPARED"
                valid = valid and material == {k: v for k, v in prepared.items() if k != "binding_sha256"}
                valid = valid and committed.get("seal_sha256") == hashlib.sha256(rows["SEALED"][1]).hexdigest()
                for key, state, number, relative in (
                    ("active_ref", "ACTIVE", generation, f"v3/ledgers/{ledger}.jsonl"),
                    ("sealed_ref", "SEALED", generation, f"v3/ledgers/{ledger}.jsonl.{generation}"),
                    ("successor_ref", "ACTIVE", generation + 1, f"v3/ledgers/{ledger}.jsonl")):
                    ref = {"schema": "v3_ledger_generation_ref_v1", "state": state, "ledger": ledger,
                           "generation": number, "relative_path": relative}
                    valid = valid and prepared.get(key) == seal.get(key) == ref
                signature = prepared.get("source_signature")
                valid = valid and isinstance(signature, dict) and set(signature) == {"device", "inode", "size", "mtime_ns"}
                if valid:
                    valid = all(type(v) is int and v >= 0 for v in signature.values()) and signature["size"] > 0
                    valid = valid and seal.get("size") == signature["size"] and seal.get("mtime_ns") == signature["mtime_ns"]
                    valid = valid and isinstance(seal.get("sha256"), str) and bool(re.fullmatch(r"[0-9a-f]{64}", seal["sha256"]))
                if valid:
                    completed.append({"ledger": ledger, "generation": generation, "status": "COMMITTED_JOURNAL_CHAIN_VERIFIED"})
                else:
                    block("ROTATION_CHAIN_INVALID_OR_IDENTITY_MISMATCH", paths["COMMITTED"])
    blockers.sort(key=lambda row: (row["path"] or "", row["code"]))
    retained.sort(key=lambda row: row["path"])
    result = {"schema": "research_reset_recovery_audit_v1", "runtime_root": str(root),
              "identity": expected_identity, "complete": complete, "safe_for_reset_recovery_scope": complete and not blockers,
              "scope": "APPEND_HEADS_AND_ROTATION_JOURNAL_CLOSURE_ONLY", "deletion_performed": False,
              "requires_caller_exclusive_barriers": True, "ledger_payload_integrity_verified": False,
              "pending_or_unknown_count": len(blockers) if complete else None,
              "scanned_entries": seen, "metadata_bytes_read": consumed,
              "blockers": blockers, "retained_paths": retained, "completed_rotations": completed,
              "completed_epoch_retirements": retired}
    result["receipt_sha256"] = hashlib.sha256(canonical_json(result).encode()).hexdigest()
    return result
