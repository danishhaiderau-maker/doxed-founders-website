"""Durable, bounded admission receipts for the optional bundle producer.

This module never starts work.  The bot's existing coordinator lock remains the
single in-process owner and the bundle worker's existing OS lease remains the
cross-process owner.  Receipts describe attempts; they are not liveness proof.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import tempfile
import time


SCHEMA = "fly_transport_bundle_admission_v1"
IDENTITY_FIELDS = (
    "generation_id", "page_index_sha256", "source_git_rev",
    "collection_epoch_id", "tile_registry_signature",
)
OUTCOMES = frozenset({
    "STARTING", "STARTED", "COMPLETE", "DISABLED", "IDENTITY_REJECTED",
    "BOOTSTRAP_REJECTED", "SINGLETON_BUSY", "REGISTRY_HYDRATING",
    "MAINTENANCE_DEFERRED", "MAINTENANCE_FAILED", "THREAD_START_FAILED",
    "INTERRUPTED_BEFORE_PUBLICATION", "TERMINAL_FAILURE", "ATTEMPT_LIMIT",
    "CORRUPT_STATE",
})
TRANSIENT = frozenset({
    "REGISTRY_HYDRATING", "MAINTENANCE_DEFERRED", "THREAD_START_FAILED",
    "INTERRUPTED_BEFORE_PUBLICATION",
})
INCARNATION = re.compile(r"[0-9a-f]{32}")
HEX64 = re.compile(r"[0-9a-f]{64}")
OPAQUE_ID = re.compile(r"[A-Za-z0-9._:-]{1,160}")
MAX_ATTEMPTS = 4
MAX_BYTES = 16 * 1024
BACKOFF_SECONDS = (5, 15, 30, 60)


class AdmissionStateError(ValueError):
    """A fixed admission receipt is malformed or unsafe to access."""


def _valid_identity(identity: object) -> bool:
    if not isinstance(identity, dict) or set(identity) != set(IDENTITY_FIELDS):
        return False
    return bool(
        HEX64.fullmatch(str(identity["generation_id"]))
        and HEX64.fullmatch(str(identity["page_index_sha256"]))
        and OPAQUE_ID.fullmatch(str(identity["source_git_rev"]))
        and OPAQUE_ID.fullmatch(str(identity["collection_epoch_id"]))
        and OPAQUE_ID.fullmatch(str(identity["tile_registry_signature"]))
    )


def _safe_file(path: Path, *, missing_ok: bool = False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise AdmissionStateError("ADMISSION_STATE_MISSING")
    if path.is_symlink() or not path.is_file() or getattr(info, "st_nlink", 1) != 1:
        raise AdmissionStateError("ADMISSION_STATE_UNSAFE")
    return info


def _safe_parent(path: Path) -> Path:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise AdmissionStateError("ADMISSION_PARENT_UNSAFE")
    return parent


def _validated(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "identity", "process_incarnation", "outcome", "attempt_count",
        "next_retry_unix", "updated_unix",
    }:
        raise AdmissionStateError("ADMISSION_STATE_INVALID")
    if payload["schema"] != SCHEMA or not _valid_identity(payload["identity"]):
        raise AdmissionStateError("ADMISSION_STATE_INVALID")
    if not INCARNATION.fullmatch(str(payload["process_incarnation"])):
        raise AdmissionStateError("ADMISSION_STATE_INVALID")
    if payload["outcome"] not in OUTCOMES or type(payload["attempt_count"]) is not int:
        raise AdmissionStateError("ADMISSION_STATE_INVALID")
    if not 0 <= payload["attempt_count"] <= MAX_ATTEMPTS:
        raise AdmissionStateError("ADMISSION_STATE_INVALID")
    for key in ("next_retry_unix", "updated_unix"):
        if type(payload[key]) not in (int, float) or not math.isfinite(payload[key]) or payload[key] < 0:
            raise AdmissionStateError("ADMISSION_STATE_INVALID")
    return payload


class AdmissionStore:
    """One fixed, atomic current-generation receipt with capped restart retry."""

    def __init__(self, path, process_incarnation, *, clock=time.time):
        self.path = Path(path)
        if not INCARNATION.fullmatch(str(process_incarnation)):
            raise AdmissionStateError("ADMISSION_INCARNATION_INVALID")
        self.incarnation = str(process_incarnation)
        self.clock = clock

    def load(self):
        if _safe_file(self.path, missing_ok=True) is None:
            return None
        if self.path.stat().st_size > MAX_BYTES:
            raise AdmissionStateError("ADMISSION_STATE_INVALID")
        with self.path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if getattr(opened, "st_nlink", 1) != 1 or opened.st_size > MAX_BYTES:
                raise AdmissionStateError("ADMISSION_STATE_UNSAFE")
            raw = handle.read(MAX_BYTES + 1)
            final = os.fstat(handle.fileno())
        current = _safe_file(self.path)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (final.st_dev, final.st_ino, final.st_size):
            raise AdmissionStateError("ADMISSION_STATE_UNSTABLE")
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise AdmissionStateError("ADMISSION_STATE_UNSTABLE")
        try:
            return _validated(json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise AdmissionStateError("ADMISSION_STATE_INVALID") from exc

    def _write(self, payload):
        payload = _validated(payload)
        parent = _safe_parent(self.path)
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise AdmissionStateError("ADMISSION_STATE_INVALID")
        fd, name = tempfile.mkstemp(prefix=".bundle-admission-", suffix=".tmp", dir=parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            _safe_file(temporary)
            if self.path.exists() or self.path.is_symlink():
                _safe_file(self.path)
            os.replace(temporary, self.path)
            _safe_file(self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return dict(payload)

    def observe(self, identity):
        """Return sanitized historical state; never treat it as live ownership."""
        if not _valid_identity(identity):
            return {"outcome": "IDENTITY_REJECTED", "retryable": False}
        try:
            state = self.load()
        except AdmissionStateError:
            return {"outcome": "CORRUPT_STATE", "retryable": False}
        if state is None or state["identity"] != identity:
            return {"outcome": "MISSING", "retryable": True, "attempt_count": 0}
        return {
            "outcome": state["outcome"],
            "retryable": state["outcome"] in TRANSIENT,
            "attempt_count": state["attempt_count"],
            "next_retry_unix": state["next_retry_unix"],
            "same_process": state["process_incarnation"] == self.incarnation,
        }

    def begin(self, identity):
        """Persist STARTING before thread creation, carrying restart attempt caps."""
        if not _valid_identity(identity):
            return {"outcome": "IDENTITY_REJECTED", "started": False}
        try:
            state = self.load()
        except AdmissionStateError:
            return {"outcome": "CORRUPT_STATE", "started": False}
        now = float(self.clock())
        attempts = 0
        if state is not None and state["identity"] == identity:
            attempts = state["attempt_count"]
            if state["outcome"] not in TRANSIENT | {
                "STARTING", "STARTED", "DISABLED", "BOOTSTRAP_REJECTED",
            }:
                return {"outcome": state["outcome"], "started": False, "attempt_count": attempts}
            if state["outcome"] in TRANSIENT and now < state["next_retry_unix"]:
                return {"outcome": state["outcome"], "started": False,
                        "attempt_count": attempts, "next_retry_unix": state["next_retry_unix"]}
            if attempts >= MAX_ATTEMPTS:
                self.publish(identity, "ATTEMPT_LIMIT", attempt_count=attempts)
                return {"outcome": "ATTEMPT_LIMIT", "started": False, "attempt_count": attempts}
        attempts += 1
        receipt = self._write({
            "schema": SCHEMA, "identity": dict(identity),
            "process_incarnation": self.incarnation, "outcome": "STARTING",
            "attempt_count": attempts, "next_retry_unix": 0.0, "updated_unix": now,
        })
        return {"outcome": receipt["outcome"], "started": True, "attempt_count": attempts}

    def publish(self, identity, outcome, *, attempt_count=None):
        if outcome not in OUTCOMES or outcome == "SINGLETON_BUSY" or not _valid_identity(identity):
            raise AdmissionStateError("ADMISSION_OUTCOME_INVALID")
        previous = self.load()
        if previous is not None and previous["identity"] == identity:
            attempts = previous["attempt_count"]
        else:
            attempts = 0
        if attempt_count is not None:
            attempts = attempt_count
        now = float(self.clock())
        delay = BACKOFF_SECONDS[min(max(attempts - 1, 0), len(BACKOFF_SECONDS) - 1)] if outcome in TRANSIENT else 0
        return self._write({
            "schema": SCHEMA, "identity": dict(identity),
            "process_incarnation": self.incarnation, "outcome": outcome,
            "attempt_count": attempts, "next_retry_unix": now + delay,
            "updated_unix": now,
        })


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdmissionStateError("ADMISSION_STATE_INVALID")
        result[key] = value
    return result
