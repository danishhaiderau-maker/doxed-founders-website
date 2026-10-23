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

import threading
from collections import deque
from typing import Any, Callable, Mapping, Optional

FILL_MODEL = "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY"
# REALISM_V2 (HM/RK 23 Oct): ONE version bump for the F1-F6 group (they deploy together). Rows priced by version 1
# (pre-V2) are UNVERIFIED and hard-rejected by every analyzer via row_fresh_strict -> realism_v2_reasons.
FILL_MODEL_VERSION = 2
REALISM_V2_FILL_MODEL_VERSION = 2
REALISM_V2_FLOOR_ENV = "REALISM_V2_FLOOR_TS"  # the REALISM_V2 deploy ts (epoch s); unset/0 = version gate only
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


def _paper_fill_core(side: str, qty: float, signal_ts: float, delay_s: Optional[float],
               book_at: Callable[[float], Optional[Mapping[str, Any]]], *, leg: str = "entry",
               delay_source: str = "DEFAULT_UNMEASURED", top_at: Optional[Callable[[float], Optional[tuple]]] = None,
               cap_px: Optional[float] = None) -> dict:
    """book_at(t) -> {"s": sec, "b": [[px, sz]...], "a": [...]} for the latest snapshot at or before t."""
    # REALISM_V2: an exchange-resident stop order is already on the exchange; it fills at its trigger instant (no
    # bot send delay). Every other leg keeps the MIN_DELAY_S floor.
    resident = leg == RESIDENT_LEG
    d = 0.0 if resident else max(MIN_DELAY_S, _f(delay_s) or DEFAULT_DELAY_S)
    t = float(signal_ts) + d
    q = _f(qty) or 0.0
    base = {"fill_model": FILL_MODEL, "fill_model_version": FILL_MODEL_VERSION, "delay_s": round(d, 3),
            "delay_source": delay_source if (delay_s or resident) else DELAY_DEFAULT_UNMEASURED, "fill_ts": t, "leg": leg, "qty": q,
            "signal_ts": float(signal_ts), "side": str(side).upper()}
    sig_book = _safe(book_at, float(signal_ts))
    base["depth_snapshot_signal"] = _snap(sig_book)
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
               delay_source: str = "DEFAULT_UNMEASURED", top_at: Optional[Callable[[float], Optional[tuple]]] = None,
               cap_px: Optional[float] = None) -> dict:
    """Shared pricer; every result carries an explicit ``evidence_status``: COMPLETE only for a fully filled
    book-walk (status FILLED, price from the 10-level book). Anything else is never COMPLETE. NOTE: bot.py
    ``_paper_result_as_sim`` deliberately re-labels a PARTIAL *entry* COMPLETE (position sized to filled_qty)."""
    out = _paper_fill_core(side, qty, signal_ts, delay_s, book_at, leg=leg, delay_source=delay_source,
                           top_at=top_at, cap_px=cap_px)
    if out.get("status") == "FILLED" and out.get("price_source") == "BFX_BOOK_10L" and out.get("price") is not None:
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

# REALISM_V2 F1 (HM 23 Oct): the delay of every BOT_SENT leg. Never a lane p50, never paper signal->fill.
DELAY_DEFAULT_UNMEASURED = "DEFAULT_UNMEASURED"  # the 1.5 s placeholder: NOT known to be conservative; UNVERIFIED
DELAY_MEASURED_LIVE_ACK = "MEASURED_LIVE_ACK"    # this trade's own live copy: exchange ack - paper signal/trigger
DELAY_LIVE_POOLED_PREFIX = "LIVE_POOLED_"        # + ENTRY / EXIT: pooled live acks of the same leg type
DELAY_PROXY = "PROXY"                            # e.g. a WS ping round trip: stamped only, never a pricing input
POOLED_QUANTILE = 0.9  # deterministic high percentile of the pooled live distribution (slow acks hit fast markets)
LIVE_ACK_SOURCES = frozenset({"LIVE_ACK"})       # the only sample kind add() accepts (paper signal->fill is refused)
MEASURED_DELAY_SOURCES = frozenset({DELAY_MEASURED_LIVE_ACK, DELAY_LIVE_POOLED_PREFIX + "ENTRY",
                                    DELAY_LIVE_POOLED_PREFIX + "EXIT"})


def _quantile(xs, q: float) -> float:
    """Deterministic nearest-rank quantile (no interpolation, no randomness)."""
    ys = sorted(xs)
    k = min(len(ys) - 1, max(0, int(-(-q * len(ys) // 1)) - 1))
    return ys[k]


def delay_is_measured(source) -> bool:
    """True only for live-measured delay sources (per-trade ack or pooled live acks). DEFAULT_UNMEASURED and any
    legacy label (default_until_n10, lane_p50_*) are UNVERIFIED and never count as measured."""
    return str(source or "") in MEASURED_DELAY_SOURCES


class DelaySampler:
    """REALISM_V2 F1 live delay book. Fed ONLY from live executor acks (entry: exchange ack - paper signal; exit:
    exit ack - paper exit trigger). Per-trade measured delays are kept for that trade's own legs; every other tile
    uses a deterministic high quantile of the pooled live distribution of the same leg type. With no live acks the
    1.5 s placeholder is stamped DEFAULT_UNMEASURED. There is no lane p50 and paper signal->fill never enters."""

    def __init__(self, maxlen: int = 500, max_trades: int = 2000) -> None:
        self._lock = threading.Lock()
        self._pooled: dict = {}
        self._trade: dict = {}
        self._proxy = None
        self.maxlen = maxlen
        self.max_trades = max_trades
        self.refused = 0

    def add(self, lane: str, leg: str, delay_s: float, *, trade_id=None, measured_ts=None,
            source: str = "LIVE_ACK") -> bool:
        v = _f(delay_s)
        if source not in LIVE_ACK_SOURCES or v is None or v < 0 or v > 30:
            self.refused += 1
            return False
        leg = "exit" if str(leg).lower() == "exit" else "entry"
        with self._lock:
            self._pooled.setdefault(leg, deque(maxlen=self.maxlen)).append(v)
            if trade_id:
                if len(self._trade) >= self.max_trades:
                    self._trade.pop(next(iter(self._trade)))
                self._trade[(str(trade_id), leg)] = (v, _f(measured_ts), str(lane or "").upper())
        return True

    def add_proxy(self, rtt_s: float, measured_ts=None) -> None:
        """A public WS round trip: recorded as PROXY context on rows, never used to price."""
        v = _f(rtt_s)
        if v is not None and 0 <= v <= 30:
            self._proxy = (v, _f(measured_ts))

    def describe(self, lane: str, leg: str = "entry", trade_id=None) -> dict:
        leg = "exit" if str(leg).lower() == "exit" else "entry"
        with self._lock:
            own = self._trade.get((str(trade_id), leg)) if trade_id else None
            xs = list(self._pooled.get(leg) or ())
            proxy = self._proxy
        out = {"delay_quantile": None, "delay_n_samples": None, "delay_measured_ts": None,
               "delay_proxy_s": proxy[0] if proxy else None, "delay_proxy_source": DELAY_PROXY if proxy else None}
        if own is not None:
            out.update(delay_s=max(MIN_DELAY_S, own[0]), delay_source=DELAY_MEASURED_LIVE_ACK,
                       delay_measured_ts=own[1], delay_raw_s=own[0])
        elif len(xs) >= MIN_SAMPLES:
            out.update(delay_s=max(MIN_DELAY_S, _quantile(xs, POOLED_QUANTILE)),
                       delay_source=DELAY_LIVE_POOLED_PREFIX + leg.upper(), delay_quantile=POOLED_QUANTILE,
                       delay_n_samples=len(xs))
        else:
            out.update(delay_s=DEFAULT_DELAY_S, delay_source=DELAY_DEFAULT_UNMEASURED,
                       delay_n_samples=len(xs))
        return out

    def delay(self, lane: str, leg: str = "entry", trade_id=None) -> tuple:
        d = self.describe(lane, leg, trade_id)
        return d["delay_s"], d["delay_source"]

    def has_trade(self, trade_id, leg: str = "entry") -> bool:
        with self._lock:
            return (str(trade_id), "exit" if str(leg).lower() == "exit" else "entry") in self._trade

    def snapshot(self) -> dict:
        with self._lock:
            pooled = {k: list(v) for k, v in self._pooled.items()}
            n_trades = len(self._trade)
        return {"pooled": {k: {"n": len(v), "q": POOLED_QUANTILE, "value": _quantile(v, POOLED_QUANTILE) if v else None}
                           for k, v in pooled.items()},
                "per_trade_n": n_trades, "refused": self.refused, "proxy_s": self._proxy[0] if self._proxy else None}


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
                                          ("tile_fill.py", "price_exit_resident")),
    "FAMILY_GS01_XV_PREMIUM_ATR_TP": (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                          ("tile_fill.py", "price_exit_resident")),
    "FAMILY_GSB1_CVD_DIV_REGIME": (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                          ("tile_fill.py", "price_exit_resident")),
    "FAMILY_GSB2_REGIME_SWITCHER": (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                          ("tile_fill.py", "price_exit_resident")),
    "FAMILY_GS06_COMMITTED_FADE_ATR_TP": (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                          ("tile_fill.py", "price_exit_resident")),
    "FAMILY_DISLOC_BFX_DISLOCATION_1M": (("disloc_engine.py", "_price_entry"), ("disloc_engine.py", "_price_exit")),
    "FAMILY_DISLOC_FOI_LONG_1M": (("disloc_engine.py", "_price_entry"), ("disloc_engine.py", "_price_exit")),
}
# Step B migrates these and empties the set. HM 12:35: the list may only shrink (CI pins the
# baseline), no new registry lane may join it, and it expires: after PENDING_MIGRATION_EXPIRES a
# non-empty list fails CI. Paper rows from pending lanes are not fresh data (fresh_data_eligible).
# Step B (HM 13:16): DISLOC and DISLOC-FOI migrated (disloc_engine._price_entry / _price_exit).
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


# Terminal exit statuses of a fully book-priced close: bot rows say PAPER_FILL, DISLOC ledger rows say FILLED or
# FILLED_IN_PARTS (multi-leg / TP1-style). FORCED_NO_BOOK and PAPER_FILL_FORCED are never fresh.
FRESH_EXIT_STATUSES = frozenset({"PAPER_FILL", "FILLED", "FILLED_IN_PARTS"})


# REALISM_V2 F6: the exit stamps every row built at/after V2 must carry (no back-fill; older rows are quarantined).
REALISM_V2_STAMP_FIELDS = ("exit_pricing_basis", "exit_order_class", "fresh_epoch_id", "fill_model_version")
REALISM_V2_EXIT_ORDER_CLASSES = frozenset({"EXCHANGE_RESIDENT", "BOT_SENT"})
_EXIT_DELAY_OK = frozenset({"EXCHANGE_RESIDENT_NO_DELAY"})


def realism_v2_floor_ts(env=None) -> float:
    import os as _os
    n = as_number((env if env is not None else _os.environ).get(REALISM_V2_FLOOR_ENV))
    return float(n) if n is not None and n > 0 else 0.0


def _row_fill_ts(row) -> Optional[float]:
    """The row's entry model fill time (epoch s), else its numeric / ISO entry ts."""
    for k in ("model_fill_ts", "entry_ts_epoch", "entry_ts"):
        v = row.get(k)
        n = as_number(v)
        if n is not None and n > 0:
            return n
        if isinstance(v, str) and v.strip():
            try:
                from datetime import datetime, timezone
                d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
                return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
            except ValueError:
                continue
    return None


def realism_v2_missing_stamps(row) -> list:
    """F6: the stamp fields a row is missing (empty / None / 'nan' strings count as missing)."""
    if not isinstance(row, dict):
        return list(REALISM_V2_STAMP_FIELDS)
    return [k for k in REALISM_V2_STAMP_FIELDS if str(row.get(k) if row.get(k) is not None else "").strip().lower()
            in ("", "none", "nan", "null")]


def realism_v2_reasons(row, *, floor_ts: Optional[float] = None) -> list:
    """REALISM_V2 hard rejection (every analyzer, via row_fresh_strict): the reasons a closed row is UNVERIFIED.
    Empty list = the row may count. Pre-V2 rows (version < 2, or a fill before the deploy floor), the 1.5 s
    DEFAULT_UNMEASURED delay (either leg), ticker-triggered rows of unflipped tiles, unverified funding, a
    REPRICED_CURRENT_TIME / NO_BOOK exit and any missing F6 stamp are all UNVERIFIED. Never pooled across versions."""
    if not isinstance(row, dict):
        return ["NOT_A_ROW"]
    out = []
    version = as_number(row.get("fill_model_version"))
    if version is None or version < REALISM_V2_FILL_MODEL_VERSION:
        out.append("PRE_REALISM_V2_FILL_MODEL")
    floor = realism_v2_floor_ts() if floor_ts is None else float(floor_ts or 0)
    if floor > 0:
        t = _row_fill_ts(row)
        if t is None or t < floor:
            out.append("BEFORE_REALISM_V2_FLOOR")
    if not delay_is_measured(row.get("delay_source")) or str(row.get("priced_delay_model") or "") == "DEFAULT_1_5S":
        out.append("ENTRY_DELAY_NOT_LIVE_MEASURED")
    xd = str(row.get("exit_delay_source") or "")
    if not (delay_is_measured(xd) or xd in _EXIT_DELAY_OK):
        out.append("EXIT_DELAY_NOT_LIVE_MEASURED")
    if str(row.get("trigger_price_source") or "ticker").strip().lower() != "book":
        out.append("TICKER_TRIGGER_UNFLIPPED_TILE")
    if as_bool(row.get("funding_verified"), False) is not True:
        out.append("FUNDING_UNVERIFIED")
    basis = str(row.get("exit_pricing_basis") or "")
    if basis in ("REPRICED_CURRENT_TIME", "NO_BOOK_UNPRICED") or basis.startswith(("NO_BOOK", "BOOK_DEPTH_EXCEEDED")):
        out.append("EXIT_BASIS_" + basis)
    if str(row.get("non_fill_category") or "").strip() not in ("", "None", "nan"):
        out.append("NON_FILL_" + str(row.get("non_fill_category")))  # NO_BOOK_EXIT, BOOK_DEPTH_EXCEEDED, TP1_UNPRICED_LEG
    if row.get("exit_order_class") and str(row.get("exit_order_class")) not in REALISM_V2_EXIT_ORDER_CLASSES:
        out.append("EXIT_ORDER_CLASS_UNKNOWN")
    out.extend("MISSING_" + k.upper() for k in realism_v2_missing_stamps(row))
    return out


def row_fresh_strict(row) -> bool:
    """A closed paper row is fresh only if every pricing fact says so, whatever a stored flag claims:
    exit_status PAPER_FILL (not FORCED_NO_BOOK / PAPER_FILL_FORCED), a REALISM_V2 (>= 2) fill_model_version with
    no realism_v2_reasons, an allowed fill_model, not legacy-tagged and not excluded. Tolerates CSV-reloaded strings."""
    if not isinstance(row, dict):
        return False
    if realism_v2_reasons(row):
        return False  # REALISM_V2 hard rejection: UNVERIFIED rows are counted nowhere
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
