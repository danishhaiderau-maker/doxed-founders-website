"""Shadow-only cross-venue BTC perp tape collector (separate Fly process).

Started by fly-entrypoint.sh as its own niced process: it never imports bot.py
or Flask, holds no trade lock, serves no HTTP, uses no API keys and cannot
place, change or cancel an order. It subscribes to public WebSocket market
data (see ``cross_venue_tape.VENUES``), buckets it on the epoch-second clock
shared with the Bitfinex ``market_microstructure_1s`` tape, and writes:

* ``cross_venue_tape_1m.jsonl`` - one compact row per UTC minute (evidence;
  shipped by the research segment shipper like every other JSONL stream);
* ``cross_venue_live.json`` - atomically replaced each second with the last
  ``LIVE_HISTORY_SEC`` per-second mids and connection health, read by the bot
  for the shadow ``leader`` challenger and the staleness alarm (not evidence).

Every connection reconnects with capped exponential backoff and jitter, and a
socket that goes silent for ``SILENT_RECONNECT_SEC`` is recycled.
"""
from __future__ import annotations

import glob
import json
import os
import random
import re
import signal
import sys
import threading
import time
from collections import deque
from typing import Callable, Optional

import cross_venue_tape as cvt
from data_epoch import activate_from_env, stamp_active

SILENT_RECONNECT_SEC = 30.0
APP_PING_SEC = 15.0
BACKOFF_BASE_SEC = 1.0
BACKOFF_MAX_SEC = 60.0
STABLE_SESSION_SEC = 60.0
CLOSE_LAG_SEC = 0.3
MINUTE_FINALIZE_LAG_SEC = 3.0
MAX_CATCHUP_SEC = 10
BFX_TAIL_BYTES = 96 * 1024
STATUS_LOG_EVERY_SEC = 900
_BUCKET_RE = re.compile(rb'"bucket_ts":\s*(\d+)')


def backoff_delay(attempt: int, rng: random.Random) -> float:
    base = min(BACKOFF_MAX_SEC, BACKOFF_BASE_SEC * (2 ** max(0, int(attempt))))
    return round(base * rng.uniform(0.8, 1.2), 3)


LOG_TAG = "cross-venue"


def _log(message: str) -> None:
    sys.stdout.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} [{LOG_TAG}] {message}\n")
    sys.stdout.flush()


class ConnectionWorker(threading.Thread):
    """One public WebSocket connection feeding a venue accumulator."""

    def __init__(self, venue: str, spec: dict, accumulator: cvt.VenueAccumulator,
                 stop: threading.Event, ws_factory: Optional[Callable] = None,
                 clock: Callable[[], float] = time.time, sleep: Optional[Callable] = None,
                 seed: Optional[int] = None, parser: Optional[Callable] = None) -> None:
        super().__init__(name=f"cv-{spec['name']}", daemon=True)
        self.venue = venue
        self.spec = spec
        self.acc = accumulator
        self.stop_event = stop
        self.parser = parser or cvt.PARSERS[venue]
        self._factory = ws_factory
        self._clock = clock
        self._sleep = sleep or stop.wait
        self._rng = random.Random(seed)
        self.connected = False
        self.reconnects = 0
        self.last_error = None
        self.session_started_ts = None

    def _connect(self):
        if self._factory is not None:
            return self._factory(self.spec["url"])
        import websocket
        return websocket.create_connection(self.spec["url"], timeout=10)

    def run_session(self) -> None:
        import websocket
        ws = self._connect()
        try:
            if self.spec.get("subscribe"):
                ws.send(json.dumps(self.spec["subscribe"]))
            ws.settimeout(1.0)
            self.connected = True
            self.session_started_ts = self._clock()
            last_rx = last_ping = self._clock()
            ping = self.spec.get("app_ping")
            while not self.stop_event.is_set():
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    message = None
                now = self._clock()
                if message:
                    last_rx = now
                    if message != "pong":
                        try:
                            data = json.loads(message)
                        except ValueError:
                            data = None
                        if isinstance(data, dict):
                            self.acc.on_events(self.parser(data), now)
                if ping and now - last_ping >= APP_PING_SEC:
                    ws.send(ping)
                    last_ping = now
                if now - last_rx > SILENT_RECONNECT_SEC:
                    raise TimeoutError(f"silent for {now - last_rx:.0f}s")
        finally:
            self.connected = False
            try:
                ws.close()
            except Exception:
                pass

    def run(self) -> None:
        attempt = 0
        while not self.stop_event.is_set():
            started = self._clock()
            try:
                self.run_session()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            if self.stop_event.is_set():
                break
            if self._clock() - started >= STABLE_SESSION_SEC:
                attempt = 0
            delay = backoff_delay(attempt, self._rng)
            attempt += 1
            self.reconnects += 1
            _log(f"{self.spec['name']} reconnect #{self.reconnects} in {delay:.1f}s ({self.last_error})")
            self._sleep(delay)


def read_bfx_mids(path: str, minute_ts: int, tail_bytes: int = BFX_TAIL_BYTES) -> list:
    """Fresh Bitfinex mids for the 60 buckets of ``minute_ts`` from the tape tail."""
    mids = [None] * 60
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            chunk = handle.read()
    except OSError:
        return mids
    for line in chunk.split(b"\n"):
        match = _BUCKET_RE.search(line)
        if not match:
            continue
        offset = int(match.group(1)) - int(minute_ts)
        if not 0 <= offset < 60:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("fresh") is True and row.get("valid_bbo") is True:
            bid, ask = cvt._finite(row.get("bid")), cvt._finite(row.get("ask"))
            if bid and ask:
                mids[offset] = (bid + ask) / 2.0
    return mids


def _rss_mb() -> Optional[float]:
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024, 1)
    except (OSError, ValueError, AttributeError, IndexError):
        return None


class Collector:
    def __init__(self, runtime_dir: str, venues=None, *, clock: Callable[[], float] = time.time,
                 ws_factory: Optional[Callable] = None, start_workers: bool = True) -> None:
        self.dir = runtime_dir
        self.tape_path = os.path.join(runtime_dir, cvt.FILE_NAME)
        self.live_path = os.path.join(runtime_dir, cvt.LIVE_FILE)
        self.bfx_path = os.path.join(runtime_dir, "market_microstructure_1s.jsonl")
        self.venues = [v for v in (venues or cvt.VENUES) if v in cvt.VENUES]
        self.clock = clock
        self.stop = threading.Event()
        self.acc = {v: cvt.VenueAccumulator(v) for v in self.venues}
        self.workers = {
            v: [ConnectionWorker(v, spec, self.acc[v], self.stop, ws_factory=ws_factory, clock=clock)
                for spec in cvt.VENUES[v]["connections"]]
            for v in self.venues
        }
        self._start_workers = start_workers
        self.started_ts = clock()
        self.last_closed = int(self.started_ts) - 1
        self._prev_quote = {v: None for v in self.venues}
        self._history = {v: deque(maxlen=cvt.LIVE_HISTORY_SEC) for v in self.venues}
        self._minutes: dict = {}
        self._cpu_mark = (time.process_time(), time.monotonic())
        self.stats = {"rows_written": 0, "bytes_written": 0, "bytes_today": 0, "day": None,
                      "write_failures": 0, "live_write_failures": 0, "cpu_pct_1m": None,
                      "rss_mb": None, "seconds_closed": 0, "seconds_skipped": 0}
        self._last_status_log = 0.0

    def start(self) -> None:
        if self._start_workers:
            for workers in self.workers.values():
                for w in workers:
                    w.start()

    def close_seconds(self, now: float) -> None:
        target = int(now - CLOSE_LAG_SEC) - 1
        if target - self.last_closed > MAX_CATCHUP_SEC:
            skipped = target - MAX_CATCHUP_SEC - self.last_closed
            self.stats["seconds_skipped"] += skipped
            for v in self.venues:
                self._history[v].extend([None] * min(skipped, cvt.LIVE_HISTORY_SEC))
            self.last_closed = target - MAX_CATCHUP_SEC
        while self.last_closed < target:
            sec = self.last_closed + 1
            minute = sec - sec % 60
            bucket = self._minutes.setdefault(minute, {v: [] for v in self.venues})
            for v in self.venues:
                sample = self.acc[v].close_second(sec, self._prev_quote[v])
                # Zero taker flow while a socket is down is "unknown", not "no trades".
                sample["up"] = all(w.connected for w in self.workers[v])
                if sample["quote"] is not None:
                    self._prev_quote[v] = sample["quote"]
                bucket[v].append(sample)
                self._history[v].append(None if sample["mid"] is None else round(sample["mid"], 2))
            self.last_closed = sec
            self.stats["seconds_closed"] += 1

    def finalize_minutes(self, now: float) -> list:
        written = []
        for minute in sorted(self._minutes):
            if minute + 60 + MINUTE_FINALIZE_LAG_SEC > now or self.last_closed < minute + 59:
                break
            samples = self._minutes.pop(minute)
            row = cvt.encode_minute(
                minute,
                {v: [{k: s.get(k) for k in ("sec", "mid", "last", "buy", "sell", "up")} for s in samples[v]]
                 for v in self.venues},
                read_bfx_mids(self.bfx_path, minute),
                derivatives={v: self.acc[v].derivatives() for v in self.venues},
                latency_ms={v: self.acc[v].drain_latency_ms() for v in self.venues},
                meta=self._meta(),
            )
            if self._append(row):
                written.append(row)
        return written

    def _meta(self) -> dict:
        cpu, wall = time.process_time(), time.monotonic()
        prev_cpu, prev_wall = self._cpu_mark
        self._cpu_mark = (cpu, wall)
        if wall > prev_wall:
            self.stats["cpu_pct_1m"] = round((cpu - prev_cpu) / (wall - prev_wall) * 100.0, 3)
        self.stats["rss_mb"] = _rss_mb()
        return {
            "collector_version": cvt.COLLECTOR_VERSION,
            "cpu_pct": self.stats["cpu_pct_1m"],
            "rss_mb": self.stats["rss_mb"],
            "msgs": {v: self.acc[v].msgs for v in self.venues},
            "reconnects": {v: sum(w.reconnects for w in self.workers[v]) for v in self.venues},
        }

    def _rotate_if_needed(self) -> None:
        try:
            if os.path.getsize(self.tape_path) <= cvt.ROTATE_BYTES:
                return
        except OSError:
            return
        suffixes = [int(p.rsplit(".", 1)[-1]) for p in glob.glob(self.tape_path + ".*")
                    if p.rsplit(".", 1)[-1].isdigit()]
        os.rename(self.tape_path, f"{self.tape_path}.{max(suffixes, default=0) + 1}")

    def _append(self, row: dict) -> bool:
        line = (json.dumps(stamp_active(row), separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        try:
            self._rotate_if_needed()
            with open(self.tape_path, "ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, ValueError) as exc:
            self.stats["write_failures"] += 1
            _log(f"tape append failed: {type(exc).__name__}: {exc}")
            return False
        day = time.strftime("%Y-%m-%d", time.gmtime(row["minute_ts"]))
        if day != self.stats["day"]:
            self.stats["day"], self.stats["bytes_today"] = day, 0
        self.stats["rows_written"] += 1
        self.stats["bytes_written"] += len(line)
        self.stats["bytes_today"] += len(line)
        return True

    def live_payload(self, now: float) -> dict:
        venues = {}
        for v in self.venues:
            workers = self.workers[v]
            errors = [w.last_error for w in workers if w.last_error]
            venues[v] = {
                "connected": all(w.connected for w in workers),
                "reconnects": sum(w.reconnects for w in workers),
                "last_error": errors[-1] if errors else None,
                "last_msg_ts": self.acc[v].last_msg_ts,
                "last_bbo_ts": (self._prev_quote[v] or (None, None, None))[2],
                "msgs": self.acc[v].msgs,
            }
        return {
            "schema": cvt.LIVE_SCHEMA,
            "collector_version": cvt.COLLECTOR_VERSION,
            "written_ts": round(now, 3),
            "started_ts": round(self.started_ts, 3),
            "pid": os.getpid(),
            "history_start_ts": self.last_closed - len(self._history[self.venues[0]]) + 1
            if self.venues else None,
            "history_end_ts": self.last_closed,
            "mids": {v: list(self._history[v]) for v in self.venues},
            "venues": venues,
            "derivatives": {v: self.acc[v].derivatives() for v in self.venues},
            "stats": {**self.stats, "file": cvt.FILE_NAME},
        }

    def write_live(self, now: float) -> None:
        tmp = self.live_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.live_payload(now), handle, separators=(",", ":"))
            os.replace(tmp, self.live_path)
        except (OSError, ValueError):
            self.stats["live_write_failures"] += 1

    def tick(self, now: float) -> None:
        self.close_seconds(now)
        self.finalize_minutes(now)
        self.write_live(now)
        if now - self._last_status_log >= STATUS_LOG_EVERY_SEC:
            self._last_status_log = now
            _log(f"status rows={self.stats['rows_written']} bytes_today={self.stats['bytes_today']} "
                 f"cpu_pct_1m={self.stats['cpu_pct_1m']} rss_mb={self.stats['rss_mb']} "
                 f"msgs={ {v: self.acc[v].msgs for v in self.venues} }")

    def run(self) -> None:
        self.start()
        _log(f"started venues={self.venues} dir={self.dir}")
        while not self.stop.is_set():
            now = self.clock()
            wait = (int(now) + 1 + CLOSE_LAG_SEC) - now
            if self.stop.wait(max(0.05, wait)):
                break
            try:
                self.tick(self.clock())
            except Exception as exc:
                _log(f"tick failed: {type(exc).__name__}: {exc}")
        _log("stopped")


def main() -> int:
    venues = [v.strip() for v in os.getenv("CROSS_VENUE_VENUES", "binance,bybit,okx").split(",") if v.strip()]
    activate_from_env(os.getcwd())
    collector = Collector(os.getcwd(), venues)

    def _stop(*_):
        collector.stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    collector.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
