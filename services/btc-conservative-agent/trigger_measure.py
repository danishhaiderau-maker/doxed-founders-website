"""Trigger / quote measurement (issues #807, #809). MEASUREMENT ONLY, no behaviour change.

This module is the ONE shared helper for these fields (no per-tile code). Nothing here is read by paper_fill or by any
fill price, limit price or pnl; test_trigger_measure.py asserts that (fill isolation). Pure functions; never raise;
None when an input is missing or not finite.

Exit side (#807)
- trigger_vs_book_bp: the price an exit fired on vs the best level of the 1 s order book stored at the trigger
  (exit_depth_snapshot_signal {"s": sec, "b": [[px, qty], ...], "a": [...]}). LONG exits compare against the best
  bid, SHORT against the best ask. Sign: + = the real book was BETTER for the position than the trigger price (the
  exit fired on a price that looked worse than the market, e.g. a stale ticker BBO).
- trigger_book_age_s: trigger_ts - book "s". The bp value is null (with trigger_vs_book_reason) when the book is older
  than paper_fill.MAX_BOOK_AGE_S (2 s) or newer than the trigger (negative age = clock skew / look-ahead).
- trigger_tape_age_s: time since the last ticker MESSAGE (state bbo_ts) at the trigger. bbo_ts is stamped on every
  WS ticker message and on every REST refresh, NOT when the BBO changes, so this age CANNOT see a frozen price: a
  ticker that repeats the same bid/ask every second shows an age near 0. Frozen-BBO detection is
  monitor_streams.tape_freeze (#808). A negative age is never clamped: it returns None and
  trigger_tape_age_flag="NEGATIVE_AGE".

Entry side (#809)
- entry_quote_fields(decision, ...): the cheap decision quote the entry decision itself saw (ticker-fed state
  bid/ask, its age, WS or REST source, decision signal_ts). Stamped at decision time; no book read, no I/O.
- entry_book_fields(side, decision, book_signal, pricer_signal_ts): computed at tile-row build time from the book
  snapshot already stored with the trade (pricing.depth_snapshot_signal). entry_book_age_s = decision signal_ts - book
  "s" (BookTail.at(t) returns rows with s+1 <= t, so the floor is ~1 s). entry_trigger_vs_book_bp is null above
  MAX_BOOK_AGE_S or on a negative age. Sign (same as exits, + = book better for the position):
  LONG (quote_ask - book_ask) / quote_ask * 1e4; SHORT (book_bid - quote_bid) / quote_bid * 1e4.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

# Same bound the shared pricer uses for a usable book (paper_fill.MAX_BOOK_AGE_S); duplicated as a literal so this
# module never imports the pricer (and the pricer never imports this module).
MAX_BOOK_AGE_S = 2.0
NEGATIVE_AGE = "NEGATIVE_AGE"
QUOTE_SOURCE_WS = "WS"
QUOTE_SOURCE_REST = "REST"
QUOTE_SOURCE_UNKNOWN = "UNKNOWN"

ENTRY_QUOTE_KEYS = ("entry_quote_bid", "entry_quote_ask", "entry_quote_age_s", "entry_quote_age_flag",
                    "entry_quote_source", "entry_quote_bbo_ts", "entry_decision_signal_ts", "entry_decision_recorded_ts")
ENTRY_BOOK_KEYS = ("entry_book_best_bid", "entry_book_best_ask", "entry_book_s", "entry_book_age_s",
                   "entry_book_age_flag", "entry_pricer_signal_ts", "entry_trigger_vs_book_bp",
                   "entry_trigger_vs_book_reason", "entry_measure_reason")
EXIT_KEYS = ("trigger_tape_age_s", "trigger_tape_age_flag", "trigger_book_age_s", "trigger_vs_book_bp",
             "trigger_vs_book_reason")
# Every key this module can emit. None of them may ever reach a fill price, limit price or pnl.
MEASUREMENT_KEYS = ENTRY_QUOTE_KEYS + ENTRY_BOOK_KEYS + EXIT_KEYS


def _f(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _pos(v: Any) -> Optional[float]:
    x = _f(v)
    return x if x is not None and x > 0 else None


def _best(book: Any, key: str) -> Optional[float]:
    if not isinstance(book, Mapping):
        return None
    levels = book.get(key)
    if not isinstance(levels, (list, tuple)) or not levels:
        return None
    top = levels[0]
    return _pos(top[0] if isinstance(top, (list, tuple)) and top else top)


def _side(side: Any) -> Optional[str]:
    s = str(side or "").upper()
    if s in ("LONG", "BUY"):
        return "LONG"
    if s in ("SHORT", "SELL"):
        return "SHORT"
    return None


def age_s(now_ts: Any, then_ts: Any) -> tuple[Optional[float], Optional[str]]:
    """(age, flag). Negative ages are never clamped: (None, NEGATIVE_AGE)."""
    t, b = _f(now_ts), _f(then_ts)
    if t is None or b is None or b <= 0:
        return None, None
    age = t - b
    if age < 0:
        return None, NEGATIVE_AGE
    return round(age, 3), None


def book_age_s(ref_ts: Any, book: Any) -> tuple[Optional[float], Optional[str]]:
    return age_s(ref_ts, book.get("s") if isinstance(book, Mapping) else None)


# ---------------------------------------------------------------- exit side (#807)
def trigger_vs_book_bp(side: Any, trigger_price: Any, book_signal: Any) -> Optional[float]:
    side_u = _side(side)
    trig = _pos(trigger_price)
    if side_u is None or trig is None:
        return None
    touch = _best(book_signal, "b" if side_u == "LONG" else "a")
    if touch is None:
        return None
    sign = 1.0 if side_u == "LONG" else -1.0
    return round((touch / trig - 1.0) * 1e4 * sign, 3)


def trigger_tape_age_s(trigger_ts: Any, bbo_ts: Any) -> Optional[float]:
    """Time since the last ticker MESSAGE at the trigger (cannot see a frozen BBO). None on a negative age."""
    return age_s(trigger_ts, bbo_ts)[0]


def fields(*, side: Any, trigger_price: Any, trigger_ts: Any, bbo_ts: Any, book_signal: Any) -> dict:
    tape_age, tape_flag = age_s(trigger_ts, bbo_ts)
    book_age, book_flag = book_age_s(trigger_ts, book_signal)
    bp = trigger_vs_book_bp(side, trigger_price, book_signal)
    reason = None
    if bp is not None:
        if book_flag:
            bp, reason = None, "BOOK_" + book_flag
        elif book_age is None:
            bp, reason = None, "BOOK_AGE_UNKNOWN"
        elif book_age > MAX_BOOK_AGE_S:
            bp, reason = None, "BOOK_STALE_GT_2S"
    elif not isinstance(book_signal, Mapping):
        reason = "NO_BOOK_SNAPSHOT"
    return {"trigger_tape_age_s": tape_age, "trigger_tape_age_flag": tape_flag,
            "trigger_book_age_s": book_age, "trigger_vs_book_bp": bp, "trigger_vs_book_reason": reason}


# ---------------------------------------------------------------- entry side (#809)
def quote_source(bbo_ts: Any, ws_last_tick: Any, rest_last_tick: Any) -> str:
    """WS when the quote's bbo_ts is the last WS ticker message, REST when it is the last REST refresh."""
    b = _f(bbo_ts)
    if b is None or b <= 0:
        return QUOTE_SOURCE_UNKNOWN
    if _f(ws_last_tick) is not None and abs(b - _f(ws_last_tick)) < 1e-6:
        return QUOTE_SOURCE_WS
    if _f(rest_last_tick) is not None and abs(b - _f(rest_last_tick)) < 1e-6:
        return QUOTE_SOURCE_REST
    return QUOTE_SOURCE_UNKNOWN


def entry_quote_fields(decision: Mapping[str, Any] | None, *, bbo_ts: Any = None, ws_last_tick: Any = None,
                       rest_last_tick: Any = None, recorded_ts: Any = None) -> dict:
    """Cheap decision-quote stamp (no book read, no I/O): the quote the decision itself saw."""
    d = decision if isinstance(decision, Mapping) else {}
    sig = _f(d.get("signal_ts"))
    raw_age = _f(d.get("bbo_age_sec"))
    if raw_age is None and sig is not None:
        raw_age_v, flag = age_s(sig, bbo_ts)
    elif raw_age is not None and raw_age < 0:
        raw_age_v, flag = None, NEGATIVE_AGE
    else:
        raw_age_v, flag = (round(raw_age, 3) if raw_age is not None else None), None
    return {"entry_quote_bid": _pos(d.get("bid")), "entry_quote_ask": _pos(d.get("ask")),
            "entry_quote_age_s": raw_age_v, "entry_quote_age_flag": flag,
            "entry_quote_source": quote_source(bbo_ts, ws_last_tick, rest_last_tick),
            "entry_quote_bbo_ts": _pos(bbo_ts), "entry_decision_signal_ts": sig,
            "entry_decision_recorded_ts": _f(recorded_ts)}


def entry_book_fields(side: Any, decision: Mapping[str, Any] | None, book_signal: Any,
                      pricer_signal_ts: Any = None) -> dict:
    """Entry quote vs the book stored with the trade (pricing.depth_snapshot_signal). Row-build time only."""
    out = {k: None for k in ENTRY_BOOK_KEYS}
    out["entry_pricer_signal_ts"] = _f(pricer_signal_ts)
    d = decision if isinstance(decision, Mapping) else {}
    if not d:
        out["entry_measure_reason"] = "NO_DECISION_RECORD"
    if isinstance(book_signal, Mapping):
        out["entry_book_best_bid"] = _best(book_signal, "b")
        out["entry_book_best_ask"] = _best(book_signal, "a")
        out["entry_book_s"] = _f(book_signal.get("s"))
    sig = _f(d.get("entry_decision_signal_ts")) or _f(d.get("signal_ts"))
    out["entry_book_age_s"], out["entry_book_age_flag"] = book_age_s(sig, book_signal)
    if not d:
        out["entry_trigger_vs_book_reason"] = "NO_DECISION_RECORD"
        return out
    if not isinstance(book_signal, Mapping):
        out["entry_trigger_vs_book_reason"] = "NO_BOOK_SNAPSHOT"
        return out
    side_u = _side(side or d.get("direction"))
    q_bid = _pos(d.get("entry_quote_bid")) or _pos(d.get("bid"))
    q_ask = _pos(d.get("entry_quote_ask")) or _pos(d.get("ask"))
    if side_u is None:
        out["entry_trigger_vs_book_reason"] = "NO_SIDE"
    elif out["entry_book_age_flag"]:
        out["entry_trigger_vs_book_reason"] = "BOOK_" + out["entry_book_age_flag"]
    elif out["entry_book_age_s"] is None:
        out["entry_trigger_vs_book_reason"] = "BOOK_AGE_UNKNOWN"
    elif out["entry_book_age_s"] > MAX_BOOK_AGE_S:
        out["entry_trigger_vs_book_reason"] = "BOOK_STALE_GT_2S"
    elif side_u == "LONG":
        b_ask = out["entry_book_best_ask"]
        if q_ask and b_ask:
            out["entry_trigger_vs_book_bp"] = round((q_ask - b_ask) / q_ask * 1e4, 3)
        else:
            out["entry_trigger_vs_book_reason"] = "NO_QUOTE_OR_BOOK_ASK"
    else:
        b_bid = out["entry_book_best_bid"]
        if q_bid and b_bid:
            out["entry_trigger_vs_book_bp"] = round((b_bid - q_bid) / q_bid * 1e4, 3)
        else:
            out["entry_trigger_vs_book_reason"] = "NO_QUOTE_OR_BOOK_BID"
    return out


def entry_fields(side: Any, decision: Mapping[str, Any] | None, book_signal: Any, pricer_signal_ts: Any = None) -> dict:
    """All entry measurement keys for a row: the stamped quote (or NO_DECISION_RECORD nulls) + the book comparison."""
    d = decision if isinstance(decision, Mapping) else {}
    quote = {k: d.get(k) for k in ENTRY_QUOTE_KEYS}
    if d and quote.get("entry_quote_bid") is None and quote.get("entry_quote_ask") is None:
        # decisions recorded before #809 shipped: the decision's own bid/ask/bbo_age_sec are the same values
        legacy = entry_quote_fields(d)
        quote.update({k: legacy[k] for k in ("entry_quote_bid", "entry_quote_ask", "entry_quote_age_s",
                                             "entry_quote_age_flag", "entry_decision_signal_ts")})
    return {**quote, **entry_book_fields(side, d, book_signal, pricer_signal_ts)}


def live_limit_price_sent(chain: Mapping[str, Any] | None) -> Optional[float]:
    """The live chain's order_price when the entry order is a LIMIT (not STOP LIMIT); else None."""
    ch = chain if isinstance(chain, Mapping) else {}
    otype = str(ch.get("order_type") or "").upper()
    if "LIMIT" not in otype or "STOP" in otype:
        return None
    return _pos(ch.get("order_price"))
