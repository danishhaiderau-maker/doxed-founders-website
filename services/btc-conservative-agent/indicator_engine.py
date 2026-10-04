"""Live Indicator Edge engine (separate niced Fly process, pure observation).

Started by fly-entrypoint.sh as its own ``nice -n 10`` process, like the
cross-venue and market-context collectors. It never imports bot.py or Flask,
holds no trade lock, serves no HTTP, opens no network connection, uses no API
keys and has no order code. It only reads files the collectors already write:

* ``market_microstructure_1s.jsonl`` (+ rotations) - Bitfinex 1 s tape;
* ``cross_venue_tape_1m.jsonl`` - Binance/Bybit/OKX minute tape;
* ``market_context_1m.jsonl`` - funding, OI and liquidations;
* ``decision_feature_snapshots.jsonl`` - latest AI call (regime label only);

and writes:

* ``indicator_bars_v1.jsonl`` - one append-only, fsynced row per closed 3-minute
  bar (``indicator_edge_spec.BAR_SCHEMA``), shipped by the segment shipper;
* ``indicator_engine_live.json`` - operational state for /api/status and the
  monitor (excluded from shipping; not evidence).

Restart continuity: bars already written are reloaded from our own rows, newer
bars are rebuilt from the tape, so a restarted engine continues the series it
printed instead of re-deriving (and possibly repainting) it.
"""
from __future__ import annotations

import glob
import json
import os
import re
import signal
import sys
import threading
import time
import uuid
from collections import deque
from typing import Callable, Optional

import cross_venue_collector as cvc
import cross_venue_tape as cvt
import indicator_edge_spec as spec
import indicator_engine_core as core
from data_epoch import activate_from_env, stamp_active
from research_reset_writer_fence import sidecar_append_admitted

ENGINE_VERSION = "indicator_engine_v1_20261004b"  # b: PITCHFORK INSUFFICIENT_SWINGS -> W
POLL_SEC = 2.0
CLOSE_LAG_SEC = 8.0
INPUT_WAIT_MAX_SEC = 60.0
STREAM_DOWN_SEC = 300
BACKFILL_MAX_SEC = 2 * 3600
LIVE_WRITE_EVERY_SEC = 5
STATUS_LOG_EVERY_SEC = 900
ROTATE_BYTES = 20 * 1024 * 1024
MAX_RSS_MB = float(os.environ.get("INDICATOR_ENGINE_MAX_RSS_MB", "300"))
AI_TAIL_BYTES = 512 * 1024
WARM_XV_LEAD_SEC = 2 * 3600
TAPE_FILE = "market_microstructure_1s.jsonl"
AI_FILE = "decision_feature_snapshots.jsonl"
BAR_KEYS = ("ts", "close_ts", "o", "h", "l", "c", "mid_c", "bid_c", "ask_c", "vol", "buy", "sell", "trades",
            "fresh_sec", "seen_sec", "filled", "spread_bp", "tob_imb", "tape", "xv", "mc")
_BAR_TS_RE = re.compile(rb'"bar_ts":\s*(\d+)')
_MINUTE_RE = re.compile(rb'"minute_ts":\s*(\d+)')


def rotation_paths(path: str) -> list:
    """Active file plus numbered rotations, oldest first (higher suffix = newer)."""
    rotated = sorted((int(p.rsplit(".", 1)[-1]), p) for p in glob.glob(path + ".*")
                     if p.rsplit(".", 1)[-1].isdigit())
    paths = [p for _, p in rotated]
    if os.path.exists(path):
        paths.append(path)
    return paths


def _newest_rotation(path: str) -> Optional[str]:
    rotated = rotation_paths(path)
    rotated = [p for p in rotated if p != path]
    return rotated[-1] if rotated else None


class FileTail:
    """Incremental line reader that survives ``x.jsonl -> x.jsonl.N`` rotation."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.offset = 0
        self.ident = None
        self._partial = b""

    def seek_end(self) -> None:
        try:
            st = os.stat(self.path)
        except OSError:
            return
        self.offset, self.ident = st.st_size, (st.st_dev, st.st_ino)

    def _read_from(self, path: str, offset: int) -> tuple:
        try:
            with open(path, "rb") as handle:
                handle.seek(offset)
                data = handle.read()
        except OSError:
            return b"", offset
        return data, offset + len(data)

    def read_lines(self) -> list:
        out = b""
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        ident = (st.st_dev, st.st_ino)
        if self.ident is not None and ident != self.ident:
            # Rotated: finish the renamed file from our offset, then start the new one.
            rotated = _newest_rotation(self.path)
            if rotated is not None:
                try:
                    rst = os.stat(rotated)
                    if (rst.st_dev, rst.st_ino) == self.ident and rst.st_size >= self.offset:
                        out, _ = self._read_from(rotated, self.offset)
                except OSError:
                    pass
            self.offset = 0
        elif st.st_size < self.offset:
            self.offset = 0
        self.ident = ident
        data, self.offset = self._read_from(self.path, self.offset)
        buf = self._partial + out + data
        if not buf:
            return []
        lines = buf.split(b"\n")
        self._partial = lines.pop()
        return [line for line in lines if line.strip()]


def _loads(line: bytes) -> Optional[dict]:
    try:
        row = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    return row if isinstance(row, dict) else None


def iter_rows(paths: list, start_ts: int, end_ts: int, ts_re: re.Pattern):
    """Rows whose ``ts_re`` timestamp is in ``[start_ts, end_ts)``, streamed (regex prefilter before JSON)."""
    for path in paths:
        try:
            handle = open(path, "rb")
        except OSError:
            continue
        with handle:
            for line in handle:
                match = ts_re.search(line)
                if not match:
                    continue
                ts = int(match.group(1))
                if start_ts <= ts < end_ts:
                    row = _loads(line)
                    if row is not None:
                        yield row


def read_tail_rows(path: str, max_bytes: int) -> list:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if size > max_bytes:
        lines = lines[1:]
    return [r for r in (_loads(line) for line in lines if line.strip()) if r is not None]


def bar_of_row(row: dict) -> Optional[dict]:
    bar = row.get("bar")
    if row.get("schema") != spec.BAR_SCHEMA or not isinstance(bar, dict):
        return None
    try:
        int(bar["ts"])
    except (KeyError, TypeError, ValueError):
        return None
    return bar


class Engine:
    def __init__(self, runtime_dir: str, *, clock: Callable[[], float] = time.time) -> None:
        self.dir = runtime_dir
        self.clock = clock
        self.stop = threading.Event()
        self.out_path = os.path.join(runtime_dir, spec.BAR_FILE)
        self.live_path = os.path.join(runtime_dir, spec.LIVE_FILE)
        self.tape_path = os.path.join(runtime_dir, TAPE_FILE)
        self.xv_path = os.path.join(runtime_dir, cvt.FILE_NAME)
        self.mc_path = os.path.join(runtime_dir, "market_context_1m.jsonl")
        self.ai_path = os.path.join(runtime_dir, AI_FILE)
        self.boot_id = f"ie-{uuid.uuid4().hex[:12]}"
        self.started_ts = clock()
        self.sha = spec.feature_set_sha()
        self.builder = core.BarBuilder()
        self.bars: deque = deque(maxlen=spec.HISTORY_BARS)
        self.last_written: Optional[int] = None
        self.tails = {k: FileTail(p) for k, p in (("tape", self.tape_path), ("xv", self.xv_path),
                                                  ("mc", self.mc_path))}
        self._cpu_mark = (time.process_time(), time.monotonic())
        self._last_live = 0.0
        self._last_status_log = 0.0
        self.last_row: Optional[dict] = None
        self.stats = {"rows_written": 0, "late_rows_written": 0, "bytes_written": 0, "bytes_today": 0, "day": None,
                      "write_failures": 0, "reset_fenced_skips": 0, "live_write_failures": 0, "compute_failures": 0, "bars_skipped": 0,
                      "input_wait_timeouts": 0, "cpu_pct_1m": None, "rss_mb": None, "compute_ms_last": None,
                      "compute_ms_max": None, "warm": {}}

    # ------------------------------------------------------------ warm start
    def warm_start(self) -> None:
        """Reload printed bars, then feed every input row after them into the builder.

        Tails are positioned first so no row appended during the warm read is
        lost; the overlap is harmless (tape seconds are idempotent, minute rows
        must strictly advance). Restart bars are emitted (``late``) or skipped by
        :meth:`close_bars`; on a first-ever start the rebuilt bars are history
        only and the first row is the next bar that closes live.
        """
        started = time.monotonic()
        now = self.clock()
        current = core.bar_start(now)
        history_start = current - spec.HISTORY_BARS * spec.BAR_SEC
        for tail in self.tails.values():
            tail.seek_end()
        stored = {}
        for row in iter_rows(rotation_paths(self.out_path), history_start, current, _BAR_TS_RE):
            bar = bar_of_row(row)
            if bar is not None and row.get("feature_set_version") == spec.FEATURE_SET_VERSION:
                stored[int(bar["ts"])] = bar
        far = 2 ** 40
        tape_rows = 0
        for row in iter_rows(rotation_paths(self.tape_path), history_start, far, cvc._BUCKET_RE):
            tape_rows += int(self.builder.add_tape_row(row))
        for row in iter_rows(rotation_paths(self.xv_path), history_start - WARM_XV_LEAD_SEC, far, _MINUTE_RE):
            self.builder.add_cross_venue_row(row)
        for row in iter_rows(rotation_paths(self.mc_path), history_start, far, _MINUTE_RE):
            self.builder.add_market_context_row(row)
        self.builder.drop_before(history_start)
        # History ends at the last printed bar; on a first-ever start, at the last bar
        # already past its input wait (the next bar to close is the first row).
        last_hist = max(stored) if stored else core.bar_start(now - INPUT_WAIT_MAX_SEC) - spec.BAR_SEC
        rebuilt = 0
        started_data = False
        for ts in range(history_start, last_hist + 1, spec.BAR_SEC):
            built = self.builder.finalize(ts, self.bars[-1] if self.bars else None)
            # What was printed wins over a rebuild; unprinted history comes from the tape.
            bar = stored.get(ts) or built
            started_data = started_data or bar.get("c") is not None
            if started_data:
                self.bars.append(bar)
                rebuilt += int(ts not in stored)
        self.last_written = last_hist
        self.stats["warm"] = {"stored_bars": len(stored), "rebuilt_bars": rebuilt, "tape_rows": tape_rows,
                              "history_bars": len(self.bars),
                              "elapsed_ms": round((time.monotonic() - started) * 1000.0, 1)}

    # ----------------------------------------------------------------- inputs
    def poll_inputs(self) -> None:
        for line in self.tails["tape"].read_lines():
            row = _loads(line)
            if row is not None:
                self.builder.add_tape_row(row)
        for line in self.tails["xv"].read_lines():
            row = _loads(line)
            if row is not None:
                self.builder.add_cross_venue_row(row)
        for line in self.tails["mc"].read_lines():
            row = _loads(line)
            if row is not None:
                self.builder.add_market_context_row(row)

    def _inputs_ready(self, bar_ts: int) -> bool:
        """Every live input has reached the bar's last second/minute.

        A stream silent for ``STREAM_DOWN_SEC`` before the bar is treated as
        down and not waited for (the bar is health-flagged instead), so an
        outage does not add the full input wait to every row.
        """
        last_minute = bar_ts + spec.BAR_SEC - 60
        down_before = bar_ts - STREAM_DOWN_SEC
        tape = self.builder.latest_tape_ts or 0
        tape_ok = tape >= bar_ts + spec.BAR_SEC - 1 or tape < down_before
        ok = tape_ok
        for latest in (self.builder.last_xv_minute, self.builder.last_mc_minute):
            latest = latest or 0
            ok = ok and (latest >= last_minute or latest < down_before)
        return ok

    # ---------------------------------------------------------------- closing
    def close_bars(self, now: float) -> list:
        written = []
        next_ts = (self.last_written + spec.BAR_SEC) if self.last_written is not None else core.bar_start(now) - spec.BAR_SEC
        while not self.stop.is_set():
            close_ts = next_ts + spec.BAR_SEC
            if now < close_ts + CLOSE_LAG_SEC:
                break
            if not self._inputs_ready(next_ts) and now < close_ts + INPUT_WAIT_MAX_SEC:
                break
            if not self._inputs_ready(next_ts):
                self.stats["input_wait_timeouts"] += 1
            late = now > close_ts + INPUT_WAIT_MAX_SEC + POLL_SEC * 2
            if now - close_ts > BACKFILL_MAX_SEC:
                # Too old to be live evidence: keep the series continuous, do not emit.
                self.bars.append(self.builder.finalize(next_ts, self.bars[-1] if self.bars else None))
                self.stats["bars_skipped"] += 1
                self.last_written = next_ts
            else:
                row = self._emit(self.builder.finalize(next_ts, self.bars[-1] if self.bars else None), late=late)
                if row is not None:
                    written.append(row)
            next_ts += spec.BAR_SEC
        self.builder.drop_before(next_ts)
        return written

    def _latest_ai(self, close_ts: float) -> dict:
        return core.latest_ai(read_tail_rows(self.ai_path, AI_TAIL_BYTES), close_ts)

    def _emit(self, bar: dict, *, late: bool) -> Optional[dict]:
        self.bars.append(bar)
        self.last_written = bar["ts"]
        started = time.perf_counter()
        try:
            features = core.compute_features(list(self.bars), xv_seen=self.builder.xv_imb_seen,
                                             ai=self._latest_ai(bar["close_ts"]))
        except Exception as exc:  # keep the sidecar alive; a missing row is visible to the monitor
            self.stats["compute_failures"] += 1
            cvc._log(f"compute failed bar_ts={bar['ts']}: {type(exc).__name__}: {exc}")
            return None
        compute_ms = round((time.perf_counter() - started) * 1000.0, 2)
        emitted = self.clock()
        row = {
            "schema": spec.BAR_SCHEMA,
            "feature_set_version": spec.FEATURE_SET_VERSION,
            "feature_set_sha": self.sha,
            "engine_version": ENGINE_VERSION,
            "boot_id": self.boot_id,
            "bar_ts": int(bar["ts"]),
            "bar_close_ts": int(bar["close_ts"]),
            "ts": round(emitted, 3),
            "emitted_lag_sec": round(emitted - bar["close_ts"], 3),
            "late": bool(late),
            "health": core.row_health(list(self.bars)),
            "status_counts": core.status_counts(features["f"]),
            "regime": features["regime"],
            "levels": features["levels"],
            "bar": {k: bar.get(k) for k in BAR_KEYS},
            "f": features["f"],
            "compute_ms": compute_ms,
        }
        if features.get("reasons"):
            row["status_reasons"] = dict(features["reasons"])
        self.stats["compute_ms_last"] = compute_ms
        self.stats["compute_ms_max"] = max(compute_ms, self.stats["compute_ms_max"] or 0.0)
        if self._append(row):
            self.stats["rows_written"] += 1
            if late:
                self.stats["late_rows_written"] += 1
            self.last_row = row
        return row

    # ----------------------------------------------------------------- output
    def _rotate_if_needed(self) -> None:
        try:
            if os.path.getsize(self.out_path) <= ROTATE_BYTES:
                return
        except OSError:
            return
        suffixes = [int(p.rsplit(".", 1)[-1]) for p in glob.glob(self.out_path + ".*")
                    if p.rsplit(".", 1)[-1].isdigit()]
        os.rename(self.out_path, f"{self.out_path}.{max(suffixes, default=0) + 1}")

    def _append(self, row: dict) -> bool:
        try:
            data = (json.dumps(stamp_active(row), separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
            # The bar file is a reset deletion target; this process is outside
            # the bot's in-process reset barriers.
            with sidecar_append_admitted(self.dir) as admitted:
                if not admitted:
                    self.stats["reset_fenced_skips"] += 1
                    return False
                self._rotate_if_needed()
                with open(self.out_path, "ab") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
        except (OSError, ValueError) as exc:
            self.stats["write_failures"] += 1
            cvc._log(f"append failed: {type(exc).__name__}: {exc}")
            return False
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        if day != self.stats["day"]:
            self.stats["day"], self.stats["bytes_today"] = day, 0
        self.stats["bytes_written"] += len(data)
        self.stats["bytes_today"] += len(data)
        return True

    def live_payload(self, now: float) -> dict:
        last = self.last_row or {}
        return {
            "schema": spec.LIVE_SCHEMA,
            "engine_version": ENGINE_VERSION,
            "feature_set_version": spec.FEATURE_SET_VERSION,
            "feature_set_sha": self.sha,
            "boot_id": self.boot_id,
            "pid": os.getpid(),
            "written_ts": round(now, 3),
            "started_ts": round(self.started_ts, 3),
            "last_bar_ts": self.last_written,
            "last_row_bar_close_ts": last.get("bar_close_ts"),
            "last_row_ts": last.get("ts"),
            "last_row_late": last.get("late"),
            "last_health": last.get("health"),
            "last_status_counts": last.get("status_counts"),
            "last_regime": last.get("regime"),
            "history_bars": len(self.bars),
            "inputs": {"latest_tape_ts": self.builder.latest_tape_ts,
                       "last_xv_minute": self.builder.last_xv_minute,
                       "last_mc_minute": self.builder.last_mc_minute,
                       "binance_imbalance_seen": dict(self.builder.xv_imb_seen)},
            "stats": {**self.stats, "file": spec.BAR_FILE},
        }

    def write_live(self, now: float) -> None:
        tmp = self.live_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.live_payload(now), handle, separators=(",", ":"), allow_nan=False)
            os.replace(tmp, self.live_path)
        except (OSError, ValueError):
            self.stats["live_write_failures"] += 1

    def _meta(self) -> None:
        cpu, wall = time.process_time(), time.monotonic()
        prev_cpu, prev_wall = self._cpu_mark
        if wall - prev_wall >= 60.0:
            self._cpu_mark = (cpu, wall)
            self.stats["cpu_pct_1m"] = round((cpu - prev_cpu) / (wall - prev_wall) * 100.0, 3)
        self.stats["rss_mb"] = cvc._rss_mb()

    def tick(self, now: float) -> None:
        self.poll_inputs()
        self.close_bars(now)
        self._meta()
        if now - self._last_live >= LIVE_WRITE_EVERY_SEC:
            self._last_live = now
            self.write_live(now)
        if now - self._last_status_log >= STATUS_LOG_EVERY_SEC:
            self._last_status_log = now
            cvc._log(f"status rows={self.stats['rows_written']} last_bar_ts={self.last_written} "
                     f"compute_ms={self.stats['compute_ms_last']} cpu_pct_1m={self.stats['cpu_pct_1m']} "
                     f"rss_mb={self.stats['rss_mb']} bytes_today={self.stats['bytes_today']}")
        rss = self.stats.get("rss_mb")
        if rss is not None and rss > MAX_RSS_MB:
            cvc._log(f"rss {rss} MB above {MAX_RSS_MB} MB; exiting for a clean restart")
            self.stop.set()

    def run(self) -> None:
        self.warm_start()
        cvc._log(f"started dir={self.dir} boot_id={self.boot_id} sha={self.sha[:12]} warm={self.stats['warm']}")
        while not self.stop.is_set():
            try:
                self.tick(self.clock())
            except Exception as exc:
                cvc._log(f"tick failed: {type(exc).__name__}: {exc}")
            if self.stop.wait(POLL_SEC):
                break
        cvc._log("stopped")


def main() -> int:
    cvc.LOG_TAG = "indicator-engine"
    activate_from_env(os.getcwd())
    engine = Engine(os.getcwd())

    def _stop(*_):
        engine.stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    engine.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
