"""Strategist data streams (12 Oct): watch-only, public, keyless, separate niced Fly process.

Never imports bot.py, holds no trade lock, serves no HTTP, uses no API keys and cannot place,
change or cancel an order. Writes three lean append-only JSONL streams in the runtime dir:

* ``bfx_book_10l_1s.jsonl``  - Bitfinex BTC perp book, top 10 levels each side, one row per second
  {"s": sec, "b": [[px, qty], ...], "a": [[px, qty], ...]} (qty rounded to 1e-4 BTC).
* ``bfx_trades.jsonl``       - every individual public Bitfinex trade, batched per second
  {"s": sec, "tr": [[trade_id, ms, signed_amount, price], ...]} (amount > 0 = buyer aggressor).
* ``bfx_deriv_status_1s.jsonl`` - Bitfinex perp derivative status (ws ``status`` channel), one row per
  second: {"s": sec, "mts": exch_ms, "mark": px, "deriv": last, "spot": px, "fund_cur": rate,
  "fund_accr": accrued, "fund_step": step, "next_fund_ts": ms, "oi": BTC, "age_s": sec}. Funding is the
  raw 8 h rate (multiply by 1e4 for bp). Data-gap (c), 12 Oct.
* ``bfx_ws_events.jsonl``    - Bitfinex WS event log (RK, 24 Oct): every control event (info/subscribed/error/conf),
  every heartbeat (incl. the TRADES channel), every trades-channel te/tu id, the post-(re)subscribe trades
  snapshot and connect/disconnect, each with its receive time ``r`` (epoch s, ms precision). It proves when the
  trades channel was live and which prints a resubscribe may have missed. Never merged into ``bfx_trades`` rows.
* ``venue_bbo_1m.jsonl``     - Lighter + Paradex BTC perp best bid/ask, 60 per-second samples per
  UTC-minute row {"m": minute_ts, "lighter": [[bid, ask] | null x60], "paradex": [...]}.

Each file rotates at ROTATE_BYTES to ``<file>.N`` and only KEEP_ROTATIONS rotated files are kept
locally (the segment shipper mirrors them), so on-disk use stays bounded.

No look-ahead (RK, 24 Oct): second ``s`` is closed ~1.3-1.5 s after it starts. Every 1 s row describes the state as
of the END of second ``s`` (receive time <= s+1): the book is frozen at the second boundary before later deltas are
applied, trades are bucketed by receive second, deriv/venue quotes received after s+1 are not used for row ``s``.
Deriv/venue history is kept as ONE value per receive second (the last one received in it) for HIST_SEC seconds, so a
burst of updates between s+1 and the close, or a catch-up after a stall, can never push the s+1 value out (HM #851 C1).

Event rows also carry ``mono`` (time.monotonic(), s, ms precision) so a wall-clock step can neither fake nor hide a
gap, and a disconnect row carries ``reason`` (the exception class, or ``stop`` on a graceful shutdown).

Measured book receive time (RK/HM #851, 24 Oct): every ``bfx_book_10l_1s`` row carries ``receive_ts`` (epoch s, the
wall clock read right after the WS recv of the LAST book frame applied to that row's frozen state, rounded UP to the
ms), ``receive_mono`` (time.monotonic() at that same frame) and ``receive_ts_source`` = ``WS_FRAME``. The row is the
exact book for any time t in [receive_ts, next frame), so a pricer proves no look-ahead with ``receive_ts <= t``.
Rows written before this field existed carry no ``receive_ts`` and are non-countable for pricing; a bucket + write-lag
estimate is never written here (it may only ever be a diagnostic label INFERRED_BUCKET_LAG, never countable).
A CARRIED book row keeps the receive_ts/receive_mono of the frame it was built from (never the bucket second).
The wall and monotonic receive times are read together, right after ``ws.recv()`` returns (HM #851 nit a; Paradex:
right after the HTTP GET returns), before parsing. Trades (``bfx_trades``: per-print ``rx`` [[receive_ts,
receive_mono], ...] aligned with ``tr`` + row receive_ts/receive_mono = the LAST print's), deriv (``bfx_deriv``) and
venue quotes (``venue_bbo_1m``: ``rx`` {venue: 60 x [receive_ts, receive_mono] | null}) carry the same measured
stamps (HM #851 nit b).

Disconnect zeroing is conservative only: on_bfx_disconnect also clears the book-liveness of seconds that are already
frozen but not yet closed, so a disconnect seen after s+1 can suppress a carried row ``s`` (never the reverse). A
fresh (non-carried) row is unaffected.
"""
from __future__ import annotations

import collections
import glob
import json
import math
import os
import random
import signal
import sys
import threading
import time
import urllib.request
from typing import Optional

import data_version as _data_version

BFX_WS = "wss://api-pub.bitfinex.com/ws/2"
BFX_SYMBOL = os.getenv("STRATEGIST_BFX_SYMBOL", "tBTCF0:USTF0")
LIGHTER_WS = "wss://mainnet.zklighter.elliot.ai/stream"
LIGHTER_MARKET = os.getenv("STRATEGIST_LIGHTER_MARKET", "1")  # 1 = BTC perp
PARADEX_BBO = "https://api.prod.paradex.trade/v1/bbo/BTC-USD-PERP"  # WS needs SBE binary; REST 1/s
BOOK_FILE, TRADES_FILE, VENUE_FILE = "bfx_book_10l_1s.jsonl", "bfx_trades.jsonl", "venue_bbo_1m.jsonl"
DERIV_FILE = "bfx_deriv_status_1s.jsonl"
EVENTS_FILE = "bfx_ws_events.jsonl"
HIST_SEC = 15  # per-source history: last value per receive second, kept this long (close lags <= ~10 s catch-up)
DERIV_KEY = "deriv:" + BFX_SYMBOL
MAX_DERIV_AGE_SEC = 10.0  # Bitfinex pushes deriv status every ~1-4 s; rows carry age_s


def parse_deriv_status(body) -> Optional[dict]:
    """Bitfinex ws v2 ``status`` (deriv) array -> named fields; None when malformed."""
    if not isinstance(body, list) or len(body) < 18:
        return None

    def at(i):
        return _f(body[i]) if i < len(body) else None
    out = {"mts": at(0), "deriv": at(2), "spot": at(3), "insurance": at(5), "next_fund_ts": at(7),
           "fund_accr": at(8), "fund_step": at(9), "fund_cur": at(11), "mark": at(14), "oi": at(17),
           "clamp_min": at(21), "clamp_max": at(22)}
    return out if out["mark"] or out["fund_cur"] is not None else None
LEVELS = 10
ROTATE_BYTES = int(os.getenv("STRATEGIST_STREAM_ROTATE_BYTES", str(32 * 1024 * 1024)))
KEEP_ROTATIONS = int(os.getenv("STRATEGIST_STREAM_KEEP_ROTATIONS", "6"))
SILENT_RECONNECT_SEC = 30.0
MAX_QUOTE_AGE_SEC = 2.0
# A quiet book sends no delta; Bitfinex guarantees a heartbeat at least every 15 s on a live connection. While ANY message
# (delta, trade, status, heartbeat) arrived on the book connection within this window the last book is still the book.
BOOK_LIVENESS_SEC = 20.0


def _log(msg: str) -> None:
    sys.stdout.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} [strategist-streams] {msg}\n")
    sys.stdout.flush()


def _f(v) -> Optional[float]:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


class Sink:
    """Append-only JSONL with bounded rotation."""

    def __init__(self, path: str, rotate_bytes: int = ROTATE_BYTES, keep: int = KEEP_ROTATIONS) -> None:
        self.path, self.rotate_bytes, self.keep = path, rotate_bytes, keep
        self.rows = self.failures = 0
        self._lock = threading.Lock()

    def _rotate(self) -> None:
        try:
            if os.path.getsize(self.path) <= self.rotate_bytes:
                return
        except OSError:
            return
        sufs = sorted(int(p.rsplit(".", 1)[-1]) for p in glob.glob(self.path + ".*") if p.rsplit(".", 1)[-1].isdigit())
        os.rename(self.path, f"{self.path}.{(sufs[-1] if sufs else 0) + 1}")
        sufs = sorted(int(p.rsplit(".", 1)[-1]) for p in glob.glob(self.path + ".*") if p.rsplit(".", 1)[-1].isdigit())
        for s in sufs[:-self.keep] if self.keep > 0 else []:
            try:
                os.remove(f"{self.path}.{s}")
            except OSError:
                pass

    def write(self, row: dict) -> None:
        line = json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n"
        with self._lock:
            try:
                self._rotate()
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
                self.rows += 1
            except (OSError, ValueError) as exc:
                self.failures += 1
                _log(f"write failed {self.path}: {type(exc).__name__}")


def _rx_ms(receive_ts: float) -> float:
    return math.ceil(receive_ts * 1000.0 - 1e-6) / 1000.0  # rounded UP: never earlier than measured


def rx_pair(receive_ts: float, receive_mono: float) -> list:
    """[receive_ts (wall, ceil ms), receive_mono (ms)] of one measured WS/HTTP receipt."""
    return [_rx_ms(receive_ts), round(receive_mono, 3)]


def book_receive_stamp(receive_ts: float, receive_mono: float) -> dict:
    """Measured receive time of a book row (RK/HM #851): wall rounded UP to the ms (never earlier than measured),
    monotonic at the same frame. Empty when there is no measured frame time (the row is then non-countable)."""
    if not receive_ts or receive_ts <= 0:
        return {}
    return {"receive_ts": _rx_ms(receive_ts), "receive_mono": round(receive_mono, 3), "receive_ts_source": "WS_FRAME"}


class BfxBook:
    """Bitfinex P0 book (price, count, amount): count 0 deletes; amount > 0 bid, < 0 ask."""

    def __init__(self) -> None:
        self.bids: dict = {}
        self.asks: dict = {}
        self.ts = 0.0
        self.mono = 0.0  # time.monotonic() at the WS recv of the last frame applied (set by the collector)

    def apply(self, entry, now: float) -> None:
        price, count, amount = float(entry[0]), int(entry[1]), float(entry[2])
        side = self.bids if amount > 0 else self.asks
        if count == 0:
            (self.bids if amount == 1 else self.asks if amount == -1 else side).pop(price, None)
        else:
            side[price] = abs(amount)
        self.ts = now

    def snapshot(self, entries, now: float) -> None:
        self.bids, self.asks = {}, {}
        for e in entries:
            self.apply(e, now)

    def top(self, n: int = LEVELS):
        b = sorted(self.bids.items(), reverse=True)[:n]
        a = sorted(self.asks.items())[:n]
        return ([[_num(p), round(q, 4)] for p, q in b], [[_num(p), round(q, 4)] for p, q in a])


class _SecondHistory:
    """Last value received in each receive second, for HIST_SEC seconds. Bounded by time, not count: a burst cannot
    evict the value a late close needs (HM #851 C1). Always keeps the newest value even when older than HIST_SEC."""

    def __init__(self, keep_s: int = HIST_SEC) -> None:
        self.keep_s = keep_s
        self._by_sec: "collections.OrderedDict[int, tuple]" = collections.OrderedDict()

    def add(self, value: tuple, recv: float) -> None:
        sec = int(recv)
        self._by_sec[sec] = value
        self._by_sec.move_to_end(sec)
        while len(self._by_sec) > 1 and next(iter(self._by_sec)) < sec - self.keep_s:
            self._by_sec.popitem(last=False)

    def as_of(self, sec: int):
        """Newest value received before the end of second ``sec`` (receive time < sec+1)."""
        best = None
        for k, v in self._by_sec.items():
            if k <= sec and (best is None or k >= best[0]):
                best = (k, v)
        return best[1] if best else None

    def __len__(self) -> int:
        return len(self._by_sec)


def _num(p: float):
    return int(p) if float(p).is_integer() else p


class Collector:
    def __init__(self, runtime_dir: str, ws_factory=None, http_get=None, clock=time.time,
                 mono=time.monotonic) -> None:
        self.dir, self.clock, self.mono = runtime_dir, clock, mono
        self.stop = threading.Event()
        self.book_sink = Sink(os.path.join(runtime_dir, BOOK_FILE))
        self.trade_sink = Sink(os.path.join(runtime_dir, TRADES_FILE))
        self.venue_sink = Sink(os.path.join(runtime_dir, VENUE_FILE))
        self.deriv_sink = Sink(os.path.join(runtime_dir, DERIV_FILE))
        self.events_sink = Sink(os.path.join(runtime_dir, EVENTS_FILE))
        self._dv = _data_version.Stamp()  # D8: rows carry the fresh-start epoch they belong to
        self.deriv: Optional[tuple] = None  # (fields, recv_ts), latest
        self._deriv_hist = _SecondHistory()
        self._quote_hist = {"lighter": _SecondHistory(), "paradex": _SecondHistory()}
        # Book state frozen at each second boundary: {sec: (b, a, book_ts, carry_ok, alive_ts, book_mono)} = state at
        # END of sec; book_ts/book_mono = measured wall/monotonic receive time of the last book frame applied.
        self._open_sec: Optional[int] = None
        self._frozen: dict = {}
        self.book = BfxBook()
        self.lock = threading.Lock()
        self.trades: list = []  # [recv_sec, [id, ms, amt, px], [receive_ts, receive_mono]]
        self.quotes = {"lighter": None, "paradex": None}  # (bid, ask, recv_ts, recv_mono)
        self.minute: dict = {}
        self.minute_rx: dict = {}  # {minute: {venue: 60 x [receive_ts, receive_mono] | None}} (HM #851 nit b)
        self._ws_factory = ws_factory or self._default_ws
        self._http_get = http_get or self._default_get
        self.reconnects = {"bitfinex": 0, "lighter": 0}
        self.book_alive_ts = 0.0  # last frame on the subscribed book channel (delta/snapshot/heartbeat)

    @staticmethod
    def _default_ws(url):
        import websocket
        return websocket.create_connection(url, timeout=10)

    @staticmethod
    def _default_get(url):
        with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310 - fixed public https URL
            return json.loads(r.read())

    # ---- message handlers (pure, unit-tested) ----------------------------------
    def _roll(self, now: float) -> None:
        """Caller holds self.lock. Freeze the book as it stood at the end of every second before int(now)."""
        sec = int(now)
        if self._open_sec is None:
            self._open_sec = sec
            return
        if sec > self._open_sec:
            b, a = self.book.top()
            carry_ok = bool(self.book.ts and self.book.bids and self.book.asks and max(self.book.bids) < min(self.book.asks))
            self._frozen[self._open_sec] = (b, a, self.book.ts, carry_ok, self.book_alive_ts, self.book.mono)
            self._open_sec = sec

    def _book_at(self, sec: int):
        keys = [k for k in self._frozen if k <= sec]
        return self._frozen[max(keys)] if keys else None

    def _event(self, now: float, ev: str, ch=None, _mono: Optional[float] = None, **extra) -> None:
        row = {"r": round(now, 3), "mono": round(self.mono() if _mono is None else _mono, 3), "ev": ev}
        if ch is not None:
            row["ch"] = ch
        row.update(extra)
        dv = self._dv.current()
        if dv:
            row["dv"] = dv
        self.events_sink.write(row)

    def on_bfx(self, msg, chans: dict, now: float, mono: Optional[float] = None) -> None:
        """``now``/``mono``: wall + monotonic clock read together right after the WS recv (HM #851 nit a)."""
        with self.lock:
            self._roll(now)
        if isinstance(msg, dict):
            if msg.get("event") == "subscribed":
                chans[msg.get("chanId")] = msg.get("channel")
            self._event(now, str(msg.get("event")), _mono=mono, msg={k: v for k, v in msg.items() if k != "event"})
            return
        if isinstance(msg, list) and len(msg) >= 2:
            kind = chans.get(msg[0])
            if msg[1] == "hb":
                self._event(now, "hb", _mono=mono, ch=kind)
            elif kind == "trades":
                if msg[1] in ("te", "tu") and len(msg) > 2 and isinstance(msg[2], list) and len(msg[2]) >= 2:
                    self._event(now, msg[1], _mono=mono, ch=kind, id=msg[2][0], mts=msg[2][1])
                elif isinstance(msg[1], list):
                    # (Re)subscribe snapshot: exchange history, possibly prints missed while disconnected. Kept here
                    # (not in bfx_trades rows, which hold only live prints received after subscribe).
                    self._event(now, "snapshot", _mono=mono, ch=kind, tr=[[int(t[0]), int(t[1]), float(t[2]), _num(float(t[3]))]
                                                             for t in msg[1] if isinstance(t, list) and len(t) >= 4])
        if isinstance(msg, list) and msg and chans.get(msg[0]) == "book":
            with self.lock:
                # Bitfinex heartbeats are per channel: only a frame (delta, snapshot or "hb") on the subscribed BOOK
                # channel proves the book is still being delivered. Frames on trades/status do not.
                self.book_alive_ts = now
        if not isinstance(msg, list) or len(msg) < 2 or msg[1] == "hb":
            return
        kind = chans.get(msg[0])
        with self.lock:
            mono = self.mono() if mono is None else mono  # only when a caller did not pass the recv-point reading
            if kind == "book":
                body = msg[1]
                if body and isinstance(body[0], list):
                    self.book.snapshot(body, now)
                    self.book.mono = mono
                elif body:
                    self.book.apply(body, now)
                    self.book.mono = mono
            elif kind == "status":
                if isinstance(msg[1], list) and msg[1] and not isinstance(msg[1][0], list):
                    parsed = parse_deriv_status(msg[1])
                    if parsed:
                        self.deriv = (parsed, now, mono)
                        self._deriv_hist.add(self.deriv, now)
            elif kind == "trades":
                if msg[1] == "te" and isinstance(msg[2], list):
                    t = msg[2]
                    self.trades.append([int(now), [int(t[0]), int(t[1]), float(t[2]), _num(float(t[3]))],
                                        rx_pair(now, mono)])
                # "tu" duplicates "te"; the (re)subscribe snapshot is history, not new prints (logged to EVENTS_FILE).

    def on_bfx_disconnect(self, now: Optional[float] = None, reason: Optional[str] = None) -> None:
        """The book channel is gone with the socket: nothing may be carried forward until a new book frame arrives.
        Conservative: no not-yet-written second may carry the book either."""
        now = self.clock() if now is None else now
        with self.lock:
            self._roll(now)
            self.book_alive_ts = 0.0
            self._frozen = {k: (b, a, ts, ok, 0.0, m) for k, (b, a, ts, ok, _alive, m) in self._frozen.items()}
        self._event(now, "disconnect", reason=reason or "unknown")

    def on_lighter(self, msg: dict, now: float, mono: Optional[float] = None) -> None:
        mono = self.mono() if mono is None else mono
        tk = msg.get("ticker") if isinstance(msg, dict) else None
        if isinstance(tk, dict):
            bid, ask = _f((tk.get("b") or {}).get("price")), _f((tk.get("a") or {}).get("price"))
            if bid and ask:
                with self.lock:
                    self.quotes["lighter"] = (bid, ask, now, mono)
                    self._quote_hist["lighter"].add(self.quotes["lighter"], now)

    def on_paradex(self, body: dict, now: float, mono: Optional[float] = None) -> None:
        mono = self.mono() if mono is None else mono
        bid, ask = _f((body or {}).get("bid")), _f((body or {}).get("ask"))
        if bid and ask:
            with self.lock:
                self.quotes["paradex"] = (bid, ask, now, mono)
                self._quote_hist["paradex"].add(self.quotes["paradex"], now)

    # ---- per-second close ----------------------------------------------------
    def close_second(self, sec: int, now: Optional[float] = None) -> None:
        end = sec + 1  # row ``s`` = state as of receive time <= s+1 (no look-ahead into the ~0.3-0.5 s of s+1 already seen)
        with self.lock:
            self._roll(self.clock() if now is None else now)
            snap = self._book_at(sec)
            b = a = []
            fresh = carried = False
            bts = bmono = 0.0
            if snap:
                sb, sa, bts, carry_ok, alive, bmono = snap
                fresh = bool(bts and end - bts <= MAX_QUOTE_AGE_SEC)
                # Quiet book, live connection: carry the unchanged book forward instead of writing no row (18 Oct
                # 17:38:08-13: a 6 s gap forced an exit to FORCED_NO_BOOK although the book had not changed).
                carried = bool(not fresh and carry_ok  # a crossed book is never carried
                               and alive > 0.0 and end - alive <= BOOK_LIVENESS_SEC)
                if fresh or carried:
                    b, a = sb, sa
            due = [x for x in self.trades if x[0] <= sec]
            trades, trade_rx = [x[1] for x in due], [x[2] for x in due]
            self.trades = [x for x in self.trades if x[0] > sec]
            deriv = self._deriv_hist.as_of(sec)
            quotes = {}
            for v, hist in self._quote_hist.items():
                q = hist.as_of(sec)
                quotes[v] = (q[0], q[1], q[2], q[3]) if q and end - q[2] <= MAX_QUOTE_AGE_SEC else None
            for k in [k for k in self._frozen if k < sec - 60]:
                if any(j > k and j <= sec for j in self._frozen):
                    self._frozen.pop(k)
        dv = self._dv.current()
        stamp = {"dv": dv} if dv else {}
        if b and a:
            self.book_sink.write({"s": sec, "b": b, "a": a, **({"carried": True} if carried else {}),
                                  **book_receive_stamp(bts, bmono), **stamp})
        if trades:
            last_rx = max(trade_rx, key=lambda r: r[0])
            self.trade_sink.write({"s": sec, "tr": trades, "rx": trade_rx, "receive_ts": last_rx[0],
                                   "receive_mono": last_rx[1], "receive_ts_source": "WS_FRAME", **stamp})
        if deriv and end - deriv[1] <= MAX_DERIV_AGE_SEC:
            self.deriv_sink.write({"s": sec, **{k: v for k, v in deriv[0].items() if v is not None},
                                   "age_s": round(end - deriv[1], 3), **book_receive_stamp(deriv[1], deriv[2]), **stamp})
        minute = sec - sec % 60
        row = self.minute.setdefault(minute, {v: [None] * 60 for v in quotes})
        rx = self.minute_rx.setdefault(minute, {v: [None] * 60 for v in quotes})
        for v, q in quotes.items():
            row[v][sec - minute] = None if q is None else [_num(q[0]), _num(q[1])]
            rx[v][sec - minute] = None if q is None else rx_pair(q[2], q[3])
        for m in [m for m in self.minute if m < minute]:
            data = self.minute.pop(m)
            data_rx = self.minute_rx.pop(m, {})
            if any(x is not None for seq in data.values() for x in seq):
                self.venue_sink.write({"m": m, **data, "rx": data_rx, "receive_ts_source": "WS_FRAME_OR_HTTP", **stamp})

    # ---- workers --------------------------------------------------------------
    def _ws_loop(self, name, url, subs, handler) -> None:
        attempt = 0
        while not self.stop.is_set():
            ws = None
            try:
                ws = self._ws_factory(url)
                if name == "bitfinex":
                    self._event(self.clock(), "connect")
                for s in subs:
                    ws.send(json.dumps(s))
                chans: dict = {}
                last = time.monotonic()
                attempt = 0
                while not self.stop.is_set():
                    try:
                        raw = ws.recv()
                    except Exception as exc:  # timeout or socket error
                        if "timed out" in str(exc).lower() and time.monotonic() - last < SILENT_RECONNECT_SEC:
                            continue
                        raise
                    rx_wall, rx_mono = self.clock(), self.mono()  # HM #851 nit a: one receive point, before parsing
                    last = time.monotonic()
                    msg = json.loads(raw)
                    if name == "bitfinex":
                        handler(msg, chans, rx_wall, mono=rx_mono)
                    else:
                        handler(msg, rx_wall, mono=rx_mono)
                if name == "bitfinex":
                    self.on_bfx_disconnect(reason="stop")  # graceful shutdown
            except Exception as exc:  # noqa: BLE001
                self.reconnects[name] += 1
                if name == "bitfinex":
                    self.on_bfx_disconnect(reason=type(exc).__name__)
                _log(f"{name} reconnect ({type(exc).__name__})")
            finally:
                try:
                    ws and ws.close()
                except Exception:  # noqa: BLE001
                    pass
            attempt += 1
            self.stop.wait(min(60.0, 2 ** min(attempt, 6)) * random.uniform(0.8, 1.2))

    def _paradex_loop(self) -> None:
        while not self.stop.is_set():
            t0 = time.monotonic()
            try:
                body = self._http_get(PARADEX_BBO)
                self.on_paradex(body, self.clock(), mono=self.mono())  # read together right after the GET returns
            except Exception:  # noqa: BLE001
                pass
            self.stop.wait(max(0.1, 1.0 - (time.monotonic() - t0)))

    def run(self) -> None:
        threads = [
            threading.Thread(target=self._ws_loop, daemon=True, args=("bitfinex", BFX_WS, [
                {"event": "subscribe", "channel": "book", "symbol": BFX_SYMBOL, "prec": "P0", "len": "25"},
                {"event": "subscribe", "channel": "trades", "symbol": BFX_SYMBOL},
                {"event": "subscribe", "channel": "status", "key": DERIV_KEY}], self.on_bfx)),
            threading.Thread(target=self._ws_loop, daemon=True, args=("lighter", LIGHTER_WS, [
                {"type": "subscribe", "channel": f"ticker/{LIGHTER_MARKET}"}], self.on_lighter)),
            threading.Thread(target=self._paradex_loop, daemon=True),
        ]
        for t in threads:
            t.start()
        _log(f"started dir={self.dir} symbol={BFX_SYMBOL}")
        last = int(self.clock()) - 1
        last_status = 0.0
        while not self.stop.is_set():
            now = self.clock()
            target = int(now - 0.3) - 1
            if target - last > 10:
                last = target - 10
            while last < target:
                last += 1
                self.close_second(last, now)
            if now - last_status > 900:
                last_status = now
                _log(f"rows book={self.book_sink.rows} trades={self.trade_sink.rows} venue={self.venue_sink.rows} deriv={self.deriv_sink.rows} "
                     f"events={self.events_sink.rows} "
                     f"failures={self.book_sink.failures + self.trade_sink.failures + self.venue_sink.failures + self.deriv_sink.failures + self.events_sink.failures} "
                     f"reconnects={self.reconnects}")
            self.stop.wait(0.2)


def main() -> int:
    c = Collector(os.getcwd())
    signal.signal(signal.SIGTERM, lambda *_: c.stop.set())
    c.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
