"""Watch-only market-context collector (separate niced Fly process).

Started by fly-entrypoint.sh as its own ``nice -n 10`` process, exactly like
``cross_venue_collector``: it never imports bot.py or Flask, holds no trade
lock, serves no HTTP, uses no API keys and has no order, fee or venue-account
code. It subscribes to public WebSockets (``market_context_tape.FEEDS``),
polls public REST once a minute (``REST_ENDPOINTS``) and writes:

* ``market_context_1m.jsonl`` - one row per UTC minute: per-second spot mids
  with ``up`` masks, Coinbase premiums, derivatives snapshot + deltas,
  liquidation minute summary, session/macro flags, trailing regime, meta;
* ``liquidations.jsonl`` - one row per forced-liquidation event;
* ``market_context_live.json`` - operational state for the bot's status
  snapshot and monitors (excluded from shipping; not evidence).

Both JSONL files are append-only, fsynced, rotated at ``ROTATE_BYTES`` and
shipped by the existing research segment shipper.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import signal
import sys
import threading
import time
import urllib.request
from collections import deque
from typing import Callable, Optional

import cross_venue_collector as cvc
import cross_venue_tape as cvt
from data_epoch import activate_from_env, stamp_active
import market_context_tape as mct
import market_session_calendar as msc
import trailing_regime as tr

CLOSE_LAG_SEC = 0.3
MINUTE_FINALIZE_LAG_SEC = 4.0
MAX_CATCHUP_SEC = 10
LIVE_WRITE_EVERY_SEC = 5
REST_OFFSET_SEC = 20
REST_TIMEOUT_SEC = 5.0
REST_MAX_BYTES = 256 * 1024
RV_WINDOW_MIN = 15
STATUS_LOG_EVERY_SEC = 900
USER_AGENT = "doxed-market-context/1 (watch-only research)"
_RV_RE = re.compile(rb'"rv15_bps":\s*([0-9.]+)')
_MINUTE_RE = re.compile(rb'"minute_ts":\s*(\d+)')
_FRESH_RE = re.compile(rb'"fresh":\s*true')
_VALID_BBO_RE = re.compile(rb'"valid_bbo":\s*true')
_BID_RE = re.compile(rb'"bid":\s*([0-9][0-9.eE+-]*)')
_ASK_RE = re.compile(rb'"ask":\s*([0-9][0-9.eE+-]*)')


def _rotation_paths(path: str) -> list:
    """Active file plus numbered rotations, oldest first (higher suffix = newer)."""
    rotated = sorted(
        (int(p.rsplit(".", 1)[-1]), p) for p in glob.glob(path + ".*")
        if p.rsplit(".", 1)[-1].isdigit()
    )
    paths = [p for _, p in rotated]
    if os.path.exists(path):
        paths.append(path)
    return paths


def rv15_from_closes(closes: list) -> Optional[float]:
    """rv15 of consecutive 1-minute closes; shared by live labelling and seeding."""
    rets = [math.log(b / a) if a and b else None for a, b in zip(closes, closes[1:])]
    if sum(1 for r in rets if r is not None) < RV_WINDOW_MIN - 2:
        return None
    return tr.rv_bps(rets)


def tape_minute_closes(paths: list, start_ts: int, end_ts: int) -> dict:
    """{minute_ts: last fresh valid Bitfinex mid} from the 1 s tape, ``start_ts <= s < end_ts``.

    Same selection as ``cross_venue_collector.read_bfx_mids`` + ``_regime_for``:
    fresh rows with a valid BBO, the close being the latest such second.
    """
    best: dict = {}
    for path in paths:
        try:
            handle = open(path, "rb")
        except OSError:
            continue
        with handle:
            for line in handle:
                match = cvc._BUCKET_RE.search(line)
                if not match:
                    continue
                sec = int(match.group(1))
                if sec < start_ts or sec >= end_ts:
                    continue
                if not (_FRESH_RE.search(line) and _VALID_BBO_RE.search(line)):
                    continue
                bid_m, ask_m = _BID_RE.search(line), _ASK_RE.search(line)
                if not bid_m or not ask_m:
                    continue
                try:
                    bid, ask = float(bid_m.group(1)), float(ask_m.group(1))
                except ValueError:
                    continue
                if not (math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask > 0):
                    continue
                minute = sec - sec % 60
                offset = sec - minute
                prev = best.get(minute)
                if prev is None or offset >= prev[0]:
                    best[minute] = (offset, (bid + ask) / 2.0)
    return {minute: mid for minute, (_, mid) in best.items()}


def context_rv_values(paths: list, start_ts: int, end_ts: int) -> list:
    """[(minute_ts, rv15_bps)] already stamped in market_context rows, chronological."""
    out = []
    for path in paths:
        try:
            handle = open(path, "rb")
        except OSError:
            continue
        with handle:
            for line in handle:
                minute_m = _MINUTE_RE.search(line)
                rv_m = _RV_RE.search(line)
                if not minute_m or not rv_m:
                    continue
                minute = int(minute_m.group(1))
                if start_ts <= minute < end_ts:
                    try:
                        out.append((minute, float(rv_m.group(1))))
                    except ValueError:
                        continue
    out.sort(key=lambda item: item[0])
    return out


def _http_json(url: str, timeout: float = REST_TIMEOUT_SEC):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(REST_MAX_BYTES).decode("utf-8"))


class RestPoller(threading.Thread):
    """Fetches the public derivatives endpoints once a minute at ``REST_OFFSET_SEC``."""

    def __init__(self, stop: threading.Event, fetch: Callable = _http_json,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(name="mc-rest", daemon=True)
        self.stop_event = stop
        self.fetch = fetch
        self.clock = clock
        self._lock = threading.Lock()
        self._by_minute: dict = {}
        self.latest: dict = {}
        self.errors = 0
        self.last_ms = None

    def poll_once(self, now: Optional[float] = None) -> dict:
        now = self.clock() if now is None else now
        minute = int(now) - int(now) % 60
        snap = {}
        t0 = time.monotonic()
        for venue, endpoints in mct.REST_ENDPOINTS.items():
            payloads, errors = {}, []
            for key, url in endpoints:
                try:
                    payloads[key] = self.fetch(url)
                except Exception as exc:
                    errors.append(f"{key}:{type(exc).__name__}")
                    self.errors += 1
            fields = mct.parse_rest(venue, payloads)
            fetched = round(self.clock(), 3)
            core = (fields.get("mark"), fields.get("oi_btc"),
                    fields.get("funding_rate") if fields.get("funding_rate") is not None
                    else fields.get("predicted_funding_rate"))
            status = "OK" if not errors and all(v is not None for v in core) else (
                "ERROR" if len(errors) == len(endpoints) else "PARTIAL")
            snap[venue] = {**fields, "fetched_ts": fetched, "status": status,
                           "errors": errors or None}
        self.last_ms = round((time.monotonic() - t0) * 1000.0, 1)
        with self._lock:
            prev = self.latest
            for venue, cur in snap.items():
                cur.update(mct.deriv_deltas(cur, prev.get(venue)))
            self.latest = snap
            self._by_minute[minute] = snap
            for old in sorted(self._by_minute)[:-5]:
                self._by_minute.pop(old, None)
        return snap

    def for_minute(self, minute_ts: int) -> dict:
        """Snapshot fetched inside ``minute_ts`` or explicit MISSING rows (never carried forward)."""
        with self._lock:
            snap = self._by_minute.get(int(minute_ts))
        if snap:
            return snap
        return {v: {"status": "MISSING", "fetched_ts": None} for v in mct.REST_ENDPOINTS}

    def run(self) -> None:
        while not self.stop_event.is_set():
            now = self.clock()
            target = now - now % 60 + REST_OFFSET_SEC
            if target <= now:
                target += 60
            if self.stop_event.wait(target - now):
                break
            try:
                self.poll_once()
            except Exception as exc:
                self.errors += 1
                cvc._log(f"rest poll failed: {type(exc).__name__}: {exc}")


class Collector:
    def __init__(self, runtime_dir: str, *, clock: Callable[[], float] = time.time,
                 ws_factory: Optional[Callable] = None, fetch: Callable = _http_json,
                 start_workers: bool = True) -> None:
        self.dir = runtime_dir
        self.path = os.path.join(runtime_dir, mct.FILE_NAME)
        self.liq_path = os.path.join(runtime_dir, mct.LIQ_FILE_NAME)
        self.live_path = os.path.join(runtime_dir, mct.LIVE_FILE)
        self.bfx_path = os.path.join(runtime_dir, "market_microstructure_1s.jsonl")
        self.cv_live_path = os.path.join(runtime_dir, cvt.LIVE_FILE)
        self.clock = clock
        self.stop = threading.Event()
        self.spot = {f: cvt.VenueAccumulator(f) for f in mct.SPOT_FEEDS + mct.AUX_SPOT_FEEDS}
        self.liq = {v: mct.LiquidationBuffer(v) for v in mct.LIQ_VENUES}
        self.routers = {}
        for feed, spec in mct.FEEDS.items():
            routes = {r: self.spot[r] for r in spec["routes"] if r in self.spot}
            if feed in mct.LIQ_FEED_VENUE:
                routes = {"liq": self.liq[mct.LIQ_FEED_VENUE[feed]]}
            self.routers[feed] = mct.FeedRouter(routes)
        self.workers = {
            feed: cvc.ConnectionWorker(feed, spec["connection"], self.routers[feed], self.stop,
                                       ws_factory=ws_factory, clock=clock, parser=mct.PARSERS[feed])
            for feed, spec in mct.FEEDS.items()
        }
        self.rest = RestPoller(self.stop, fetch=fetch, clock=clock)
        self._start_workers = start_workers
        self.started_ts = clock()
        self.last_closed = int(self.started_ts) - 1
        self._prev_quote = {f: None for f in self.spot}
        self._minutes: dict = {}
        self._liq_minutes: dict = {}
        self._bfx_closes: deque = deque(maxlen=RV_WINDOW_MIN + 1)
        self._regime = tr.TrailingPercentile()
        self._cpu_mark = (time.process_time(), time.monotonic())
        self._last_live = 0.0
        self._last_status_log = 0.0
        self.stats = {"rows_written": 0, "liq_rows_written": 0, "bytes_written": 0, "bytes_today": 0,
                      "day": None, "write_failures": 0, "live_write_failures": 0, "cpu_pct_1m": None,
                      "rss_mb": None, "seconds_closed": 0, "seconds_skipped": 0,
                      "regime_backfill_n": 0}
        self.regime_seed: dict = {}
        self._last_regime: dict = {}
        self._backfill_regime()

    def _feed_connected(self, feed: str) -> bool:
        worker = self.workers.get(feed)
        return bool(worker and worker.connected)

    def _backfill_regime(self) -> None:
        """Rehydrate the trailing regime from durable files so a restart is not a fresh WARMUP.

        The Bitfinex 1 s tape (active file + rotations, ~55 h on the volume) is
        the primary source: rv15 is recomputed from its 1-minute closes with
        the exact live formula, so a restarted collector labels the next minute
        as an uninterrupted one would. rv15 values already stamped in older
        market_context rows fill the part of the trailing window the tape no
        longer covers. Values are pushed oldest first so window eviction stays
        chronological; everything is strictly before the first live minute.
        """
        started = time.monotonic()
        start_minute = int(self.started_ts) - int(self.started_ts) % 60
        window_start = start_minute - (self._regime.window + RV_WINDOW_MIN) * 60
        closes = tape_minute_closes(_rotation_paths(self.bfx_path), window_start, start_minute)
        tape_first = min(closes) if closes else start_minute
        context = context_rv_values(_rotation_paths(self.path), window_start, tape_first)
        for _, rv in context:
            self._regime.push(rv)
        tape_rv_n = 0
        if closes:
            window: deque = deque(maxlen=RV_WINDOW_MIN + 1)
            for minute in range(tape_first, start_minute, 60):
                window.append(closes.get(minute))
                rv = rv15_from_closes(list(window))
                if rv is not None:
                    self._regime.push(rv)
                    tape_rv_n += 1
            self._bfx_closes.extend(window)
        self.stats["regime_backfill_n"] = len(self._regime)
        self.regime_seed = {
            "source": "bitfinex_1s_tape+market_context_rv15",
            "tape_minutes": len(closes),
            "tape_rv_n": tape_rv_n,
            "context_rv_n": len(context),
            "first_minute_ts": context[0][0] if context else (tape_first if closes else None),
            "last_minute_ts": (start_minute - 60) if closes else (context[-1][0] if context else None),
            "history_n": len(self._regime),
            "min_history": self._regime.min_history,
            "labels_ready": len(self._regime) >= self._regime.min_history,
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 1),
        }

    def start(self) -> None:
        if self._start_workers:
            for w in self.workers.values():
                w.start()
            self.rest.start()

    def close_seconds(self, now: float) -> None:
        target = int(now - CLOSE_LAG_SEC) - 1
        if target - self.last_closed > MAX_CATCHUP_SEC:
            self.stats["seconds_skipped"] += target - MAX_CATCHUP_SEC - self.last_closed
            self.last_closed = target - MAX_CATCHUP_SEC
        coinbase_up = self._feed_connected("coinbase")
        binance_up = self._feed_connected("binance_spot")
        conn = {"coinbase": coinbase_up, "coinbase_usdt": coinbase_up, "binance_spot": binance_up}
        liq_up = {v: self._feed_connected(f) for f, v in mct.LIQ_FEED_VENUE.items()}
        while self.last_closed < target:
            sec = self.last_closed + 1
            minute = sec - sec % 60
            bucket = self._minutes.setdefault(minute, {"mid": {f: [None] * 60 for f in self.spot},
                                                       "up": {f: [False] * 60 for f in self.spot},
                                                       "liq_up": {v: 0 for v in mct.LIQ_VENUES}})
            off = sec - minute
            for f, acc in self.spot.items():
                sample = acc.close_second(sec, self._prev_quote[f])
                if sample["quote"] is not None:
                    self._prev_quote[f] = sample["quote"]
                fresh = conn[f] and sample["mid"] is not None
                bucket["mid"][f][off] = sample["mid"] if fresh else None
                bucket["up"][f][off] = fresh
            for v, up in liq_up.items():
                bucket["liq_up"][v] += 1 if up else 0
            self.last_closed = sec
            self.stats["seconds_closed"] += 1
        self._flush_liquidations()

    def _flush_liquidations(self) -> None:
        events = []
        for buf in self.liq.values():
            events.extend(buf.drain())
        if not events:
            return
        events.sort(key=lambda e: e["recv_ts"])
        rows = []
        for e in events:
            row = {"schema": mct.LIQ_SCHEMA, "ts": e["recv_ts"], **e,
                   "collector_version": mct.COLLECTOR_VERSION}
            rows.append(row)
            minute = int(e["recv_ts"]) - int(e["recv_ts"]) % 60
            self._liq_minutes.setdefault(minute, []).append(e)
        if self._append(self.liq_path, rows, liq=True):
            self.stats["liq_rows_written"] += len(rows)

    def _binance_perp_mids(self, minute_ts: int) -> list:
        try:
            with open(self.cv_live_path, "r", encoding="utf-8") as handle:
                live = json.load(handle)
        except (OSError, ValueError):
            return [None] * 60
        if not isinstance(live, dict) or live.get("schema") != cvt.LIVE_SCHEMA:
            return [None] * 60
        return [cvt.live_mid_at(live, "binance", minute_ts + i) for i in range(60)]

    def _regime_for(self, bfx_mids: list) -> dict:
        close = next((m for m in reversed(bfx_mids) if m), None)
        self._bfx_closes.append(close)
        rv = rv15_from_closes(list(self._bfx_closes))
        obs = self._regime.observe(rv)
        self._last_regime = {"label": obs["label"], "rank_pct": obs["rank_pct"],
                             "history_n": obs["history_n"]}
        return {"schema": tr.SCHEMA, "source": "bitfinex_1m_close", "rv15_bps": rv,
                "trailing_window_min": self._regime.window, **obs}

    def finalize_minutes(self, now: float) -> list:
        written = []
        for minute in sorted(self._minutes):
            if minute + 60 + MINUTE_FINALIZE_LAG_SEC > now or self.last_closed < minute + 59:
                break
            bucket = self._minutes.pop(minute)
            bfx = cvc.read_bfx_mids(self.bfx_path, minute)
            usdt_vals = [m for m, u in zip(bucket["mid"]["coinbase_usdt"], bucket["up"]["coinbase_usdt"]) if u and m]
            liq_events = self._liq_minutes.pop(minute, [])
            liqs = {v: mct.liquidation_minute_summary([e for e in liq_events if e["venue"] == v],
                                                      bucket["liq_up"][v])
                    for v in mct.LIQ_VENUES}
            perp = self._binance_perp_mids(minute)
            spot_last = next((m for m in reversed(bucket["mid"]["binance_spot"]) if m), None)
            perp_last = next((m for m in reversed(perp) if m), None)
            deriv = self.rest.for_minute(minute)
            deriv = {**deriv, "binance_perp_vs_spot": {
                "basis_bp": None if not spot_last or not perp_last
                else round((perp_last / spot_last - 1.0) * 1e4, 3),
                "source": "cross_venue_live.binance_mid/binance_spot_mid_last_fresh_second"}}
            row = mct.encode_minute(
                minute,
                {f: bucket["mid"][f] for f in mct.SPOT_FEEDS},
                {f: bucket["up"][f] for f in mct.SPOT_FEEDS},
                bfx_mids=bfx,
                binance_perp_mids=perp,
                usdt={"mean": None if not usdt_vals else round(sum(usdt_vals) / len(usdt_vals), 6),
                      "last": usdt_vals[-1] if usdt_vals else None, "up_sec": len(usdt_vals)},
                derivatives=deriv,
                liquidations=liqs,
                flags=msc.flags(minute),
                regime=self._regime_for(bfx),
                meta=self._meta(),
            )
            if self._append(self.path, [row]):
                written.append(row)
                self.stats["rows_written"] += 1
        stale = [m for m in self._liq_minutes if m < self.last_closed - 600]
        for m in stale:
            self._liq_minutes.pop(m, None)
        return written

    def _meta(self) -> dict:
        cpu, wall = time.process_time(), time.monotonic()
        prev_cpu, prev_wall = self._cpu_mark
        self._cpu_mark = (cpu, wall)
        if wall > prev_wall:
            self.stats["cpu_pct_1m"] = round((cpu - prev_cpu) / (wall - prev_wall) * 100.0, 3)
        self.stats["rss_mb"] = cvc._rss_mb()
        return {
            "collector_version": mct.COLLECTOR_VERSION,
            "calendar_version": msc.CALENDAR_VERSION,
            "cpu_pct": self.stats["cpu_pct_1m"],
            "rss_mb": self.stats["rss_mb"],
            "msgs": {f: r.msgs for f, r in self.routers.items()},
            "reconnects": {f: w.reconnects for f, w in self.workers.items()},
            "rest_ms": self.rest.last_ms,
            "rest_errors": self.rest.errors,
            "seconds_skipped": self.stats["seconds_skipped"],
        }

    def _rotate_if_needed(self, path: str) -> None:
        try:
            if os.path.getsize(path) <= mct.ROTATE_BYTES:
                return
        except OSError:
            return
        suffixes = [int(p.rsplit(".", 1)[-1]) for p in glob.glob(path + ".*")
                    if p.rsplit(".", 1)[-1].isdigit()]
        os.rename(path, f"{path}.{max(suffixes, default=0) + 1}")

    def _append(self, path: str, rows: list, liq: bool = False) -> bool:
        data = "".join(json.dumps(stamp_active(r), separators=(",", ":"), allow_nan=False) + "\n"
                       for r in rows).encode("utf-8")
        try:
            self._rotate_if_needed(path)
            with open(path, "ab") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, ValueError) as exc:
            self.stats["write_failures"] += 1
            cvc._log(f"append failed {os.path.basename(path)}: {type(exc).__name__}: {exc}")
            return False
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        if day != self.stats["day"]:
            self.stats["day"], self.stats["bytes_today"] = day, 0
        self.stats["bytes_written"] += len(data)
        self.stats["bytes_today"] += len(data)
        return True

    def live_payload(self, now: float) -> dict:
        feeds = {}
        for feed, w in self.workers.items():
            feeds[feed] = {"connected": w.connected, "reconnects": w.reconnects,
                           "last_error": w.last_error, "last_msg_ts": self.routers[feed].last_msg_ts,
                           "msgs": self.routers[feed].msgs}
        latest = self.rest.latest
        return {
            "schema": mct.LIVE_SCHEMA,
            "collector_version": mct.COLLECTOR_VERSION,
            "written_ts": round(now, 3),
            "started_ts": round(self.started_ts, 3),
            "pid": os.getpid(),
            "last_closed_ts": self.last_closed,
            "feeds": feeds,
            "derivatives": {v: {k: d.get(k) for k in ("status", "fetched_ts", "funding_rate",
                                                       "predicted_funding_rate", "basis_bp", "oi_btc")}
                            for v, d in latest.items()},
            "liquidation_events": {v: b.events for v, b in self.liq.items()},
            "stats": {**self.stats, "file": mct.FILE_NAME, "liq_file": mct.LIQ_FILE_NAME},
            "regime": {"history_n": len(self._regime), "min_history": self._regime.min_history,
                       "last": self._last_regime or None, "seed": self.regime_seed or None},
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
        if now - self._last_live >= LIVE_WRITE_EVERY_SEC:
            self._last_live = now
            self.write_live(now)
        if now - self._last_status_log >= STATUS_LOG_EVERY_SEC:
            self._last_status_log = now
            cvc._log(f"status rows={self.stats['rows_written']} liq_rows={self.stats['liq_rows_written']} "
                     f"bytes_today={self.stats['bytes_today']} cpu_pct_1m={self.stats['cpu_pct_1m']} "
                     f"rss_mb={self.stats['rss_mb']} msgs={ {f: r.msgs for f, r in self.routers.items()} }")

    def run(self) -> None:
        self.start()
        cvc._log(f"started feeds={list(self.workers)} dir={self.dir} regime_backfill={len(self._regime)}")
        while not self.stop.is_set():
            now = self.clock()
            wait = (int(now) + 1 + CLOSE_LAG_SEC) - now
            if self.stop.wait(max(0.05, wait)):
                break
            try:
                self.tick(self.clock())
            except Exception as exc:
                cvc._log(f"tick failed: {type(exc).__name__}: {exc}")
        cvc._log("stopped")


def main() -> int:
    cvc.LOG_TAG = "market-context"
    activate_from_env(os.getcwd())
    collector = Collector(os.getcwd())

    def _stop(*_):
        collector.stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    collector.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
