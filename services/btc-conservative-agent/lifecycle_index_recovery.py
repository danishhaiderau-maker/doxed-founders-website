"""Restart-safe recovery of a derived lifecycle cursor after source rotation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from lifecycle_bundles import (
    LEDGER_NAMES, MAX_JSONL_RECORD_BYTES, _exclusive_index_lock,
    _index_ledger_chunk, _open_incremental_index, _source_anchor,
)

SCHEMA = "lifecycle_index_rotation_recovery_v1"
RECEIPT_SCHEMA = "lifecycle_index_rotation_recovery_receipt_v1"
COPY_BUDGET = 8 * 1024 * 1024
REBUILD_BUDGET = 8 * 1024 * 1024
HASH_TIMEOUT_SEC = 20
MAX_RESET_RECEIPT_BYTES = 64 * 1024 * 1024


def _strict_reset_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("LIFECYCLE_RESET_RECEIPT_DUPLICATE_KEY")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("LIFECYCLE_RESET_RECEIPT_NONFINITE")
    def finite_float(value):
        import math
        number = float(value)
        if not math.isfinite(number):
            return constant(value)
        return number
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)


def _directory_id(state: dict[str, Any]) -> str:
    # Keep Windows paths bounded; the receipt retains and verifies the full hash.
    return str(state["recovery_id"])[:16]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_state(path: Path, state: dict[str, Any]) -> None:
    material = dict(state); material.pop("state_sha256", None)
    material["state_sha256"] = hashlib.sha256(json.dumps(
        material, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest()
    state.clear(); state.update(material)
    _atomic_json(path, state)


def _load_state(path: Path) -> dict[str, Any]:
    try: state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("LIFECYCLE_RECOVERY_STATE_INVALID") from exc
    claimed = state.pop("state_sha256", None)
    calculated = hashlib.sha256(json.dumps(
        state, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest()
    if claimed != calculated:
        raise ValueError("LIFECYCLE_RECOVERY_STATE_TAMPERED")
    state["state_sha256"] = claimed
    return state


def _identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"dev": int(stat.st_dev), "ino": int(stat.st_ino),
            "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def _same(path: Path, expected: dict[str, int]) -> bool:
    try:
        return _identity(path) == expected
    except OSError:
        return False


def _same_append_source(path: Path, expected: dict[str, int]) -> bool:
    """Permit append-only growth while protecting the captured source object."""
    try:
        current = _identity(path)
    except OSError:
        return False
    return (
        current["dev"] == expected["dev"]
        and current["ino"] == expected["ino"]
        and current["size"] >= expected["size"]
    )


def _sha_prefix(path: Path, length: int) -> str:
    digest = hashlib.sha256()
    remaining = int(length)
    with path.open("rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError(f"LIFECYCLE_RECOVERY_SOURCE_TRUNCATED:{path.name}")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _bounded_hash(path: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--hash", str(path.resolve())],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=HASH_TIMEOUT_SEC, check=False,
    )
    value = result.stdout.strip().lower()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"LIFECYCLE_RECOVERY_HASH_FAILED:{path.name}")
    return value


def _components(index_dir: Path) -> list[Path]:
    base = index_dir / "lifecycle_index.sqlite3"
    return [path for path in (base, Path(f"{base}-wal"), Path(f"{base}-shm")) if path.is_file()]


def _initial(root: Path, trigger: str, index_dir: Path, reset_authorized=False) -> dict[str, Any]:
    pattern = r"SOURCE_LEDGER_(?:ROTATED|TRUNCATED):[A-Za-z0-9_.-]+\.jsonl" if reset_authorized else r"SOURCE_LEDGER_ROTATED:[A-Za-z0-9_.-]+\.jsonl"
    if not re.fullmatch(pattern, trigger):
        raise ValueError("LIFECYCLE_RECOVERY_TRIGGER_INVALID")
    sources = []
    ledger_root = (root / "v3" / "ledgers").resolve(strict=True)
    for ledger in LEDGER_NAMES:
        path = root / "v3" / "ledgers" / f"{ledger}.jsonl"
        if not path.exists():
            continue
        lexical = Path(os.path.abspath(path))
        resolved = lexical.resolve(strict=True)
        try: resolved.relative_to(ledger_root)
        except ValueError as exc:
            raise ValueError(f"LIFECYCLE_RECOVERY_SOURCE_INVALID:{path.name}") from exc
        if resolved != lexical or path.is_symlink() or not path.is_file():
            raise ValueError(f"LIFECYCLE_RECOVERY_SOURCE_INVALID:{path.name}")
        identity = _identity(path)
        if identity["size"]:
            with path.open("rb") as handle:
                handle.seek(identity["size"] - 1)
                if handle.read(1) != b"\n":
                    raise ValueError(f"TRUNCATED_JSONL_LINE:{path.name}")
        sources.append({"ledger": ledger, "path": str(path), "identity": identity,
                        "copy_offset": 0, "line_count": 0, "sha256": None})
    components = []
    for path in _components(index_dir):
        before = _identity(path); digest = _sha(path)
        if not _same(path, before):
            raise ValueError("LIFECYCLE_RECOVERY_INDEX_UNSTABLE")
        components.append({"name": path.name, "size": before["size"], "sha256": digest})
    if not any(row["name"] == "lifecycle_index.sqlite3" for row in components):
        raise ValueError("LIFECYCLE_RECOVERY_INDEX_MISSING")
    recovery_id = hashlib.sha256(json.dumps(
        {"trigger": trigger, "sources": [(x["ledger"], x["identity"]) for x in sources],
         "components": components}, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest()
    return {"schema": SCHEMA, "recovery_id": recovery_id, "trigger": trigger,
            "phase": "COPY", "sources": sources, "components": components,
            "rebuild_ledger": 0, "verify_ledger": 0}


def _copy_step(state: dict[str, Any], staging: Path) -> None:
    row = next((x for x in state["sources"] if x["sha256"] is None), None)
    if row is None:
        state["phase"] = "QUARANTINE"; return
    source = Path(row["path"]); expected = row["identity"]
    if not _same_append_source(source, expected):
        raise ValueError(f"LIFECYCLE_RECOVERY_SOURCE_UNSTABLE:{source.name}")
    target = staging / "sources" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    offset = int(row["copy_offset"]); consumed = 0
    if target.exists():
        if target.stat().st_size < offset:
            raise ValueError(f"LIFECYCLE_RECOVERY_STAGED_SOURCE_TAMPERED:{source.name}")
        if target.stat().st_size > offset:
            with target.open("r+b") as handle:
                handle.truncate(offset); handle.flush(); os.fsync(handle.fileno())
    mode = "ab" if offset else "wb"
    with source.open("rb") as src, target.open(mode) as dst:
        src.seek(offset)
        while consumed < COPY_BUDGET and offset + consumed < expected["size"]:
            remaining = expected["size"] - (offset + consumed)
            raw = src.readline(min(MAX_JSONL_RECORD_BYTES + 1, remaining))
            if not raw.endswith(b"\n"):
                raise ValueError(f"TRUNCATED_JSONL_LINE:{source.name}")
            if len(raw) > MAX_JSONL_RECORD_BYTES:
                raise ValueError(f"JSONL_RECORD_TOO_LARGE:{source.name}")
            try: value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"INVALID_JSONL_ROW:{source.name}:{offset + consumed}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"NON_OBJECT_JSONL_ROW:{source.name}:{offset + consumed}")
            dst.write(raw); consumed += len(raw); row["line_count"] += 1
        dst.flush(); os.fsync(dst.fileno())
    if not _same_append_source(source, expected):
        raise ValueError(f"LIFECYCLE_RECOVERY_SOURCE_UNSTABLE:{source.name}")
    row["copy_offset"] = offset + consumed
    if row["copy_offset"] == expected["size"]:
        row["sha256"] = _bounded_hash(target)
        if _sha_prefix(source, expected["size"]) != row["sha256"]:
            raise ValueError(f"LIFECYCLE_RECOVERY_SOURCE_PREFIX_CHANGED:{source.name}")


def _quarantine(state: dict[str, Any], index_dir: Path, staging: Path) -> None:
    quarantine = index_dir / "recovery-quarantine" / _directory_id(state)
    quarantine.mkdir(parents=True, exist_ok=True)
    for row in state["sources"]:
        source = Path(row["path"])
        staged = staging / "sources" / source.name
        if (
            not _same_append_source(source, row["identity"])
            or not staged.is_file()
            or staged.stat().st_size != row["identity"]["size"]
            or _bounded_hash(staged) != row["sha256"]
            or _sha_prefix(source, row["identity"]["size"]) != row["sha256"]
        ):
            raise ValueError(f"LIFECYCLE_RECOVERY_STAGED_SOURCE_TAMPERED:{source.name}")
    for row in state["components"]:
        quarantine.mkdir(parents=True, exist_ok=True)
        source = index_dir / row["name"]
        if not source.is_file() or source.stat().st_size != row["size"] or _sha(source) != row["sha256"]:
            raise ValueError("LIFECYCLE_RECOVERY_INDEX_TAMPERED")
        target = quarantine / row["name"]
        if target.exists():
            if target.stat().st_size != row["size"] or _sha(target) != row["sha256"]:
                raise ValueError("LIFECYCLE_RECOVERY_QUARANTINE_TAMPERED")
        else:
            temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                shutil.copyfile(source, temporary)
                with temporary.open("r+b") as handle: os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    receipt = {"schema": RECEIPT_SCHEMA, "status": "QUARANTINED",
               "recovery_id": state["recovery_id"], "trigger": state["trigger"],
               "components": state["components"],
               "sources": [{k: x[k] for k in ("ledger", "identity", "line_count", "sha256")}
                           for x in state["sources"]]}
    receipt["receipt_sha256"] = hashlib.sha256(json.dumps(
        receipt, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    _atomic_json(quarantine / "receipt.json", receipt)
    state["phase"] = "REBUILD"


def _verify_quarantine(state: dict[str, Any], index_dir: Path) -> None:
    quarantine = index_dir / "recovery-quarantine" / _directory_id(state)
    receipt_path = quarantine / "receipt.json"
    try: receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("LIFECYCLE_RECOVERY_QUARANTINE_TAMPERED") from exc
    claimed = receipt.pop("receipt_sha256", None)
    calculated = hashlib.sha256(json.dumps(
        receipt, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest()
    if claimed != calculated or receipt.get("recovery_id") != state["recovery_id"]:
        raise ValueError("LIFECYCLE_RECOVERY_QUARANTINE_TAMPERED")
    for row in state["components"]:
        path = quarantine / row["name"]
        if not path.is_file() or path.stat().st_size != row["size"] or _sha(path) != row["sha256"]:
            raise ValueError("LIFECYCLE_RECOVERY_QUARANTINE_TAMPERED")


def _rebuild_step(state: dict[str, Any], root: Path, staging: Path) -> None:
    database = staging / "lifecycle_index.rebuilt.sqlite3"
    connection = _open_incremental_index(root, database_path=database)
    try:
        for index in range(int(state["rebuild_ledger"]), len(state["sources"])):
            row = state["sources"][index]
            source = Path(row["path"])
            staged_source = staging / "sources" / source.name
            if (
                not _same_append_source(source, row["identity"])
                or _sha_prefix(source, row["identity"]["size"]) != row["sha256"]
            ):
                raise ValueError(f"LIFECYCLE_RECOVERY_SOURCE_UNSTABLE:{source.name}")
            receipt = _index_ledger_chunk(connection, staged_source, row["ledger"],
                                          max_bytes=REBUILD_BUDGET, max_rows=100_000)
            if receipt["caught_up"]:
                live = _identity(source)
                cursor = connection.execute(
                    "SELECT byte_offset FROM ledger_cursor WHERE ledger = ?", (row["ledger"],)
                ).fetchone()
                offset = int(cursor["byte_offset"]) if cursor is not None else 0
                if offset != int(row["identity"]["size"]):
                    raise ValueError(f"LIFECYCLE_RECOVERY_CURSOR_INVALID:{source.name}")
                with connection:
                    connection.execute(
                        """UPDATE ledger_cursor SET source_dev = ?, source_ino = ?,
                           source_anchor_sha256 = ?, source_mtime_ns = ? WHERE ledger = ?""",
                        (live["dev"], live["ino"], _source_anchor(source, offset),
                         live["mtime_ns"], row["ledger"]),
                    )
                state["rebuild_ledger"] = index + 1
            return
        state["phase"] = "VERIFY"; state["verify_ledger"] = 0
    finally:
        connection.close()


def _verify_step(state: dict[str, Any], staging: Path) -> None:
    index = int(state["verify_ledger"])
    if index >= len(state["sources"]):
        database = staging / "lifecycle_index.rebuilt.sqlite3"
        state["rebuilt_sha256"] = _bounded_hash(database)
        state["phase"] = "SWAP"; return
    row = state["sources"][index]
    source = Path(row["path"])
    staged = staging / "sources" / source.name
    if (
        not _same_append_source(source, row["identity"])
        or not staged.is_file()
        or _bounded_hash(staged) != row["sha256"]
        or _sha_prefix(source, row["identity"]["size"]) != row["sha256"]
    ):
        raise ValueError(f"LIFECYCLE_RECOVERY_SOURCE_UNSTABLE:{source.name}")
    state["verify_ledger"] = index + 1


def _swap(state: dict[str, Any], index_dir: Path, staging: Path) -> None:
    # This is the final publication fence. Earlier per-ledger verification may
    # span several worker invocations, so every captured prefix must still be
    # byte-identical immediately before the active derived index can move.
    for row in state["sources"]:
        source = Path(row["path"])
        if (
            not _same_append_source(source, row["identity"])
            or _sha_prefix(source, row["identity"]["size"]) != row["sha256"]
        ):
            raise ValueError(f"LIFECYCLE_RECOVERY_SOURCE_PREFIX_CHANGED:{source.name}")
    database = staging / "lifecycle_index.rebuilt.sqlite3"
    quarantine = index_dir / "recovery-quarantine" / _directory_id(state)
    active = index_dir / "lifecycle_index.sqlite3"
    retired = quarantine / "retired-active.sqlite3"
    expected = state.get("rebuilt_sha256")
    if not database.is_file():
        if active.is_file() and expected and _sha(active) == expected:
            state["phase"] = "COMPLETE"; state["active_sha256"] = expected
            _atomic_json(quarantine / "completion.json", state); return
        raise ValueError("LIFECYCLE_RECOVERY_REBUILD_MISSING")
    if not expected or _sha(database) != expected:
        raise ValueError("LIFECYCLE_RECOVERY_REBUILD_TAMPERED")
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{active}{suffix}")
        retired_sidecar = quarantine / f"retired-active.sqlite3{suffix}"
        if sidecar.exists() and not retired_sidecar.exists():
            os.replace(sidecar, retired_sidecar)
    if active.exists() and not retired.exists(): os.replace(active, retired)
    if not active.exists(): os.replace(database, active)
    if not active.is_file() or _sha(active) != expected:
        raise ValueError("LIFECYCLE_RECOVERY_SWAP_FAILED")
    state["phase"] = "COMPLETE"; state["active_sha256"] = _sha(active)
    _atomic_json(quarantine / "completion.json", state)


def _reset_binding(root, trigger, operation_path, expected_sha256, current_epoch_id):
    path = Path(operation_path)
    lexical = Path(os.path.abspath(path))
    if path.is_symlink() or lexical.resolve() != lexical or not path.is_file():
        raise ValueError("LIFECYCLE_RESET_RECEIPT_PATH_INVALID")
    try: lexical.relative_to(root / "research_reset_receipts")
    except ValueError as exc: raise ValueError("LIFECYCLE_RESET_RECEIPT_PATH_INVALID") from exc
    if path.stat().st_size > MAX_RESET_RECEIPT_BYTES:
        raise ValueError("LIFECYCLE_RESET_RECEIPT_TOO_LARGE")
    with path.open("rb") as handle:
        raw = handle.read(MAX_RESET_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RESET_RECEIPT_BYTES:
        raise ValueError("LIFECYCLE_RESET_RECEIPT_TOO_LARGE")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("LIFECYCLE_RESET_RECEIPT_HASH_MISMATCH")
    operation = _strict_reset_json(raw)
    _validate_reset_operation(root, trigger, operation, current_epoch_id)
    return {"operation_path": str(lexical), "operation_sha256": expected_sha256,
            "epoch_id": current_epoch_id, "source_revision": operation["proof"]["source_revision"]}


def _validate_reset_operation(root, trigger, operation, current_epoch_id):
    """Pure receipt comparisons; does not access or change the bound runtime."""
    proof = operation.get("proof") or {}
    from research_reset_inventory import _proof_valid
    deletion = (operation.get("deletion") or {}).get("deletion_receipt") or {}
    canonical = json.dumps(proof, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    ledger = trigger.removeprefix("SOURCE_LEDGER_TRUNCATED:").removesuffix(".jsonl")
    target = str(root / "v3" / "ledgers" / f"{ledger}.jsonl")
    if (not _proof_valid(root, proof)
            or not trigger.startswith("SOURCE_LEDGER_TRUNCATED:") or ledger not in LEDGER_NAMES
            or operation.get("schema") != "bot_destructive_research_reset_v1"
            or operation.get("stage") != "COMPLETE" or operation.get("accounting_preserved") is not True
            or proof.get("runtime_root") != str(root)
            or not current_epoch_id or operation.get("new_epoch_id") != current_epoch_id
            or proof.get("new_epoch_id") != current_epoch_id
            or proof.get("retired_epoch_id") in (None, "", current_epoch_id)
            or not proof.get("source_revision")
            or deletion.get("status") != "COMPLETE" or deletion.get("root") != str(root)
            or deletion.get("context", {}).get("proof_sha256") != hashlib.sha256(canonical.encode()).hexdigest()
            or target not in deletion.get("deleted", []) or target not in operation.get("deleted", [])):
        raise ValueError("LIFECYCLE_RESET_PROOF_INVALID")


def recover_reset_index(root, trigger, *, operation_path, operation_sha256, current_epoch_id):
    """Caller supplies a freshly authoritative epoch under its reset barrier.

    This only repairs a derived index; it never mutates source ledgers.
    """
    root = Path(root).resolve()
    # COMPLETE is a historical receipt, not a frozen hash of the mutable live
    # index. Normal pipeline validation remains responsible for its sources.
    state_path = root / "v3" / "lifecycle_bundle_index" / "recovery-state.json"
    if state_path.is_file():
        with _exclusive_index_lock(root):
            state = _load_state(state_path)
            if state.get("phase") == "COMPLETE" and state.get("completion_receipt_sha256"):
                binding = state.get("reset_proof") or {}
                if (state.get("schema") != SCHEMA or state.get("trigger") != trigger
                        or binding.get("operation_path") != str(Path(os.path.abspath(operation_path)))
                        or binding.get("operation_sha256") != operation_sha256
                        or binding.get("epoch_id") != current_epoch_id):
                    raise ValueError("LIFECYCLE_RESET_PROOF_INVALID")
                index_dir = state_path.parent
                completion = index_dir / "recovery-quarantine" / _directory_id(state) / "completion.json"
                if completion.stat().st_size > 1024 * 1024:
                    raise ValueError("LIFECYCLE_RECOVERY_COMPLETION_INVALID")
                with completion.open("rb") as handle:
                    raw = handle.read(1024 * 1024 + 1)
                if (len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != state["completion_receipt_sha256"]):
                    raise ValueError("LIFECYCLE_RECOVERY_COMPLETION_TAMPERED")
                receipt = _strict_reset_json(raw)
                if (receipt.get("recovery_id") != state.get("recovery_id")
                        or receipt.get("reset_proof") != binding or receipt.get("phase") != "COMPLETE"
                        or not (index_dir / "lifecycle_index.sqlite3").is_file()):
                    raise ValueError("LIFECYCLE_RECOVERY_COMPLETION_INVALID")
                return {"status": "COMPLETE", "complete": True, "recovery_id": state["recovery_id"]}
    binding = _reset_binding(root, trigger, operation_path, operation_sha256, current_epoch_id)
    return recover_rotated_index(root, trigger, _reset_proof=binding)


def recover_rotated_index(root: str | Path, trigger: str, *, _reset_proof=None) -> dict[str, Any]:
    root = Path(root).resolve(); index_dir = root / "v3" / "lifecycle_bundle_index"
    state_path = index_dir / "recovery-state.json"
    with _exclusive_index_lock(root):
        if state_path.is_file():
            state = _load_state(state_path)
        else:
            if _reset_proof is not None:
                import sqlite3
                ledger = trigger.split(":", 1)[1].removesuffix(".jsonl")
                database = index_dir / "lifecycle_index.sqlite3"
                connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                try:
                    cursor = connection.execute("SELECT byte_offset FROM ledger_cursor WHERE ledger=?", (ledger,)).fetchone()
                finally:
                    connection.close()
                if cursor is None or int(cursor[0]) <= (root / "v3" / "ledgers" / f"{ledger}.jsonl").stat().st_size:
                    raise ValueError("LIFECYCLE_RESET_CURSOR_MISMATCH_NOT_PROVEN")
            state = _initial(root, trigger, index_dir, reset_authorized=_reset_proof is not None)
            if _reset_proof is not None:
                state["reset_proof"] = _reset_proof
                state["recovery_id"] = hashlib.sha256(json.dumps(
                    [state["recovery_id"], _reset_proof], sort_keys=True).encode()).hexdigest()
        if state.get("reset_proof") != _reset_proof:
            raise ValueError("LIFECYCLE_RESET_CURRENT_PROOF_REQUIRED")
        if _reset_proof is not None:
            actual = _reset_binding(root, trigger, _reset_proof["operation_path"],
                                    _reset_proof["operation_sha256"], _reset_proof["epoch_id"])
            if actual != _reset_proof:
                raise ValueError("LIFECYCLE_RESET_PROOF_CHANGED")
        if state.get("schema") != SCHEMA or state.get("trigger") != trigger:
            raise ValueError("LIFECYCLE_RECOVERY_STATE_MISMATCH")
        staging = index_dir / "recovery-staging" / _directory_id(state)
        staging.mkdir(parents=True, exist_ok=True)
        phase = state["phase"]
        if phase == "COPY": _copy_step(state, staging)
        elif phase == "QUARANTINE": _quarantine(state, index_dir, staging)
        elif phase in {"REBUILD", "VERIFY", "SWAP", "COMPLETE"}:
            _verify_quarantine(state, index_dir)
            if phase == "REBUILD": _rebuild_step(state, root, staging)
            elif phase == "VERIFY": _verify_step(state, staging)
            elif phase == "SWAP": _swap(state, index_dir, staging)
        else: raise ValueError("LIFECYCLE_RECOVERY_PHASE_INVALID")
        if state["phase"] == "COMPLETE" and state.get("reset_proof"):
            completion = index_dir / "recovery-quarantine" / _directory_id(state) / "completion.json"
            state["completion_receipt_sha256"] = _sha(completion)
        _write_state(state_path, state)
        return {"status": state["phase"], "complete": state["phase"] == "COMPLETE",
                "recovery_id": state["recovery_id"]}


def resume_rotated_index_recovery(root: str | Path, *, current_epoch_id=None) -> dict[str, Any] | None:
    root = Path(root).resolve()
    state_path = root / "v3" / "lifecycle_bundle_index" / "recovery-state.json"
    if not state_path.is_file():
        return None
    state = _load_state(state_path)
    if state.get("reset_proof"):
        binding = state["reset_proof"]
        return recover_reset_index(root, str(state.get("trigger") or ""),
                                   operation_path=binding["operation_path"],
                                   operation_sha256=binding["operation_sha256"],
                                   current_epoch_id=current_epoch_id)
    return recover_rotated_index(root, str(state.get("trigger") or ""))


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False); parser.add_argument("--hash")
    args = parser.parse_args()
    if not args.hash: return 2
    print(_sha(Path(args.hash).resolve())); return 0


if __name__ == "__main__": raise SystemExit(main())
