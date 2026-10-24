"""Shared book-priced paper fill (all-tile book-pricing plan, HM-approved 10:25; Danish 09:08 / 10:26).

THE single fill function every tile must use (new-tile rule, CI-enforced):

    paper_fill(side, qty, signal_ts, delay_s, book_at) -> dict

* Source: the Bitfinex 10-level book snapshot (bfx_book_10l, 1 s) at t = signal_ts + delay_s.
  Walks depth for the full qty; insufficient depth -> partial (filled_qty < qty) or no fill.
* Fallback: the WS book top (bfx_book_top) only when the 10-level snapshot is missing.
* Never the ticker and never book_tape_1s / market_microstructure_1s (Strategist 10:37: it lags
  ~1.2 s and overstates fast tiles by ~3 bp). No book -> REJECT_NO_BOOK (no fill).
* Long entries / short exits take the ask; short entries / long exits take the bid.

Every result carries fill_model, fill_model_version, ask_age_ms (book age at fill time), delay_s,
delay_source, book_s and a depth snapshot so the tile-integrity monitor can re-price it.
"""
from __future__ import annotations

import statistics
import threading
from collections import deque
from typing import Any, Callable, Mapping, Optional

FILL_MODEL = "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY"
FILL_MODEL_VERSION = 1
FILL_BASIS_TAKER_WALK = "BOOK10L_TAKER_WALK"
EVIDENCE_COMPLETE = "COMPLETE"  # the only fill_basis a paper_fill result can carry
DEFAULT_DELAY_S = 1.5
MIN_DELAY_S = 0.3
MIN_SAMPLES = 10
MAX_BOOK_AGE_S = 2.0
BANNED_SOURCES = ("ticker", "book_tape_1s", "market_microstructure_1s")


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _walk(levels, qty: float):
    left, cost, filled = qty, 0.0, 0.0
    for lv in levels or ():
        px, sz = _f(lv[0]), _f(lv[1])
        if not px or not sz or sz <= 0:
            continue
        take = min(left, sz)
        cost += take * px
        filled += take
        left -= take
        if left <= 1e-12:
            break
    return (cost / filled if filled > 0 else None), filled


COUNTERS = {"top_only": 0, "top_only_rejected": 0, "reject_no_book": 0}


def _safe(fn, t):
    try:
        return fn(t) if fn else None
    except Exception:  # noqa: BLE001 - a failed book read is a no-book outcome (no fallback price)
        return None


def _snap(book):
    if not book:
        return None
    out = {"s": book.get("s"), "b": list(book.get("b") or [])[:10], "a": list(book.get("a") or [])[:10]}
    if book.get("carried"):
        out["carried"] = True  # the collector carried an unchanged book into this second (visible in fill provenance)
    return out


def taker_side_levels(side: str, leg: str, book: Mapping[str, Any]):
    """Entry LONG / exit SHORT buy at the ask; entry SHORT / exit LONG sell at the bid."""
    buy = (str(side).upper() == "LONG") == (leg == "entry")
    return book.get("a") if buy else book.get("b")


RESIDENT_LEG = "exit_resident"  # REALISM_V2: exchange-resident stop leg (delay 0, exit side of the book)
FORCED_LEG = "exit_forced"      # RK 17:18 (4): a BOT_SENT exit with no fresh book row: first available row, UNVERIFIED
FIRST_ROW_LEGS = (RESIDENT_LEG, FORCED_LEG)
FIRST_ROW_HORIZON_S = 120       # how far after the fill time the first-row search looks (rows are 1 s)
ROW_RULE_LATEST = "LATEST_ROW_AT_OR_BEFORE_FILL_TS"
ROW_RULE_FIRST_AFTER = "FIRST_ROW_AT_OR_AFTER_FILL_TS"


MIN_POOL_N = 10  # RK 23 Oct: pre-declared minimum live-ack pool size before a delay counts as realistic
UNVERIFIED_DELAY = "UNVERIFIED_DELAY"


def delay_stamp(delay_source: Any, pool_ts: Any = None) -> dict:
    """Per-row delay provenance (RK delay-pool conditions): delay_source_kind live_ack | default | resident, pool_n,
    pool_ts and delay_unverified. A pool below MIN_POOL_N (or the 1.5 s default) is UNVERIFIED_DELAY, never
    realistic. The sampler's ``lane_max_n<N>`` / ``lane_q90_n<N>`` label carries N; the F1 live-ack pool supplies pool_ts."""
    import re as _re

    import r17_fields as _r17f  # the ONE r17 label set (R4); local import keeps paper_fill's module body unchanged
    src = str(delay_source or "")
    if src == "EXCHANGE_RESIDENT_NO_DELAY":
        return {"delay_source_kind": "resident", "pool_n": None, "pool_ts": None, "quantile_method": None,
                "delay_unverified": False}
    if src == _r17f.STOP_RULE_NO_POOL:
        # RK 24 Oct: a stop-rule exit (first book row received at/after the trigger) has no pool delay at all.
        return {"delay_source_kind": "stop_rule", "pool_n": None, "pool_ts": None, "quantile_method": None,
                "delay_unverified": False, "delay_flag": None}
    # ONE label set (R4), shared with r17_exclusion._delay_measured and live_ack_pool.CrossLanePool: only a
    # POOLED_LIVE_ACK_CROSS_LANE:<KEY>_<max|q90>_n<N> label is live_ack; anything else (the retired sampler
    # lane_max_n<N>, a pooled default) is "default" and UNVERIFIED_DELAY.
    m = _re.fullmatch(_r17f.MEASURED_DELAY_PATTERN, src)
    tail = _re.search(r"_n(\d+)$", src)
    n = int(m.group(1)) if m else (int(tail.group(1)) if tail and "default" not in src else 0)  # N kept as diagnostic
    kind = "live_ack" if m else "default"
    if not m:
        return {"delay_source_kind": kind, "pool_n": n, "pool_ts": pool_ts, "quantile_method": quantile_method_of(src),
                "delay_unverified": True, "delay_flag": UNVERIFIED_DELAY}
    return {"delay_source_kind": kind, "pool_n": n, "pool_ts": pool_ts, "quantile_method": quantile_method_of(src),
            "delay_unverified": n < MIN_POOL_N, "delay_flag": UNVERIFIED_DELAY if n < MIN_POOL_N else None}


def first_row_at_or_after(book_at, t: float, horizon_s: float = FIRST_ROW_HORIZON_S):
    """The FIRST 1 s book row whose second contains or follows ``t`` (row s with t in [s, s + 1), or later; available
    at s + 1), within ``horizon_s``; None if no such row exists yet. Never a row completed before the trigger
    (HM M2 / RK 17:24). A trigger exactly on a second boundary t == s belongs to row s (its own second)."""
    import math
    t = float(t)
    first_s = math.floor(t + 1e-9)
    for k in range(int(horizon_s) + 1):
        b = _safe(book_at, float(first_s + 1 + k))
        if b and _f(b.get("s")) is not None and float(b["s"]) >= first_s:
            return b
    return None


BOOK_RECEIVE_BEFORE_TRIGGER = "BOOK_RECEIVE_BEFORE_TRIGGER"   # RK 24 Oct (3): FIRST_AFTER row received before the target
BOOK_FIRST_AFTER_NOT_FIRST = "BOOK_FIRST_AFTER_NOT_FIRST"     # RK 24 Oct (3): an earlier row could have been the first
FIRST_AFTER_PROBE_STEP_S = 0.25


def _book_rows_near(book_at, lo_s: int, hi_s: int) -> list:
    """Every book row with lo_s <= s <= hi_s, ordered by s. Uses the source's own row list when it exposes one
    (BookTail.rows_between / book10l_at.rows_between: exact); otherwise probes book_at every 0.25 s (test doubles)."""
    rows_fn = getattr(book_at, "rows_between", None)
    if callable(rows_fn):
        try:
            return [dict(r) for r in (rows_fn(lo_s, hi_s) or []) if r and lo_s <= int(r["s"]) <= hi_s]
        except Exception:  # noqa: BLE001
            return []
    seen: dict = {}
    n = int((hi_s + 3 - lo_s) / FIRST_AFTER_PROBE_STEP_S) + 1
    for k in range(n):
        b = _safe(book_at, lo_s + k * FIRST_AFTER_PROBE_STEP_S)
        if b and _f(b.get("s")) is not None and lo_s <= int(float(b["s"])) <= hi_s:
            seen.setdefault(int(float(b["s"])), b)
    return [seen[k] for k in sorted(seen)]


def _rx(row):
    v = _f((row or {}).get("receive_ts"))
    return v if v and v > 0 and str((row or {}).get("receive_ts_source") or "") == RECEIVE_TS_MEASURED else None


def first_row_received_at_or_after(book_at, t: float, horizon_s: float = FIRST_ROW_HORIZON_S):
    """RK 24 Oct (3) FIRST_AFTER: the FIRST book row whose MEASURED receive_ts >= t (t = trigger, or trigger + delay
    for a delayed leg), searching rows s in [floor(t) - 2, floor(t) + horizon_s]. No other row may be picked. When
    receive times are missing the row is chosen by second (first_row_at_or_after) and the proof fails closed later.
    Returns a copy of the row with ``first_after_unproven`` = reason (or None) - set when any earlier candidate row
    has no measured receive_ts (it could have been the first) - or None if no row exists yet."""
    import math
    t = float(t)
    fs = math.floor(t + 1e-9)
    rows = _book_rows_near(book_at, fs - 2, fs + int(horizon_s))
    if not rows:
        return None
    gap = False
    for r in rows:
        rx = _rx(r)
        if rx is None:
            if int(float(r["s"])) >= fs:
                return {**r, "first_after_unproven": BOOK_RECEIVE_TS_MISSING}  # cannot prove which row was first
            gap = True  # a pre-trigger-second row with unknown receive time may have been received after t
            continue
        if rx >= t - 1e-9:
            return {**r, "first_after_unproven": BOOK_FIRST_AFTER_NOT_FIRST if gap else None}
    return None


def first_after_proof(book, target_ts: float, fill_ts: float) -> dict:
    """RK 24 Oct (3), the ONE FIRST_AFTER check (R4): countable only if the walked row has a measured receive_ts,
    target_ts <= receive_ts <= fill_ts, and it was proven the FIRST row received at/after target_ts."""
    rx = _rx(book)
    if rx is None:
        return {"first_after_ok": False, "first_after_reason": BOOK_RECEIVE_TS_MISSING}
    if rx < float(target_ts) - 1e-9:
        return {"first_after_ok": False, "first_after_reason": BOOK_RECEIVE_BEFORE_TRIGGER}
    if rx > float(fill_ts) + 1e-9:
        return {"first_after_ok": False, "first_after_reason": BOOK_RECEIVE_AFTER_FILL}
    why = (book or {}).get("first_after_unproven")
    return {"first_after_ok": not why, "first_after_reason": why or None}


DEPTH_SHORT_PARTIAL = "DEPTH_SHORT_PARTIAL"  # HM C7: a first-row walk the visible 10 levels could not fill

# RK/HM #851 (24 Oct): the no-look-ahead proof is the collector's MEASURED per-row receive time (WS receipt of the last
# book frame in the row). A row without it (written before #851) or with any other source is non-countable; the
# bucket + write-lag estimate may only ever appear as the diagnostic label INFERRED_BUCKET_LAG, never countable.
RECEIVE_TS_MEASURED = "WS_FRAME"
RECEIVE_TS_INFERRED = "INFERRED_BUCKET_LAG"  # diagnostic only, always non-countable
RECEIVE_TS_MISSING = "MISSING"
BOOK_RECEIVE_TS_MISSING = "BOOK_RECEIVE_TS_MISSING"
BOOK_RECEIVE_AFTER_FILL = "BOOK_RECEIVE_AFTER_FILL"


def book_receive_proof(book, t: float, rule: str) -> dict:
    """ONE shared check (R4) for every book walk: the walked row's measured receive time and whether it proves the
    fill used no look-ahead. LATEST rule (bot-sent fills): receive_ts <= fill time t. FIRST_AFTER rule (exchange-
    resident stop / forced exit, HM M2 / RK 17:24 / RK 24 Oct (3)): first_after_proof - target <= receive_ts <= fill
    time and the row is the FIRST received at/after the target. ``book_receive_unverified`` is the reason (or None).

    receive_ts <= fill_ts holds BY CONSTRUCTION on every countable row (HM 02:55 doc nit): LATEST rows with
    receive_ts > fill_ts are refused (BOOK_RECEIVE_AFTER_FILL); FIRST_AFTER sets fill_ts = the row's own receipt.
    ``book_receive_age_ms`` (HM 02:55 nit) is the price age measured from that RECEIPT: (fill_ts - receive_ts) ms;
    None when the receipt is not measured (the by-second book_age_ms / price_age_s stay as the bucket age)."""
    rx = _f((book or {}).get("receive_ts"))
    rx = rx if rx and rx > 0 else None
    src = str((book or {}).get("receive_ts_source") or "") if rx else RECEIVE_TS_MISSING
    reason = None
    if src != RECEIVE_TS_MEASURED:  # missing, unlabelled or INFERRED_BUCKET_LAG: never countable
        reason = BOOK_RECEIVE_TS_MISSING
    elif rule == ROW_RULE_LATEST and rx > float(t) + 1e-9:
        reason = BOOK_RECEIVE_AFTER_FILL
    elif rule == ROW_RULE_FIRST_AFTER:
        # RK 24 Oct (3): t is the FIRST_AFTER fill time (the row's receipt); the target is stamped on the row.
        reason = first_after_proof(book, (book or {}).get("first_after_target_ts", t), t)["first_after_reason"]
    mono = _f((book or {}).get("receive_mono"))
    age = round((float(t) - rx) * 1000.0, 3) if rx and src == RECEIVE_TS_MEASURED else None
    return {"book_receive_ts": rx, "book_receive_mono": mono if rx else None,
            "book_receive_ts_source": src or RECEIVE_TS_MISSING, "book_receive_unverified": reason,
            "book_receive_age_ms": age}


def _paper_fill_core(side: str, qty: float, signal_ts: float, delay_s: Optional[float],
               book_at: Callable[[float], Optional[Mapping[str, Any]]], *, leg: str = "entry",
               delay_source: str = "lane_p50", top_at: Optional[Callable[[float], Optional[tuple]]] = None,
               cap_px: Optional[float] = None) -> dict:
    """book_at(t) -> {"s": sec, "b": [[px, sz]...], "a": [...]} for the latest snapshot at or before t."""
    # REALISM_V2: an exchange-resident stop order is already on the exchange; it fills at its trigger instant (no
    # bot send delay). Every other leg keeps the MIN_DELAY_S floor.
    resident = leg == RESIDENT_LEG
    d = 0.0 if resident else max(MIN_DELAY_S, _f(delay_s) or DEFAULT_DELAY_S)
    t = float(signal_ts) + d
    q = _f(qty) or 0.0
    base = {"fill_model": FILL_MODEL, "fill_model_version": FILL_MODEL_VERSION, "delay_s": round(d, 3),
            "delay_source": delay_source if (delay_s or resident) else "default_until_n10", "fill_ts": t, "leg": leg, "qty": q,
            "signal_ts": float(signal_ts), "side": str(side).upper()}
    sig_book = _safe(book_at, float(signal_ts))
    base["depth_snapshot_signal"] = _snap(sig_book)
    if leg in FIRST_ROW_LEGS:
        # RK 17:18 (2)/(4): the resident stop (and a forced no-book exit) walks the FIRST row completed at or after the
        # fill time - never the pre-trigger row. Full size, price age recorded; no ticker / top fallback, no penalty.
        # RK 24 Oct (3): the row is the FIRST one RECEIVED at/after t (trigger, or trigger + delay); the fill time
        # is that row's measured receipt (receive_ts <= fill_ts), the target is kept as fill_target_ts.
        book = first_row_received_at_or_after(book_at, t)
        if book is None:
            # no row received at/after t yet: the by-second row is still priced, but first_after_proof fails it closed
            legacy = first_row_at_or_after(book_at, t)
            book = {**legacy, "first_after_unproven": BOOK_FIRST_AFTER_NOT_FIRST} if legacy else None
        base["book_row_rule"] = ROW_RULE_FIRST_AFTER
        base["fill_target_ts"] = t
        if not book:
            COUNTERS["reject_no_book"] += 1
            return {**base, "status": "REJECT_NO_BOOK", "price": None, "filled_qty": 0.0, "price_source": None}
        book["first_after_target_ts"] = t
        frx = _rx(book)
        if frx is not None and frx >= t:
            base["fill_ts"] = frx
        ready = float(book["s"]) + 1.0
        price_age = round(ready - t, 3)
        px, filled = _walk(taker_side_levels(side, leg, book), q)
        out = {**base, "book_s": int(book["s"]), "price_age_s": price_age,
               "price_stale": price_age > MAX_BOOK_AGE_S, "depth_snapshot_fill": _snap(book),
               "book_age_ms": round((t - float(book["s"])) * 1000), "ask_age_ms": round((t - float(book["s"])) * 1000),
               **book_receive_proof(book, base["fill_ts"], ROW_RULE_FIRST_AFTER)}
        if leg == FORCED_LEG:
            out["unverified_reason"] = "FORCED_NO_BOOK"
        elif out["price_stale"]:
            out["unverified_reason"] = "STALE_BOOK"
        if px is None:
            return {**out, "status": "NO_FILL_NO_DEPTH", "price": None, "filled_qty": 0.0}
        if filled + 1e-12 < q:
            # HM C7: the 10-level row is too shallow for the open qty. The exchange would walk deeper; the paper price
            # covers only the visible depth -> UNVERIFIED (depth short), never COMPLETE.
            out["depth_short_qty"] = round(q - filled, 8)
            out["unverified_reason"] = out.get("unverified_reason") or DEPTH_SHORT_PARTIAL
            out["depth_short"] = True
        if out["book_receive_unverified"] and not out.get("unverified_reason"):
            out["unverified_reason"] = out["book_receive_unverified"]  # RK/HM #851: no measured receive -> UNVERIFIED
        return {**out, "status": "FILLED" if filled + 1e-12 >= q else "PARTIAL", "price": px,
                "filled_qty": round(filled, 8), "price_source": "BFX_BOOK_10L"}
    base["book_row_rule"] = ROW_RULE_LATEST
    book = _safe(book_at, t)
    if book and _f(book.get("s")) is not None and t - float(book["s"]) <= MAX_BOOK_AGE_S:
        levels = taker_side_levels(side, leg, book)
        if cap_px is not None:
            # IOC limit: only levels at or inside the cap can fill (buy <= cap, sell >= cap).
            buy = (str(side).upper() == "LONG") == (leg == "entry")
            levels = [lv for lv in levels or () if _f(lv[0]) is not None
                      and ((_f(lv[0]) <= cap_px + 1e-9) if buy else (_f(lv[0]) >= cap_px - 1e-9))]
        px, filled = _walk(levels, q)
        rxp = book_receive_proof(book, t, ROW_RULE_LATEST)
        if px is None:
            st = "NO_FILL_IOC_CAP" if cap_px is not None and taker_side_levels(side, leg, book) else "NO_FILL_NO_DEPTH"
            return {**base, "status": st, "cap_px": cap_px, "depth_snapshot_fill": _snap(book), "price": None, "filled_qty": 0.0,
                    "book_s": int(book["s"]), "book_age_ms": round((t - float(book["s"])) * 1000), **rxp}
        return {**base, "status": "FILLED" if filled + 1e-12 >= q else "PARTIAL", "price": px, **rxp,
                **({"unverified_reason": rxp["book_receive_unverified"]} if rxp["book_receive_unverified"] else {}),
                "filled_qty": round(filled, 8), "book_s": int(book["s"]),
                "book_age_ms": round((t - float(book["s"])) * 1000),
                "ask_age_ms": round((t - float(book["s"])) * 1000),
                # R17 contract: the walked row's age at the model fill time (s), and the stale flag (> 2 s refused above)
                "price_age_s": round(t - float(book["s"]), 3), "price_stale": False,
                "depth_snapshot_fill": _snap(book), "price_source": "BFX_BOOK_10L"}
    if top_at is not None:
        # HM 12:20: top-of-book fallback fills at most the top size (PARTIAL), else rejects; counted.
        top = _safe(top_at, t)
        if top and len(top) >= 5 and all(top[:5]) and t - top[4] <= MAX_BOOK_AGE_S:
            bid, bid_sz, ask, ask_sz, ts = top[:5]
            buy = (str(side).upper() == "LONG") == (leg == "entry")
            px, size = (ask, ask_sz) if buy else (bid, bid_sz)
            filled = min(q, float(size))
            COUNTERS["top_only"] += 1
            return {**base, "status": "FILLED" if filled + 1e-12 >= q else "PARTIAL_TOP_ONLY", "price": px,
                    "filled_qty": round(filled, 8), "book_s": None, "book_age_ms": round((t - ts) * 1000),
                    "ask_age_ms": round((t - ts) * 1000), "depth_snapshot_fill": None,
                    "price_source": "BFX_BOOK_TOP_WS"}
        COUNTERS["top_only_rejected"] += 1
    COUNTERS["reject_no_book"] += 1
    return {**base, "status": "REJECT_NO_BOOK", "price": None, "filled_qty": 0.0, "price_source": None}


def paper_fill(side: str, qty: float, signal_ts: float, delay_s: Optional[float],
               book_at: Callable[[float], Optional[Mapping[str, Any]]], *, leg: str = "entry",
               delay_source: str = "lane_p50", top_at: Optional[Callable[[float], Optional[tuple]]] = None,
               cap_px: Optional[float] = None) -> dict:
    """Shared pricer; every result carries an explicit ``evidence_status``: COMPLETE only for a fully filled
    book-walk (status FILLED, price from the 10-level book). Anything else is never COMPLETE. NOTE: bot.py
    ``_paper_result_as_sim`` deliberately re-labels a PARTIAL *entry* COMPLETE (position sized to filled_qty)."""
    out = _paper_fill_core(side, qty, signal_ts, delay_s, book_at, leg=leg, delay_source=delay_source,
                           top_at=top_at, cap_px=cap_px)
    out.update(delay_stamp(out.get("delay_source")))
    if out.get("unverified_reason") and out.get("price") is not None:
        out["evidence_status"] = "UNVERIFIED"  # priced and kept, never counted as realistic (RK 17:18 (4))
    elif out.get("status") == "FILLED" and out.get("price_source") == "BFX_BOOK_10L" and out.get("price") is not None:
        out["evidence_status"] = EVIDENCE_COMPLETE
    elif out.get("status") in ("PARTIAL", "PARTIAL_TOP_ONLY") or out.get("filled_qty"):
        out["evidence_status"] = "INCOMPLETE"
    else:
        out["evidence_status"] = "NO_FILL"
    return out


class BookTail:
    """book_at(t) over the collector's bfx_book_10l_1s.jsonl (rows {"s", "b", "a", "receive_ts", ...}).

    A row for second s is complete only at s + 1, so at(t) returns the latest row with s + 1 <= t AND a measured
    receive_ts <= t (RK/HM #851: no look-ahead proven per row). A row with no receive_ts (pre-#851) is still returned,
    labelled receive_ts_source MISSING, and the pricer makes the fill non-countable.

    CPU fix (RK PASS 24 Oct, RCA 21:30 AEDT): the file is read INCREMENTALLY by byte offset. Only complete lines are
    parsed; a trailing partial line is held until its newline arrives. A reload happens only when the file identity
    (st_ino, st_size, st_mtime_ns) changes. Size is part of the key, so a same-mtime append is never missed. A shrink,
    rotation or inode change is a full reset: re-read the last tail_bytes and drop the first, possibly cut, line.
    at() walks back from the newest row (normally 1 step) and copies one row only. The selection rule is unchanged.
    """

    def __init__(self, path: str, keep: int = 600, tail_bytes: int = 512 * 1024) -> None:
        self.path, self.keep, self.tail_bytes = path, keep, tail_bytes
        self._lock = threading.Lock()
        self._rows: dict = {}
        self._secs: list = []          # sorted keys of _rows
        self._max_s = None
        self._ident = None             # (st_ino, st_size, st_mtime_ns) last synced
        self._ino = None
        self._offset = 0               # bytes consumed (complete lines + held partial)
        self._partial = b""
        self.refresh_count = 0         # syncs that actually read bytes (telemetry: book_refreshes_in_scan)
        self.refresh_ms_total = 0.0    # wall ms spent in those reads + parses (telemetry: book_refresh_ms)
        self.reset_count = 0

    # ---------------------------------------------------------------- ingest
    def _ingest(self, line: bytes) -> None:
        import json
        try:
            r = json.loads(line)
            sec = int(r["s"])
        except (ValueError, KeyError, TypeError):
            return
        rx = _f(r.get("receive_ts"))
        if sec not in self._rows:
            import bisect
            if not self._secs or sec > self._secs[-1]:
                self._secs.append(sec)
            else:
                bisect.insort(self._secs, sec)
        self._rows[sec] = {"s": sec, "b": r.get("b") or [], "a": r.get("a") or [],
                           **({"carried": True} if r.get("carried") else {}),
                           "receive_ts": rx if rx and rx > 0 else None, "receive_mono": _f(r.get("receive_mono")),
                           "receive_ts_source": (str(r.get("receive_ts_source") or "")
                                                 if rx and rx > 0 else RECEIVE_TS_MISSING)}

    def _reset(self) -> None:
        self._rows, self._secs, self._max_s = {}, [], None
        self._offset, self._partial, self._ident, self._ino = 0, b"", None, None
        self.reset_count += 1

    def _sync(self) -> None:
        """Bring the cache up to the file. Cheap no-op (one stat) when the identity is unchanged."""
        import os
        import time as _t
        try:
            st = os.stat(self.path)
        except OSError:
            return
        ident = (st.st_ino, st.st_size, st.st_mtime_ns)
        if ident == self._ident:
            return
        t0 = _t.perf_counter()
        first = False
        rewritten = (self._ident is not None and st.st_size == self._offset and st.st_mtime_ns != self._ident[2])
        if self._ino is None or st.st_ino != self._ino or st.st_size < self._offset or rewritten:
            if self._ino is not None:
                self._reset()
            self._ino = st.st_ino
            self._offset = max(0, st.st_size - self.tail_bytes)
            self._partial = b""
            first = self._offset > 0  # the first line of a mid-file read may be cut: drop it (as before)
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read(max(0, st.st_size - self._offset))
        except OSError:
            return
        self._offset += len(chunk)
        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()  # b"" when data ends with a newline; else the held partial line
        if first and lines:
            lines = lines[1:]
        for line in lines:
            if line.strip():
                self._ingest(line)
        if len(self._secs) > self.keep:
            drop = self._secs[:-self.keep]
            self._secs = self._secs[-self.keep:]
            for sec in drop:
                del self._rows[sec]
        self._max_s = self._secs[-1] if self._secs else None
        self._ident = (st.st_ino, self._offset, st.st_mtime_ns) if self._offset == st.st_size else None
        self.refresh_count += 1
        self.refresh_ms_total += (_t.perf_counter() - t0) * 1000.0

    # kept for callers/tests that poke the old name
    _refresh = _sync

    # ---------------------------------------------------------------- queries
    def newest_key(self):
        """(newest s, newest receive_ts, file size) after a sync: the memo key of book_exit_price (RK (4))."""
        with self._lock:
            self._sync()
            if self._max_s is None:
                return None
            r = self._rows[self._max_s]
            return (self._max_s, r.get("receive_ts"), self._offset)

    def newest_eligible(self, t: float) -> bool:
        """True when at(t) would return the newest cached row (no sync)."""
        with self._lock:
            if self._max_s is None:
                return False
            r = self._rows[self._max_s]
            return self._max_s + 1 <= t and (r.get("receive_ts") is None or r["receive_ts"] <= t)

    def at(self, t: float):
        with self._lock:
            self._sync()
            rows = self._rows
            for sec in reversed(self._secs):
                if sec + 1 <= t:
                    r = rows[sec]
                    if r.get("receive_ts") is None or r["receive_ts"] <= t:
                        return dict(r)
            return None

    def rows_between(self, lo_s: int, hi_s: int) -> list:
        """RK 24 Oct (3): every cached row with lo_s <= s <= hi_s (ordered), for the FIRST_AFTER receive-time search."""
        import bisect
        with self._lock:
            self._sync()
            i, j = bisect.bisect_left(self._secs, lo_s), bisect.bisect_right(self._secs, hi_s)
            return [dict(self._rows[k]) for k in self._secs[i:j]]

    def missing_seconds(self, start: float, end: float) -> int:
        """Whole seconds in [floor(start), floor(end)] with no book row (read-only; syncs the tail first)."""
        a, b = int(start), int(end)
        if b < a:
            return 0
        with self._lock:
            self._sync()
            return sum(1 for sec in range(a, b + 1) if sec not in self._rows)


def book_tail_stats() -> dict:
    """Process-wide BookTail counters (telemetry for per-exit stamps)."""
    bt = _BOOK_TAIL
    if bt is None:
        return {"refresh_count": 0, "refresh_ms_total": 0.0, "reset_count": 0}
    return {"refresh_count": bt.refresh_count, "refresh_ms_total": bt.refresh_ms_total, "reset_count": bt.reset_count}


BOOK_FILE = "bfx_book_10l_1s.jsonl"
_BOOK_TAIL: Optional[BookTail] = None


def book10l_at(t: float):
    """THE book source for every fill site (HM 13:20): the collector's 10-level book in the
    working directory (/app/data/runtime on Fly), latest complete second at or before t."""
    global _BOOK_TAIL
    if _BOOK_TAIL is None:
        import os
        _BOOK_TAIL = BookTail(os.path.join(os.getcwd(), BOOK_FILE))
    return _BOOK_TAIL.at(t)


def _book10l_rows_between(lo_s: int, hi_s: int) -> list:
    book10l_at(float(lo_s))  # makes sure the tail exists
    return _BOOK_TAIL.rows_between(int(lo_s), int(hi_s)) if _BOOK_TAIL is not None else []


book10l_at.rows_between = _book10l_rows_between  # RK 24 Oct (3): exact row list for FIRST_AFTER


def _ensure_book_tail():
    global _BOOK_TAIL
    if _BOOK_TAIL is None:
        import os
        _BOOK_TAIL = BookTail(os.path.join(os.getcwd(), BOOK_FILE))
    return _BOOK_TAIL


def _book10l_newest_key():
    _ensure_book_tail()
    return _BOOK_TAIL.newest_key() if _BOOK_TAIL is not None else None


def _book10l_newest_eligible(t: float) -> bool:
    return _BOOK_TAIL.newest_eligible(float(t)) if _BOOK_TAIL is not None else False


book10l_at.newest_key = _book10l_newest_key          # RK (4): book_exit_price memo key
book10l_at.newest_eligible = _book10l_newest_eligible


def book10l_missing_seconds(start: float, end: float):
    """Seconds without a book row in [start, end] (None if the book source is unavailable). Diagnostic only."""
    try:
        book10l_at(float(end))  # makes sure the tail exists
        return _BOOK_TAIL.missing_seconds(float(start), float(end)) if _BOOK_TAIL is not None else None
    except Exception:  # noqa: BLE001
        return None


DELAYS = None  # set by the bot: DelaySampler of measured live delays (empty while live is OFF)


QUANTILE_SWITCH_N = 20  # pre-declared (AMEND2 B1): MAX of the pool until n >= 20, then q0.90
QM_DEFAULT, QM_MAX, QM_Q90 = "DEFAULT", "MAX", "Q90"


def quantile_nearest_rank(sorted_xs, q: float) -> float:
    """Nearest-rank quantile (no interpolation): the ceil(q * n)-th smallest value."""
    import math
    n = len(sorted_xs)
    return float(sorted_xs[max(0, min(n - 1, int(math.ceil(q * n)) - 1))])


def quantile_method_of(delay_source: Any) -> str:
    s = str(delay_source or "").lower()
    if "_q90_" in s:
        return QM_Q90
    if "_max_" in s:
        return QM_MAX
    return QM_DEFAULT


DELAY_LEGS = ("entry", "exit", "stop")  # F1 legs: entry signal->ack, bot-sent exit trigger->ack, stop fill->confirmed
PRICED_DELAY_LEGS = ("entry", "exit")     # the stop-placement leg is stamped only (it never times a fill)
POOL_SCHEMA = "paper_delay_pool_v1"


class DelaySampler:
    """F1 pool of MEASURED LIVE ACK delays per lane and leg (entry: Fly signal -> exchange ack; exit: paper exit
    trigger -> exit ack; stop: entry fill -> STOP_CONFIRMED, stamped only). Live acks are the only samples (never a
    paper signal->fill). Capped at ``maxlen`` per (lane, leg), one sample per trade and leg, persisted per DATA epoch
    (to_json / load) and cleared at reset (clear)."""

    def __init__(self, maxlen: int = 200) -> None:
        self._lock = threading.Lock()
        self._d: dict = {}
        self._seen: dict = {}
        self.maxlen = maxlen

    def add(self, lane: str, leg: str, delay_s: float, *, measured_ts: Any = None, trade_id: Any = None) -> Optional[float]:
        v = _f(delay_s)
        if v is None or v < 0 or v > 30:
            return None
        key = (str(lane or "").upper(), str(leg))
        with self._lock:
            if trade_id:
                seen = self._seen.setdefault(key, deque(maxlen=self.maxlen * 2))
                if str(trade_id) in seen:
                    return None
                seen.append(str(trade_id))
            self._d.setdefault(key, deque(maxlen=self.maxlen)).append((v, _f(measured_ts)))
        return v

    def has_trade(self, lane: str, leg: str, trade_id: Any) -> bool:
        with self._lock:
            return str(trade_id) in (self._seen.get((str(lane or "").upper(), str(leg))) or ())

    def _xs(self, lane: str, leg: str) -> list:
        with self._lock:
            return list(self._d.get((str(lane or "").upper(), str(leg))) or ())

    def delay(self, lane: str, leg: str = "entry") -> tuple:
        """RK 17:24 / AMEND2 B1: n < 10 -> default (UNVERIFIED_DELAY); 10 <= n < 20 -> MAX of the pool; n >= 20 ->
        q0.90 (nearest-rank). The source string carries the method + n (quantile_method_of() parses it)."""
        xs = sorted(v for v, _ in self._xs(lane, leg))
        if len(xs) < MIN_SAMPLES:
            return DEFAULT_DELAY_S, "default_until_n10"
        if len(xs) < QUANTILE_SWITCH_N:
            return max(MIN_DELAY_S, xs[-1]), f"lane_max_n{len(xs)}"
        return max(MIN_DELAY_S, quantile_nearest_rank(xs, 0.90)), f"lane_q90_n{len(xs)}"

    def pool_ts(self, lane: str, leg: str) -> Optional[float]:
        """The measured ts of the newest live ack in the pool (None for an empty pool)."""
        ts = [t for _, t in self._xs(lane, leg) if t]
        return max(ts) if ts else None

    def describe(self, lane: str, leg: str = "entry") -> dict:
        """delay_s, delay_source plus the per-row delay stamp (pool_n, pool_ts, quantile_method, delay_unverified,
        delay_flag) for this lane/leg."""
        d, src = self.delay(lane, leg)
        return {"delay_s": d, "delay_source": src, **delay_stamp(src, self.pool_ts(lane, leg))}

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._seen.clear()

    def to_json(self, epoch_id: Any) -> dict:
        with self._lock:
            return {"schema": POOL_SCHEMA, "epoch_id": epoch_id, "maxlen": self.maxlen,
                    "pools": {f"{k[0]}|{k[1]}": [[v, t] for v, t in dq] for k, dq in self._d.items()},
                    "seen": {f"{k[0]}|{k[1]}": list(dq) for k, dq in self._seen.items()}}

    def load(self, payload: Any, epoch_id: Any) -> bool:
        """Restore a persisted pool. A pool from another DATA epoch (or another schema) is ignored: per-epoch."""
        if not isinstance(payload, Mapping) or payload.get("schema") != POOL_SCHEMA \
                or payload.get("epoch_id") != epoch_id:
            return False
        with self._lock:
            self._d.clear()
            self._seen.clear()
            for k, rows in (payload.get("pools") or {}).items():
                lane, _, leg = str(k).partition("|")
                dq = self._d.setdefault((lane, leg), deque(maxlen=self.maxlen))
                for r in rows or ():
                    v = _f(r[0]) if isinstance(r, (list, tuple)) and r else None
                    if v is not None and 0 <= v <= 30:
                        dq.append((v, _f(r[1]) if len(r) > 1 else None))
            for k, ids in (payload.get("seen") or {}).items():
                lane, _, leg = str(k).partition("|")
                self._seen[(lane, leg)] = deque((str(i) for i in ids or ()), maxlen=self.maxlen * 2)
        return True

    def snapshot(self) -> dict:
        with self._lock:
            items = {k: [v for v, _ in dq] for k, dq in self._d.items()}
        return {f"{k[0]}|{k[1]}": {"n": len(v), "p50": statistics.median(v) if v else None,
                                    "pool_ts": self.pool_ts(*k)} for k, v in items.items()}


# ---------------------------------------------------------------- measured slippage (HM 17 Oct)
# Pure measurement over RECORDED values (the fill price the row booked and the depth snapshots stored with it).
# Nothing here prices, re-walks or writes a fill. Sign conventions:
#   walk_*  : COST of walking the book vs the UNCAPPED best touch of the same snapshot, >= 0, positive = worse.
#   drift_* : signed, positive = favourable to the position (negative = adverse), the same sign as the
#             existing trigger_slippage_bp. Buys (entry LONG / exit SHORT) are hurt by a higher price.
IOC_CAP_BP = 5.0  # same as tile_fill.IOC_CAP_BP: only the levels the capped IOC could walk count as walked


def _is_buy(side: str, leg: str) -> bool:
    return (str(side).upper() == "LONG") == (leg == "entry")


def best_touch(side: str, leg: str, snapshot) -> Optional[float]:
    """Best taker-side level of a depth snapshot (uncapped), or None without a usable snapshot."""
    if not isinstance(snapshot, Mapping):
        return None
    for lv in (taker_side_levels(side, leg, snapshot) or ()):
        try:
            px, sz = _f(lv[0]), _f(lv[1])
        except (TypeError, IndexError):
            continue
        if px and px > 0 and sz and sz > 0:
            return px
    return None


def _bp(diff: float, ref: float) -> float:
    return round(diff / ref * 1e4, 4) + 0.0  # + 0.0 turns -0.0 into 0.0


def leg_slippage(side: str, leg: str, qty, fill_price, *, fill_snapshot=None, signal_snapshot=None,
                 trigger_price=None, cap_bp: float = IOC_CAP_BP) -> dict:
    """Slippage of one leg from its recorded fill price. Every number is None when its inputs are missing
    (never 0.0): a leg without a snapshot is unmeasured, not free.
      walk_usd/walk_bp           recorded fill vs uncapped best touch of the fill snapshot (cost, >= 0)
      touch_drift_usd/bp         recorded fill vs best touch of the signal snapshot (delay drift, signed)
      trigger_drift_usd/bp       recorded fill vs trigger price (exits only, signed, the exit headline)
      trigger_overshoot_bp       trigger price vs best touch of the signal snapshot (rule level vs book, signed)
      touch_px                   the uncapped best touch the walk is measured against
      walk_within_cap            walk_bp <= cap_bp (the cap only ever limits the levels walked)"""
    out = {"walk_usd": None, "walk_bp": None, "touch_drift_usd": None, "touch_drift_bp": None,
           "trigger_drift_usd": None, "trigger_drift_bp": None, "trigger_overshoot_bp": None,
           "walk_within_cap": None, "touch_px": None}
    fill, q = _f(fill_price), _f(qty)
    if not fill or fill <= 0 or not q or q <= 0:
        return out
    buy = _is_buy(side, leg)
    fav = -1.0 if buy else 1.0  # +1: a higher price helps (sell), -1: a higher price hurts (buy)
    touch = best_touch(side, leg, fill_snapshot)
    if touch:
        out["touch_px"] = touch
        cost = -fav * (fill - touch)
        out["walk_usd"], out["walk_bp"] = round(cost * q, 6) + 0.0, _bp(cost, touch)
        out["walk_within_cap"] = out["walk_bp"] <= cap_bp + 1e-6
    sig = best_touch(side, leg, signal_snapshot)
    if sig:
        out["touch_drift_usd"] = round(fav * (fill - sig) * q, 6) + 0.0
        out["touch_drift_bp"] = _bp(fav * (fill - sig), sig)
    trig = _f(trigger_price)
    if leg == "exit" and trig and trig > 0:
        out["trigger_drift_usd"] = round(fav * (fill - trig) * q, 6) + 0.0
        out["trigger_drift_bp"] = _bp(fav * (fill - trig), trig)
        if sig:
            out["trigger_overshoot_bp"] = _bp(fav * (trig - sig), sig)
    return out


def aggregate_legs(legs) -> dict:
    """Sum / quantity-weight per-leg slippage over a close. One unmeasured leg makes that figure None."""
    legs = [x for x in (legs or ()) if isinstance(x, Mapping)]
    out = {"legs": len(legs), "legs_unmeasured": 0}
    qty = [_f(x.get("qty")) or 0.0 for x in legs]
    for key, weighted in (("walk_usd", False), ("walk_bp", True), ("touch_drift_usd", False),
                          ("touch_drift_bp", True), ("trigger_drift_usd", False), ("trigger_drift_bp", True),
                          ("trigger_overshoot_bp", True)):
        vals = [_f(x.get(key)) for x in legs]
        if not legs or any(v is None for v in vals):
            out[key] = None
        elif weighted:
            tot = sum(qty)
            out[key] = round(sum(v * q for v, q in zip(vals, qty)) / tot, 4) + 0.0 if tot > 0 else None
        else:
            out[key] = round(sum(vals), 6) + 0.0
    out["legs_unmeasured"] = sum(1 for x in legs if _f(x.get("walk_usd")) is None)
    return out


# ---------------------------------------------------------------- new-tile rule (CI, HM 12:20)
# Every ACTIVE_TILE_REGISTRY lane must name the functions that price its paper fills. A migrated
# site must call paper_fill.paper_fill and must not read state price/ask/bid or tape streams.
TILE_FILL_SITES = {
    "FAMILY_RANDOM_CONTROL_TAKER_90": (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                          ("tile_fill.py", "price_exit_resident"), ("tile_fill.py", "price_exit_forced")),
    "FAMILY_GS01_XV_PREMIUM_ATR_TP": (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                          ("tile_fill.py", "price_exit_resident"), ("tile_fill.py", "price_exit_forced")),
    "FAMILY_GSB1_CVD_DIV_REGIME": (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                          ("tile_fill.py", "price_exit_resident"), ("tile_fill.py", "price_exit_forced")),
    "FAMILY_GSB2_REGIME_SWITCHER": (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                          ("tile_fill.py", "price_exit_resident"), ("tile_fill.py", "price_exit_forced")),
    "FAMILY_GS06_COMMITTED_FADE_ATR_TP": (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                          ("tile_fill.py", "price_exit_resident"), ("tile_fill.py", "price_exit_forced")),
}
# R4 shared-helper note (23 Oct 2026, Tile 6/7 removal, owner R9): the two DISLOC / DISLOC-FOI entries
# (disloc_engine._price_entry / _price_exit) were deleted with disloc_engine.py. Pure deletion: the five
# remaining entries, PENDING_MIGRATION, ALLOWED_FILL_MODELS, FRESH_EXIT_STATUSES and every pricing
# function in this module are unchanged (byte-identity pinned by test_tile67_byte_identity.py).
# Step B migrates these and empties the set. HM 12:35: the list may only shrink (CI pins the
# baseline), no new registry lane may join it, and it expires: after PENDING_MIGRATION_EXPIRES a
# non-empty list fails CI. Paper rows from pending lanes are not fresh data (fresh_data_eligible).
# Step B (HM 13:16): DISLOC and DISLOC-FOI migrated (both removed 23 Oct 2026).
PENDING_MIGRATION = frozenset()  # 16 Oct 2026: tiles 1-5 migrated (tile_fill.py); must stay empty
PENDING_MIGRATION_BASELINE = frozenset(TILE_FILL_SITES)  # 13 Oct 2026; never add to this
# GS-FADE3-TP30 (gf3, paper only; RK PASS 25 Oct 06:24 AEDT): a NEW tile on paper_fill from day one (registered after
# the 13 Oct baseline is frozen, so the baseline is unchanged): same four sites; its resting remainder is priced by the
# shared A5 helper via REST_FILL_SITES (tile_fill.price_entry_rest).
TILE_FILL_SITES["FAMILY_GS_FADE3_TP30"] = (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                           ("tile_fill.py", "price_exit_resident"), ("tile_fill.py", "price_exit_forced"))
ALLOWED_FILL_MODELS = frozenset({FILL_MODEL})
LEGACY_TAG = "LEGACY_PRICING_INVALID"
FORBIDDEN_NAMES = ("book_tape_1s", "market_microstructure_1s", "ticker")
FORBIDDEN_IDENTIFIERS = ("ticker", "last_px", "last_price", "last_ask", "last_bid", "touch", "mark_price")
FORBIDDEN_STATE_KEYS = ("price", "ask", "bid", "last_price", "mark")
_DYNAMIC_CALLS = ("getattr", "__import__", "eval", "exec", "globals", "locals", "vars", "import_module")


def fresh_data_eligible(lane: str) -> bool:
    """Paper rows count as fresh data only once the lane prices fills through paper_fill."""
    return str(lane) not in PENDING_MIGRATION


FRESH_EPOCH_TS = None  # set by the bot from the stamped fresh-start epoch (None = not stamped yet)


def row_before_epoch(row) -> bool:
    """True when the row's entry/exit time precedes the stamped fresh-start epoch."""
    if FRESH_EPOCH_TS is None or not isinstance(row, dict):
        return False
    for k in ("entry_ts", "exit_ts", "ts_epoch", "entry_time_ts"):
        v = _f(row.get(k))
        if v is not None:
            return v < float(FRESH_EPOCH_TS)
    return True  # fail closed: a row with no usable timestamp cannot be proven post-epoch


def tag_legacy(row):
    """Read-time tag for rows written before the shared pricer: legacy_pricing + LEGACY_PRICING_INVALID."""
    if isinstance(row, dict) and row.get("exit_reason") and not row.get("fill_model_version"):
        return {**row, "legacy_pricing": True, "fill_model_tag": LEGACY_TAG, "fresh_eligible": False}
    return row


def as_bool(value, default=None):
    """Parse a bool that may have been reloaded from CSV as a string ("True"/"False"/"1"/"0").
    bool("False") is True, so a stored flag must never be tested with bool()/truthiness."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "t", "y"):
        return True
    if text in ("false", "0", "no", "f", "n"):
        return False
    return default


def is_excluded(value) -> bool:
    """fresh_data_excluded is a flag or a reason string ("FORCED_NO_BOOK"): any non-empty value except an
    explicit false/0 excludes the row."""
    if value is None or value == "":
        return False
    return as_bool(value, True) is not False


def as_number(value):
    """Number or None (CSV reload gives "1" / "1.5" / ""); bools are not numbers here."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


# Terminal exit statuses of a fully book-priced close: bot rows say PAPER_FILL, (retired) DISLOC ledger rows said
# FILLED or FILLED_IN_PARTS (multi-leg / TP1-style; kept so historical rows classify the same). FORCED_NO_BOOK and PAPER_FILL_FORCED are never fresh.
FRESH_EXIT_STATUSES = frozenset({"PAPER_FILL", "FILLED", "FILLED_IN_PARTS"})


def row_fresh_strict(row) -> bool:
    """A closed paper row is fresh only if every pricing fact says so, whatever a stored flag claims:
    exit_status PAPER_FILL (not FORCED_NO_BOOK / PAPER_FILL_FORCED), a v1 fill_model_version, an allowed
    fill_model, not legacy-tagged and not excluded. Tolerates CSV-reloaded strings."""
    if not isinstance(row, dict):
        return False
    version = as_number(row.get("fill_model_version"))
    if str(row.get("exit_status") or "") not in FRESH_EXIT_STATUSES or version is None or version < 1:
        return False
    if str(row.get("fill_model") or "") not in ALLOWED_FILL_MODELS:
        return False
    if is_excluded(row.get("fresh_data_excluded")) or as_bool(row.get("legacy_pricing"), False):
        return False
    if row.get("fill_model_tag") == LEGACY_TAG:
        return False
    if as_bool(row.get("fill_valid"), True) is False or row.get("non_fill_category") or row.get("exit_fallback"):
        return False  # no-book exit / unpriced TP1 leg: not a paper fill, never countable
    if as_bool(row.get("all_legs_book10l"), True) is False or not _legs_book10l(row):
        return False
    return as_bool(row.get("fresh_eligible"), True) is not False and row_fresh_eligible(row)


def _legs_book10l(row) -> bool:
    """Every priced exit leg carried by the row (partial TP1 legs, the terminal exit sim) is a BOOK10L fill whose
    own flags say it is fresh-eligible. Absent leg detail (older / CSV rows) is not evidence against the row; the
    explicit all_legs_book10l / fill_valid / exit_fallback fields stamped at close time carry that."""
    import json as _json
    receipts = row.get("partial_exit_receipts")
    if isinstance(receipts, str):
        try:
            receipts = _json.loads(receipts)
        except ValueError:
            receipts = None
    for r in receipts if isinstance(receipts, list) else ():
        if not isinstance(r, dict) or r.get("realized_gross_usd") is None:
            continue
        if r.get("leg_priced_by") != "paper_fill_book10l" or as_bool(r.get("leg_fresh_eligible"), True) is False \
                or r.get("leg_fallback"):
            return False
    sim = row.get("exit_fill_sim")
    if isinstance(sim, dict) and sim.get("fill_model") and str(sim.get("fill_model")) not in ALLOWED_FILL_MODELS:
        return False
    return True


def row_fresh_eligible(row) -> bool:
    """Epoch-row predicate: rows of lanes still pending migration are never fresh data."""
    if not isinstance(row, dict):
        return True
    if row_before_epoch(row):
        return False
    # R17 (RK 23 Oct 2026): pinned discard ids, rows stamped non_countable on write, and any row that does not PROVE
    # book realism against the r17_fields contract are never fresh (fails closed if a pinned list is missing/altered).
    import r17_exclusion as _r17
    if _r17.non_countable_reason(row):
        return False
    row = tag_legacy(row)
    if is_excluded(row.get("fresh_data_excluded")) or row.get("exit_status") == "FORCED_NO_BOOK" \
            or row.get("fill_model_tag") == LEGACY_TAG or as_bool(row.get("fresh_eligible")) is False:
        return False
    lane = row.get("research_lane") or row.get("lane") or row.get("pathway")
    return True if not lane else fresh_data_eligible(lane)


def _aliases(tree) -> tuple:
    """Names bound to the paper_fill module, the paper_fill function and book10l_at (any alias)."""
    import ast
    mods, funcs, books = set(), set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "paper_fill":
                    mods.add(a.asname or a.name)
        elif isinstance(n, ast.ImportFrom) and n.module == "paper_fill":
            for a in n.names:
                if a.name == "paper_fill":
                    funcs.add(a.asname or a.name)
                elif a.name == "book10l_at":
                    books.add(a.asname or a.name)
    return mods, funcs, books


def _folded_strings(node) -> list:
    """Every string constant plus every constant-folded concatenation / f-string / join."""
    import ast
    out = []

    def fold(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Mod)):
            a, b = fold(n.left), fold(n.right)
            return a + b if a is not None and b is not None else (a or "") + (b or "") or None
        if isinstance(n, ast.JoinedStr):
            return "".join(v.value for v in n.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "join" and n.args \
                and isinstance(n.args[0], (ast.List, ast.Tuple)):
            parts = [fold(e) for e in n.args[0].elts]
            return "".join(x for x in parts if x)
        return None
    for n in ast.walk(node):
        v = fold(n)
        if v:
            out.append(v)
    return out


def _state_reads(node, where: str) -> list:
    """state[...] with a forbidden or non-literal key, state.get(...) likewise, and aliasing x = state."""
    import ast
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Subscript) and (getattr(n.value, "id", "") == "state" or getattr(n.value, "attr", "") == "state"):
            key = n.slice.value if isinstance(n.slice, ast.Constant) else None
            if not isinstance(key, str):
                out.append(f"{where}: state[<non-literal key>]")
            elif key in FORBIDDEN_STATE_KEYS:
                out.append(f"{where}: reads state[{key!r}]")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" \
                and (getattr(n.func.value, "id", "") == "state" or getattr(n.func.value, "attr", "") == "state"):
            k = n.args[0] if n.args else None
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                out.append(f"{where}: state.get(<non-literal key>)")
            elif k.value in FORBIDDEN_STATE_KEYS:
                out.append(f"{where}: reads state.get({k.value!r})")
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            v = n.value
            if isinstance(v, ast.Name) and v.id == "state":
                out.append(f"{where}: aliases state")
    return out


def _is_pf_call(n, mods, funcs) -> bool:
    import ast
    if not isinstance(n, ast.Call):
        return False
    f = n.func
    if isinstance(f, ast.Attribute) and f.attr == "paper_fill" and isinstance(f.value, ast.Name) and f.value.id in mods:
        return True
    return isinstance(f, ast.Name) and f.id in funcs


def _is_book10l(n, mods, books) -> bool:
    import ast
    if isinstance(n, ast.Attribute) and n.attr == "book10l_at" and isinstance(n.value, ast.Name) and n.value.id in mods:
        return True
    return isinstance(n, ast.Name) and n.id in books


ALLOWED_FILL_KWARGS = frozenset({"leg", "delay_source", "cap_px"})  # HM 14:10


def fill_site_violations(source: str, func_name: str) -> list:
    """AST check of one fill site (HM 13:20).

    A fill site is a pricing function whose LAST statement is ``return paper_fill.paper_fill(...)``
    (any import alias) and every return returns that call directly; its book argument is
    paper_fill.book10l_at; it has no top_at; it never touches ``state`` / ``sys.modules`` / dynamic
    lookups, never names ticker/touch/last_* identifiers, and no folded string names a banned stream.
    """
    import ast
    tree = ast.parse(source)
    fn = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == func_name), None)
    if fn is None:
        return [f"{func_name}: not found"]
    mods, funcs, books = _aliases(tree)
    out = []
    body = [b for b in fn.body if not (isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant)
                                       and isinstance(b.value.value, str))]
    if len(body) != 1:
        out.append(f"{func_name}: fill site must be a single `return paper_fill.paper_fill(...)` (no other statements)")
    last = fn.body[-1] if fn.body else None
    if not (isinstance(last, ast.Return) and _is_pf_call(last.value, mods, funcs)):
        out.append(f"{func_name}: last statement must be `return paper_fill.paper_fill(...)`")
    pf_calls = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and not _is_pf_call(n.value, mods, funcs):
            out.append(f"{func_name}: returns something other than paper_fill(...)")
        if _is_pf_call(n, mods, funcs):
            pf_calls.append(n)
        if isinstance(n, ast.Call):
            f = n.func
            fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            owner = getattr(getattr(f, "value", None), "id", "")
            if fname in _DYNAMIC_CALLS or owner == "importlib":
                out.append(f"{func_name}: dynamic call {fname}()")
        if isinstance(n, ast.Attribute) and n.attr == "modules" and getattr(n.value, "id", "") == "sys":
            out.append(f"{func_name}: sys.modules lookup")
        if isinstance(n, ast.Name) and n.id in ("state", "sys", "importlib"):
            out.append(f"{func_name}: references {n.id}")
        ident = n.id if isinstance(n, ast.Name) else (n.attr if isinstance(n, ast.Attribute) else (
            n.arg if isinstance(n, ast.keyword) and n.arg else ""))
        if ident and any(b in ident.lower() for b in FORBIDDEN_IDENTIFIERS) and ident not in ("paper_fill",):
            out.append(f"{func_name}: references {ident} (prices only from paper_fill.book10l_at)")
        if isinstance(n, ast.Lambda):
            out.append(f"{func_name}: lambda (book/top must be paper_fill.book10l_at)")
    for st in _folded_strings(fn):
        if any(b in st for b in FORBIDDEN_NAMES):
            out.append(f"{func_name}: references {st!r}")
    allowed_deco = {"staticmethod", "classmethod"}
    for d in fn.decorator_list:
        if not (isinstance(d, ast.Name) and d.id in allowed_deco):
            out.append(f"{func_name}: decorator not allowed on a fill site")
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    for c in pf_calls:
        for label, idx, kw, keys in (("signal_ts", 2, "signal_ts", ("signal_ts", "trigger_ts")),
                                     ("delay_s", 3, "delay_s", ("delay_s",)),
                                     ("cap_px", None, "cap_px", ("ioc_cap_px", "cap_px"))):
            a = c.args[idx] if idx is not None and len(c.args) > idx else next(
                (k.value for k in c.keywords if k.arg == kw), None)
            if a is None:
                continue
            ok = (isinstance(a, ast.Name) and a.id in params and a.id in keys) or (
                isinstance(a, ast.Subscript) and isinstance(a.value, ast.Name) and a.value.id in params
                and isinstance(a.slice, ast.Constant) and a.slice.value in keys)
            if not ok:
                out.append(f"{func_name}: {label} must be a plain parameter or order field {keys}")
        book = c.args[4] if len(c.args) >= 5 else next((k.value for k in c.keywords if k.arg == "book_at"), None)
        if book is None or not _is_book10l(book, mods, books):
            out.append(f"{func_name}: book_at must be paper_fill.book10l_at")
        if any(k.arg is None for k in c.keywords) or any(isinstance(x, ast.Starred) for x in c.args):
            out.append(f"{func_name}: ** / * unpacking not allowed in a paper_fill call")
        bad_kw = sorted(k.arg for k in c.keywords if k.arg is not None and k.arg not in ALLOWED_FILL_KWARGS)
        if bad_kw:
            out.append(f"{func_name}: keyword(s) {bad_kw} not allowed (only leg, delay_source, cap_px)")
        if any(k.arg == "top_at" for k in c.keywords) or len(c.args) > 5:
            out.append(f"{func_name}: top_at fallback not allowed at a fill site")
    out += _state_reads(fn, func_name)
    return sorted(set(out))


def tile_module_violations(source: str, module: str, fill_sites=()) -> list:
    """Whole-module scan of a tile module: no forbidden/non-literal state reads, no state aliasing,
    no sys.modules lookups, no folded string naming a banned stream."""
    import ast
    tree = ast.parse(source)
    out = _state_reads(tree, module)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr == "modules" and getattr(n.value, "id", "") == "sys":
            out.append(f"{module}: sys.modules lookup")
    for st in _folded_strings(tree):
        if any(b in st for b in ("book_tape_1s", "market_microstructure_1s")):
            out.append(f"{module}: references {st!r}")
    mods, funcs, books = _aliases(tree)
    pf_names = mods | {"paper_fill"}
    for n in ast.walk(tree):
        # no monkeypatching paper_fill.* from a tile module
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign)) else []
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in pf_names:
                out.append(f"{module}: assigns paper_fill.{t.attr}")
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "setattr" and n.args \
                and isinstance(n.args[0], ast.Name) and n.args[0].id in pf_names:
            out.append(f"{module}: setattr on paper_fill")
    out += _post_fill_overrides(tree, module, fill_sites or ())
    return sorted(set(out))


_PRICE_KEYS = ("price", "filled_qty", "fill_ts", "book_s", "status")


def _post_fill_overrides(tree, module: str, fill_sites) -> list:
    """The caller may read a fill result but never rewrite its price/qty/time/book/status."""
    import ast
    out = []
    sites = set(fill_sites) | {"paper_fill"}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
                f = n.value.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name in sites:
                    bound |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        if not bound:
            continue
        for n in ast.walk(fn):
            tgts = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AugAssign) else []
            for t in tgts:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id in bound:
                    out.append(f"{module}:{fn.name}: overrides fill result field")
                if isinstance(t, ast.Name) and t.id in bound and isinstance(n, ast.Assign) \
                        and not (isinstance(n.value, ast.Call) and (getattr(n.value.func, "attr", "") in sites
                                                                    or getattr(n.value.func, "id", "") in sites)):
                    out.append(f"{module}:{fn.name}: rebinds fill result")
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in (
                    "update", "__setitem__", "setdefault", "pop") \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id in bound:
                out.append(f"{module}:{fn.name}: mutates fill result via .{n.func.attr}()")
    return out


# ---------------------------------------------------------------- REALISM_V2 SHARED A5 REST FILL (AMEND1 A5)
# ONE shared helper for the resting remainder of a marketable GTC entry (R4). Reached only through the registered
# tile_fill.price_entry_rest site (REST_FILL_SITES / rest_site_violations). Existing lanes are untouched.
REST_TTL_S = 3.0  # the ONE rest TTL; pinned equal to order_model.DEFAULT_TAKER_TTL_S (the live GTC cancel TTL)
REST_RULE_A5 = "A5_TRADE_THROUGH"
NO_FILL_REST_TTL = "NO_FILL_REST_TTL"
REST_SOURCE = "BFX_TRADES"  # the receive-stamped trades feed of the exchange-stop trigger; never the ticker/tape


def rest_fill(side: str, qty_remaining: float, limit_px: float, t_start: float, trades, ttl_s: float = REST_TTL_S) -> dict:
    """AMEND1 A5: a resting entry fills ONLY from real trade prints with ts in (t_start, t_start + ttl_s] at prices
    STRICTLY beyond the limit (buy: px < limit; sell: px > limit). qty = min(remaining, sum |amount| of those prints),
    price = the limit. Touch / at-limit / book-cross never fill. Otherwise NO_FILL_REST_TTL.

    ``trades`` is a list of {"ts", "px", "amt"} prints (quote_trigger.trades_between over (t_start, t_start+ttl_s])."""
    q = _f(qty_remaining) or 0.0
    lim = _f(limit_px)
    t0 = float(t_start)
    t1 = t0 + float(ttl_s)
    buy = str(side).upper() in ("LONG", "BUY")
    base = {"fill_model": FILL_MODEL, "fill_model_version": FILL_MODEL_VERSION, "leg": "entry_rest",
            "rest_rule": REST_RULE_A5, "rest_ttl_s": float(ttl_s), "rest_t_start": t0, "rest_t_end": t1,
            "side": str(side).upper(), "qty": q, "limit_px": lim, "price_source": None}
    beyond = []
    for p in trades or ():
        ts, px, amt = _f((p or {}).get("ts")), _f((p or {}).get("px")), _f((p or {}).get("amt"))
        if ts is None or px is None or amt is None or lim is None:
            continue
        if not (t0 < ts <= t1):
            continue
        if (px < lim) if buy else (px > lim):
            beyond.append((ts, abs(amt)))
    vol = sum(a for _, a in beyond)
    base.update(trades_n=len(beyond), trade_vol_beyond=round(vol, 8))
    if q <= 0 or lim is None or vol <= 0:
        return {**base, "status": NO_FILL_REST_TTL, "price": None, "filled_qty": 0.0, "fill_ts": None,
                "evidence_status": "NO_FILL"}
    filled = min(q, vol)
    # the fill completes at the print that exhausts the remaining qty (or the last beyond print)
    acc, fill_ts = 0.0, beyond[-1][0]
    for ts, a in sorted(beyond):
        acc += a
        if acc + 1e-12 >= filled:
            fill_ts = ts
            break
    full = filled + 1e-12 >= q
    return {**base, "status": "FILLED" if full else "PARTIAL", "price": lim, "filled_qty": round(filled, 8),
            "fill_ts": fill_ts, "price_source": REST_SOURCE, "evidence_status": "COMPLETE" if full else "INCOMPLETE"}


# lane -> the registered rest sites (each must be a single `return paper_fill.rest_fill(...)` over trades_between)
REST_FILL_SITES = {
    "FAMILY_GS_FADE3_TP30": (("tile_fill.py", "price_entry_rest"),),
}


def rest_site_violations(source: str, func_name: str) -> list:
    """AST check of a rest site: single `return paper_fill.rest_fill(...)`; trades from quote_trigger.trades_between;
    no ticker / tape / state / dynamic lookups."""
    import ast
    tree = ast.parse(source)
    fn = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == func_name), None)
    if fn is None:
        return [f"{func_name}: not found"]
    out = []
    body = [b for b in fn.body if not (isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant))]
    call = body[0].value if len(body) == 1 and isinstance(body[0], ast.Return) else None
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "rest_fill"
            and getattr(call.func.value, "id", "") == "paper_fill"):
        out.append(f"{func_name}: must be a single `return paper_fill.rest_fill(...)`")
    for n in ast.walk(fn):
        ident = n.id if isinstance(n, ast.Name) else (n.attr if isinstance(n, ast.Attribute) else "")
        if ident and any(b in ident.lower() for b in FORBIDDEN_IDENTIFIERS):
            out.append(f"{func_name}: references {ident}")
        if isinstance(n, ast.Name) and n.id in ("state", "sys", "importlib"):
            out.append(f"{func_name}: references {n.id}")
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") in _DYNAMIC_CALLS:
            out.append(f"{func_name}: dynamic call")
    for st in _folded_strings(fn):
        if any(b in st for b in FORBIDDEN_NAMES):
            out.append(f"{func_name}: references {st!r}")
    if call is not None and isinstance(call, ast.Call):
        tr = call.args[4] if len(call.args) >= 5 else next((k.value for k in call.keywords if k.arg == "trades"), None)
        if not (isinstance(tr, ast.Call) and isinstance(tr.func, ast.Attribute) and tr.func.attr == "trades_between"
                and getattr(tr.func.value, "id", "") in ("quote_trigger", "_qt")):
            out.append(f"{func_name}: trades must be quote_trigger.trades_between(...)")
    return sorted(set(out))
