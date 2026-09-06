"""Isolated, bounded scheduling of derivative transport work, never source GC.

The owner supplies frozen inventory identity and O(1) pressure/retention probes.
HTTP handlers must not call this coordinator. Each child has a hard deadline;
the original manifest and ACK protocol remain authoritative.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

SCHEMA = "fly_transport_bundle_slice_v1"
MAX_REQUEST = 64 * 1024
MAX_RESULT = 16 * 1024
MIN_FREE_BYTES = 512 * 1024 * 1024
# Includes conservative TAR/descriptor overhead for the observed 34,433-file
# backlog; the independent 512MiB free-space reserve is checked before each slice.
MAX_GENERATION_BYTES = 256 * 1024 * 1024
IDENTITY = ("generation_id", "page_index_sha256", "source_git_rev",
            "collection_epoch_id", "tile_registry_signature")


def _identity(metadata):
    return {key: metadata.get(key) for key in IDENTITY}


def _reject_links(path):
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        if component.exists() or component.is_symlink():
            stat = component.lstat()
            if component.is_symlink() or int(getattr(stat, "st_file_attributes", 0) or 0) & 0x400:
                raise ValueError("BUNDLE_LINK_REJECTED")


def run_slice(metadata, source_root, output_root, *, timeout=12, runner=subprocess.run):
    """Hard-timeout subprocess, bounded protocol, no credential inheritance."""
    if not 1 <= timeout <= 30:
        raise ValueError("INVALID_SLICE_TIMEOUT")
    nonce = uuid.uuid4().hex
    payload = {"schema": SCHEMA, "nonce": nonce, "generation": metadata,
               "source_root": str(source_root), "output_root": str(output_root)}
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    if len(encoded) > MAX_REQUEST:
        raise ValueError("SLICE_REQUEST_LIMIT")
    environment = {key: value for key, value in os.environ.items() if key.upper() in
                   {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG"}}
    environment["PYTHONUTF8"] = "1"
    try:
        result = runner([sys.executable, str(Path(__file__).resolve()), "--slice"],
                        input=encoded, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        timeout=timeout, check=False, cwd=str(Path(__file__).resolve().parent),
                        env=environment)
    except subprocess.TimeoutExpired:
        return {"status": "FAILED", "error": "BUNDLE_SLICE_TIMEOUT"}
    if len(result.stdout) > MAX_RESULT:
        return {"status": "FAILED", "error": "BUNDLE_SLICE_RESULT_LIMIT"}
    try:
        receipt = json.loads(result.stdout)
        valid = (isinstance(receipt, dict) and receipt.get("schema") == SCHEMA
                 and receipt.get("nonce") == nonce
                 and receipt.get("identity") == _identity(metadata)
                 and receipt.get("status") in {"BUILDING", "COMPLETE", "FAILED"}
                 and result.returncode == (1 if receipt.get("status") == "FAILED" else 0))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        return {"status": "FAILED", "error": "BUNDLE_SLICE_RECEIPT_INVALID"}
    return receipt


COORDINATOR_STATUS_FILE = "bundle-coordinator-status.json"
COORDINATOR_EARLY_STATUS_FILE = "bundle-coordinator-early-status.json"
TRANSPORT_FAILURE_CODES = {
    "source generation differs from inventory": "BUNDLE_SOURCE_GENERATION_MISMATCH",
    "source generation changed while bundling": "BUNDLE_SOURCE_CHANGED_DURING_READ",
    "idempotency receipt is not valid JSON": "BUNDLE_RECEIPT_JSON_INVALID",
    "idempotency receipt is not COMMITTED": "BUNDLE_RECEIPT_NOT_COMMITTED",
    "signal snapshot exceeds hard limit": "BUNDLE_SIGNAL_SNAPSHOT_TOO_LARGE",
    "signal snapshot filename checksum mismatch": "BUNDLE_SIGNAL_SNAPSHOT_HASH_MISMATCH",
    "signal snapshot is not valid JSON": "BUNDLE_SIGNAL_SNAPSHOT_JSON_INVALID",
    "signal snapshot schema mismatch": "BUNDLE_SIGNAL_SNAPSHOT_SCHEMA_INVALID",
}


def _slice_failure_code(exc):
    # Preserve known validation causes without exposing arbitrary messages/paths.
    from data_sync_bundle_transport import BundleTransportError
    code = str(exc)
    if isinstance(exc, BundleTransportError) and code in TRANSPORT_FAILURE_CODES:
        return TRANSPORT_FAILURE_CODES[code]
    return code if re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", code) else "BUNDLE_SLICE_FAILED"


DERIVATIVE_ADMISSION_CODES = frozenset({
    "BUNDLE_DERIVATIVE_DIAGNOSTIC_INVALID", "BUNDLE_DERIVATIVE_DIAGNOSTIC_IDENTITY",
    "BUNDLE_DERIVATIVE_LINK_REJECTED", "BUNDLE_DERIVATIVE_TYPE_INVALID",
    "BUNDLE_DERIVATIVE_ENTRY_LIMIT", "BUNDLE_DERIVATIVE_DUPLICATE_JSON_KEY",
    "BUNDLE_DERIVATIVE_ORPHAN_ARTIFACT", "BUNDLE_DERIVATIVE_STATE_LIMIT",
    "BUNDLE_DERIVATIVE_STATE_CHANGED", "BUNDLE_DERIVATIVE_JSON_INVALID",
    "BUNDLE_DERIVATIVE_GENERATION_INVALID", "BUNDLE_DERIVATIVE_INDEX_LIMIT",
    "BUNDLE_DERIVATIVE_INDEX_INVALID", "BUNDLE_DERIVATIVE_INDEXED_ARTIFACT_MISSING",
    "BUNDLE_DERIVATIVE_ARTIFACT_SIZE_LIMIT", "BUNDLE_DERIVATIVE_ESTIMATE_UNDERSTATES_FILES",
    "BUNDLE_DERIVATIVE_ADMISSION_ARGUMENTS", "BUNDLE_DERIVATIVE_LEASE_LIMIT",
    "BUNDLE_DERIVATIVE_GENERATION_LIMIT", "BUNDLE_DERIVATIVE_TOTAL_BUDGET",
    "BUNDLE_DERIVATIVE_ADMISSION_UNAVAILABLE",
})

COORDINATOR_ERRORS = frozenset({
    "RESOURCE_PRESSURE", "GENERATION_AUTHORITY_UNAVAILABLE", "BUNDLE_ADMISSION_UNAVAILABLE",
    "BUNDLE_COORDINATOR_BUDGET", "BUNDLE_COORDINATOR_SLICE_LIMIT", "BUNDLE_NO_CURSOR_PROGRESS",
    "BUNDLE_CIRCUIT_OPEN", "BUNDLE_SLICE_TIMEOUT", "BUNDLE_SLICE_RESULT_LIMIT",
    "BUNDLE_SLICE_RECEIPT_INVALID", "BUNDLE_GENERATION_STORAGE_BUDGET",
    "BUNDLE_COORDINATOR_EXCEPTION", "BUNDLE_WORKER_FAILURE",
    "BUNDLE_SLICE_FAILED", "BUNDLE_STATE_LIMIT", "BUNDLE_INDEX_LIMIT",
    "INVOCATION_TIME_BUDGET_EXHAUSTED_BEFORE_BUILD", "PACKAGE_HARD_BUDGET_EXCEEDED",
    "PACKAGE_INDEX_LIMIT_EXCEEDED", "PACKAGE_DESCRIPTOR_TOO_LARGE",
    "PAGE_INDEX_CURSOR_OR_READ_BUDGET_INVALID", "INVENTORY_PAGE_ROW_CURSOR_INVALID",
}) | frozenset(TRANSPORT_FAILURE_CODES.values()) | DERIVATIVE_ADMISSION_CODES


def _persist_coordinator_status(metadata, source_root, output_root, receipt, *, started_at,
                                terminal=False, prior_cursor=None):
    """Best-effort atomic diagnostic only; never create generation directories.

    A saved timestamp is not proof of a live owner. A hard process kill may leave
    a nonterminal receipt; readers must retain that uncertainty.
    """
    temporary = None
    try:
        from data_sync_bundle_worker import _validate_output_root
        identity = _identity(metadata)
        generation_id = identity["generation_id"]
        if not isinstance(generation_id, str) or not re.fullmatch(r"[0-9a-f]{64}", generation_id):
            return False
        if any(not isinstance(v, str) or not 1 <= len(v) <= 256 for v in identity.values()):
            return False
        source = Path(source_root)
        _reject_links(source)
        output = _validate_output_root(source.resolve(strict=True), output_root)
        directory = output / f"g-{generation_id[:16]}"
        _reject_links(directory)
        state_path = directory / "bundle-worker-state.json"
        _reject_links(state_path)
        state = {}
        early = not state_path.exists()
        if not early:
            with state_path.open("rb") as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                return False
            state = json.loads(raw)
            generation = state.get("generation") or {}
            if generation.get("inventory_generation_id") != generation_id or any(
                generation.get(key) != identity[key] for key in IDENTITY if key != "generation_id"
            ):
                return False
        else:
            # One bounded diagnostic slot, not a generation directory or lease.
            # Admission can fail before the first worker invocation creates state.
            output.mkdir(parents=True, exist_ok=True)
            _reject_links(output)
            directory = output
        status = receipt.get("status")
        if status not in {"STARTING", "BUILDING", "COMPLETE", "FAILED", "DEFERRED", "STOPPED"}:
            status = "FAILED"
        cursor = receipt.get("cursor") or prior_cursor or state.get("cursor") or {}
        safe_cursor = {key: cursor[key] for key in ("page_index", "page_row_index", "index_offset")
                       if isinstance(cursor, dict) and type(cursor.get(key)) is int
                       and 0 <= cursor[key] <= 2**63 - 1}
        payload = {"schema": "fly_transport_bundle_coordinator_status_v1", "identity": identity,
                   "status": status, "terminal": terminal, "cursor": safe_cursor,
                   "started_at": started_at, "updated_at": datetime.now(timezone.utc).isoformat(),
                   "authority": "DIAGNOSTIC_ONLY_NO_ACK_OR_LIVENESS_AUTHORITY"}
        for key in ("error", "last_error"):
            if receipt.get(key) is not None:
                payload[key] = receipt[key] if receipt[key] in COORDINATOR_ERRORS else "BUNDLE_WORKER_FAILURE"
        for key in ("package_index_count", "inventory_rows_selected", "retry_seconds"):
            if type(receipt.get(key)) is int and 0 <= receipt[key] <= 2**63 - 1:
                payload[key] = receipt[key]
        payload["worker_state_present"] = not early
        if early and status == "COMPLETE":
            return False  # No state means no completion evidence.
        target = directory / (COORDINATOR_EARLY_STATUS_FILE if early else COORDINATOR_STATUS_FILE)
        _reject_links(target)
        temporary = directory / (".bundle-coordinator-status-" + uuid.uuid4().hex + ".tmp")
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        _reject_links(directory)
        _reject_links(target)
        os.replace(temporary, target)
        return True
    except Exception:
        return False  # Diagnostics never change transfer/ACK or scheduling outcome.
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def run_managed_generation(metadata, source_root, output_root, *, pressure_probe,
                           generation_available, stop_event=None, publish=lambda value: None,
                           slice_runner=run_slice, max_slices=512, max_seconds=1800):
    started_at = datetime.now(timezone.utc).isoformat()
    prior_cursor = None
    def persist(receipt, terminal=False):
        nonlocal prior_cursor
        if isinstance(receipt.get("cursor"), dict):
            prior_cursor = receipt["cursor"]
        _persist_coordinator_status(metadata, source_root, output_root, receipt,
            started_at=started_at, terminal=terminal, prior_cursor=prior_cursor)
    def observed(receipt):
        persist(receipt)
        publish(receipt)
    persist({"status": "STARTING"})
    try:
        result = _run_managed_generation(metadata, source_root, output_root,
            pressure_probe=pressure_probe, generation_available=generation_available,
            stop_event=stop_event, publish=observed, slice_runner=slice_runner,
            max_slices=max_slices, max_seconds=max_seconds)
    except BaseException:
        persist({"status": "FAILED", "error": "BUNDLE_COORDINATOR_EXCEPTION"}, terminal=True)
        raise
    persist(result, terminal=True)
    return result


def _run_managed_generation(metadata, source_root, output_root, *, pressure_probe,
                           generation_available, stop_event=None, publish=lambda value: None,
                           slice_runner=run_slice, max_slices=512, max_seconds=1800):
    """Run serial slices with pressure deferral; never admit a second owner here.

    The caller owns a singleton coordinator. Worker additionally holds its OS
    lease. Partial packages remain optional usable acceleration, never full ACK.
    """
    stop = stop_event or threading.Event()
    start = time.monotonic()
    failures = 0
    pressure_wait = 3
    prior_cursor = None
    stagnant = 0
    if not 1 <= max_slices <= 512 or not 1 <= max_seconds <= 1800:
        raise ValueError("INVALID_COORDINATOR_LIMIT")
    for _ in range(max_slices):
        if stop.is_set():
            return {"status": "STOPPED"}
        if time.monotonic() - start >= max_seconds:
            return {"status": "DEFERRED", "error": "BUNDLE_COORDINATOR_BUDGET"}
        try:
            available = generation_available(metadata)
            pressure = pressure_probe()
            if available is not True:
                return {"status": "DEFERRED", "error": "GENERATION_AUTHORITY_UNAVAILABLE"}
            if (not isinstance(pressure, dict) or type(pressure.get("pressure")) is not bool
                    or type(pressure.get("emergency")) is not bool):
                raise ValueError("PRESSURE_PROBE_INVALID")
            free = shutil.disk_usage(source_root).free
        except Exception:
            return {"status": "DEFERRED", "error": "BUNDLE_ADMISSION_UNAVAILABLE"}
        if pressure["pressure"] or pressure["emergency"] or pressure.get("overlap") or free < MIN_FREE_BYTES:
            publish({"status": "DEFERRED", "error": "RESOURCE_PRESSURE", "retry_seconds": pressure_wait})
            stop.wait(min(pressure_wait, max(0, max_seconds - (time.monotonic() - start))))
            pressure_wait = min(30, pressure_wait * 2)
            continue
        pressure_wait = 3
        receipt = slice_runner(metadata, source_root, output_root)
        publish(receipt)
        if receipt.get("status") == "COMPLETE":
            return receipt
        if receipt.get("status") != "BUILDING":
            failures += 1
            if failures >= 2:
                return {"status": "FAILED", "error": "BUNDLE_CIRCUIT_OPEN", "last_error": receipt.get("error")}
            stop.wait(3)
            continue
        failures = 0
        cursor = receipt.get("cursor")
        stagnant = stagnant + 1 if cursor == prior_cursor else 0
        prior_cursor = cursor
        if stagnant >= 2:
            return {"status": "DEFERRED", "error": "BUNDLE_NO_CURSOR_PROGRESS"}
        stop.wait(1)  # Yield between fsync/hash slices; never spin on success.
    return {"status": "DEFERRED", "error": "BUNDLE_COORDINATOR_SLICE_LIMIT"}


def _child():
    # This program emits only a small controlled receipt, never worker members.
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    payload = json.loads(raw) if len(raw) <= MAX_REQUEST else {}
    if (payload.get("schema") != SCHEMA
            or not re.fullmatch(r"[0-9a-f]{32}", str(payload.get("nonce") or ""))):
        raise ValueError("INVALID_SLICE_REQUEST")
    metadata = payload["generation"]
    receipt = {"schema": SCHEMA, "nonce": payload["nonce"], "identity": _identity(metadata)}
    try:
        from data_sync_bundle_worker import run_bundle_worker, _bounded_read, _validate_output_root
        from data_sync_bundle_transport import MAX_PACKAGE_BYTES
        from data_sync_bundle_storage import check_derivative_admission
        source = Path(payload["source_root"])
        _reject_links(source)
        output = _validate_output_root(source.resolve(strict=True), payload["output_root"])
        # Budget derivative bytes from bounded owned state, without walking the volume.
        state = output / f"g-{metadata['generation_id'][:16]}" / "bundle-worker-state.json"
        _reject_links(output)
        if state.exists():
            _reject_links(state)
            previous = json.loads(_bounded_read(state, 2 * 1024 * 1024, "BUNDLE_STATE_LIMIT"))
            entries = previous.get("package_index")
            if not isinstance(entries, list) or len(entries) > 4096:
                raise ValueError("BUNDLE_INDEX_LIMIT")
            estimated = sum(int(entry["payload_bytes"]) + int(entry["member_count"]) * 4096
                            + 32 * 1024 for entry in entries)
            if estimated + MAX_PACKAGE_BYTES >= MAX_GENERATION_BYTES:
                raise ValueError("BUNDLE_GENERATION_STORAGE_BUDGET")
        check_derivative_admission(output, metadata["generation_id"], MAX_PACKAGE_BYTES)
        result = run_bundle_worker(metadata, payload["source_root"], output,
                                   max_pages=2, max_members=128, max_payload_bytes=8 * 1024 * 1024,
                                   max_read_bytes=32 * 1024 * 1024, max_elapsed_sec=5)
        receipt.update({key: result.get(key) for key in
                        ("status", "cursor", "package_index_count", "inventory_rows_selected")})
    except Exception as exc:
        receipt.update(status="FAILED", error=_slice_failure_code(exc))
    sys.stdout.write(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 1 if receipt["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(_child())
