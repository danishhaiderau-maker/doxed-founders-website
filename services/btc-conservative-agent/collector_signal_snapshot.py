"""Bounded immutable first-collector snapshots, separate from mutable recovery JSON.

This preserves available evidence; it does not certify that a delayed first
capture was available at signal time or provide missing forward candles.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
SCHEMA = "collector_signal_snapshot_v1"
REF_SCHEMA = "collector_signal_snapshot_ref_v1"
FIELDS = (
    "pre_signal_context", "feature_snapshot_at_signal", "decision_tree_snapshot",
    "rsi_at_signal", "would_block", "would_block_reason", "atr14_pct",
)


def _directory(data_dir):
    root = Path(data_dir).resolve()
    directory = root / "v3" / "signal_snapshots_v1"
    for part in (root / "v3", directory):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("SIGNAL_SNAPSHOT_LINK_REFUSED")
    return directory


def _identity(event_id, epoch_id, signal_ts):
    if any(not isinstance(value, str) or not value.strip() or value != value.strip()
           for value in (event_id, epoch_id)) or isinstance(signal_ts, bool):
        raise ValueError("SIGNAL_SNAPSHOT_IDENTITY_INVALID")
    timestamp = float(signal_ts)
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError("SIGNAL_SNAPSHOT_IDENTITY_INVALID")
    return {"event_id": event_id, "epoch_id": epoch_id, "signal_ts": timestamp}


def _valid_timestamp(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value > 0 and datetime.fromtimestamp(value, timezone.utc) is not None
    except (OverflowError, OSError, ValueError):
        return False


def validate_signal_snapshot_content(snapshot, *, identity):
    """Validate capture semantics independently of transport/storage hashes."""
    if (not isinstance(snapshot, dict) or snapshot.get("schema") != SCHEMA
            or not isinstance(identity, dict)
            or set(identity) != {"event_id", "epoch_id", "signal_ts"}
            or not isinstance(snapshot.get("identity"), dict)
            or snapshot["identity"] != identity
            or not _valid_timestamp(snapshot["identity"].get("signal_ts"))
            or not isinstance(snapshot.get("evidence"), dict)
            or set(snapshot["evidence"]) != set(FIELDS)
            or snapshot.get("capture_basis") != "FIRST_COLLECTOR_CAPTURE"
            or snapshot.get("availability_at_signal_verified") is not False):
        raise ValueError("SIGNAL_SNAPSHOT_CONTENT_INVALID")
    _identity(**identity)
    if (not _valid_timestamp(snapshot.get("captured_at"))
            or snapshot["captured_at"] < identity["signal_ts"]):
        raise ValueError("SIGNAL_SNAPSHOT_CAPTURE_TIME_INVALID")
    return snapshot


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("SIGNAL_SNAPSHOT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("SIGNAL_SNAPSHOT_NONFINITE_JSON")
    return number


def _reject_constant(value):
    raise ValueError("SIGNAL_SNAPSHOT_NONFINITE_JSON")


def decode_signal_snapshot(payload, *, identity):
    """Shared strict bounded JSON decoder for collector, bundle and readers."""
    if not isinstance(payload, (bytes, bytearray)) or not 0 < len(payload) <= MAX_SNAPSHOT_BYTES:
        raise ValueError("SIGNAL_SNAPSHOT_SIZE_LIMIT")
    snapshot = json.loads(payload, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant, parse_float=_finite_float)
    return validate_signal_snapshot_content(snapshot, identity=identity)


def load_signal_snapshot(ref, *, data_dir, event_id, epoch_id, signal_ts):
    identity = _identity(event_id, epoch_id, signal_ts)
    if (not isinstance(ref, dict) or ref.get("schema") != REF_SCHEMA
            or ref.get("identity") != identity
            or not re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256") or ""))
            or ref.get("relative_path") != f"v3/signal_snapshots_v1/{ref.get('sha256')}.json"
            or type(ref.get("bytes")) is not int
            or not 0 < ref["bytes"] <= MAX_SNAPSHOT_BYTES):
        raise ValueError("SIGNAL_SNAPSHOT_REFERENCE_INVALID")
    path = _directory(data_dir) / (ref["sha256"] + ".json")
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("SIGNAL_SNAPSHOT_LINK_REFUSED")
    with path.open("rb") as handle:
        payload = handle.read(MAX_SNAPSHOT_BYTES + 1)
    if len(payload) != ref["bytes"] or hashlib.sha256(payload).hexdigest() != ref["sha256"]:
        raise ValueError("SIGNAL_SNAPSHOT_HASH_MISMATCH")
    return decode_signal_snapshot(payload, identity=identity)


def freeze_signal_snapshot(record, *, data_dir, captured_at):
    """Persist first available context, or verify/reuse the existing reference."""
    identity = _identity(record["event_id"], record["epoch_id"], record["envelope"]["signal_ts"])
    existing = record.get("research_signal_snapshot_ref")
    if existing is not None:
        load_signal_snapshot(existing, data_dir=data_dir, **identity)
        return dict(existing)
    if not _valid_timestamp(captured_at):
        raise ValueError("SIGNAL_SNAPSHOT_CAPTURE_TIME_INVALID")
    capture = float(captured_at)
    material = {
        "schema": SCHEMA, "identity": identity, "captured_at": capture,
        "capture_basis": "FIRST_COLLECTOR_CAPTURE",
        "availability_at_signal_verified": False,
        "evidence": {field: record.get(field) for field in FIELDS},
    }
    validate_signal_snapshot_content(material, identity=identity)
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    payload = bytearray()
    for fragment in encoder.iterencode(material):
        encoded = fragment.encode("utf-8")
        if len(payload) + len(encoded) > MAX_SNAPSHOT_BYTES:
            raise ValueError("SIGNAL_SNAPSHOT_SIZE_LIMIT")
        payload.extend(encoded)
    digest = hashlib.sha256(payload).hexdigest()
    ref = {"schema": REF_SCHEMA, "identity": identity, "sha256": digest, "bytes": len(payload),
           "relative_path": f"v3/signal_snapshots_v1/{digest}.json"}
    directory = _directory(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (digest + ".json")
    candidate = None
    try:
        fd, candidate = tempfile.mkstemp(prefix=".snapshot-", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(candidate, target)  # atomic create, never replace another snapshot
        except FileExistsError:
            pass
        try:
            directory_fd = os.open(str(directory), os.O_RDONLY)
        except OSError:  # Windows directory handles are not opened this way.
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        load_signal_snapshot(ref, data_dir=data_dir, **identity)
        return ref
    finally:
        if candidate is not None:
            os.unlink(candidate)
