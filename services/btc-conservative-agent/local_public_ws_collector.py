"""Bitfinex **public** websocket collector → local MarketStore (SQLite/Parquet + gap log).

Keyless. Never imports bot.py. Never places orders. Day-1 local fresh-start path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from typing import Any, Callable, Optional

from local_fresh_runtime import (
    apply_local_fresh_environ,
    build_health_payload,
    is_local_fresh_start,
    resolve_data_dir,
    resolve_health_path,
    validate_paper_safe_env,
    write_health_json,
)
from local_market_persist import MarketStore

BFX_WS = "wss://api-pub.bitfinex.com/ws/2"
BFX_SYMBOL = os.getenv("LOCAL_FRESH_BFX_SYMBOL") or os.getenv(
    "STRATEGIST_BFX_SYMBOL", "tBTCF0:USTF0"
)
LEVELS = 10
SCHEMA = "local_public_ws_collector_v1"


class _Book:
    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.ts = 0.0

    def snapshot(self, rows: list, now: float) -> None:
        self.bids.clear()
        self.asks.clear()
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            px, _count, qty = float(row[0]), row[1], float(row[2])
            if qty > 0:
                self.bids[px] = qty
            elif qty < 0:
                self.asks[px] = abs(qty)
        self.ts = now

    def apply(self, row: list, now: float) -> None:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            return
        px, count, qty = float(row[0]), int(row[1]), float(row[2])
        side = self.bids if qty >= 0 else self.asks
        other = self.asks if qty >= 0 else self.bids
        if count == 0:
            side.pop(px, None)
            other.pop(px, None)
        else:
            other.pop(px, None)
            side[px] = abs(qty)
        self.ts = now

    def top10(self) -> tuple[list, list]:
        bids = sorted(self.bids.items(), key=lambda kv: -kv[0])[:LEVELS]
        asks = sorted(self.asks.items(), key=lambda kv: kv[0])[:LEVELS]
        return [[p, q] for p, q in bids], [[p, q] for p, q in asks]


class LocalPublicCollector:
    """Public book+trades → MarketStore; records gap log on missing book seconds."""

    def __init__(
        self,
        store: MarketStore,
        *,
        symbol: str = BFX_SYMBOL,
        ws_factory: Optional[Callable[[str], Any]] = None,
        clock=time.time,
        health_path: Optional[str] = None,
    ) -> None:
        self.store = store
        self.symbol = symbol
        self.ws_factory = ws_factory or self._default_ws
        self.clock = clock
        self.health_path = health_path
        self.stop = threading.Event()
        self.book = _Book()
        self.chans: dict[Any, str] = {}
        self._open_sec: Optional[int] = None
        self._frozen: dict[int, tuple] = {}
        self._trades: list = []
        self.reconnects = 0
        self.frames = 0
        self.last_error: Optional[str] = None

    @staticmethod
    def _default_ws(url: str):
        import websocket

        return websocket.create_connection(url, timeout=10)

    def request_stop(self) -> None:
        self.stop.set()

    def on_message(self, msg: Any, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else float(now)
        self.frames += 1
        sec = int(now)
        if self._open_sec is None:
            self._open_sec = sec
        elif sec > self._open_sec:
            self._close_seconds(sec)
        if isinstance(msg, dict):
            if msg.get("event") == "subscribed":
                self.chans[msg.get("chanId")] = str(msg.get("channel") or "")
            return
        if not isinstance(msg, list) or len(msg) < 2:
            return
        kind = self.chans.get(msg[0])
        if msg[1] == "hb":
            return
        if kind == "book":
            body = msg[1]
            if body and isinstance(body[0], list):
                self.book.snapshot(body, now)
            elif isinstance(body, list):
                self.book.apply(body, now)
        elif kind == "trades":
            if msg[1] == "te" and isinstance(msg[2], list) and len(msg[2]) >= 4:
                t = msg[2]
                self._trades.append(
                    [int(t[0]), int(t[1]), float(t[2]), float(t[3])]
                )

    def on_disconnect(self, now: Optional[float] = None, reason: str = "disconnect") -> None:
        now = self.clock() if now is None else float(now)
        if self._open_sec is not None:
            # Force-close current open second as a gap boundary marker.
            self.store.log_explicit_gap(
                "WS_DISCONNECT",
                int(now),
                int(now),
                detail={"reason": reason, "reconnects": self.reconnects},
            )
        self.last_error = reason

    def _close_seconds(self, new_sec: int) -> None:
        assert self._open_sec is not None
        while self._open_sec < new_sec:
            s = self._open_sec
            bids, asks = self.book.top10()
            carried = 0
            if not bids or not asks:
                # No book yet — record gap for this second.
                self.store.log_explicit_gap(
                    "BOOK_EMPTY", s, s, detail={"reason": "no_top_of_book"}
                )
            else:
                row = {
                    "s": s,
                    "b": bids,
                    "a": asks,
                    "bid": bids[0][0],
                    "ask": asks[0][0],
                    "receive_ts": self.book.ts or float(s),
                    "carried": carried,
                }
                self.store.append_book_second(row)
            if self._trades:
                self.store.append_trades_second(
                    {
                        "s": s,
                        "tr": list(self._trades),
                        "receive_ts": float(s) + 0.999,
                    }
                )
                self._trades.clear()
            self._open_sec = s + 1

    def write_health(self, *, ok: bool, reasons: Optional[list] = None) -> Optional[str]:
        if not self.health_path:
            return None
        payload = build_health_payload(
            ok=ok,
            reasons=reasons or [],
            data_dir=str(self.store.root),
            gap_log_path=str(self.store.gap_log_path),
            collector={
                "schema": SCHEMA,
                "symbol": self.symbol,
                "frames": self.frames,
                "reconnects": self.reconnects,
                "last_error": self.last_error,
                "store": self.store.stats(),
            },
        )
        return write_health_json(self.health_path, payload)

    def run_forever(self, *, max_seconds: float = 0.0) -> int:
        """Connect public WS, loop until stop or max_seconds. Returns exit code."""
        started = self.clock()
        while not self.stop.is_set():
            if max_seconds > 0 and (self.clock() - started) >= max_seconds:
                break
            try:
                ws = self.ws_factory(BFX_WS)
                ws.send(
                    json.dumps(
                        {
                            "event": "subscribe",
                            "channel": "book",
                            "symbol": self.symbol,
                            "prec": "P0",
                            "freq": "F0",
                            "len": str(LEVELS * 2),
                        }
                    )
                )
                ws.send(
                    json.dumps(
                        {
                            "event": "subscribe",
                            "channel": "trades",
                            "symbol": self.symbol,
                        }
                    )
                )
                while not self.stop.is_set():
                    if max_seconds > 0 and (self.clock() - started) >= max_seconds:
                        break
                    raw = ws.recv()
                    if raw is None:
                        break
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    msg = json.loads(raw)
                    self.on_message(msg)
                try:
                    ws.close()
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001 — reconnect loop
                self.reconnects += 1
                self.on_disconnect(reason=type(exc).__name__)
                self.write_health(ok=False, reasons=[f"WS:{type(exc).__name__}"])
                if self.stop.wait(2.0):
                    break
                continue
            break
        # Flush open second if we have book.
        if self._open_sec is not None:
            self._close_seconds(self._open_sec + 1)
        self.write_health(ok=True)
        return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Local public Bitfinex WS → SQLite/gap log")
    parser.add_argument("--data-dir", default="", help="Override BOT_DATA_DIR")
    parser.add_argument("--health-json", default="", help="Override LOCAL_FRESH_HEALTH_JSON")
    parser.add_argument("--max-seconds", type=float, default=0.0)
    parser.add_argument(
        "--skip-env-gate",
        action="store_true",
        help="Unit/dev only: skip LOCAL_FRESH_START confirmation gate",
    )
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ["BOT_DATA_DIR"] = args.data_dir
    if args.health_json:
        os.environ["LOCAL_FRESH_HEALTH_JSON"] = args.health_json

    if not args.skip_env_gate:
        if not is_local_fresh_start():
            print(
                "REFUSED: set LOCAL_FRESH_START=1 and "
                "LOCAL_FRESH_START_CONFIRM=I_OWN_LOCAL_PAPER_AFTER_FLY_DELETE",
                file=sys.stderr,
            )
            return 78
        apply_local_fresh_environ()
        ok, reasons = validate_paper_safe_env()
        if not ok:
            print("REFUSED_LOCAL_FRESH_START: " + ";".join(reasons), file=sys.stderr)
            return 78

    data_dir = resolve_data_dir()
    health_path = str(resolve_health_path())
    store = MarketStore(data_dir)
    collector = LocalPublicCollector(store, health_path=health_path)
    try:
        return collector.run_forever(max_seconds=float(args.max_seconds or 0.0))
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
