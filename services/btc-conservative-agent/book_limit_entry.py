"""Research conservative limit entry on the 10-level book (RK R3/R4; replaces evaluate_limit_fill's 1 s pricing).

The CONSERVATIVE protection-replay entry used to be decided and priced by
``research.conservative_limit_fill.evaluate_limit_fill`` on ``market_microstructure_1s`` rows, which R3 forbids.
This module composes only shared helpers - no pricing, trigger or queue logic of its own (R4):

* marketable at placement (HM #848 C1): each chase interval is a LIMIT order sent at its start (the signal) that
  reaches the exchange after the delay.  ``paper_fill(direction, qty, signal_ts, delay_s, book_at, leg="entry",
  cap_px=L)`` walks the book row latest at arrival, levels at or better than L only - the part of the order that
  is marketable when it lands (an IOC walk ONLY at placement).  The book seen is bounded to the interval: an order
  that would arrive after the interval ends never reads a later row (``ARRIVAL_AFTER_INTERVAL_END``).
* resting remainder (HM #848 C1): whatever is not filled at arrival RESTS at L until the interval ends and fills
  only through ``order_model.maker_rest_step`` - the same queue model the bot's paper MAKER_REST uses - over REAL
  trade prints (``trades_between``): a print through L fills us, a print AT L on the aggressor side first depletes
  the queue ahead (``order_model.queue_ahead_at`` on the arrival row), then fills us.  Resting fills are AT L (no
  price improvement).  A book row touching L with no print never fills.  When the order rested through a book touch
  of L that sits in a print gap the shared C2 detector flags (``gap_clause_at``), a print that would have filled us
  may be missing: the entry is R17_UNPROVEN_ENTRY (``REST_TOUCH_IN_TRADE_GAP``).
* the first interval with any fill ends the chase (the remainder is cancelled; PARTIAL_FILL).

Countability (HM #848 C2/C3): the receipt stamps the R17 contract's entry fields at the top level
(``trigger_price_source``, ``delay_source`` / ``delay_source_kind``, ``evidence_status``, ``entry_book_age_ms``,
``entry_price_source``) and gates on ``r17_exclusion.entry_proof_failures`` - the SAME check the row contract
uses: a q90/max live-ack delay with n >= 10 (or EXCHANGE_RESIDENT_NO_DELAY) and evidence COMPLETE.  Anything
else - a PARTIAL fill, the 1.5 s default delay, a NO_FILL on an unmeasured delay - is R17_UNPROVEN_ENTRY: kept for
diagnostics, never counted.  Research has no live-ack pool yet (C5), so today every research entry is unproven.
A missing 1 s book row anywhere in the schedule (``r17_gap_detector.missing_seconds_are_a_hole``) or a truncated
book window makes the entry UNSUPPORTED (``ENTRY_BOOK_GAP`` / ``ENTRY_BOOK_WINDOW_TRUNCATED``).

Price never comes from the ticker, book_tape_1s or market_microstructure_1s (R3).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import order_model as _om
import paper_fill as _pf
import quote_trigger as _qt
import r17_exclusion as _r17x
import r17_fields as _r17f
import r17_gap_detector as _gd

try:
    from research.conservative_limit_fill import _normalise_schedule  # pure schedule hash; reads no tape
    from research.quantity_execution import apply_quantity_constraints, validate_signed_quantity_constraints
except ImportError:  # pragma: no cover - direct script execution
    from conservative_limit_fill import _normalise_schedule
    from quantity_execution import apply_quantity_constraints, validate_signed_quantity_constraints

RECEIPT_SCHEMA = "book_limit_entry_receipt_v2"
EVALUATOR_VERSION = "book10l-place-then-rest-v3"
ENTRY_PRICE_SOURCE_BOOK = "BFX_BOOK_10L"
TRIGGER_PRICE_SOURCE_BOOK = "book"  # r17_fields.TRIGGER_PRICE_SOURCE_OK
R17_UNPROVEN_ENTRY = _r17f.R17_UNPROVEN_ENTRY  # the ONE R17 contract module (R4)
REASON_UNVERIFIED_DELAY = _pf.UNVERIFIED_DELAY
REASON_REST_TOUCH_IN_TRADE_GAP = "REST_TOUCH_IN_TRADE_GAP"
WALK_ROW_RULE = "LATEST_BOOK_ROW_AT_SIGNAL_PLUS_DELAY"
FILL_BASIS_PLACEMENT = "MARKETABLE_AT_PLACEMENT_WALK"
FILL_BASIS_REST = "RESTING_AT_LIMIT_TRADES_QUEUE"
FILL_BASIS_PLACEMENT_AND_REST = "PLACEMENT_WALK_THEN_REST"
REST_TRIGGER_SOURCE = "BFX_TRADES"
_FILLED = ("FILLED", "PARTIAL")
_EPS = 1e-9


def _flip(direction: str) -> str:
    return "SHORT" if direction == "LONG" else "LONG"


def _base(direction: str, qty: Any) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA, "evaluator_version": EVALUATOR_VERSION,
        "fill_model": _pf.FILL_MODEL, "trigger_version": _qt.TRIGGER_VERSION,
        "entry_price_source": ENTRY_PRICE_SOURCE_BOOK, "trigger_price_source": TRIGGER_PRICE_SOURCE_BOOK,
        "rest_fill_model": "order_model.maker_rest_step", "rest_fill_basis": _om.REST_FILL_QUEUE_CLEAR,
        "rest_trigger_source": REST_TRIGGER_SOURCE, "direction": direction, "requested_qty": qty,
        "outcome": "UNSUPPORTED", "supported": False, "final_classification": "UNSUPPORTED",
        "filled_qty": 0.0, "fill_price": None, "fill_ts": None, "trigger_bucket_ts": None,
        "chase_bucket_id": None, "limit_price": None, "schedule_sha256": None, "evidence_bucket_ids": [],
        "attempts": [], "book_gap_seconds": [], "entry_fill": None, "quantity_decision": None,
        "delay_s": None, "delay_source": None, "delay_source_kind": None, "delay_unverified": None,
        "evidence_status": None, "entry_book_age_ms": None, "rest_gap_clause": None,
        "r17_entry_status": None, "r17_entry_reason": None, "negative_reasons": [],
    }


def _unsupported(receipt: dict[str, Any], *reasons: str) -> dict[str, Any]:
    receipt["negative_reasons"] = list(dict.fromkeys(reasons))
    receipt["trigger_price_source"] = None  # nothing was decided on the book: never claim a book trigger
    return receipt


def _bounded(book_at: Callable[[float], Any], t1: float) -> Callable[[float], Any]:
    """book_at that never reads a row completed after the interval end (HM #848 nit: no rows past t1)."""
    return lambda t: book_at(t) if float(t) <= t1 + _EPS else None


def _touch_ts(side: str, limit: float, t0: float, t1: float, rows: Iterable[Mapping[str, Any]]) -> list[float]:
    """Ready times of completed rows in (t0, t1] whose opposite side reached the limit (quote_trigger, R4)."""
    out = []
    for e in _qt.exit_events(_flip(side), t0, t1, book_rows=rows, include_trades=False, include_book=True):
        if _qt.level_reached(_flip(side), _qt.KIND_TARGET, limit, e["px"]):
            out.append(float(e["ts"]))
    return out


def _receive_fields(fill: dict[str, Any] | None) -> dict[str, Any]:
    """The walked row's measured receipt (paper_fill.book_receive_proof), copied onto the receipt as entry_book_*
    so r17_exclusion.entry_proof_failures checks it (R4: the same book_receive_failures rule as every row)."""
    f = fill or {}
    return {f"entry_{k}": f.get(k) for k in ("book_receive_ts", "book_receive_mono", "book_receive_ts_source",
                                             "book_receive_age_ms", "book_receive_unverified")}


def _walk_evidence_ok(fill: dict[str, Any] | None) -> bool:
    """FAIL-OPEN fix (RK 24 Oct): the receipt may be COMPLETE only when the walked fill's own evidence is not
    UNVERIFIED (measured, no-look-ahead receipt; no unverified reason)."""
    f = fill or {}
    return (f.get("evidence_status") != "UNVERIFIED" and not f.get("book_receive_unverified")
            and not f.get("unverified_reason"))


def _countability(receipt: dict[str, Any], *extra: str) -> None:
    """Stamp r17_entry_status from the shared entry contract check (+ any receipt-level reasons)."""
    failures = list(_r17x.entry_proof_failures(receipt)) + [r for r in extra if r]
    receipt["r17_entry_failures"] = failures
    receipt["r17_entry_status"] = R17_UNPROVEN_ENTRY if failures else None
    receipt["r17_entry_reason"] = "|".join(failures) if failures else None


def book_limit_entry(direction: str, requested_qty: Any, chase_schedule: Sequence[Mapping[str, Any]],
                     book_at: Optional[Callable[[float], Optional[Mapping[str, Any]]]], *,
                     trades_between: Optional[Callable[[float, float], list]] = None,
                     gap_clause_at: Optional[Callable[[Iterable[float]], int]] = None,
                     delay_s: Optional[float] = None, delay_source: Optional[str] = None,
                     quantity_constraints: Optional[Mapping[str, Any]] = None,
                     symbol: Optional[str] = None) -> dict[str, Any]:
    """Decide and price one chase-scheduled limit entry (place, then rest on real prints); auditable receipt."""
    side = str(direction or "").upper()
    qty = _pf._f(requested_qty)
    receipt = _base(side, qty)
    if side not in ("LONG", "SHORT"):
        return _unsupported(receipt, "INVALID_DIRECTION")
    if qty is None or not math.isfinite(qty) or qty <= 0:
        return _unsupported(receipt, "INVALID_REQUESTED_QTY")
    if book_at is None:
        return _unsupported(receipt, "BOOK_WALK_UNAVAILABLE")
    if trades_between is None:
        # A resting limit is decided by real prints; without them neither a fill nor its absence is observable.
        return _unsupported(receipt, "ENTRY_TRADES_UNAVAILABLE")
    normalized, reasons = validate_signed_quantity_constraints(quantity_constraints, symbol=symbol)
    if normalized is None:
        return _unsupported(receipt, *reasons)
    try:
        schedule, schedule_hash = _normalise_schedule(chase_schedule)
    except (TypeError, ValueError) as exc:
        return _unsupported(receipt, str(exc))
    receipt["schedule_sha256"] = schedule_hash
    receipt["quantity_constraints"] = normalized
    gaps: list[int] = []
    rest_mask = 0
    last_fill: Optional[dict] = None
    for interval in schedule:
        signal_ts, t1 = float(interval["start_ts"]), float(interval["end_ts"])
        limit = float(interval["limit_price"])
        window = _qt.book_rows_window(signal_ts - 1e-6, t1, book_at, late_grace_s=0.0)
        if any(not isinstance(g, (int, float)) for g in window["gap_seconds"]):
            # quote_trigger's ("WINDOW_TRUNCATED", lo, hi) marker: rows before the cap were never checked.
            return _unsupported(receipt, "ENTRY_BOOK_WINDOW_TRUNCATED")
        gaps.extend(int(g) for g in window["gap_seconds"])
        receipt["evidence_bucket_ids"].append(interval["bucket_id"])
        book = _bounded(book_at, t1)
        fill = _pf.paper_fill(side, qty, signal_ts, delay_s, book, leg="entry",
                              delay_source=delay_source or "default_until_n10", cap_px=limit)
        last_fill = fill
        arrival = float(fill.get("fill_ts") or signal_ts)
        attempt = {"bucket_id": interval["bucket_id"], "limit_price": limit, "signal_ts": signal_ts,
                   "end_ts": t1, "arrival_ts": arrival, "walk_row_rule": WALK_ROW_RULE,
                   "walk_book_s": fill.get("book_s"), "status": fill.get("status"),
                   "walked_qty": fill.get("filled_qty") or 0.0}
        receipt["attempts"].append(attempt)
        if arrival > t1 + _EPS:
            attempt["status"] = "ARRIVAL_AFTER_INTERVAL_END"  # never live inside this interval: next reprice
            continue
        if fill.get("status") == "REJECT_NO_BOOK":
            # No complete book row at arrival: neither the placement fill nor its absence is observable.
            return _unsupported(receipt, "ENTRY_ARRIVAL_BOOK_MISSING")
        walk_q = float(fill.get("filled_qty") or 0.0) if (
            fill.get("status") in _FILLED and fill.get("price_source") == ENTRY_PRICE_SOURCE_BOOK) else 0.0
        walk_px = float(fill["price"]) if walk_q > 0 else None
        remaining = max(0.0, qty - walk_q)
        rest = {"fills": [], "remaining": remaining, "queue_ahead": 0.0}
        queue0 = 0.0
        if remaining > _EPS:
            queue0 = _om.queue_ahead_at(side, limit, book(arrival))
            rest = _om.maker_rest_step(side, limit, remaining, queue0, trades_between(arrival, t1),
                                       _om.REST_FILL_QUEUE_CLEAR)
            touches = _touch_ts(side, limit, arrival, t1, window["rows"])
            mask = int(gap_clause_at(touches)) if (touches and gap_clause_at is not None) else 0
            if touches and gap_clause_at is None:
                mask = _gd.CLAUSE_BOOK_MISSING  # no detector input: a touch with unknown print coverage
            rest_mask |= mask
            attempt.update({"rest_from_ts": arrival, "rest_until_ts": t1, "rest_queue_ahead_start": queue0,
                            "rest_queue_ahead_end": rest["queue_ahead"], "rest_fills": [list(f) for f in rest["fills"]],
                            "rest_touch_ts": touches[:20], "rest_touch_count": len(touches),
                            "rest_gap_clause": _gd.clause_label(mask)})
        rest_q = sum(f[2] for f in rest["fills"])
        raw = walk_q + rest_q
        if raw <= _EPS:
            continue  # nothing filled at placement nor on prints while resting: chase the next interval
        px = ((walk_px or 0.0) * walk_q + limit * rest_q) / raw
        decision = apply_quantity_constraints(
            requested_qty=qty, raw_partial_qty=raw, execution_price=px,
            accumulated_qty=0, constraints=quantity_constraints, symbol=symbol)
        receipt["quantity_decision"] = decision
        if decision.get("final_classification") == "UNSUPPORTED":
            return _unsupported(receipt, *(decision.get("reasons") or ["QUANTITY_DECISION_UNSUPPORTED"]))
        if not decision.get("accepted"):
            continue
        filled = float(decision["accumulated_quantity_after"])
        full = decision["final_classification"] == "FULL_FILL"
        fill_ts = max([f[0] for f in rest["fills"]] + ([arrival] if walk_q > 0 else []))
        basis = (FILL_BASIS_PLACEMENT_AND_REST if walk_q > 0 and rest_q > 0
                 else FILL_BASIS_PLACEMENT if walk_q > 0 else FILL_BASIS_REST)
        receipt["book_gap_seconds"] = sorted(set(gaps))
        if _gd.missing_seconds_are_a_hole(receipt["book_gap_seconds"]):
            return _unsupported(receipt, "ENTRY_BOOK_GAP")
        stamp = _pf.delay_stamp(fill.get("delay_source"))
        receipt.update({
            "chase_bucket_id": interval["bucket_id"], "limit_price": limit, "signal_ts": signal_ts,
            "walk_row_rule": WALK_ROW_RULE, "walk_book_s": fill.get("book_s"), "supported": True,
            "delay_s": fill.get("delay_s"), "delay_source": fill.get("delay_source"),
            "delay_source_kind": stamp.get("delay_source_kind"), "delay_unverified": stamp.get("delay_unverified"),
            "entry_book_age_ms": fill.get("book_age_ms"),
            "outcome": "FILL" if full else "PARTIAL_FILL",
            "final_classification": "FULL_FILL" if full else "PARTIAL_FILL",
            "fill_basis": basis, "filled_qty": filled, "remaining_qty": round(max(0.0, qty - filled), 12),
            "fill_price": px, "fill_ts": fill_ts,
            # The replay starts its exit path when the entry is (last) filled: arrival for the placement walk, the
            # filling print for a resting fill.
            "trigger_bucket_ts": fill_ts,
            "placement_walk_qty": walk_q, "placement_walk_px": walk_px, "rest_filled_qty": rest_q,
            "entry_fill": fill,
            # Contract evidence: COMPLETE only for the full size, book-observed; a PARTIAL is never COMPLETE (C3).
            # RK 24 Oct fail-open fix: and only when the walked fill's own evidence is not UNVERIFIED.
            "evidence_status": (_pf.EVIDENCE_COMPLETE if _walk_evidence_ok(fill) else "UNVERIFIED") if full
            else "INCOMPLETE",
            **_receive_fields(fill),
            "rest_gap_clause": _gd.clause_label(rest_mask),
        })
        _countability(receipt, REASON_REST_TOUCH_IN_TRADE_GAP + ":" + _gd.clause_label(rest_mask) if rest_mask else "")
        return receipt
    receipt["book_gap_seconds"] = sorted(set(gaps))
    if _gd.missing_seconds_are_a_hole(receipt["book_gap_seconds"]):
        # A no-fill is proven only when every schedule second had a book row (shared detector: any missing row).
        return _unsupported(receipt, "ENTRY_BOOK_GAP")
    stamp = _pf.delay_stamp((last_fill or {}).get("delay_source"))
    receipt.update({
        "outcome": "NO_FILL", "supported": True, "final_classification": "NO_FILL",
        "delay_s": (last_fill or {}).get("delay_s"), "delay_source": (last_fill or {}).get("delay_source"),
        "delay_source_kind": stamp.get("delay_source_kind"), "delay_unverified": stamp.get("delay_unverified"),
        "entry_book_age_ms": (last_fill or {}).get("book_age_ms"),
        # RK 24 Oct fail-open fix: a NO_FILL is COMPLETE only when the last walked row's receipt is proven.
        "evidence_status": _pf.EVIDENCE_COMPLETE if last_fill and _walk_evidence_ok(last_fill) else "UNVERIFIED",
        "rest_gap_clause": _gd.clause_label(rest_mask), **_receive_fields(last_fill),
    })
    # HM #848 C2: whether the order filled depends on the delay; on an unmeasured delay the no-fill is unproven.
    _countability(receipt, REASON_REST_TOUCH_IN_TRADE_GAP + ":" + _gd.clause_label(rest_mask) if rest_mask else "")
    return receipt
