"""Bitfinex perp top-of-book from the WS ``book`` channel (P0, F0), isolated from the main WS.

LIVE-FIX-1510 (HM 09:05): written for DISLOC (order-book ask at signal time, not the throttled
ticker channel). DISLOC was removed 23 Oct 2026; the remaining user is the tile-integrity frozen-feed
check (bot._tile_integrity_run_once, FeedWatch), which starts it on demand. One daemon thread, its own socket, never touches bot state.
``top()`` -> (bid, ask, last_update_ts) or (None, None, None) when not ready.
"""
from __future__ import annotations

import json
import os
import threading
import time

SYMBOL = os.getenv("BFX_BOOK_SYMBOL", "tBTCF0:USTF0")
URL = "wss://api-pub.bitfinex.com/ws/2"
_lock = threading.Lock()
_bids: dict = {}
_asks: dict = {}
_state = {"ts": None, "started": False, "connected": False, "msgs": 0, "reconnects": 0, "last_error": None}


def apply_levels(levels, bids=None, asks=None) -> None:
    """Pure: apply Bitfinex P0 book rows [price, count, amount] to bid/ask dicts."""
    bids = _bids if bids is None else bids
    asks = _asks if asks is None else asks
    for lv in levels:
        if not isinstance(lv, (list, tuple)) or len(lv) < 3:
            continue
        price, count, amount = float(lv[0]), int(lv[1]), float(lv[2])
        side = bids if amount > 0 else asks
        if count == 0:
            (bids if amount == 1 else asks if amount == -1 else side).pop(price, None)
        else:
            side[price] = abs(amount)


def top():
    with _lock:
        if not _bids or not _asks or _state["ts"] is None:
            return None, None, None
        bid, ask = max(_bids), min(_asks)
        if ask < bid:
            return None, None, None
        return bid, ask, _state["ts"]


def status() -> dict:
    with _lock:
        return {**_state, "levels": [len(_bids), len(_asks)]}


def _on_message(_ws, msg) -> None:
    try:
        data = json.loads(msg)
    except ValueError:
        return
    if isinstance(data, dict):
        if data.get("event") == "subscribed":
            with _lock:
                _bids.clear()
                _asks.clear()
        return
    if not isinstance(data, list) or len(data) < 2 or data[1] == "hb":
        return
    body = data[1]
    with _lock:
        if body and isinstance(body[0], list):
            _bids.clear()
            _asks.clear()
            apply_levels(body)
        else:
            apply_levels([body])
        _state["ts"] = time.time()
        _state["msgs"] += 1


def _run() -> None:
    import websocket
    while True:
        try:
            def _open(ws):
                with _lock:
                    _state["connected"] = True
                ws.send(json.dumps({"event": "subscribe", "channel": "book", "symbol": SYMBOL,
                                    "prec": "P0", "freq": "F0", "len": "25"}))
            app = websocket.WebSocketApp(URL, on_open=_open, on_message=_on_message)
            app.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _state["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        with _lock:
            _state["connected"] = False
            _state["ts"] = None
            _state["reconnects"] += 1
            _bids.clear()
            _asks.clear()
        time.sleep(2)


def ensure_started() -> None:
    with _lock:
        if _state["started"]:
            return
        _state["started"] = True
    threading.Thread(target=_run, name="bfx-book-top", daemon=True).start()
