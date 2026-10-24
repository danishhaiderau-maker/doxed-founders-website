"""ONE shared write-time stamp for every V3 ledger row (R4; RK R8 fix-now, 24 Oct 2026).

Root cause this fixes: ``V3EvidenceStore.append`` was the single funnel for decision / lifecycle / order_intent /
market_segment rows but stamped no write time, and ``data_epoch_id`` only when an epoch manifest happened to be active.
Most decision and lifecycle rows carry no ``*_ts`` field of their own, so the analyzer epoch guard
(``analyzer_epoch_guard.nested_ts_class``) could not place them and classified them UNSTAMPED.

Every row now gets ``write_stamp``:
  data_epoch_id         the pinned clean epoch (R6: ce-20261004-v31-freeze21b), only if it is the ACTIVE epoch
  fresh_start_epoch_ts  epoch_ts from fresh_start_epoch.json (the guard's floor)
  written_at_ts         write time (a nested ``*_ts``, so the guard places the row at/after the floor)
  realism               the paper fill model the row was produced under
  countable             True only when every field above is known and written_at_ts >= floor

FAIL CLOSED: if any part cannot be determined the row is still written (mandatory lifecycle evidence must not be
lost) but with ``countable: false`` and ``non_countable_reason``; the guard never admits such a row.
Pure apart from reading fresh_start_epoch.json (cached on mtime).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import data_epoch as de

SCHEMA = "v3_ledger_write_stamp_v1"
STAMP_KEY = "write_stamp"
EXPECTED_DATA_EPOCH_ID = "ce-20261004-v31-freeze21b"
FRESH_START_FILE = "fresh_start_epoch.json"

REASON_NO_EPOCH = "WRITE_STAMP_NO_ACTIVE_DATA_EPOCH"
REASON_WRONG_EPOCH = "WRITE_STAMP_FOREIGN_DATA_EPOCH"
REASON_NO_FLOOR = "WRITE_STAMP_FRESH_FLOOR_MISSING"
REASON_BEFORE_FLOOR = "WRITE_STAMP_BEFORE_FRESH_FLOOR"
REASON_NO_REALISM = "WRITE_STAMP_REALISM_UNKNOWN"
REASON_MISSING = "WRITE_STAMP_MISSING"

_floor_cache: dict[str, tuple[tuple[int, int], float | None]] = {}


def fresh_floor_ts(data_dir: str | os.PathLike) -> float | None:
    """epoch_ts of fresh_start_epoch.json in ``data_dir`` or its parent; None when absent/unreadable."""
    base = Path(data_dir)
    for directory in (base, base.parent):
        path = directory / FRESH_START_FILE
        try:
            st = path.stat()
        except OSError:
            continue
        sig = (st.st_mtime_ns, st.st_size)
        cached = _floor_cache.get(str(path))
        if cached and cached[0] == sig:
            if cached[1]:
                return cached[1]
            continue
        try:
            ts = float(json.loads(path.read_text(encoding="utf-8")).get("epoch_ts") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            ts = 0.0
        _floor_cache[str(path)] = (sig, ts if ts > 0 else None)
        if ts > 0:
            return ts
    return None


def realism_stamp() -> dict[str, Any] | None:
    try:
        import paper_fill
        model = str(os.getenv("PAPER_FILL_MODEL") or paper_fill.FILL_MODEL).strip().upper()
        return {"fill_model": model, "fill_model_version": paper_fill.FILL_MODEL_VERSION,
                "price_source": "ORDER_BOOK"}
    except Exception:  # noqa: BLE001 - unknown realism must fail closed, never raise into a writer
        return None


def build_write_stamp(*, data_epoch_id: str | None, floor_ts: float | None, now: float,
                      realism: dict | None) -> dict[str, Any]:
    reason = None
    if not data_epoch_id:
        reason = REASON_NO_EPOCH
    elif data_epoch_id != EXPECTED_DATA_EPOCH_ID:
        reason = REASON_WRONG_EPOCH
    elif not floor_ts:
        reason = REASON_NO_FLOOR
    elif float(now) < float(floor_ts):
        reason = REASON_BEFORE_FLOOR
    elif not realism:
        reason = REASON_NO_REALISM
    stamp = {"schema": SCHEMA, "data_epoch_id": data_epoch_id, "fresh_start_epoch_ts": floor_ts,
             "written_at_ts": float(now), "realism": realism, "countable": reason is None}
    if reason:
        stamp["non_countable_reason"] = reason
    return stamp


def stamp_ledger_row(material: dict[str, Any], *, data_dir: str | os.PathLike, now: float | None = None) -> dict[str, Any]:
    """``material`` plus ``write_stamp`` (and ``data_epoch_id`` when known). An existing stamp is kept unchanged
    (idempotent replay of a frozen row). Never raises."""
    if isinstance(material.get(STAMP_KEY), dict):
        return material
    try:
        epoch = de.active_epoch_id()
    except Exception:  # noqa: BLE001
        epoch = None
    try:
        floor = fresh_floor_ts(data_dir)
    except Exception:  # noqa: BLE001
        floor = None
    stamp = build_write_stamp(data_epoch_id=epoch, floor_ts=floor, now=time.time() if now is None else now,
                              realism=realism_stamp())
    out = dict(material)
    if epoch:
        out.setdefault(de.STAMP_FIELD, epoch)
    out[STAMP_KEY] = stamp
    return out


def row_non_countable_reason(row: Any) -> str | None:
    """None when the row carries a countable write stamp; else the reason (missing stamp included)."""
    if not isinstance(row, dict):
        return REASON_MISSING
    stamp = row.get(STAMP_KEY)
    if not isinstance(stamp, dict) or stamp.get("schema") != SCHEMA:
        return REASON_MISSING
    if stamp.get("countable") is not True:
        return str(stamp.get("non_countable_reason") or "WRITE_STAMP_NON_COUNTABLE")
    if row.get(de.STAMP_FIELD) not in (None, stamp.get("data_epoch_id")):
        return REASON_WRONG_EPOCH
    return None
