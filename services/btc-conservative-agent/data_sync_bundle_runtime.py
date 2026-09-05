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


def run_managed_generation(metadata, source_root, output_root, *, pressure_probe,
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
        code = str(exc)
        receipt.update(status="FAILED", error=code if re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", code)
                       else "BUNDLE_SLICE_FAILED")
    sys.stdout.write(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 1 if receipt["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(_child())
