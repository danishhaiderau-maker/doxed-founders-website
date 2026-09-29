"""Deny-by-default pruning design hook for the segment pipeline (OFF).

This module only *plans*. It has no delete, unlink or rename capability, and
``PRUNE_ENABLED`` is a code constant, so no environment change can turn
pruning on. A later, separately reviewed change may add execution; it must
keep every gate below.

A source file is a prune candidate only when all of these hold:

* it is a rotated append file (``x.jsonl.N`` and the other rotation suffixes
  the inventory selects), never an active file, snapshot, SQLite or JSON;
* the shipper checkpoint says it was shipped whole (SEAL or SNAPSHOT, with
  its final sha256) at ``shipped_seq``;
* the laptop has ACKed ``through_seq >= shipped_seq`` with the exact
  manifest-chain head hash, and the ACK is recorded write-once on Fly;
* the file on disk still has the size, inode and mtime that were shipped.

Execution additionally requires, deny-by-default:

* ``PRUNE_ENABLED`` (code) and ``RESEARCH_SEGMENTS_PRUNE_ENABLED=1`` (env);
* at least ``REQUIRED_PROVEN_ACK_CYCLES`` distinct, advancing ACK receipts;
* a Fly volume snapshot receipt taken *after* the covering ACK was received.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import research_segment_format as fmt

PRUNE_ENABLED = False
REQUIRED_PROVEN_ACK_CYCLES = 2
SNAPSHOT_RECEIPT_SCHEMA = "fly_volume_snapshot_receipt_v1"


def _rotation(name: str, extensions) -> bool:
    base, separator, generation = name.rpartition(".")
    return bool(separator and generation.isdigit() and not generation.startswith("0")
                and Path(base).suffix.lower() in extensions)


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def ack_receipts(store_root: Path, prefix: str) -> list[dict]:
    directory = Path(store_root).joinpath(*fmt.validate_prefix(prefix).split("/"), "acks",
                                          "laptop-receipts")
    if not directory.is_dir():
        return []
    receipts = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".json") and not entry.name.startswith("."):
                receipt = _load(Path(entry.path))
                if isinstance(receipt.get("through_seq"), int):
                    receipts.append(receipt)
    return sorted(receipts, key=lambda item: item["through_seq"])


def plan_prune(*, shipper_state: dict, rules: dict, universe: dict, store_root: Path,
               prefix: str, snapshot_receipt: dict | None, environ=None) -> dict:
    """Return candidates and the gate verdict. Never modifies anything."""
    env = os.environ if environ is None else environ
    receipts = ack_receipts(store_root, prefix)
    acked = receipts[-1] if receipts else None
    acked_seq = int(acked["through_seq"]) if acked else 0
    candidates = []
    for relpath, tracked in sorted((shipper_state.get("files") or {}).items()):
        name = relpath.rsplit("/", 1)[-1]
        if tracked.get("class") != "snapshot" or not _rotation(name, rules["extensions"]):
            continue
        shipped_seq = int(tracked.get("shipped_seq") or 0)
        if not shipped_seq or shipped_seq > acked_seq:
            continue
        current = universe.get(relpath)
        if current is None:
            continue
        _path, stat = current
        if (int(stat.st_size), int(stat.st_ino), int(stat.st_mtime_ns)) != (
                tracked.get("size"), tracked.get("inode"), tracked.get("mtime_ns")):
            continue
        candidates.append({"path": relpath, "size": int(stat.st_size), "shipped_seq": shipped_seq,
                           "sha256": tracked.get("sha256")})
    reasons = []
    if not PRUNE_ENABLED:
        reasons.append("PRUNE_DISABLED_IN_CODE")
    if (env.get("RESEARCH_SEGMENTS_PRUNE_ENABLED") or "0").strip() != "1":
        reasons.append("PRUNE_DISABLED_BY_ENV")
    advancing = [r for i, r in enumerate(receipts) if i == 0 or r["through_seq"] > receipts[i - 1]["through_seq"]]
    if len(advancing) < REQUIRED_PROVEN_ACK_CYCLES:
        reasons.append("INSUFFICIENT_PROVEN_ACK_CYCLES")
    snapshot = snapshot_receipt or {}
    if snapshot.get("schema") != SNAPSHOT_RECEIPT_SCHEMA or not snapshot.get("created_at"):
        reasons.append("NO_VOLUME_SNAPSHOT_RECEIPT")
    elif acked and str(snapshot["created_at"]) <= str(acked.get("received_at") or ""):
        reasons.append("VOLUME_SNAPSHOT_OLDER_THAN_ACK")
    return {
        "allowed": not reasons, "deny_reasons": reasons, "acked_seq": acked_seq,
        "proven_ack_cycles": len(advancing), "candidates": candidates,
        "candidate_bytes": sum(item["size"] for item in candidates),
    }
