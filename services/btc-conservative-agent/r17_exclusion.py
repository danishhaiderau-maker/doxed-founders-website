"""R17 (RK 23 Oct 2026): dynamic non-countable exclusion for closed paper rows.

A closed row is NON-COUNTABLE (never fresh data, never in an analyzer verdict) when either:

1. its trade_id is on a pinned R17 discard list shipped in ``r17/`` (sha256-verified at import):
   * ``r17_ids_20261023.json`` (RK list, 995 ids, file sha256 46bc5012...a801b7), and
   * ``r17_final_ids_20261023.json`` (reconciled r17_final list, 1030 ids = the 995 + 35 ADMIN_MANUAL_CLOSE rows,
     sha256 of the compact sorted id list a4701c55...2bc7);
2. OR (the dynamic rule: countable ONLY with proof, HM/RK 23 Oct) the row fails any check of the closed-row field
   contract in ``r17_fields.CONTRACT`` (book trigger, measured entry+exit delay, both legs book-walked with
   COMPLETE evidence and a recorded price age, no unfired stop/TP/BE crossing, stop_coverage == STOP_COVERAGE_OK).
   ANY missing or unknown field excludes the row; the reason is ``R17_UNPROVEN:`` plus every failed check joined
   by ``|``. levels_crossed_unfired / stop_coverage are stamped by REALISM_V2 C3, so until then every row fails.
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

import exit_lag_regression as _elr  # RK PASS (7)
import r17_fields as F  # the ONE closed-row field contract (HM C4)

LIST_DIR = Path(__file__).resolve().parent / "r17"
PINNED_LISTS = {
    "r17_ids_20261023.json": "46bc50129176563e15e68591d8f419b66815daec58475206f89b288367a801b7",
    "r17_final_ids_20261023.json": "a4701c554f5085408bbde2c206b0d043169577359d52f17b6a41b40fc36e2bc7",
}
RESIDENT_EXIT_BASIS = F.RESIDENT_EXIT_BASIS

REASON_ID_LIST = "R17_ID_LIST"
REASON_UNPROVEN = "R17_UNPROVEN"
REASON_UNVERIFIED = "R17_LIST_UNVERIFIED"
REASON_STAMPED = "R17_STAMPED"
REASON_PRECOUNT = "R17_PRECOUNT"


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
    """Kept for diagnostics only (the loop name is NOT evidence of the price basis)."""
    if not isinstance(row, dict):
        return ""
    src = row.get("exit_trigger_source")
    if not src:
        ev = _event_block(row)
        src = ev.get("exit_trigger_source") if ev else None
    return str(src or "").strip().upper()


def _event_block(row):
    ev = row.get("event_timestamps")
    if isinstance(ev, str) and ev.strip().startswith("{"):
        ev = _parse_block(ev)
    return ev if isinstance(ev, dict) else None


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes")


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    import math
    # NaN (CSV/pandas blank), +-inf and negatives are not proof: every contract number is a finite age/size >= 0
    return f if math.isfinite(f) and f >= 0 else None


def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and v != v) or (isinstance(v, str) and not v.strip())


def _delay_measured(src) -> bool:
    import re
    t = str(src or "").strip()
    if t == F.RESIDENT_DELAY_SOURCE:
        return True
    m = re.fullmatch(F.MEASURED_DELAY_PATTERN, t)
    return bool(m) and int(m.group(1)) >= F.MIN_POOL_N


def _no_unfired_cross(v) -> bool:
    """Only a REAL empty list or False proves no crossing. CSV round-trips keep "[]" / "false"; the string
    "None" (a str()'d Python None), "0", "none" and anything else fail."""
    if v is False or (isinstance(v, (list, tuple)) and len(v) == 0):
        return True
    return isinstance(v, str) and v.strip() in ("[]", "false", "False")


def _exact(v) -> str:
    """Exact contract compare: strip surrounding spaces only, never case-fold ("BOOK", "complete" fail)."""
    return v.strip() if isinstance(v, str) else ""


def _nonneg(v) -> bool:
    n = _num(v)
    return n is not None and n >= 0


def _false(v) -> bool:
    return v is False or (isinstance(v, str) and v.strip() in ("false", "False"))


def liq_failure(row):
    """RK/HM K1 (24 Oct): a row counts only when the hold path is PROVEN clear of the liquidation distance:
    liq_unverified is a real False, the mark series was read (liq_mark_rows_n > 0) and nothing reached liq_px
    (no liq_trigger_ts). liq_unverified=True (liquidation before the stop, stop slipped past liq, no mark) or a
    missing check fails closed. Returns the failure label or None."""
    if not isinstance(row, dict):
        return "LIQ_CHECK_MISSING"
    lu = row.get("liq_unverified")
    if _truthy(lu):
        return "LIQ_UNVERIFIED"
    if not _false(lu):
        return "LIQ_CHECK_MISSING"
    n = _num(row.get("liq_mark_rows_n"))
    if n is None or n <= 0:
        return "LIQ_MARKS_MISSING"
    if not _missing(row.get("liq_trigger_ts")) and str(row.get("liq_trigger_ts")).strip() not in ("None", "nan"):
        return "LIQ_DISTANCE_REACHED"
    return None


def book_receive_failures(row) -> list:
    """RK/HM #851 (24 Oct): each priced book row must carry the collector's MEASURED receive time (wall + monotonic,
    source WS_FRAME). A missing / non-finite stamp or any other source (pre-#851 row, INFERRED_BUCKET_LAG) fails."""
    out = []
    for side, label in (("entry", "BOOK_RECEIVE_TS_MISSING"), ("exit", "EXIT_BOOK_RECEIVE_TS_MISSING")):
        rx, mono = _num(row.get(f"{side}_book_receive_ts")), _num(row.get(f"{side}_book_receive_mono"))
        if (_exact(row.get(f"{side}_book_receive_ts_source")) not in F.BOOK_RECEIVE_SOURCE_OK
                or rx is None or rx <= 0 or mono is None or mono < 0
                or not _nonneg(row.get(f"{side}_book_receive_age_ms"))):  # HM 02:55: age from receipt >= 0 (proof)
            out.append(label)
    return out


def entry_proof_failures(row) -> list:
    """The ENTRY-leg subset of ``proof_failures`` (trigger source, measured delay, COMPLETE evidence, book age, never
    the 1 s tape). One rule, one module (R4): research place-then-rest entry receipts gate on this, and the full
    row check below re-uses it, so the entry rule cannot drift from the row rule (HM #848 C3)."""
    out = []
    tps = row.get("trigger_price_source")
    if _exact(tps) not in F.TRIGGER_PRICE_SOURCE_OK:
        out.append("TRIGGER_SOURCE_MISSING" if _missing(tps) else "TRIGGER_NOT_BOOK")
    if not _delay_measured(row.get("delay_source")):
        out.append("DELAY_SOURCE_MISSING" if _missing(row.get("delay_source")) else "DELAY_NOT_MEASURED")
    if _exact(row.get("evidence_status")) not in F.EVIDENCE_OK:
        out.append("ENTRY_EVIDENCE_NOT_COMPLETE")
    if not _nonneg(row.get("entry_book_age_ms")):
        out.append("ENTRY_PRICE_AGE_MISSING")
    if entry_priced_on_1s_tape(row):
        out.append(F.REASON_ENTRY_PRICED_ON_1S_TAPE)
    # RK 24 Oct fail-open fix: the entry receipt must carry the measured receive proof (same helper as the row check).
    out.extend(r for r in book_receive_failures(row) if r == "BOOK_RECEIVE_TS_MISSING")
    return out


def proof_failures(row) -> list:
    """Every failed countability check against the r17_fields contract (empty list = the row proves book realism)."""
    entry = entry_proof_failures(row)
    out = [r for r in entry if r in ("TRIGGER_SOURCE_MISSING", "TRIGGER_NOT_BOOK",
                                     "DELAY_SOURCE_MISSING", "DELAY_NOT_MEASURED")]
    resident_exit = str(row.get("exit_pricing_basis") or "") == F.RESIDENT_EXIT_BASIS
    if not resident_exit and not _delay_measured(row.get("exit_priced_delay_source")):
        out.append("EXIT_DELAY_NOT_MEASURED")
    if _num(row.get("entry_walk_bp")) is None:
        out.append("ENTRY_NOT_BOOK_WALKED")
    out.extend(r for r in entry if r in ("ENTRY_EVIDENCE_NOT_COMPLETE", "ENTRY_PRICE_AGE_MISSING"))
    if _num(row.get("exit_walk_bp")) is None:
        out.append("EXIT_NOT_BOOK_WALKED")
    if _num(row.get("exit_legs_unmeasured")) != 0:
        out.append("EXIT_LEG_UNMEASURED")
    if not (_nonneg(row.get("exit_price_age_s")) or _nonneg(row.get("exit_book_age_ms"))):
        out.append("EXIT_PRICE_AGE_MISSING")
    if _exact(row.get("exit_evidence_status")) not in F.EVIDENCE_OK:
        out.append("EXIT_EVIDENCE_NOT_COMPLETE")
    if not _missing(row.get("exit_unverified_reason")) and str(row.get("exit_unverified_reason")).strip() not in ("None", "nan"):
        out.append("EXIT_UNVERIFIED")
    if not _no_unfired_cross(row.get("levels_crossed_unfired")):
        out.append("LEVEL_CROSS_UNKNOWN" if _missing(row.get("levels_crossed_unfired")) else "LEVEL_CROSSED_UNFIRED")
    sc = _exact(row.get("stop_coverage"))
    if sc not in F.STOP_COVERAGE_OK:
        out.append("STOP_COVERAGE_MISSING" if _missing(row.get("stop_coverage")) else "STOP_COVERAGE_UNKNOWN")
    lf = liq_failure(row)
    if lf:
        out.append(lf)
    out.extend(book_receive_failures(row))
    out.extend(_elr.reasons(row))  # RK PASS (7): trigger->decision lag above the pre-346ef2bf p95
    if entry_priced_on_1s_tape(row) or F.REASON_ENTRY_PRICED_ON_1S_TAPE in entry:
        out.append(F.REASON_ENTRY_PRICED_ON_1S_TAPE)
    return out


def entry_priced_on_1s_tape(row) -> bool:
    """RK #847 cond 1 / R3: entry priced on market_microstructure_1s / book_tape_1s / ticker, or stamped
    R17_UNPROVEN_ENTRY (r17_fields.FORBIDDEN). Never countable whatever else the row proves."""
    src = row.get("entry_price_source")
    if isinstance(src, str) and src.strip().upper() in F.ENTRY_PRICE_SOURCES_1S:
        return True
    return any(isinstance(row.get(k), str) and row.get(k).strip().upper() == F.R17_UNPROVEN_ENTRY
               for k in F.ENTRY_STATUS_FIELDS)


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
    # RK (24 Oct): no early return. proof_failures ALWAYS runs and every reason is recorded, so a stamped row is
    # never a bare R17_STAMPED. Countability is unchanged: the row is non-countable iff any part below is present.
    parts = []
    stamped = _truthy(row.get("non_countable"))
    prior = row.get("non_countable_reason")
    # Recorded reasons only describe a row that is stamped non_countable (countability is decided as before).
    if stamped and isinstance(prior, str) and prior.strip() and prior.strip() != REASON_STAMPED:
        parts.extend(p for p in prior.strip().split(";") if p and p != REASON_STAMPED
                     and not p.startswith((REASON_UNPROVEN + ":", REASON_PRECOUNT + ":")))
    plural = row.get("non_countable_reasons")
    if isinstance(plural, str):
        plural = [plural]
    if stamped and isinstance(plural, (list, tuple)):
        names = [str(r).strip() for r in plural if str(r).strip()]
        if names:
            parts.append(REASON_PRECOUNT + ":" + "|".join(names))
    failed = proof_failures(row)
    if failed:
        parts.append(REASON_UNPROVEN + ":" + "|".join(failed))
    out = []
    for part in parts:
        if part not in out:
            out.append(part)
    if out:
        return ";".join(out)
    return REASON_STAMPED if stamped else None


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
    return {"schema": "r17_exclusion_v2", "ids": len(R17_IDS), "lists": dict(PINNED_LISTS),
            "load_errors": list(LOAD_ERRORS), "rule": "countable_only_with_proof",
            "fail_closed": bool(LOAD_ERRORS)}
