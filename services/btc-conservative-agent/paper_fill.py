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
    return {"s": book.get("s"), "b": list(book.get("b") or [])[:10], "a": list(book.get("a") or [])[:10]}


def taker_side_levels(side: str, leg: str, book: Mapping[str, Any]):
    """Entry LONG / exit SHORT buy at the ask; entry SHORT / exit LONG sell at the bid."""
    buy = (str(side).upper() == "LONG") == (leg == "entry")
    return book.get("a") if buy else book.get("b")


def paper_fill(side: str, qty: float, signal_ts: float, delay_s: Optional[float],
               book_at: Callable[[float], Optional[Mapping[str, Any]]], *, leg: str = "entry",
               delay_source: str = "lane_p50", top_at: Optional[Callable[[float], Optional[tuple]]] = None) -> dict:
    """book_at(t) -> {"s": sec, "b": [[px, sz]...], "a": [...]} for the latest snapshot at or before t."""
    d = max(MIN_DELAY_S, _f(delay_s) or DEFAULT_DELAY_S)
    t = float(signal_ts) + d
    q = _f(qty) or 0.0
    base = {"fill_model": FILL_MODEL, "fill_model_version": FILL_MODEL_VERSION, "delay_s": round(d, 3),
            "delay_source": delay_source if delay_s else "default_until_n10", "fill_ts": t, "leg": leg, "qty": q,
            "signal_ts": float(signal_ts)}
    sig_book = _safe(book_at, float(signal_ts))
    base["depth_snapshot_signal"] = _snap(sig_book)
    book = _safe(book_at, t)
    if book and _f(book.get("s")) is not None and t - float(book["s"]) <= MAX_BOOK_AGE_S:
        levels = taker_side_levels(side, leg, book)
        px, filled = _walk(levels, q)
        if px is None:
            return {**base, "status": "NO_FILL_NO_DEPTH", "price": None, "filled_qty": 0.0,
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


class DelaySampler:
    """Rolling measured live delay per lane and leg (entry: signal->exchange_ack; exit: trigger->exit ack)."""

    def __init__(self, maxlen: int = 200) -> None:
        self._lock = threading.Lock()
        self._d: dict = {}
        self.maxlen = maxlen

    def add(self, lane: str, leg: str, delay_s: float) -> None:
        v = _f(delay_s)
        if v is None or v < 0 or v > 30:
            return
        with self._lock:
            self._d.setdefault((lane, leg), deque(maxlen=self.maxlen)).append(v)

    def delay(self, lane: str, leg: str = "entry") -> tuple:
        with self._lock:
            xs = list(self._d.get((lane, leg)) or ())
        if len(xs) < MIN_SAMPLES:
            return DEFAULT_DELAY_S, "default_until_n10"
        return max(MIN_DELAY_S, statistics.median(xs)), f"lane_p50_n{len(xs)}"

    def snapshot(self) -> dict:
        with self._lock:
            return {f"{k[0]}|{k[1]}": {"n": len(v), "p50": statistics.median(v) if v else None}
                    for k, v in self._d.items()}


# ---------------------------------------------------------------- new-tile rule (CI, HM 12:20)
# Every ACTIVE_TILE_REGISTRY lane must name the functions that price its paper fills. A migrated
# site must call paper_fill.paper_fill and must not read state price/ask/bid or tape streams.
TILE_FILL_SITES = {
    "FAMILY_RANDOM_CONTROL_TAKER_90": (("bot.py", "simulate_marketable_limit_fill"),),
    "FAMILY_GS01_XV_PREMIUM_ATR_TP": (("bot.py", "simulate_marketable_limit_fill"),),
    "FAMILY_GSB1_CVD_DIV_REGIME": (("bot.py", "simulate_marketable_limit_fill"),),
    "FAMILY_GSB2_REGIME_SWITCHER": (("bot.py", "simulate_marketable_limit_fill"),),
    "FAMILY_GS06_COMMITTED_FADE_ATR_TP": (("bot.py", "simulate_marketable_limit_fill"),),
    "FAMILY_DISLOC_BFX_DISLOCATION_1M": (("disloc_engine.py", "_taker_fill"), ("disloc_engine.py", "_close")),
    "FAMILY_DISLOC_FOI_LONG_1M": (("disloc_engine.py", "_taker_fill"), ("disloc_engine.py", "_close")),
}
# Step B migrates these and empties the set; CI forbids adding to it (test pins the size).
PENDING_MIGRATION = frozenset(TILE_FILL_SITES)
FORBIDDEN_NAMES = ("book_tape_1s", "market_microstructure_1s", "ticker")
FORBIDDEN_STATE_KEYS = ("price", "ask", "bid", "last_price", "mark")


def fill_site_violations(source: str, func_name: str) -> list:
    """AST check of one fill-pricing function. Returns a list of violation strings."""
    import ast
    tree = ast.parse(source)
    fn = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == func_name), None)
    if fn is None:
        return [f"{func_name}: not found"]
    out, calls_pf = [], False
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            owner = getattr(getattr(f, "value", None), "id", "") if isinstance(f, ast.Attribute) else ""
            if name == "paper_fill" and owner in ("paper_fill", "pf", ""):
                calls_pf = True
        if isinstance(n, ast.Subscript):
            base = getattr(n.value, "id", "") or getattr(n.value, "attr", "")
            key = n.slice.value if isinstance(n.slice, ast.Constant) else None
            if base == "state" and isinstance(key, str) and key in FORBIDDEN_STATE_KEYS:
                out.append(f"{func_name}: reads state[{key!r}]")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get":
            base = getattr(n.func.value, "id", "")
            if base == "state" and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value in FORBIDDEN_STATE_KEYS:
                out.append(f"{func_name}: reads state.get({n.args[0].value!r})")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and any(b in n.value for b in FORBIDDEN_NAMES):
            out.append(f"{func_name}: references {n.value!r}")
        if isinstance(n, ast.Attribute) and n.attr in ("touch_fn", "_touch"):
            out.append(f"{func_name}: prices from {n.attr} (WS top/ticker), not paper_fill")
    if not calls_pf:
        out.append(f"{func_name}: does not call paper_fill.paper_fill")
    return out
