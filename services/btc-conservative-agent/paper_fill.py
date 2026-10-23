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
    src = str(delay_source or "")
    if src == "EXCHANGE_RESIDENT_NO_DELAY":
        return {"delay_source_kind": "resident", "pool_n": None, "pool_ts": None, "quantile_method": None,
                "delay_unverified": False}
    m = _re.search(r"_n(\d+)$", src)
    n = int(m.group(1)) if m and "default" not in src else 0
    kind = "live_ack" if n > 0 else "default"
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


DEPTH_SHORT_PARTIAL = "DEPTH_SHORT_PARTIAL"  # HM C7: a first-row walk the visible 10 levels could not fill


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
        book = first_row_at_or_after(book_at, t)
        base["book_row_rule"] = ROW_RULE_FIRST_AFTER
        if not book:
            COUNTERS["reject_no_book"] += 1
            return {**base, "status": "REJECT_NO_BOOK", "price": None, "filled_qty": 0.0, "price_source": None}
        ready = float(book["s"]) + 1.0
        price_age = round(ready - t, 3)
        px, filled = _walk(taker_side_levels(side, leg, book), q)
        out = {**base, "book_s": int(book["s"]), "price_age_s": price_age,
               "price_stale": price_age > MAX_BOOK_AGE_S, "depth_snapshot_fill": _snap(book),
               "book_age_ms": round((t - float(book["s"])) * 1000), "ask_age_ms": round((t - float(book["s"])) * 1000)}
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
        if px is None:
            st = "NO_FILL_IOC_CAP" if cap_px is not None and taker_side_levels(side, leg, book) else "NO_FILL_NO_DEPTH"
            return {**base, "status": st, "cap_px": cap_px, "depth_snapshot_fill": _snap(book), "price": None, "filled_qty": 0.0,
                    "book_s": int(book["s"]), "book_age_ms": round((t - float(book["s"])) * 1000)}
        return {**base, "status": "FILLED" if filled + 1e-12 >= q else "PARTIAL", "price": px,
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
    """book_at(t) over the collector's bfx_book_10l_1s.jsonl (rows {"s", "b", "a"}).

    A row for second s is complete only at s + 1, so at(t) returns the latest row with s + 1 <= t
    (no look-ahead). Reads only the file tail, and only when cached rows are older than t.
    """

    def __init__(self, path: str, keep: int = 600, tail_bytes: int = 512 * 1024) -> None:
        self.path, self.keep, self.tail_bytes = path, keep, tail_bytes
        self._lock = threading.Lock()
        self._rows: dict = {}
        self._max_s = None

    def _refresh(self) -> None:
        import json
        import os
        try:
            size = os.path.getsize(self.path)
            with open(self.path, "rb") as fh:
                fh.seek(max(0, size - self.tail_bytes))
                chunk = fh.read()
        except OSError:
            return
        lines = chunk.splitlines()
        for line in (lines[1:] if size > self.tail_bytes else lines):
            try:
                r = json.loads(line)
                sec = int(r["s"])
            except (ValueError, KeyError, TypeError):
                continue
            self._rows[sec] = {"s": sec, "b": r.get("b") or [], "a": r.get("a") or [],
                               **({"carried": True} if r.get("carried") else {})}
        if self._rows:
            for sec in sorted(self._rows)[:-self.keep]:
                del self._rows[sec]
            self._max_s = max(self._rows)

    def at(self, t: float):
        with self._lock:
            if self._max_s is None or self._max_s + 1 < t - 0.5:
                self._refresh()
            ok = [sec for sec in self._rows if sec + 1 <= t]
            return dict(self._rows[max(ok)]) if ok else None


    def missing_seconds(self, start: float, end: float) -> int:
        """Whole seconds in [floor(start), floor(end)] with no book row (read-only; refreshes the tail first)."""
        a, b = int(start), int(end)
        if b < a:
            return 0
        with self._lock:
            if self._max_s is None or self._max_s < b:
                self._refresh()
            return sum(1 for sec in range(a, b + 1) if sec not in self._rows)


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


def book10l_missing_seconds(start: float, end: float):
    """Seconds without a book row in [start, end] (None if the book source is unavailable). Diagnostic only."""
    try:
        book10l_at(float(end))  # makes sure the tail exists
        return _BOOK_TAIL.missing_seconds(float(start), float(end)) if _BOOK_TAIL is not None else None
    except Exception:  # noqa: BLE001
        return None


# REALISM_V2 F5 (HM 23 Oct): funding is paid only at the 00/08/16 UTC settlement instants, priced from the
# collector's deriv-status file (never the REST "predicted" state rate). Read-only; no new stored data.
DERIV_FILE = "bfx_deriv_status_1s.jsonl"
DERIV_WINDOW_S = 60  # a settlement needs a status row within this many seconds before (rate/mark) it


class DerivStatusFiles:
    """deriv_settlement(t) over bfx_deriv_status_1s.jsonl and its rotations (<file>.N, N rising = newer).

    Rows {"s", "mts", "mark", "fund_cur", "fund_accr", "next_fund_ts" (ms), ...} are time-ordered inside a file,
    so each lookup bisects the file by byte offset and reads only a +-window around t (no full scans, rotation
    safe). A result is cached once a post-settlement row exists (it can no longer change).
    """

    def __init__(self, path: str, window_s: int = DERIV_WINDOW_S) -> None:
        self.path, self.window_s = path, int(window_s)
        self._lock = threading.Lock()
        self._cache: dict = {}

    def _files(self) -> list:
        import glob
        import os
        rot = sorted((p for p in glob.glob(self.path + ".*") if p.rsplit(".", 1)[-1].isdigit()),
                     key=lambda p: int(p.rsplit(".", 1)[-1]), reverse=True)
        return ([self.path] if os.path.exists(self.path) else []) + rot  # newest first

    @staticmethod
    def _row_at(fh, off: int, size: int):
        import json
        fh.seek(off)
        if off:
            fh.readline()  # skip the partial line
        while fh.tell() < size:
            line = fh.readline()
            try:
                r = json.loads(line)
                return int(r["s"]), r
            except (ValueError, KeyError, TypeError):
                continue
        return None, None

    def _window_rows(self, path: str, lo: int, hi: int) -> list:
        import json
        import os
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                first, _ = self._row_at(fh, 0, size)
                if first is None or first > hi:
                    return []
                a, b = 0, size
                while b - a > 4096:  # bisect to an offset whose next row is < lo
                    m = (a + b) // 2
                    s, _ = self._row_at(fh, m, size)
                    if s is None or s >= lo:
                        b = m
                    else:
                        a = m
                fh.seek(a)
                if a:
                    fh.readline()
                out = []
                for line in fh:
                    try:
                        r = json.loads(line)
                        sec = int(r["s"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if sec > hi:
                        break
                    if sec >= lo:
                        out.append(r)
                return out
        except OSError:
            return []

    def settlement(self, t: float):
        t = int(t)
        with self._lock:
            if t in self._cache:
                return dict(self._cache[t])
            rows = {}
            for path in self._files():
                for r in self._window_rows(path, t - self.window_s, t + self.window_s):
                    rows.setdefault(int(r["s"]), r)
            out = _settlement_from_rows(t, [rows[k] for k in sorted(rows)])
            if out is not None and out.get("confirmed"):
                self._cache[t] = dict(out)
            return out


def _num_or_none(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _settlement_from_rows(t: int, rows: list):
    """Pure: the applied rate / mark of the settlement at t from time-ordered deriv rows near t.

    rate = fund_cur of the last row with s < t (CURRENT_FUNDING just before the settlement; fund_accr kept raw);
    mark = MARK_PRICE of that same last pre-settlement row (the mark at the settlement instant, no look-ahead);
    confirmed = a row with s >= t shows the flip (next_fund_ts moved past t). None when no usable pre row.
    """
    pre = [r for r in rows if int(r["s"]) < t and _num_or_none(r.get("fund_cur")) is not None]
    mk = [r for r in rows if int(r["s"]) < t and _num_or_none(r.get("mark")) is not None]
    if not pre or not mk:
        return None
    rr, mr = pre[-1], mk[-1]
    post = [r for r in rows if int(r["s"]) >= t and _num_or_none(r.get("next_fund_ts")) is not None]
    flip = next((r for r in post if float(r["next_fund_ts"]) > t * 1000.0), None)

    def _ts(r):
        m = _num_or_none(r.get("mts"))
        return m / 1000.0 if m else float(r["s"])
    return {"settlement_ts": t, "rate_applied": float(rr["fund_cur"]), "rate_accrued": _num_or_none(rr.get("fund_accr")),
            "rate_ts": _ts(rr), "rate_row_s": int(rr["s"]),
            "rate_next_fund_ts": _num_or_none(rr.get("next_fund_ts")),
            "mark_price": float(mr["mark"]), "mark_ts": _ts(mr), "mark_row_s": int(mr["s"]),
            "confirmed": flip is not None, "flip_row_s": int(flip["s"]) if flip else None}


_DERIV_FILES: Optional[DerivStatusFiles] = None


def deriv_settlement(t: float):
    """THE funding price source (F5): the deriv-status settlement at t, or None (no status rows near t)."""
    global _DERIV_FILES
    if _DERIV_FILES is None:
        import os
        _DERIV_FILES = DerivStatusFiles(os.path.join(os.getcwd(), DERIV_FILE))
    try:
        return _DERIV_FILES.settlement(t)
    except Exception:  # noqa: BLE001 - a read failure is an unpriced (UNVERIFIED) settlement, never a crash
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
