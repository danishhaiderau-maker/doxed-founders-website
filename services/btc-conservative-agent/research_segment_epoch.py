"""Fresh-start epoch identity + laptop/Fly sync verdict for the research segment channel (pure).

HM (b), 16 Oct 2026. The 13 Oct wipe reset Fly's segment numbering (published 2,456) while the laptop
kept applied=7073 and Fly acked=0; the puller asked for seq 7074, got nothing and exited 0. Nothing in
the manifest or keys said "a wipe happened", and nothing compared the counters. This module provides:

* ``fresh_start_epoch_id``: ``fse-<epoch_ts>`` read from ``fresh_start_epoch.json`` (bot.py's file).
* ``resolve_prefix`` / ``resolve_state_dir``: optional per-wipe key prefix (``v3-w<epoch_ts>``), so a wipe
  can never reuse sequence numbers or object names of an archived epoch. Opt-in via
  ``RESEARCH_SEGMENTS_PREFIX_PER_WIPE=1``; default off, so deploying this changes no key.
* ``sync_verdict``: RED on missing laptop beacon (R27/R36), on beacon age ≥ 30 min (shared
  ``BEACON_STALE_*`` family; AMBER at ≥ 10 min), on published_seq < applied_seq, on Fly ack 0 while
  laptop applied > 0 past the amber window, on an epoch/prefix mismatch and on a pending
  "laptop reset required" marker; AMBER on ack lag > 5 min.
* ``validate_beacon``: the small laptop status document POSTed to ``.../laptop-status`` each pull.
* the "laptop reset required" marker written by the wipe (see ``clean_epoch_wipe.execute``).

Rollout (nothing here changes fly.toml): ``RESEARCH_SEGMENTS_PREFIX_PER_WIPE=1`` is flipped in fly.toml in the SAME
change that runs the Fly wipe and the laptop reset. ``clean_epoch_wipe.execute(scope="fly")`` refuses (WipeRefused,
exit 8, nothing deleted) while the flag is off, because a wipe without per-wipe prefixes would reuse the old prefix
and sequence numbers. The reset marker records prefix_before / epoch_before / published_at_wipe and clears only on a
beacon received after the wipe that reports a different prefix or fresh_start_epoch_id (or echoes the wipe's
plan_sha256 with applied_seq <= published). The ``ce-...`` clean-epoch id is never compared with laptop data.
A marker also stays RED while Fly's own ``fresh_start_epoch_id`` still equals ``epoch_before`` (the wipe has not
re-stamped the epoch yet).

RUNBOOK (#677): the Fly wipe MUST re-stamp ``fresh_start_epoch.json`` (the bot does this on the first start after the
wipe, via the fresh-start epoch route) or the reset marker stays RED forever, whatever the laptop reports. Order for the
wipe change: (1) set RESEARCH_SEGMENTS_PREFIX_PER_WIPE=1 in fly.toml, (2) run the Fly wipe, (3) restart so the epoch is
re-stamped and the shipper rotates to the new prefix, (4) reset the laptop mirror/puller, (5) confirm a beacon received
after the wipe shows the new prefix/epoch and sync_verdict leaves RED. If step (3) is skipped the marker stays RED.

``reset_ack_plan_sha256`` (beacon field): the laptop operator sets it AFTER completing the laptop reset (mirror/state
moved aside, puller re-pointed) by passing the wipe receipt's ``plan_sha256`` to the puller
(``--reset-ack-plan-sha256`` or ``RESEARCH_SEGMENTS_RESET_ACK_PLAN_SHA256``); the puller echoes it in every beacon.
It is the explicit-ack fallback for when neither the prefix nor the epoch visibly changes (per-wipe off). It only
counts if it equals the marker's ``plan_sha256`` and ``applied_seq <= published`` and the beacon is newer than the
marker. It is an operator assertion, not proof; the prefix/epoch change path is the verifiable one.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import tempfile
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
# Shared beacon-staleness family (card seal 9fd17232b95b / R27 / R36):
# one base drives AMBER@10m and RED@30m — no divergent hard-codes in evaluators.
BEACON_STALE_AMBER_SEC = 10 * 60
BEACON_STALE_RED_SEC = 3 * BEACON_STALE_AMBER_SEC  # 30 min
BEACON_STALE_SEC = BEACON_STALE_RED_SEC  # alias: RED-tier age (callers / older tests)
# Laptop pull-loop cadence upper bound (scripts/research-segment-pull-loop.ps1 default 120).
BEACON_POST_CADENCE_MAX_SEC = 300
BEACON_POST_CADENCE_DEFAULT_SEC = 120
MAX_BEACON_BYTES = 4096

_BEACON_INT_FIELDS = ("applied_seq", "pull_exit", "cycle_exit")
_BEACON_STR_FIELDS = {  # field -> max length
    "applied_prefix": 64, "collection_epoch_id": 128, "fresh_start_epoch_id": 64,
    "pull_error": 200, "cycle_stop_reason": 120, "cycle_finished_at": 40, "applied_at": 40,
    "puller_version": 64, "reset_ack_plan_sha256": 64,
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


def _atomic_write_json(path: Path, doc: dict) -> None:
    """Unique temp name per writer (two concurrent POSTs must not share one ``.tmp``), then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(doc, sort_keys=True))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_beacon_doc(path, doc: dict) -> None:
    """Atomic write of the stored beacon (the segment server itself never replaces/renames files)."""
    _atomic_write_json(Path(path), doc)


# --------------------------------------------------------------------------- marker
def write_reset_marker(directory, *, epoch_id: str, plan_sha256: str, scope: str, now: float | None = None,
                       prefix_before: str = "", epoch_before: str = "", published_at_wipe: int = 0) -> Path:
    """Leave the "laptop reset required" marker.

    ``prefix_before`` / ``epoch_before`` / ``published_at_wipe`` are the Fly segment prefix, the
    ``fse-...`` fresh-start epoch id and the published seq as they were BEFORE the wipe. The marker
    clears only when a beacon received after ``created_at`` reports a different prefix or epoch (or
    echoes ``reset_ack_plan_sha256`` with applied_seq <= published). ``wipe_epoch_id`` is the ``ce-...``
    clean-epoch id: it is informational and is never compared with anything the laptop reports.
    """
    directory = Path(directory)
    now = time.time() if now is None else now
    doc = {"schema": RESET_MARKER_SCHEMA, "status": "REQUIRED", "scope": scope, "wipe_epoch_id": epoch_id,
           "plan_sha256": plan_sha256, "created_at": now,
           "prefix_before": prefix_before or "", "epoch_before": epoch_before or "",
           "published_at_wipe": int(published_at_wipe or 0),
           "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
           "instruction": "Laptop puller/mirror/acks must be reset to the new prefix/epoch before pulling again; "
                          "sync_verdict stays RED until a beacon received after created_at reports a different "
                          "prefix or fresh_start_epoch_id than prefix_before/epoch_before (or echoes "
                          "reset_ack_plan_sha256 with applied_seq <= published)."}
    path = directory / RESET_MARKER_NAME
    _atomic_write_json(path, doc)
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


def _marker_cleared(marker: dict, beacon: dict | None, beacon_received_at, published: int,
                    current_epoch: str = "") -> bool:
    """True when a beacon received after the marker was written proves the laptop was reset.

    Only ``prefix_before`` / ``epoch_before`` (the pre-wipe values) are compared; never the ``ce-...``
    clean-epoch id. A marker without them (legacy) can only clear through the explicit reset ack.
    """
    if not beacon or beacon_received_at is None:
        return False
    try:
        if float(beacon_received_at) <= float(marker.get("created_at") or 0):
            return False
    except (TypeError, ValueError):
        return False
    applied = int(beacon.get("applied_seq") or 0)
    before_prefix = str(marker.get("prefix_before") or "")
    before_epoch = str(marker.get("epoch_before") or "")
    # The wipe must actually have rotated Fly's own epoch: if Fly still reports the pre-wipe epoch
    # (it has not re-stamped fresh_start_epoch.json), no laptop report can prove a reset to the new one.
    if before_epoch and current_epoch and current_epoch == before_epoch:
        return False
    bp = str(beacon.get("applied_prefix") or "")
    be = str(beacon.get("fresh_start_epoch_id") or "")
    if before_prefix and bp and bp != before_prefix:
        return True
    if before_epoch and be and be != before_epoch:
        return True
    ack = str(beacon.get("reset_ack_plan_sha256") or "")
    return bool(ack and ack == str(marker.get("plan_sha256") or "") and applied <= published)


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
        # Shared AMBER@10m / RED@30m tiers (R27/R36): missing handled below as RED.
        if age is not None and age >= BEACON_STALE_RED_SEC:
            red.append(f"BEACON_STALE: laptop status {int(age / 60)} min old")
        elif age is not None and age >= BEACON_STALE_AMBER_SEC:
            amber.append(f"BEACON_STALE: laptop status {int(age / 60)} min old")
        if applied > published:
            red.append(f"SEQ_REGRESSION: laptop applied {applied} > Fly published {published}")
        if acked == 0 and applied > 0:
            # AMBER first (a fresh pull applies before its ack lands); RED only once the ack is
            # still missing more than ACK_LAG_AMBER_SEC after the laptop applied.
            applied_ts = _iso_to_ts(beacon.get("applied_at"))
            since = None if applied_ts is None else now - applied_ts
            if since is not None and since > ACK_LAG_AMBER_SEC:
                red.append(f"ACK_ZERO_WHILE_APPLIED: Fly acked 0, laptop applied {applied} "
                           f"{int(since / 60)} min ago")
            else:
                amber.append(f"ACK_ZERO_WHILE_APPLIED: Fly acked 0, laptop applied {applied}"
                             + ("" if since is not None else " (applied_at unknown)"))
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
        le = str(beacon.get("fresh_start_epoch_id") or "")
        if fresh_start_epoch_id and le:
            if le != fresh_start_epoch_id:
                red.append(f"EPOCH_MISMATCH: laptop epoch {le} != Fly {fresh_start_epoch_id}")
        elif fresh_start_epoch_id and not le and applied:
            amber.append(f"EPOCH_UNREPORTED: laptop tree has no fresh-start epoch stamp (Fly {fresh_start_epoch_id})")
        if beacon.get("pull_exit") not in (None, 0):
            amber.append(f"PULL_EXIT_{beacon.get('pull_exit')}: {beacon.get('pull_error') or 'no detail'}")
        if beacon.get("cycle_exit") not in (None, 0):
            amber.append(f"CYCLE_STOPPED:{beacon.get('cycle_stop_reason') or 'UNKNOWN'} exit={beacon.get('cycle_exit')}")
    # Only meaningful when per-wipe prefixing is on (prefix_epoch_id non-empty) AND the epoch file exists.
    if prefix_epoch_id and fresh_start_epoch_id and prefix_epoch_id != fresh_start_epoch_id:
        amber.append(f"EPOCH_ROTATION_PENDING: prefix derived from {prefix_epoch_id}, "
                     f"disk epoch {fresh_start_epoch_id}; restart the shipper to rotate")
    if reset_marker and reset_marker.get("status") == "REQUIRED":
        if not _marker_cleared(reset_marker, beacon, beacon_received_at, published, fresh_start_epoch_id):
            red.append("LAPTOP_RESET_REQUIRED: wipe marker set and no beacon since the wipe reports a "
                       "different prefix/epoch than before it")
    applied_source = "BEACON" if beacon else None
    ack_ts = _iso_to_ts(ack_received_at)
    ack_age = None if ack_ts is None else max(0.0, now - ack_ts)
    beacon_age = None if (not beacon or beacon_received_at is None) else max(0.0, now - float(beacon_received_at))
    if beacon is None:
        # R27/R36: missing beacon is always RED (never soft-AMBER / UNKNOWN for the missing key).
        # Ack receipt may still supply applied_seq evidence, but the missing-beacon key stays RED.
        if acked > 0:
            applied, applied_source = acked, "ACK_RECEIPT"
            lag = published - acked
            if lag > 0 and ack_age is not None and ack_age > ACK_LAG_RED_SEC:
                red.append(f"ACK_STALE: laptop ack receipt through {acked} is {int(ack_age / 60)} min old, "
                           f"Fly published {published} ({lag} behind)")
            elif lag > 0 and ack_age is not None and ack_age > ACK_LAG_AMBER_SEC:
                amber.append(f"ACK_LAG: laptop ack through {acked} for {int(ack_age / 60)} min, Fly published {published}")
            red.append(f"NO_LAPTOP_BEACON: no beacon posted; applied seq taken from the ack receipt ({acked}, "
                       f"{'age unknown' if ack_age is None else f'{int(ack_age)} s old'})")
        else:
            red.append("NO_LAPTOP_BEACON_NO_ACK: no beacon and no ack receipt (acked_seq 0): "
                       "laptop applied seq unknown")
    status = "RED" if red else "AMBER" if amber else "GREEN"
    reasons_all = red + amber
    if status == "RED" and not red:  # a RED verdict always carries its reason
        reasons_all = ["RED_UNSPECIFIED: verdict RED without a recorded reason"] + reasons_all
    return {"schema": VERDICT_SCHEMA, "status": status, "alert": status == "RED",
            "reasons": reasons_all, "reason_codes": [r.split(":", 1)[0] for r in reasons_all],
            "laptop_applied_source": applied_source, "ack_age_sec": ack_age, "beacon_age_sec": beacon_age,
            "ack_lag_seq": (published - acked) if published and acked is not None else None,
            "published_seq": published, "acked_seq": acked, "laptop_applied_seq": applied,
            "prefix": prefix, "fresh_start_epoch_id": fresh_start_epoch_id or None,
            "laptop_cycle_stop_reason": (beacon or {}).get("cycle_stop_reason"),
            "laptop_cycle_exit": (beacon or {}).get("cycle_exit"), "checked_at": now}
