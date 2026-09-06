"""Bounded coordinator-only retirement admission; not wired into production.

Caller owns the coordinator lock. protection_boundary(candidate_id) must be a
nonblocking candidate reservation, yielding a frozen snapshot:
{current_identity: exact current identity, protected_generations: full IDs}.
The set includes every retained, served/ACK-pinned, and coordinator generation.
The boundary must prevent candidate re-admission/publication until exit. It
must RELEASE any inventory mutex before yielding: existing HTTP code takes
worker lease then inventory mutex. Holding that mutex here would invert order.
It must not acquire inventory locks from callbacks under worker lease either.
Unrelated read admission need not be blocked. This module never drops raw data.
"""
import hashlib
import json
import os
from pathlib import Path
import time

from data_sync_bundle_download_pins import DownloadProtection, _directory, _safe
from data_sync_bundle_retirement import retire_derivative_generation, _stable_read, _seal
from data_sync_bundle_storage import check_derivative_admission, _entries, _generation_usage, GEN, HEX
from data_sync_bundle_transport import MAX_PACKAGE_BYTES
from data_sync_bundle_worker import _atomic_json, _validate_output_root

FIELDS = {"inventory_generation_id", "source_git_rev", "collection_epoch_id", "tile_registry_signature"}
SCHEMA = "bundle_capacity_maintenance_v1"


def require(value, reason):
    if not value:
        raise ValueError("BUNDLE_MAINTENANCE_" + reason)


def _read_operation(path):
    raw = _stable_read(path, 65536)
    result = json.loads(raw)
    require(result.get("schema") == SCHEMA and result.get("receipt_sha256") == _seal(result)["receipt_sha256"],
            "INTENT_INVALID")
    require(all(isinstance(result.get(key), str) and HEX.fullmatch(result[key])
                for key in ("candidate", "state_sha256", "fence_token", "target_generation"))
            and type(result.get("complete")) is bool, "INTENT_INVALID")
    return result


def maintain_capacity(*, source_root, output_root, pin_root, receipt_root,
                      current_identity, target_generation, protection_boundary,
                      timeout_seconds=120, clock=time.monotonic):
    """At most one generation retired per call, only for cap-blocked admission.

    Existing intent recovery is allowed even after capacity has been freed.
    All ambiguous evidence raises/defer; caller must not treat it as admission.
    The returned ADMITTED means storage admission only, never inventory ACK.
    """
    require(isinstance(current_identity, dict) and set(current_identity) == FIELDS
            and all(isinstance(v, str) and v for v in current_identity.values()), "IDENTITY_INVALID")
    current = current_identity["inventory_generation_id"]
    require(HEX.fullmatch(current) and isinstance(target_generation, str) and HEX.fullmatch(target_generation), "IDENTITY_INVALID")
    require(callable(protection_boundary) and type(timeout_seconds) in (int, float)
            and 0 < timeout_seconds <= 120, "ARGUMENTS_INVALID")
    source = _directory(source_root)
    output = _validate_output_root(source, output_root)
    pins = _directory(pin_root)
    receipts = _directory(receipt_root)
    for protected_root in (source, output, pins):
        require(receipts != protected_root and protected_root not in receipts.parents
                and receipts not in protected_root.parents, "RECEIPT_ROOT_NOT_SEPARATE")
    owner = DownloadProtection(pins, output / ".bundle-worker.lease")
    started = clock()
    def remaining():
        left = timeout_seconds - (clock() - started)
        require(0 < left <= timeout_seconds, "DEADLINE_OR_CLOCK_INVALID")
        return left
    active = receipts / "active-maintenance.json"
    pending = _read_operation(active) if active.exists() or active.is_symlink() else None
    if pending is not None and pending.get("complete") is True:
        pending = None
    if pending is not None:
        require(pending["current_identity"] == current_identity and pending["target_generation"] == target_generation
                and pending["source_root"] == str(source) and pending["output_root"] == str(output)
                and pending["pin_root"] == str(pins), "INTENT_IDENTITY_CHANGED")
        # A deferred reader admission may have left an intent but no fence or
        # retirement. Such an unfenced intent is safely abandonable, so one
        # busy generation cannot starve later idle candidates on every pass.
        with owner._locked():
            state = owner._load(pending["candidate"])
            now = owner._now(state)
            if state["fence"] is None and any(expiry > now for expiry in state["sessions"].values()):
                require(not (receipts / ("r-" + pending["fence_token"] + ".json")).exists(), "UNFENCED_RETIREMENT_RECEIPT")
                _atomic_json(active, _seal({**pending, "complete": True, "abandoned_unfenced": True}))
                pending = None
    if pending is None:
        try:
            return check_derivative_admission(output, target_generation, MAX_PACKAGE_BYTES)
        except ValueError as exc:
            if str(exc) != "BUNDLE_DERIVATIVE_GENERATION_LIMIT":
                raise
        # Only after cap-blocked admission: reclaim expired unfenced metadata
        # so it cannot starve the next candidate fence. No artifact authority.
        owner.reclaim_expired_unfenced()
        # Validate bounded ownership before candidate selection. No volume walk.
        candidates = []
        for path in sorted(_entries(output, 6)):
            remaining()
            if GEN.fullmatch(path.name):
                _, generation = _generation_usage(path, target_generation)
                if generation not in {current, target_generation}:
                    raw = _stable_read(path / "bundle-worker-state.json", 2 * 1024 * 1024)
                    candidates.append((generation, hashlib.sha256(raw).hexdigest()))
    else:
        candidates = [(pending["candidate"], pending["state_sha256"])]
    for candidate, state_sha in candidates:
        remaining()
        require(HEX.fullmatch(candidate) and HEX.fullmatch(state_sha) and candidate not in {current, target_generation}, "CANDIDATE_INVALID")
        with protection_boundary(candidate) as snapshot:
            require(isinstance(snapshot, dict) and snapshot.get("current_identity") == current_identity,
                    "PROTECTION_IDENTITY_CHANGED")
            raw_ids = snapshot.get("protected_generations")
            require(isinstance(raw_ids, (set, frozenset, tuple, list)) and len(raw_ids) <= 1024
                    and all(isinstance(v, str) and HEX.fullmatch(v) for v in raw_ids), "PROTECTION_UNAVAILABLE")
            protected = frozenset(raw_ids) | {current, target_generation}
            if candidate in protected:
                if pending:
                    return {"status": "DEFERRED", "reason": "GENERATION_PROTECTED"}
                continue
            if pending is None:
                with owner._locked():
                    pin_state = owner._load(candidate)
                    now = owner._now(pin_state)
                    if any(expiry > now for expiry in pin_state["sessions"].values()):
                        continue
                intent = {"schema": SCHEMA, "current_identity": current_identity,
                          "target_generation": target_generation, "candidate": candidate,
                          "state_sha256": state_sha, "source_root": str(source), "output_root": str(output),
                          "pin_root": str(pins), "complete": False}
                intent["fence_token"] = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
                _atomic_json(active, _seal(intent))
                pending = _read_operation(active)
            token = pending["fence_token"]
            fenced = owner.fence_if_idle_unprotected(candidate, fence_token=token,
                                                     protected_generations=lambda: protected)
            if not fenced["ready"]:
                # If a reader won the race between the precheck and atomic
                # fence admission, abandon only a still-unfenced/no-receipt
                # intent and continue the bounded candidate list.
                with owner._locked():
                    pin_state = owner._load(candidate)
                    require(pin_state["fence"] is None
                            and not (receipts / ("r-" + token + ".json")).exists(), "DEFERRED_FENCE_NOT_ABANDONABLE")
                    _atomic_json(active, _seal({**pending, "complete": True, "abandoned_unfenced": True}))
                pending = None
                continue
            # Short filename for Windows; full identities stay bound in receipt.
            receipt_path = receipts / ("r-" + token + ".json")
            result = retire_derivative_generation(source, output, candidate,
                current_generation=current, expected_state_sha256=state_sha,
                protected_generations=lambda: protected, receipt_path=receipt_path,
                timeout_seconds=remaining())
            require(result.get("status") == "COMPLETE" and result.get("raw_source_deleted") is False,
                    "RETIREMENT_INCOMPLETE")
            # Proof-bound finalization, never age-based fence expiry. Retention
            # reservation remains held; no reader can be admitted in this gap.
            with owner._locked():
                remaining()
                receipt = json.loads(_stable_read(receipt_path, 4 * 1024 * 1024))
                require(receipt.get("receipt_sha256") == _seal(receipt)["receipt_sha256"]
                        and receipt.get("status") == "COMPLETE" and receipt.get("generation_id") == candidate
                        and receipt.get("state_sha256") == state_sha and receipt.get("raw_source_deleted") is False,
                        "FINALIZATION_RECEIPT_INVALID")
                derivative = output / ("g-" + candidate[:16])
                require(not derivative.exists() and not derivative.is_symlink(), "DERIVATIVE_STILL_PRESENT")
                pin_path = pins / (candidate + ".json")
                state = owner._load(candidate)
                now = owner._now(state)
                require(state["fence"] is not None and state["fence"]["token"] == token
                        and not any(expiry > now for expiry in state["sessions"].values()), "FINALIZATION_PIN_INVALID")
                _directory(pins)
                _safe(pin_path)
                pin_path.unlink()
                if os.name != "nt":
                    fd = os.open(pins, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                _atomic_json(active, _seal({**pending, "complete": True}))
            admission = check_derivative_admission(output, target_generation, MAX_PACKAGE_BYTES)
            return {**admission, "retired_generation": candidate, "cleanup_performed": True,
                    "raw_source_deleted": False}
    return {"status": "DEFERRED", "reason": "ALL_GENERATIONS_PROTECTED"}
