"""Research conservative limit entry on the 10-level book (RK R3/R4; replaces evaluate_limit_fill's 1 s pricing).

The CONSERVATIVE protection-replay entry used to be decided and priced by
``research.conservative_limit_fill.evaluate_limit_fill`` on ``market_microstructure_1s`` rows, which R3 forbids.
This module composes only the two shared helpers R4 allows - no pricing or trigger logic of its own:

* price (RK #848 (a)): each chase interval is an order SENT at its start (the signal) that arrives after the
  measured delay; ``paper_fill(direction, qty, signal_ts, delay_s, book_at, leg="entry", cap_px=L)`` walks the FULL
  size on the book row latest at signal + delay, levels at or better than L only.  No fee term (RK #848 (c)).
* remainder (RK #848 (b)): size not available within L at arrival is NOT filled.  It rests at L until the interval
  ends; ``quote_trigger.first_level_trigger`` (a TARGET on the opposite side: LONG -> ask at/through L) says whether
  the book reached L meanwhile.  If not, the remainder is proven NOT_FILLED.  If it did, a resting fill would need
  real trades through L with our queue position - research has no such proof, so the entry is R17_UNPROVEN_ENTRY
  (``RESTING_FILL_NO_TRADE_QUEUE_PROOF``).  With nothing filled at arrival, the next interval is tried; a resting
  claim with nothing filled is kept (at L) for diagnostics only.

Countability (R17): a fill is countable only when it is priced from ``BFX_BOOK_10L`` and its send delay is
verified (``paper_fill.delay_stamp``: live-ack pool n >= 10).  Research has no live-ack pool, so the default
1.5 s delay applies and every research fill is ``R17_UNPROVEN_ENTRY`` (reason ``UNVERIFIED_DELAY``): priced on
the book, kept for diagnostics, never counted.  A NO_FILL is countable only when every schedule second had a
complete book row; a book gap inside the schedule makes the no-fill UNSUPPORTED (``ENTRY_BOOK_GAP``).

Trade prints never decide an entry here; only completed book rows do (R3).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Optional, Sequence

import paper_fill as _pf
import quote_trigger as _qt
import r17_fields as _r17f

try:
    from research.conservative_limit_fill import _normalise_schedule  # pure schedule hash; reads no tape
    from research.quantity_execution import apply_quantity_constraints, validate_signed_quantity_constraints
except ImportError:  # pragma: no cover - direct script execution
    from conservative_limit_fill import _normalise_schedule
    from quantity_execution import apply_quantity_constraints, validate_signed_quantity_constraints

RECEIPT_SCHEMA = "book_limit_entry_receipt_v1"
EVALUATOR_VERSION = "book10l-arrival-walk-v2"
ENTRY_PRICE_SOURCE_BOOK = "BFX_BOOK_10L"
R17_UNPROVEN_ENTRY = _r17f.R17_UNPROVEN_ENTRY  # the ONE R17 contract module (R4)
REASON_UNVERIFIED_DELAY = _pf.UNVERIFIED_DELAY
REASON_NOT_BOOK_PRICED = "ENTRY_NOT_BOOK_PRICED"
REASON_RESTING_NO_QUEUE_PROOF = "RESTING_FILL_NO_TRADE_QUEUE_PROOF"
WALK_ROW_RULE = "LATEST_BOOK_ROW_AT_SIGNAL_PLUS_DELAY"
FILL_BASIS_IMMEDIATE = "IMMEDIATE_WALK_WITHIN_LIMIT"
FILL_BASIS_RESTING = "RESTING_AT_LIMIT_UNPROVEN"
_FILLED = ("FILLED", "PARTIAL")
_EPS = 1e-6


def _flip(direction: str) -> str:
    return "SHORT" if direction == "LONG" else "LONG"


def _longest_run(seconds: Sequence[int]) -> int:
    best = run = 0
    previous = None
    for sec in seconds:
        run = run + 1 if previous is not None and sec == previous + 1 else 1
        best, previous = max(best, run), sec
    return best


def _base(direction: str, qty: Any) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA, "evaluator_version": EVALUATOR_VERSION,
        "fill_model": _pf.FILL_MODEL, "trigger_version": _qt.TRIGGER_VERSION,
        "entry_price_source": ENTRY_PRICE_SOURCE_BOOK, "direction": direction, "requested_qty": qty,
        "outcome": "UNSUPPORTED", "supported": False, "final_classification": "UNSUPPORTED",
        "filled_qty": 0.0, "fill_price": None, "fill_ts": None, "trigger_bucket_ts": None,
        "chase_bucket_id": None, "limit_price": None, "schedule_sha256": None, "evidence_bucket_ids": [],
        "attempts": [], "book_gap_seconds": [], "entry_fill": None, "quantity_decision": None,
        "r17_entry_status": None, "r17_entry_reason": None, "negative_reasons": [],
    }


def _unsupported(receipt: dict[str, Any], *reasons: str) -> dict[str, Any]:
    receipt["negative_reasons"] = list(dict.fromkeys(reasons))
    return receipt


def book_limit_entry(direction: str, requested_qty: Any, chase_schedule: Sequence[Mapping[str, Any]],
                     book_at: Optional[Callable[[float], Optional[Mapping[str, Any]]]], *,
                     delay_s: Optional[float] = None, delay_source: Optional[str] = None,
                     quantity_constraints: Optional[Mapping[str, Any]] = None,
                     symbol: Optional[str] = None) -> dict[str, Any]:
    """Decide and price one chase-scheduled limit entry on the book; returns an auditable receipt."""
    side = str(direction or "").upper()
    qty = _pf._f(requested_qty)
    receipt = _base(side, qty)
    if side not in ("LONG", "SHORT"):
        return _unsupported(receipt, "INVALID_DIRECTION")
    if qty is None or not math.isfinite(qty) or qty <= 0:
        return _unsupported(receipt, "INVALID_REQUESTED_QTY")
    if book_at is None:
        return _unsupported(receipt, "BOOK_WALK_UNAVAILABLE")
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
    for interval in schedule:
        signal_ts, t1 = float(interval["start_ts"]), float(interval["end_ts"])
        limit = float(interval["limit_price"])
        window = _qt.book_rows_window(signal_ts - _EPS, t1, book_at, late_grace_s=0.0)
        gaps.extend(int(g) for g in window["gap_seconds"] if isinstance(g, (int, float)))
        receipt["evidence_bucket_ids"].append(interval["bucket_id"])
        # RK #848 (a): the order is sent at the interval's signal time and arrives after the (measured) delay; the
        # FULL size walks the book row latest at signal + delay, levels at or better than the limit only (cap_px).
        fill = _pf.paper_fill(side, qty, signal_ts, delay_s, book_at, leg="entry",
                              delay_source=delay_source or "default_until_n10", cap_px=limit)
        arrival = float(fill.get("fill_ts") or signal_ts)
        attempt = {"bucket_id": interval["bucket_id"], "limit_price": limit, "signal_ts": signal_ts,
                   "arrival_ts": arrival, "walk_row_rule": WALK_ROW_RULE, "walk_book_s": fill.get("book_s"),
                   "status": fill.get("status"), "walked_qty": fill.get("filled_qty") or 0.0}
        receipt["attempts"].append(attempt)
        if fill.get("status") == "REJECT_NO_BOOK":
            # No complete book row at arrival: neither the immediate fill nor its absence is observable.
            return _unsupported(receipt, "ENTRY_ARRIVAL_BOOK_MISSING")
        decision = None
        filled = 0.0
        if fill.get("status") in _FILLED and fill.get("price_source") == ENTRY_PRICE_SOURCE_BOOK:
            decision = apply_quantity_constraints(
                requested_qty=qty, raw_partial_qty=fill.get("filled_qty"), execution_price=fill.get("price"),
                accumulated_qty=0, constraints=quantity_constraints, symbol=symbol)
            receipt["quantity_decision"] = decision
            if decision.get("final_classification") == "UNSUPPORTED":
                return _unsupported(receipt, *(decision.get("reasons") or ["QUANTITY_DECISION_UNSUPPORTED"]))
            if decision.get("accepted"):
                filled = float(decision["accumulated_quantity_after"])
        remainder = round(max(0.0, qty - filled), 12)
        # RK #848 (b): size not available within the limit at arrival is NOT filled.  It rests at the limit until
        # the interval ends; a resting fill would need real trades through the level with our queue position, which
        # research does not have - so a book row reaching the limit while it rests leaves the entry R17_UNPROVEN.
        resting = None
        if remainder > _EPS:
            cross = _qt.first_level_trigger(_flip(side), _qt.KIND_TARGET, limit, arrival, t1, book_rows=window["rows"])
            resting = {"qty": remainder, "limit_price": limit, "rest_from_ts": arrival, "rest_until_ts": t1,
                       "status": "NOT_FILLED" if cross is None else R17_UNPROVEN_ENTRY,
                       "reason": None if cross is None else REASON_RESTING_NO_QUEUE_PROOF,
                       "level_reached_ts": None if cross is None else cross.get("trigger_ts"),
                       "queue_position_proof": None}
            attempt["resting_remainder"] = resting
        resting_unproven = resting is not None and resting["status"] == R17_UNPROVEN_ENTRY
        if filled <= 0 and not resting_unproven:
            continue  # nothing filled and the level was never reached while resting: chase the next interval
        receipt["book_gap_seconds"] = sorted(set(gaps + [int(g) for g in window["gap_seconds"]
                                                         if isinstance(g, (int, float))]))
        receipt.update({"chase_bucket_id": interval["bucket_id"], "limit_price": limit, "signal_ts": signal_ts,
                        "walk_row_rule": WALK_ROW_RULE, "walk_book_s": fill.get("book_s"),
                        "delay_s": fill.get("delay_s"), "delay_source": fill.get("delay_source"),
                        "resting_remainder": resting, "supported": True})
        if filled > 0:
            full = decision["final_classification"] == "FULL_FILL"
            stamp = _pf.delay_stamp(fill.get("delay_source"))
            verified = (fill.get("evidence_status") in (_pf.EVIDENCE_COMPLETE, "INCOMPLETE")
                        and not stamp.get("delay_unverified") and not fill.get("unverified_reason"))
            price = float(fill["price"])
            reason = None
            if not verified:
                reason = REASON_UNVERIFIED_DELAY if stamp.get("delay_unverified") else REASON_NOT_BOOK_PRICED
            elif resting_unproven:
                reason = REASON_RESTING_NO_QUEUE_PROOF  # the final size is unknown -> the whole entry is unproven
            receipt.update({
                "outcome": "FILL" if full else "PARTIAL_FILL",
                "final_classification": "FULL_FILL" if full else "PARTIAL_FILL",
                "fill_basis": FILL_BASIS_IMMEDIATE,
                "filled_qty": filled, "remaining_qty": remainder,
                "fill_price": price, "fill_ts": fill.get("fill_ts"),
                # The replay starts its exit path at the moment the entry is filled (signal + delay).
                "trigger_bucket_ts": fill.get("fill_ts"),
                "entry_fill": fill, "evidence_status": fill.get("evidence_status"),
                "price_improvement_vs_limit": round((limit - price) if side == "LONG" else (price - limit), 8),
                "r17_entry_status": None if reason is None else R17_UNPROVEN_ENTRY,
                "r17_entry_reason": reason,
            })
            return receipt
        # Nothing filled at arrival, but the level was reached while the order rested: a maker fill AT the limit is
        # claimed for diagnostics only - without trade + queue proof it is never countable.
        receipt.update({
            "outcome": "FILL", "final_classification": "FULL_FILL", "fill_basis": FILL_BASIS_RESTING,
            "filled_qty": resting["qty"], "remaining_qty": 0.0, "fill_price": limit,
            "fill_ts": resting["level_reached_ts"], "trigger_bucket_ts": resting["level_reached_ts"],
            "entry_fill": None, "evidence_status": "UNVERIFIED", "price_improvement_vs_limit": 0.0,
            "r17_entry_status": R17_UNPROVEN_ENTRY, "r17_entry_reason": REASON_RESTING_NO_QUEUE_PROOF,
        })
        return receipt
    receipt["book_gap_seconds"] = sorted(set(gaps))
    if _longest_run(receipt["book_gap_seconds"]) > _pf.MAX_BOOK_AGE_S:
        # A no-fill is proven only when the book was observed: a gap longer than the shared pricer's 2 s book age
        # (the R17 coverage rule) inside the schedule leaves the no-fill unproven.
        return _unsupported(receipt, "ENTRY_BOOK_GAP")
    receipt.update({"outcome": "NO_FILL", "supported": True, "final_classification": "NO_FILL"})
    return receipt
