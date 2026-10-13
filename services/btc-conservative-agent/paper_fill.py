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
               delay_source: str = "lane_p50", top_at: Optional[Callable[[float], Optional[tuple]]] = None,
               cap_px: Optional[float] = None) -> dict:
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
            self._rows[sec] = {"s": sec, "b": r.get("b") or [], "a": r.get("a") or []}
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


DELAYS = None  # set by the bot: DelaySampler of measured live delays (empty while live is OFF)


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
    "FAMILY_DISLOC_BFX_DISLOCATION_1M": (("disloc_engine.py", "_price_entry"), ("disloc_engine.py", "_price_exit")),
    "FAMILY_DISLOC_FOI_LONG_1M": (("disloc_engine.py", "_price_entry"), ("disloc_engine.py", "_price_exit")),
}
# Step B migrates these and empties the set. HM 12:35: the list may only shrink (CI pins the
# baseline), no new registry lane may join it, and it expires: after PENDING_MIGRATION_EXPIRES a
# non-empty list fails CI. Paper rows from pending lanes are not fresh data (fresh_data_eligible).
# Step B (HM 13:16): DISLOC and DISLOC-FOI migrated (disloc_engine._price_entry / _price_exit).
PENDING_MIGRATION = frozenset(TILE_FILL_SITES) - {"FAMILY_DISLOC_BFX_DISLOCATION_1M", "FAMILY_DISLOC_FOI_LONG_1M"}
PENDING_MIGRATION_BASELINE = frozenset(TILE_FILL_SITES)  # 13 Oct 2026; never add to this
PENDING_MIGRATION_EXPIRES = "2026-10-27"
FORBIDDEN_NAMES = ("book_tape_1s", "market_microstructure_1s", "ticker")
FORBIDDEN_IDENTIFIERS = ("ticker", "last_px", "last_price", "last_ask", "last_bid", "touch", "mark_price")
FORBIDDEN_STATE_KEYS = ("price", "ask", "bid", "last_price", "mark")
_DYNAMIC_CALLS = ("getattr", "__import__", "eval", "exec", "globals", "locals", "vars", "import_module")


def fresh_data_eligible(lane: str) -> bool:
    """Paper rows count as fresh data only once the lane prices fills through paper_fill."""
    return str(lane) not in PENDING_MIGRATION


def row_fresh_eligible(row) -> bool:
    """Epoch-row predicate: rows of lanes still pending migration are never fresh data."""
    if not isinstance(row, dict):
        return True
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
    for c in pf_calls:
        book = c.args[4] if len(c.args) >= 5 else next((k.value for k in c.keywords if k.arg == "book_at"), None)
        if book is None or not _is_book10l(book, mods, books):
            out.append(f"{func_name}: book_at must be paper_fill.book10l_at")
        if any(k.arg == "top_at" for k in c.keywords) or len(c.args) > 5:
            out.append(f"{func_name}: top_at fallback not allowed at a fill site")
    out += _state_reads(fn, func_name)
    return sorted(set(out))


def tile_module_violations(source: str, module: str) -> list:
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
    return sorted(set(out))
