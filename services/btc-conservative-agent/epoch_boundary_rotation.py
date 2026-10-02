"""Writer-side cutover of pre-epoch append heads when a new clean data epoch opens.

Runs once per epoch, right after the bot opens a *new* ``data_epoch.json``.

* ``research_events_v22.jsonl``: the head is sealed into the next numbered
  generation through ``collector_v22.rotate_research_events`` (crash-safe,
  under the collector's real writer lock, never deletes), so
  ``clean_epoch_wipe`` sees a pre-epoch sealed rotation it may remove. The
  provisional store, seals and indexes are untouched.
* V3 ledgers are restart-recovery state. Their generation pointers are bound
  to the deployed revision, so adopting a legacy generation 0 here would
  invalidate every later append after the next deploy. They are assessed
  only (stat + first row); new rows carry ``data_epoch_id`` inside the hashed
  material and the analyzer epoch guard excludes the pre-epoch ones until the
  guarded Fresh Collection reset removes them at the epoch-opening paper
  boundary (``clean_epoch_reset_plan.py``, workflow ``clean-epoch-reset-*``).

Each stream writes one receipt under ``data_epoch_boundary/`` (kept by the
wipe's chain-state guard). A head is rotated only when its first row predates
the epoch start, so a restart inside the same epoch never seals current rows.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import data_epoch

RECEIPT_SCHEMA = "data_epoch_boundary_receipt_v1"
RECEIPT_DIR = "data_epoch_boundary"
RESEARCH_EVENTS_FILE = "research_events_v22.jsonl"
FIRST_ROW_MAX_BYTES = 8 * 1024 * 1024
V3_LEDGERS = ("opportunity", "pre_entry_features", "evidence_failure", "decision", "order_intent", "execution",
              "market_segment", "lifecycle")
V3_RELAY_OR_RECOVERY_LEDGERS = frozenset({"order_intent", "execution", "lifecycle"})


def receipt_path(runtime_root: str | os.PathLike, epoch_id: str, stream: str) -> Path:
    return Path(runtime_root) / RECEIPT_DIR / f"{epoch_id}.{stream}.json"


def load_receipt(runtime_root: str | os.PathLike, epoch_id: str, stream: str) -> dict | None:
    try:
        doc = json.loads(receipt_path(runtime_root, epoch_id, stream).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("epoch_id") == epoch_id else None


def _write_receipt(runtime_root: str | os.PathLike, manifest: dict, stream: str, body: dict) -> dict:
    doc = {"schema": RECEIPT_SCHEMA, "epoch_id": manifest["epoch_id"], "stream": stream,
           "epoch_started_at_utc": manifest["started_at_utc"], "decided_at_utc": data_epoch.utc_iso(time.time()),
           "deletion_invoked": False, **body}
    data_epoch.write_json_atomic(receipt_path(runtime_root, manifest["epoch_id"], stream), doc)
    return doc


def first_row_ts(path: str | os.PathLike, max_bytes: int = FIRST_ROW_MAX_BYTES) -> float | None:
    """Timestamp of the first complete row (bounded read), else None."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(max_bytes)
    except OSError:
        return None
    end = head.find(b"\n")
    return data_epoch.line_ts(head[:end]) if end > 0 else None


def head_decision(path: str | os.PathLike, started_at_ts: float) -> tuple[str, float | None, int]:
    try:
        size = os.path.getsize(path)
    except OSError:
        return "ABSENT", None, 0
    if size <= 0:
        return "EMPTY", None, 0
    ts = first_row_ts(path)
    if ts is None:
        return "UNDATED_HEAD", None, size
    return ("PRE_EPOCH_HEAD" if ts < started_at_ts else "ALREADY_CURRENT"), ts, size


def rotate_research_events_at_boundary(runtime_root: str | os.PathLike, manifest: dict, *,
                                       rotate: Callable[..., dict] | None = None) -> dict:
    """Seal the pre-epoch research_events_v22 head once for ``manifest``'s epoch."""
    done = load_receipt(runtime_root, manifest["epoch_id"], "research_events_v22")
    if done:
        return done
    decision, ts, size = head_decision(Path(runtime_root) / RESEARCH_EVENTS_FILE, float(manifest["started_at_ts"]))
    body: dict[str, Any] = {"head_decision": decision, "head_first_row_ts": ts, "head_bytes": size}
    if decision != "PRE_EPOCH_HEAD":
        return _write_receipt(runtime_root, manifest, "research_events_v22", {**body, "status": "NOT_ROTATED"})
    if rotate is None:
        from collector_v22 import rotate_research_events as rotate  # noqa: PLC0415 - heavy import, boot thread only
    try:
        seal = rotate(data_dir=str(runtime_root))
    except Exception as exc:  # fail closed: keep the head, report why
        return _write_receipt(runtime_root, manifest, "research_events_v22",
                              {**body, "status": "ROTATION_BLOCKED", "reason": f"{type(exc).__name__}:{exc}"[:300]})
    return _write_receipt(runtime_root, manifest, "research_events_v22", {
        **body, "status": "ROTATED", "sealed_generation": seal.get("generation"),
        "sealed_relative_path": seal.get("relative_path"), "sealed_sha256": seal.get("sha256"),
    })


def assess_v3_ledgers(runtime_root: str | os.PathLike, manifest: dict) -> dict:
    """Record, without mutating anything, which V3 heads hold pre-epoch rows and why they are not rotated."""
    done = load_receipt(runtime_root, manifest["epoch_id"], "v3")
    if done:
        return done
    root = Path(runtime_root)
    started = float(manifest["started_at_ts"])
    ledgers = {}
    for ledger in V3_LEDGERS:
        decision, ts, size = head_decision(root / "v3" / "ledgers" / f"{ledger}.jsonl", started)
        pointer = root / "v3" / "receipts" / "ledger_generations_v1" / ledger / "ACTIVE.json"
        entry = {"head_decision": decision, "head_first_row_ts": ts, "head_bytes": size,
                 "generation_authority": pointer.is_file()}
        if decision == "PRE_EPOCH_HEAD":
            entry["status"] = "NOT_ROTATED"
            entry["reason"] = ("RELAY_OR_RESTART_RECOVERY_LEDGER" if ledger in V3_RELAY_OR_RECOVERY_LEDGERS
                               else "GENERATION_POINTER_BOUND_TO_DEPLOYED_REVISION" if not pointer.is_file()
                               else "V3_STORE_IS_RESTART_RECOVERY_STATE")
            entry["analyzer"] = "pre-epoch rows excluded by data_epoch_id / timestamp (analyzer epoch guard)"
        ledgers[ledger] = entry
    return _write_receipt(runtime_root, manifest, "v3", {"status": "ASSESSED", "ledgers": ledgers})


def run_boundary(runtime_root: str | os.PathLike, manifest: dict | None, *, rotate: Callable[..., dict] | None = None,
                 log: Callable[[str], Any] = print) -> dict | None:
    if not manifest:
        return None
    out = {}
    for stream, step in (("v3", lambda: assess_v3_ledgers(runtime_root, manifest)),
                         ("research_events_v22",
                          lambda: rotate_research_events_at_boundary(runtime_root, manifest, rotate=rotate))):
        try:
            out[stream] = step()
            log(f"[DATA EPOCH] boundary {stream}: {out[stream].get('status')} [PIPELINE ENFORCEMENT]")
        except Exception as exc:
            out[stream] = {"status": "BOUNDARY_ERROR", "reason": f"{type(exc).__name__}:{exc}"[:300]}
            log(f"[DATA EPOCH] boundary {stream} failed: {exc} [PIPELINE ENFORCEMENT]")
    return out


def start_boundary_thread(runtime_root: str | os.PathLike, manifest: dict | None, *,
                          log: Callable[[str], Any] = print) -> threading.Thread | None:
    """Background cutover (the v22 seal hashes a >1 GB head; boot must stay inside the health grace)."""
    if not manifest or (load_receipt(runtime_root, manifest["epoch_id"], "research_events_v22")
                        and load_receipt(runtime_root, manifest["epoch_id"], "v3")):
        return None
    thread = threading.Thread(target=run_boundary, args=(runtime_root, manifest), kwargs={"log": log},
                              name="data-epoch-boundary", daemon=True)
    thread.start()
    return thread
