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
"""
from __future__ import annotations

import collections
import glob
import json
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
_HIST = 16  # per-source receive history kept so a late close can pick the value as of s+1
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


class BfxBook:
    """Bitfinex P0 book (price, count, amount): count 0 deletes; amount > 0 bid, < 0 ask."""

    def __init__(self) -> None:
        self.bids: dict = {}
        self.asks: dict = {}
        self.ts = 0.0

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


def _num(p: float):
    return int(p) if float(p).is_integer() else p


class Collector:
    def __init__(self, runtime_dir: str, ws_factory=None, http_get=None, clock=time.time) -> None:
        self.dir, self.clock = runtime_dir, clock
        self.stop = threading.Event()
        self.book_sink = Sink(os.path.join(runtime_dir, BOOK_FILE))
        self.trade_sink = Sink(os.path.join(runtime_dir, TRADES_FILE))
        self.venue_sink = Sink(os.path.join(runtime_dir, VENUE_FILE))
        self.deriv_sink = Sink(os.path.join(runtime_dir, DERIV_FILE))
        self.events_sink = Sink(os.path.join(runtime_dir, EVENTS_FILE))
        self._dv = _data_version.Stamp()  # D8: rows carry the fresh-start epoch they belong to
        self.deriv: Optional[tuple] = None  # (fields, recv_ts), latest
        self._deriv_hist: collections.deque = collections.deque(maxlen=_HIST)
        self._quote_hist = {"lighter": collections.deque(maxlen=_HIST), "paradex": collections.deque(maxlen=_HIST)}
        # Book state frozen at each second boundary: {sec: (b, a, book_ts, carry_ok, alive_ts)} = state at END of sec.
        self._open_sec: Optional[int] = None
        self._frozen: dict = {}
        self.book = BfxBook()
        self.lock = threading.Lock()
        self.trades: list = []  # [recv_sec, [id, ms, amt, px]]
        self.quotes = {"lighter": None, "paradex": None}  # (bid, ask, recv_ts)
        self.minute: dict = {}
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
            self._frozen[self._open_sec] = (b, a, self.book.ts, carry_ok, self.book_alive_ts)
            self._open_sec = sec

    def _book_at(self, sec: int):
        keys = [k for k in self._frozen if k <= sec]
        return self._frozen[max(keys)] if keys else None

    def _event(self, now: float, ev: str, ch=None, **extra) -> None:
        row = {"r": round(now, 3), "ev": ev}
        if ch is not None:
            row["ch"] = ch
        row.update(extra)
        dv = self._dv.current()
        if dv:
            row["dv"] = dv
        self.events_sink.write(row)

    def on_bfx(self, msg, chans: dict, now: float) -> None:
        with self.lock:
            self._roll(now)
        if isinstance(msg, dict):
            if msg.get("event") == "subscribed":
                chans[msg.get("chanId")] = msg.get("channel")
            self._event(now, str(msg.get("event")), msg={k: v for k, v in msg.items() if k != "event"})
            return
        if isinstance(msg, list) and len(msg) >= 2:
            kind = chans.get(msg[0])
            if msg[1] == "hb":
                self._event(now, "hb", ch=kind)
            elif kind == "trades":
                if msg[1] in ("te", "tu") and len(msg) > 2 and isinstance(msg[2], list) and len(msg[2]) >= 2:
                    self._event(now, msg[1], ch=kind, id=msg[2][0], mts=msg[2][1])
                elif isinstance(msg[1], list):
                    # (Re)subscribe snapshot: exchange history, possibly prints missed while disconnected. Kept here
                    # (not in bfx_trades rows, which hold only live prints received after subscribe).
                    self._event(now, "snapshot", ch=kind, tr=[[int(t[0]), int(t[1]), float(t[2]), _num(float(t[3]))]
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
            if kind == "book":
                body = msg[1]
                if body and isinstance(body[0], list):
                    self.book.snapshot(body, now)
                elif body:
                    self.book.apply(body, now)
            elif kind == "status":
                if isinstance(msg[1], list) and msg[1] and not isinstance(msg[1][0], list):
                    parsed = parse_deriv_status(msg[1])
                    if parsed:
                        self.deriv = (parsed, now)
                        self._deriv_hist.append(self.deriv)
            elif kind == "trades":
                if msg[1] == "te" and isinstance(msg[2], list):
                    t = msg[2]
                    self.trades.append([int(now), [int(t[0]), int(t[1]), float(t[2]), _num(float(t[3]))]])
                # "tu" duplicates "te"; the (re)subscribe snapshot is history, not new prints (logged to EVENTS_FILE).

    def on_bfx_disconnect(self, now: Optional[float] = None) -> None:
        """The book channel is gone with the socket: nothing may be carried forward until a new book frame arrives.
        Conservative: no not-yet-written second may carry the book either."""
        now = self.clock() if now is None else now
        with self.lock:
            self._roll(now)
            self.book_alive_ts = 0.0
            self._frozen = {k: (b, a, ts, ok, 0.0) for k, (b, a, ts, ok, _alive) in self._frozen.items()}
        self._event(now, "disconnect")

    def on_lighter(self, msg: dict, now: float) -> None:
        tk = msg.get("ticker") if isinstance(msg, dict) else None
        if isinstance(tk, dict):
            bid, ask = _f((tk.get("b") or {}).get("price")), _f((tk.get("a") or {}).get("price"))
            if bid and ask:
                with self.lock:
                    self.quotes["lighter"] = (bid, ask, now)
                    self._quote_hist["lighter"].append(self.quotes["lighter"])

    def on_paradex(self, body: dict, now: float) -> None:
        bid, ask = _f((body or {}).get("bid")), _f((body or {}).get("ask"))
        if bid and ask:
            with self.lock:
                self.quotes["paradex"] = (bid, ask, now)
                self._quote_hist["paradex"].append(self.quotes["paradex"])

    # ---- per-second close ----------------------------------------------------
    def close_second(self, sec: int, now: Optional[float] = None) -> None:
        end = sec + 1  # row ``s`` = state as of receive time <= s+1 (no look-ahead into the ~0.3-0.5 s of s+1 already seen)
        with self.lock:
            self._roll(self.clock() if now is None else now)
            snap = self._book_at(sec)
            b = a = []
            fresh = carried = False
            if snap:
                sb, sa, bts, carry_ok, alive = snap
                fresh = bool(bts and end - bts <= MAX_QUOTE_AGE_SEC)
                # Quiet book, live connection: carry the unchanged book forward instead of writing no row (18 Oct
                # 17:38:08-13: a 6 s gap forced an exit to FORCED_NO_BOOK although the book had not changed).
                carried = bool(not fresh and carry_ok  # a crossed book is never carried
                               and alive > 0.0 and end - alive <= BOOK_LIVENESS_SEC)
                if fresh or carried:
                    b, a = sb, sa
            trades = [t for rs, t in self.trades if rs <= sec]
            self.trades = [x for x in self.trades if x[0] > sec]
            deriv = next((d for d in reversed(self._deriv_hist) if d[1] <= end), None)
            quotes = {}
            for v, hist in self._quote_hist.items():
                q = next((x for x in reversed(hist) if x[2] <= end), None)
                quotes[v] = (q[0], q[1]) if q and end - q[2] <= MAX_QUOTE_AGE_SEC else None
            for k in [k for k in self._frozen if k < sec - 60]:
                if any(j > k and j <= sec for j in self._frozen):
                    self._frozen.pop(k)
        dv = self._dv.current()
        stamp = {"dv": dv} if dv else {}
        if b and a:
            self.book_sink.write({"s": sec, "b": b, "a": a, **({"carried": True} if carried else {}), **stamp})
        if trades:
            self.trade_sink.write({"s": sec, "tr": trades, **stamp})
        if deriv and end - deriv[1] <= MAX_DERIV_AGE_SEC:
            self.deriv_sink.write({"s": sec, **{k: v for k, v in deriv[0].items() if v is not None},
                                   "age_s": round(end - deriv[1], 3), **stamp})
        minute = sec - sec % 60
        row = self.minute.setdefault(minute, {v: [None] * 60 for v in quotes})
        for v, q in quotes.items():
            row[v][sec - minute] = None if q is None else [_num(q[0]), _num(q[1])]
        for m in [m for m in self.minute if m < minute]:
            data = self.minute.pop(m)
            if any(x is not None for seq in data.values() for x in seq):
                self.venue_sink.write({"m": m, **data, **stamp})

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
                    last = time.monotonic()
                    msg = json.loads(raw)
                    if name == "bitfinex":
                        handler(msg, chans, self.clock())
                    else:
                        handler(msg, self.clock())
            except Exception as exc:  # noqa: BLE001
                self.reconnects[name] += 1
                if name == "bitfinex":
                    self.on_bfx_disconnect()
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
                self.on_paradex(self._http_get(PARADEX_BBO), self.clock())
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
