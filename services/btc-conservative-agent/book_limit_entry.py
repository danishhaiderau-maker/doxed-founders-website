"""Research conservative limit entry on the 10-level book (RK R3/R4; replaces evaluate_limit_fill's 1 s pricing).

The CONSERVATIVE protection-replay entry used to be decided and priced by
``research.conservative_limit_fill.evaluate_limit_fill`` on ``market_microstructure_1s`` rows, which R3 forbids.
This module composes only the two shared helpers R4 allows - no pricing or trigger logic of its own:

* trigger: ``quote_trigger.first_level_trigger`` on completed ``bfx_book_10l`` rows.  A buy limit at L is reached
  when the executable ask is at/through L; a sell limit when the bid is.  quote_trigger states that as a TARGET on
  the opposite direction's exit side (LONG entry -> ``first_level_trigger("SHORT", KIND_TARGET, L)`` on the ask;
  SHORT entry -> ``("LONG", KIND_TARGET, L)`` on the bid).
* price: ``paper_fill(direction, qty, trigger_ts, delay_s, book_at, leg="entry", cap_px=L)`` - the shared
  book-walk pricer as a limit-capped IOC sent at the trigger.  A miss (``NO_FILL_IOC_CAP`` / no depth) keeps
  chasing: the next trigger in the same or a later schedule interval is tried.

Countability (R17): a fill is countable only when it is priced from ``BFX_BOOK_10L`` and its send delay is
verified (``paper_fill.delay_stamp``: live-ack pool n >= 10).  Research has no live-ack pool, so the default
1.5 s delay applies and every research fill is ``R17_UNPROVEN_ENTRY`` (reason ``UNVERIFIED_DELAY``): priced on
the book, kept for diagnostics, never counted.  A NO_FILL is countable only when every schedule second had a
complete book row; a book gap inside the schedule makes the no-fill UNSUPPORTED (``ENTRY_BOOK_GAP``).

Known model difference (flagged for RK): a resting maker limit fills AT L, while this IOC walk can fill below a
buy limit (above a sell limit) when a completed row has already crossed it; the price improvement is stamped
(``price_improvement_vs_limit``) on every fill.  The 1 s tape's aggressor-print rule is not reproduced: trade
prints never trigger an entry here, only completed book rows do.
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
EVALUATOR_VERSION = "book10l-ioc-chase-v1"
ENTRY_PRICE_SOURCE_BOOK = "BFX_BOOK_10L"
R17_UNPROVEN_ENTRY = _r17f.R17_UNPROVEN_ENTRY  # the ONE R17 contract module (R4)
REASON_UNVERIFIED_DELAY = _pf.UNVERIFIED_DELAY
REASON_NOT_BOOK_PRICED = "ENTRY_NOT_BOOK_PRICED"
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
        t0, t1, limit = float(interval["start_ts"]) - _EPS, float(interval["end_ts"]), float(interval["limit_price"])
        window = _qt.book_rows_window(t0, t1, book_at, late_grace_s=0.0)
        gaps.extend(int(g) for g in window["gap_seconds"] if isinstance(g, (int, float)))
        rows = window["rows"]
        receipt["evidence_bucket_ids"].append(interval["bucket_id"])
        cursor = t0
        while cursor < t1:
            trigger = _qt.first_level_trigger(_flip(side), _qt.KIND_TARGET, limit, cursor, t1, book_rows=rows)
            if trigger is None:
                break
            fill = _pf.paper_fill(side, qty, trigger["trigger_ts"], delay_s, book_at, leg="entry",
                                  delay_source=delay_source or "default_until_n10", cap_px=limit)
            receipt["attempts"].append({"bucket_id": interval["bucket_id"], "limit_price": limit, **trigger,
                                        "status": fill.get("status"), "fill_ts": fill.get("fill_ts")})
            if fill.get("status") in _FILLED and fill.get("price_source") == ENTRY_PRICE_SOURCE_BOOK:
                decision = apply_quantity_constraints(
                    requested_qty=qty, raw_partial_qty=fill.get("filled_qty"), execution_price=fill.get("price"),
                    accumulated_qty=0, constraints=quantity_constraints, symbol=symbol)
                receipt["quantity_decision"] = decision
                if decision.get("final_classification") == "UNSUPPORTED":
                    return _unsupported(receipt, *(decision.get("reasons") or ["QUANTITY_DECISION_UNSUPPORTED"]))
                if not decision.get("accepted"):
                    cursor = float(trigger["trigger_ts"])
                    continue
                filled = float(decision["accumulated_quantity_after"])
                full = decision["final_classification"] == "FULL_FILL"
                stamp = _pf.delay_stamp(fill.get("delay_source"))
                verified = (fill.get("evidence_status") in (_pf.EVIDENCE_COMPLETE, "INCOMPLETE")
                            and not stamp.get("delay_unverified") and not fill.get("unverified_reason"))
                price = float(fill["price"])
                receipt.update({
                    "outcome": "FILL" if full else "PARTIAL_FILL", "supported": True,
                    "final_classification": "FULL_FILL" if full else "PARTIAL_FILL",
                    "filled_qty": filled, "remaining_qty": round(max(0.0, qty - filled), 12),
                    "fill_price": price, "fill_ts": fill.get("fill_ts"),
                    # The replay starts its exit path at the moment the entry is filled (signal + delay).
                    "trigger_bucket_ts": fill.get("fill_ts"),
                    "chase_bucket_id": interval["bucket_id"], "limit_price": limit, "entry_fill": fill,
                    "entry_trigger": trigger, "delay_s": fill.get("delay_s"),
                    "delay_source": fill.get("delay_source"), "evidence_status": fill.get("evidence_status"),
                    "price_improvement_vs_limit": round((limit - price) if side == "LONG" else (price - limit), 8),
                    "book_gap_seconds": sorted(set(gaps)),
                    "r17_entry_status": None if verified else R17_UNPROVEN_ENTRY,
                    "r17_entry_reason": None if verified else (
                        REASON_UNVERIFIED_DELAY if stamp.get("delay_unverified") else REASON_NOT_BOOK_PRICED),
                })
                return receipt
            cursor = float(trigger["trigger_ts"])
    receipt["book_gap_seconds"] = sorted(set(gaps))
    if _longest_run(receipt["book_gap_seconds"]) > _pf.MAX_BOOK_AGE_S:
        # A no-fill is proven only when the book was observed: a gap longer than the shared pricer's 2 s book age
        # (the R17 coverage rule) inside the schedule leaves the no-fill unproven.
        return _unsupported(receipt, "ENTRY_BOOK_GAP")
    receipt.update({"outcome": "NO_FILL", "supported": True, "final_classification": "NO_FILL"})
    return receipt
