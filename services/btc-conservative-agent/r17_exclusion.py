"""R17 (RK 23 Oct 2026): dynamic non-countable exclusion for closed paper rows.

A closed row is NON-COUNTABLE (never fresh data, never in an analyzer verdict) when either:

1. its trade_id is on a pinned R17 discard list shipped in ``r17/`` (sha256-verified at import):
   * ``r17_ids_20261023.json`` (RK list, 995 ids, file sha256 46bc5012...a801b7), and
   * ``r17_final_ids_20261023.json`` (reconciled r17_final list, 1030 ids = the 995 + 35 ADMIN_MANUAL_CLOSE rows,
     sha256 of the compact sorted id list a4701c55...2bc7);
2. OR (the dynamic rule, so a static list is never the only guard) its exit was triggered by a ticker-sourced
   price feed instead of the order book: ``exit_trigger_source`` in POSITION_MANAGER / PROTECTIVE_EXIT_WORKER /
   WS_TICK, read from the row or from its ``event_timestamps`` block (dict, or the CSV-reloaded dict repr / JSON string);
3. OR the row was already stamped ``non_countable`` at write time.

Fail closed: if a list file is missing or its sha256 differs from the pin, EVERY row is non-countable
(reason ``R17_LIST_UNVERIFIED``) until the file is restored; nothing silently counts.

``stamp_on_write(row)`` is called on every new closed row in bot.close_position, so rows written from now on carry
``non_countable`` / ``non_countable_reason`` and ``fresh_eligible=False`` on disk. Pure, no I/O after import.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

LIST_DIR = Path(__file__).resolve().parent / "r17"
PINNED_LISTS = {
    "r17_ids_20261023.json": "46bc50129176563e15e68591d8f419b66815daec58475206f89b288367a801b7",
    "r17_final_ids_20261023.json": "a4701c554f5085408bbde2c206b0d043169577359d52f17b6a41b40fc36e2bc7",
}
# Exit triggers evaluated off a ticker / last-trade price, not the BOOK10L book: never countable.
TICKER_TRIGGER_SOURCES = frozenset({"POSITION_MANAGER", "PROTECTIVE_EXIT_WORKER", "WS_TICK"})

REASON_ID_LIST = "R17_ID_LIST"
REASON_TICKER_TRIGGER = "R17_TICKER_TRIGGER"
REASON_UNVERIFIED = "R17_LIST_UNVERIFIED"
REASON_STAMPED = "R17_STAMPED"


def _ids_from(payload) -> set:
    if isinstance(payload, dict):
        return {str(i) for v in payload.values() if isinstance(v, list) for i in v}
    if isinstance(payload, list):
        return {str(i) for i in payload}
    raise ValueError("unsupported R17 list shape")


def load_lists(list_dir: Path = LIST_DIR, pins: dict = PINNED_LISTS) -> tuple:
    """(ids, errors). Any error means the caller must treat every row as non-countable."""
    ids, errors = set(), []
    for name, pin in pins.items():
        path = Path(list_dir) / name
        try:
            raw = path.read_bytes()
        except OSError as exc:
            errors.append(f"{name}: missing ({type(exc).__name__})")
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if digest != pin:
            errors.append(f"{name}: sha256 {digest} != pinned {pin}")
            continue
        try:
            ids |= _ids_from(json.loads(raw))
        except ValueError as exc:
            errors.append(f"{name}: unreadable ({exc})")
    return frozenset(ids), tuple(errors)


R17_IDS, LOAD_ERRORS = load_lists()


def _base_trade_id(trade_id) -> str:
    """Strip wrapper prefixes (e.g. ``adopt:``/``relink:``) so a re-keyed row keeps its exclusion."""
    tid = str(trade_id or "").strip()
    return tid.rsplit(":", 1)[-1] if ":" in tid else tid


def _parse_block(text: str):
    """CSV-reloaded event_timestamps: bot.log_trade writes a dict repr; JSON is accepted too."""
    import ast
    try:
        out = json.loads(text)
    except ValueError:
        try:
            out = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return None
    return out if isinstance(out, dict) else None


def exit_trigger_source(row) -> str:
    if not isinstance(row, dict):
        return ""
    src = row.get("exit_trigger_source")
    if not src:
        ev = row.get("event_timestamps")
        if isinstance(ev, str) and ev.strip().startswith("{"):
            ev = _parse_block(ev)
        if isinstance(ev, dict):
            src = ev.get("exit_trigger_source")
    return str(src or "").strip().upper()


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes")


def non_countable_reason(row, *, ids=None, load_errors=None):
    """Reason string if the row must never count, else None."""
    if not isinstance(row, dict):
        return None
    ids = R17_IDS if ids is None else ids
    load_errors = LOAD_ERRORS if load_errors is None else load_errors
    if load_errors:
        return REASON_UNVERIFIED
    tid = str(row.get("trade_id") or "").strip()
    if tid and (tid in ids or _base_trade_id(tid) in ids):
        return REASON_ID_LIST
    src = exit_trigger_source(row)
    if src in TICKER_TRIGGER_SOURCES:
        return f"{REASON_TICKER_TRIGGER}:{src}"
    if _truthy(row.get("non_countable")):
        return str(row.get("non_countable_reason") or REASON_STAMPED)
    return None


def stamp_on_write(row: dict) -> dict:
    """Stamp a new closed row in place (and return it). Countable rows get non_countable=False explicitly."""
    if not isinstance(row, dict):
        return row
    reason = non_countable_reason(row)
    row["non_countable"] = bool(reason)
    row["non_countable_reason"] = reason
    if reason:
        row["fresh_eligible"] = False
    return row


def status() -> dict:
    return {"schema": "r17_exclusion_v1", "ids": len(R17_IDS), "lists": dict(PINNED_LISTS),
            "load_errors": list(LOAD_ERRORS), "ticker_trigger_sources": sorted(TICKER_TRIGGER_SOURCES),
            "fail_closed": bool(LOAD_ERRORS)}
