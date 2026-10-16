"""Fresh-start epoch identity + laptop/Fly sync verdict for the research segment channel (pure).

HM (b), 16 Oct 2026. The 13 Oct wipe reset Fly's segment numbering (published 2,456) while the laptop
kept applied=7073 and Fly acked=0; the puller asked for seq 7074, got nothing and exited 0. Nothing in
the manifest or keys said "a wipe happened", and nothing compared the counters. This module provides:

* ``fresh_start_epoch_id``: ``fse-<epoch_ts>`` read from ``fresh_start_epoch.json`` (bot.py's file).
* ``resolve_prefix`` / ``resolve_state_dir``: optional per-wipe key prefix (``v3-w<epoch_ts>``), so a wipe
  can never reuse sequence numbers or object names of an archived epoch. Opt-in via
  ``RESEARCH_SEGMENTS_PREFIX_PER_WIPE=1``; default off, so deploying this changes no key.
* ``sync_verdict``: RED on published_seq < applied_seq, on Fly ack 0 while laptop applied > 0, on an
  epoch/prefix mismatch and on a pending "laptop reset required" marker; AMBER on ack lag > 5 min.
* ``validate_beacon``: the small laptop status document POSTed to ``.../laptop-status`` each pull.
* the "laptop reset required" marker written by the wipe (see ``clean_epoch_wipe.execute``).
"""
from __future__ import annotations

import calendar
import json
import os
import re
import time
from pathlib import Path

import research_segment_format as fmt

FRESH_START_FILE = "fresh_start_epoch.json"
BEACON_SCHEMA = "laptop_sync_beacon_v1"
VERDICT_SCHEMA = "research_sync_verdict_v1"
RESET_MARKER_SCHEMA = "laptop_reset_required_v1"
RESET_MARKER_NAME = "laptop-reset-required.json"
ACK_LAG_AMBER_SEC = 5 * 60
ACK_LAG_RED_SEC = 15 * 60
BEACON_STALE_SEC = 30 * 60
MAX_BEACON_BYTES = 4096

_BEACON_INT_FIELDS = ("applied_seq", "pull_exit", "cycle_exit")
_BEACON_STR_FIELDS = {  # field -> max length
    "applied_prefix": 64, "collection_epoch_id": 128, "fresh_start_epoch_id": 64,
    "pull_error": 200, "cycle_stop_reason": 120, "cycle_finished_at": 40, "applied_at": 40,
    "puller_version": 64,
}


def fresh_start_epoch(data_dir) -> dict | None:
    try:
        doc = json.loads((Path(data_dir) / FRESH_START_FILE).read_text("utf-8"))
        return doc if isinstance(doc, dict) and float(doc.get("epoch_ts") or 0) > 0 else None
    except (OSError, ValueError, TypeError):
        return None


def fresh_start_epoch_id(data_dir) -> str:
    doc = fresh_start_epoch(data_dir)
    return f"fse-{int(float(doc['epoch_ts']))}" if doc else ""


def per_wipe_enabled(env) -> bool:
    return str(env.get("RESEARCH_SEGMENTS_PREFIX_PER_WIPE") or "0").strip() == "1"


def _wipe_suffix(epoch_id: str) -> str:
    return "w" + epoch_id.removeprefix("fse-") if epoch_id else ""


def resolve_prefix(base: str, epoch_id: str, *, per_wipe: bool) -> str:
    base = fmt.validate_prefix((base or "v1").strip())
    if per_wipe and epoch_id:
        return fmt.validate_prefix(f"{base}-{_wipe_suffix(epoch_id)}")
    return base


def resolve_state_dir(base_dir, epoch_id: str, *, per_wipe: bool) -> Path:
    base_dir = Path(base_dir)
    if per_wipe and epoch_id:
        return base_dir.with_name(f"{base_dir.name}-{_wipe_suffix(epoch_id)}")
    return base_dir


# --------------------------------------------------------------------------- beacon
def validate_beacon(raw: bytes) -> tuple[dict | None, str | None]:
    """Parse + whitelist the laptop's status beacon. Returns (clean_doc, error)."""
    if len(raw) > MAX_BEACON_BYTES:
        return None, "BEACON_TOO_LARGE"
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "BEACON_NOT_JSON"
    if not isinstance(doc, dict) or doc.get("schema") != BEACON_SCHEMA:
        return None, "BEACON_SCHEMA"
    clean: dict = {"schema": BEACON_SCHEMA}
    for key in _BEACON_INT_FIELDS:
        value = doc.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < -1_000_000 or value > 10**12:
            return None, f"BEACON_BAD_{key.upper()}"
        clean[key] = value
    if "applied_seq" not in clean or clean["applied_seq"] < 0:
        return None, "BEACON_APPLIED_SEQ_REQUIRED"
    for key, limit in _BEACON_STR_FIELDS.items():
        value = doc.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return None, f"BEACON_BAD_{key.upper()}"
        clean[key] = value[:limit]
    return clean, None


def write_beacon_doc(path, doc: dict) -> None:
    """Atomic write of the stored beacon (the segment server itself never replaces/renames files)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True), "utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- marker
def write_reset_marker(directory, *, epoch_id: str, plan_sha256: str, scope: str, now: float | None = None) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    doc = {"schema": RESET_MARKER_SCHEMA, "status": "REQUIRED", "scope": scope, "wipe_epoch_id": epoch_id,
           "plan_sha256": plan_sha256, "created_at": now,
           "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
           "instruction": "Laptop puller/mirror/acks must be reset to the new prefix before pulling again; "
                          "sync_verdict stays RED until the beacon reports the new fresh_start_epoch_id."}
    path = directory / RESET_MARKER_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True), "utf-8")
    os.replace(tmp, path)
    return path


def read_reset_marker(directory) -> dict | None:
    try:
        doc = json.loads((Path(directory) / RESET_MARKER_NAME).read_text("utf-8"))
        return doc if isinstance(doc, dict) and doc.get("schema") == RESET_MARKER_SCHEMA else None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- verdict
def _iso_to_ts(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return float(calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return None


def sync_verdict(*, published_seq, acked_seq, ack_received_at, beacon: dict | None, beacon_received_at,
                 prefix: str, fresh_start_epoch_id: str, prefix_epoch_id: str = "", reset_marker: dict | None = None,
                 now: float) -> dict:
    """GREEN / AMBER / RED / UNKNOWN verdict for the Fly -> laptop channel. Pure."""
    published = int(published_seq or 0)
    acked = int(acked_seq or 0)
    red: list[str] = []
    amber: list[str] = []
    applied = None
    if beacon:
        applied = int(beacon.get("applied_seq") or 0)
        age = None if beacon_received_at is None else now - float(beacon_received_at)
        if age is not None and age > BEACON_STALE_SEC:
            amber.append(f"BEACON_STALE: laptop status {int(age / 60)} min old")
        if applied > published:
            red.append(f"SEQ_REGRESSION: laptop applied {applied} > Fly published {published}")
        if acked == 0 and applied > 0:
            red.append(f"ACK_ZERO_WHILE_APPLIED: Fly acked 0, laptop applied {applied}")
        elif 0 < acked < applied:
            ack_ts = _iso_to_ts(ack_received_at)
            lag = None if ack_ts is None else now - ack_ts
            if lag is not None and lag > ACK_LAG_RED_SEC:
                red.append(f"ACK_LAG: acked {acked} < applied {applied} for {int(lag / 60)} min")
            elif lag is not None and lag > ACK_LAG_AMBER_SEC:
                amber.append(f"ACK_LAG: acked {acked} < applied {applied} for {int(lag / 60)} min")
        ap = beacon.get("applied_prefix")
        if ap and ap != prefix:
            red.append(f"PREFIX_MISMATCH: laptop applied prefix {ap!r} != Fly prefix {prefix!r}")
        le = beacon.get("fresh_start_epoch_id")
        if fresh_start_epoch_id and le != fresh_start_epoch_id:
            red.append(f"EPOCH_MISMATCH: laptop epoch {le or 'none'} != Fly {fresh_start_epoch_id}")
        if beacon.get("pull_exit") not in (None, 0):
            amber.append(f"PULL_EXIT_{beacon.get('pull_exit')}: {beacon.get('pull_error') or 'no detail'}")
        if beacon.get("cycle_exit") not in (None, 0):
            amber.append(f"CYCLE_STOPPED:{beacon.get('cycle_stop_reason') or 'UNKNOWN'} exit={beacon.get('cycle_exit')}")
    if prefix_epoch_id != fresh_start_epoch_id and (fresh_start_epoch_id or prefix_epoch_id):
        amber.append(f"EPOCH_ROTATION_PENDING: prefix derived from {prefix_epoch_id or 'none'}, "
                     f"disk epoch {fresh_start_epoch_id or 'none'}; restart the shipper to rotate")
    if reset_marker and reset_marker.get("status") == "REQUIRED":
        marker_epoch = fresh_start_epoch_id or None
        if not (beacon and marker_epoch and beacon.get("fresh_start_epoch_id") == marker_epoch
                and applied is not None and applied <= published):
            red.append("LAPTOP_RESET_REQUIRED: wipe marker set and the laptop has not reported the new epoch")
    if beacon is None and not red:
        status = "UNKNOWN"
        amber.append("NO_LAPTOP_BEACON: laptop has not reported its applied seq")
    else:
        status = "RED" if red else "AMBER" if amber else "GREEN"
    return {"schema": VERDICT_SCHEMA, "status": status, "alert": status == "RED",
            "reasons": red + amber, "published_seq": published, "acked_seq": acked, "laptop_applied_seq": applied,
            "prefix": prefix, "fresh_start_epoch_id": fresh_start_epoch_id or None,
            "laptop_cycle_stop_reason": (beacon or {}).get("cycle_stop_reason"),
            "laptop_cycle_exit": (beacon or {}).get("cycle_exit"), "checked_at": now}
