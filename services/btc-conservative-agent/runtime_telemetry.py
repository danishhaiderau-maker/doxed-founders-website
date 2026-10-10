"""Continuous, cheap Fly runtime telemetry (``runtime_telemetry_v1``).

One daemon thread wakes once per second to measure its own scheduling lag
(how late a 1 s sleep returns: a direct proxy for CPU / GIL starvation on the
single Fly vCPU) and once per minute appends one numeric-only row to
``runtime_telemetry_1m.jsonl``. The row is shipped by the existing research
segment shipper like every other runtime ``.jsonl`` and the latest row plus a
one-hour summary are exposed in ``/api/status.runtime_telemetry``.

Per-minute cost is a handful of ``/proc`` reads, one bounded tail read of the
crash-dump file and one fsynced append of roughly 1.5 KB. The module never
imports bot.py, takes no trading lock itself (the injected lock callback reads
only the lock's internal metadata mutex), and emits no paths, secrets, client
addresses or request data.
"""
from __future__ import annotations

import ctypes
import glob
import json
import math
import os
import sys
import threading
import time
from collections import deque
from typing import Callable, Optional

SCHEMA = "runtime_telemetry_v1"
STATUS_SCHEMA = "runtime_telemetry_status_v1"
FILE_NAME = "runtime_telemetry_1m.jsonl"
INTERVAL_SEC = 60.0
LAG_TICK_SEC = 1.0
ROTATE_BYTES = 8 * 1024 * 1024
SUMMARY_RING = 360          # six hours of one-minute rows (compact tuples)
CRASH_TAIL_MAX_BYTES = 1024 * 1024

# Health thresholds. Load average on the 1x Fly VM includes the niced
# collector processes and sits near 3 in steady state, so it is informational
# only; scheduling lag measures the actual harm to the bot's own threads.
LAG_P95_AMBER_MS = 250.0
LAG_MAX_RED_MS = 2000.0
LAG_RED_CONSECUTIVE = 3
PROCESS_CPU_AMBER_PCT = 85.0
PROCESS_CPU_AMBER_MINUTES = 10
TIMED_TIMEOUTS_AMBER_1H = 30
TRADE_WAIT_MAX_AMBER_MS = 500.0
TRADE_WAIT_OVER_BUDGET_AMBER_1H = 30
RSS_GROWTH_AMBER_PCT = 20.0
RSS_GROWTH_MIN_SPAN_SEC = 6 * 3600 - 120
STALE_AFTER_SEC = 3 * INTERVAL_SEC


def _finite(value, digits: int = 3):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _percentile(values: list, pct: float):
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(math.ceil(pct / 100.0 * len(ordered))) - 1))
    return round(ordered[idx], 1)


def read_proc_stat(path: str = "/proc/stat") -> Optional[dict]:
    """Aggregate host CPU jiffies (user..steal) or None off Linux."""
    try:
        with open(path, "r", encoding="ascii") as handle:
            fields = handle.readline().split()
    except OSError:
        return None
    if not fields or fields[0] != "cpu":
        return None
    try:
        values = [int(v) for v in fields[1:9]]
    except ValueError:
        return None
    values += [0] * (8 - len(values))
    user, nice, system, idle, iowait, irq, softirq, steal = values
    return {"busy": user + nice + system + irq + softirq, "idle": idle,
            "iowait": iowait, "steal": steal,
            "total": user + nice + system + idle + iowait + irq + softirq + steal}


def host_cpu_pct(prev: Optional[dict], cur: Optional[dict]) -> dict:
    if not prev or not cur:
        return {"busy_pct": None, "iowait_pct": None, "steal_pct": None}
    total = cur["total"] - prev["total"]
    if total <= 0:
        return {"busy_pct": None, "iowait_pct": None, "steal_pct": None}
    return {key + "_pct": _finite(100.0 * (cur[key] - prev[key]) / total, 2)
            for key in ("busy", "iowait", "steal")}


def native_thread_count(path: str = "/proc/self/status") -> Optional[int]:
    try:
        with open(path, "r", encoding="ascii", errors="replace") as handle:
            for line in handle:
                if line.startswith("Threads:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def kernel_ntp_state() -> Optional[dict]:
    """Read-only adjtimex(2): clock state and kernel error bounds (Linux x86_64).

    ``state`` 5 (TIME_ERROR) means the kernel clock is not NTP-synchronised.
    """
    if not sys.platform.startswith("linux") or ctypes.sizeof(ctypes.c_long) != 8:
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        buf = ctypes.create_string_buffer(256)  # struct timex; modes=0 => read only
        state = int(libc.adjtimex(buf))
        if state < 0:
            return None
        maxerror_us = ctypes.c_long.from_buffer(buf, 24).value
        esterror_us = ctypes.c_long.from_buffer(buf, 32).value
        return {"state": state, "synchronized": state != 5,
                "maxerror_ms": _finite(maxerror_us / 1000.0, 1),
                "esterror_ms": _finite(esterror_us / 1000.0, 1)}
    except (OSError, AttributeError, ValueError, TypeError):
        return None


_CLOCK_PROBE_URL = os.getenv("CLOCK_OFFSET_PROBE_URL", "https://cloudflare.com/cdn-cgi/trace")
_CLOCK_PROBE_INTERVAL_SEC = float(os.getenv("CLOCK_OFFSET_PROBE_INTERVAL_SEC", "300"))
_clock_probe_lock = threading.Lock()
_clock_probe_state: dict = {"started": False, "last": None}


def _probe_clock_offset_once(url: str = _CLOCK_PROBE_URL, samples: int = 3,
                             timeout: float = 3.0) -> Optional[dict]:
    """Wall-clock offset against an external ms-resolution clock (RTT midpoint).

    Fly VMs run no NTP daemon, so adjtimex always reports state 5 with its
    default 16 s error bound even when kvm-clock keeps the wall clock accurate.
    This measured offset is the trusted number for latency stamps.
    """
    import urllib.request
    best = None
    for _ in range(max(1, samples)):
        try:
            t0 = time.time()
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read(4096).decode("ascii", "replace")
            t1 = time.time()
        except Exception:
            continue
        remote = None
        for line in body.splitlines():
            if line.startswith("ts="):
                try:
                    remote = float(line[3:].strip())
                except ValueError:
                    remote = None
        if remote is None:
            continue
        rtt = t1 - t0
        offset = (t0 + rtt / 2.0) - remote  # positive: local clock ahead
        if best is None or rtt < best["rtt_ms"] / 1000.0:
            best = {"offset_ms": _finite(offset * 1000.0, 1), "rtt_ms": _finite(rtt * 1000.0, 1)}
    if best is None:
        return None
    best.update({"source": url, "measured_at": time.time()})
    return best


def _clock_probe_loop() -> None:
    while True:
        result = _probe_clock_offset_once()
        with _clock_probe_lock:
            if result is not None:
                _clock_probe_state["last"] = result
            _clock_probe_state["attempted_at"] = time.time()
        time.sleep(max(30.0, _CLOCK_PROBE_INTERVAL_SEC))


def external_clock_offset() -> Optional[dict]:
    """Latest external offset sample; starts the background probe on first use."""
    if os.getenv("CLOCK_OFFSET_PROBE_ENABLED", "1").strip() == "0":
        return None
    with _clock_probe_lock:
        if not _clock_probe_state["started"]:
            _clock_probe_state["started"] = True
            threading.Thread(target=_clock_probe_loop, name="clock-offset-probe",
                             daemon=True).start()
        last = _clock_probe_state.get("last")
    if not last:
        return None
    out = dict(last)
    out["age_sec"] = _finite(time.time() - float(last["measured_at"]), 1)
    out["trusted"] = bool(out["age_sec"] is not None and out["age_sec"] <= 3 * _CLOCK_PROBE_INTERVAL_SEC
                          and out.get("rtt_ms") is not None and out["rtt_ms"] <= 500)
    return out


class CrashDumpWatcher:
    """Counts new crash/incident rows by tailing only the appended bytes."""

    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self._offset = None
        self.total_new = 0

    def poll(self) -> dict:
        if not self.path:
            return {"available": False}
        try:
            stat = os.stat(self.path)
        except OSError:
            return {"available": False}
        new_rows = 0
        if self._offset is None or stat.st_size < self._offset:
            self._offset = stat.st_size
        elif stat.st_size > self._offset:
            start = max(self._offset, stat.st_size - CRASH_TAIL_MAX_BYTES)
            try:
                with open(self.path, "rb") as handle:
                    handle.seek(start)
                    chunk = handle.read(stat.st_size - start)
                new_rows = chunk.count(b"\n")
            except OSError:
                new_rows = 0
            self._offset = stat.st_size
        self.total_new += new_rows
        return {"available": True, "size_bytes": int(stat.st_size),
                "mtime": _finite(stat.st_mtime, 1), "new_rows": new_rows,
                "new_rows_since_boot": self.total_new}


class LagProbe:
    """Overshoot of fixed-period sleeps, collected between samples."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: list = []

    def record(self, overshoot_ms: float) -> None:
        with self._lock:
            self._samples.append(max(0.0, float(overshoot_ms)))

    def drain(self) -> dict:
        with self._lock:
            samples, self._samples = self._samples, []
        return {"n": len(samples), "p50_ms": _percentile(samples, 50),
                "p95_ms": _percentile(samples, 95),
                "max_ms": round(max(samples), 1) if samples else None}


def _delta(cur: dict, prev: dict, key: str):
    if not isinstance(cur, dict) or key not in cur:
        return None
    base = prev.get(key, 0) if isinstance(prev, dict) else 0
    value = cur.get(key) or 0
    return value - base if value >= base else value


def lock_interval(cur: Optional[dict], prev: Optional[dict]) -> Optional[dict]:
    """Per-interval view of ``_TrackedRLock.telemetry_counters`` output."""
    if not isinstance(cur, dict):
        return None
    prev = prev if isinstance(prev, dict) else {}
    waits = _delta(cur, prev, "waits")
    wait_total = _delta(cur, prev, "wait_total_ms")
    return {
        "name": cur.get("name"),
        "holds": _delta(cur, prev, "holds"),
        "hold_over_budget": _delta(cur, prev, "hold_over_budget"),
        "hold_max_ms": cur.get("interval_hold_max_ms"),
        "hold_max_site": cur.get("interval_hold_max_site"),
        "probe_busy": _delta(cur, prev, "probe_busy_count"),
        "timed_timeouts": _delta(cur, prev, "timed_timeout_count"),
        "waits": waits,
        "wait_mean_ms": _finite(wait_total / waits, 2) if waits else None,
        "wait_max_ms": cur.get("interval_wait_max_ms"),
        "wait_max_site": cur.get("interval_wait_max_site"),
        "wait_over_budget": _delta(cur, prev, "wait_over_budget"),
        "timeout_count_total": cur.get("timeout_count"),
        "top_wait_sites": cur.get("top_wait_sites") or [],
        "top_timeout_sites": cur.get("top_timeout_sites") or [],
    }


def evaluate(compact_rows: list, now: Optional[float] = None) -> dict:
    """GREEN/AMBER/RED/UNKNOWN from the compact ring (newest last).

    Compact row keys: ts, rss, lag_p95, lag_max, cpu_pct, timed_timeouts,
    wait_max, wait_over, crash_new. The laptop self-diagnosis applies the same
    function to rows read from the mirrored JSONL via ``compact(row)``.
    """
    if not compact_rows:
        return {"status": "UNKNOWN", "reasons": ["NO_TELEMETRY_ROWS"]}
    now = time.time() if now is None else float(now)
    newest = compact_rows[-1]
    reasons, red = [], []
    if now - float(newest.get("ts") or 0) > STALE_AFTER_SEC:
        red.append("TELEMETRY_STALE")
    hour = [r for r in compact_rows if float(r.get("ts") or 0) >= now - 3600]
    tail = compact_rows[-LAG_RED_CONSECUTIVE:]
    if len(tail) == LAG_RED_CONSECUTIVE and all(
        (r.get("lag_max") or 0) > LAG_MAX_RED_MS for r in tail
    ):
        red.append("SCHEDULER_LAG_SUSTAINED")
    recent10 = compact_rows[-10:]
    if any((r.get("lag_p95") or 0) > LAG_P95_AMBER_MS for r in recent10):
        reasons.append("SCHEDULER_LAG_P95_HIGH")
    cpu_tail = compact_rows[-PROCESS_CPU_AMBER_MINUTES:]
    if len(cpu_tail) == PROCESS_CPU_AMBER_MINUTES and all(
        (r.get("cpu_pct") or 0) > PROCESS_CPU_AMBER_PCT for r in cpu_tail
    ):
        reasons.append("PROCESS_CPU_SATURATED")
    if sum(int(r.get("timed_timeouts") or 0) for r in hour) > TIMED_TIMEOUTS_AMBER_1H:
        reasons.append("TRADE_LOCK_TIMED_TIMEOUTS_1H")
    if any((r.get("wait_max") or 0) > TRADE_WAIT_MAX_AMBER_MS for r in hour):
        reasons.append("TRADE_LOCK_WAIT_MAX_1H")
    if sum(int(r.get("wait_over") or 0) for r in hour) > TRADE_WAIT_OVER_BUDGET_AMBER_1H:
        reasons.append("TRADE_LOCK_WAIT_OVER_BUDGET_1H")
    if any(int(r.get("crash_new") or 0) > 0 for r in hour):
        reasons.append("CRASH_DUMP_NEW_ROWS_1H")
    with_rss = [r for r in compact_rows if r.get("rss")]
    if len(with_rss) >= 2:
        first, last = with_rss[0], with_rss[-1]
        span = float(last["ts"]) - float(first["ts"])
        if span >= RSS_GROWTH_MIN_SPAN_SEC and first["rss"] > 0:
            growth = 100.0 * (last["rss"] - first["rss"]) / first["rss"]
            if growth > RSS_GROWTH_AMBER_PCT:
                reasons.append("RSS_GROWTH_6H")
    status = "RED" if red else ("AMBER" if reasons else "GREEN")
    return {"status": status, "reasons": red + reasons}


def compact(row: dict) -> dict:
    lock = row.get("trade_lock") or {}
    return {
        "ts": row.get("ts"),
        "rss": (row.get("memory") or {}).get("rss_bytes"),
        "lag_p95": (row.get("scheduler_lag") or {}).get("p95_ms"),
        "lag_max": (row.get("scheduler_lag") or {}).get("max_ms"),
        "cpu_pct": (row.get("cpu") or {}).get("process_pct"),
        "timed_timeouts": lock.get("timed_timeouts"),
        "wait_max": lock.get("wait_max_ms"),
        "wait_over": lock.get("wait_over_budget"),
        "crash_new": (row.get("crash_dump") or {}).get("new_rows"),
    }


class RuntimeTelemetry(threading.Thread):
    def __init__(self, runtime_dir: str, *,
                 boot_ts: float,
                 pressure_fn: Optional[Callable[[], dict]] = None,
                 handlers_fn: Optional[Callable[[], dict]] = None,
                 lock_fn: Optional[Callable[[], dict]] = None,
                 revision_fn: Optional[Callable[[], Optional[str]]] = None,
                 crash_dump_path: Optional[str] = None,
                 stop_event: Optional[threading.Event] = None,
                 clock: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic,
                 process_cpu: Callable[[], float] = None,
                 interval_sec: float = INTERVAL_SEC,
                 lag_tick_sec: float = LAG_TICK_SEC) -> None:
        super().__init__(name="runtime-telemetry", daemon=True)
        self.path = os.path.join(runtime_dir, FILE_NAME)
        self.boot_ts = float(boot_ts)
        self.pressure_fn = pressure_fn
        self.handlers_fn = handlers_fn
        self.lock_fn = lock_fn
        self.revision_fn = revision_fn
        self.stop_event = stop_event or threading.Event()
        self.clock = clock
        self.mono = mono
        self.process_cpu = process_cpu or (lambda: sum(os.times()[:2]))
        self.interval_sec = float(interval_sec)
        self.lag_tick_sec = float(lag_tick_sec)
        self.lag = LagProbe()
        self.crash = CrashDumpWatcher(crash_dump_path)
        self._ring: deque = deque(maxlen=SUMMARY_RING)
        self._state_lock = threading.Lock()
        self.latest: Optional[dict] = None
        self.seq = 0
        self.write_failures = 0
        self.sample_failures = 0
        self._prev_mono = mono()
        self._prev_cpu = self.process_cpu()
        self._prev_proc_stat = read_proc_stat()
        self._prev_lock: Optional[dict] = None
        self._prev_rss = None
        self._wall_mono0 = clock() - self._prev_mono
        self._prev_wall_mono = self._wall_mono0

    # -- sampling -----------------------------------------------------------
    def sample(self) -> dict:
        now, mono = self.clock(), self.mono()
        cpu = self.process_cpu()
        wall_span = max(1e-6, mono - self._prev_mono)
        process_pct = _finite(100.0 * (cpu - self._prev_cpu) / wall_span, 2)
        proc_stat = read_proc_stat()
        host = host_cpu_pct(self._prev_proc_stat, proc_stat)
        pressure = {}
        if self.pressure_fn:
            try:
                pressure = self.pressure_fn() or {}
            except Exception:
                pressure = {}
        lock_now = None
        if self.lock_fn:
            try:
                lock_now = self.lock_fn()
            except Exception:
                lock_now = None
        handlers = None
        if self.handlers_fn:
            try:
                handlers = self.handlers_fn()
            except Exception:
                handlers = None
        wall_mono = now - mono
        rss = pressure.get("rss_bytes")
        row = {
            "schema": SCHEMA,
            "ts": round(now, 3),
            "seq": self.seq,
            "boot_ts": round(self.boot_ts, 3),
            "uptime_sec": round(max(0.0, now - self.boot_ts), 1),
            "interval_sec": round(wall_span, 3),
            "pid": os.getpid(),
            "revision": self.revision_fn() if self.revision_fn else None,
            "cpu": {
                "count": pressure.get("cpu_count") or os.cpu_count(),
                "process_pct": process_pct,
                "process_cpu_sec": _finite(cpu, 2),
                "load_1m": pressure.get("load_1m"),
                "load_1m_per_cpu": pressure.get("load_1m_per_cpu"),
                "host_busy_pct": host["busy_pct"],
                "host_iowait_pct": host["iowait_pct"],
                "host_steal_pct": host["steal_pct"],
            },
            "memory": {
                "rss_bytes": rss,
                "rss_delta_bytes": (rss - self._prev_rss) if rss and self._prev_rss else None,
            },
            "threads": {"python": threading.active_count(), "native": native_thread_count()},
            "scheduler_lag": self.lag.drain(),
            "http_handlers": handlers,
            "trade_lock": lock_interval(lock_now, self._prev_lock),
            "clock": {
                "wall_mono_step_ms": _finite((wall_mono - self._prev_wall_mono) * 1000.0, 1),
                "wall_mono_drift_since_boot_ms": _finite((wall_mono - self._wall_mono0) * 1000.0, 1),
                "kernel_ntp": kernel_ntp_state(),
                # kernel_ntp is always "unsynchronized, 16 s" on Fly (no ntpd;
                # 16 s is adjtimex's default bound, not a measured error).
                "external_offset": external_clock_offset(),
            },
            "disk": {"used_pct": pressure.get("disk_used_pct"),
                     "free_bytes": pressure.get("disk_free_bytes")},
            "crash_dump": self.crash.poll(),
        }
        self.seq += 1
        self._prev_mono, self._prev_cpu = mono, cpu
        self._prev_proc_stat = proc_stat
        self._prev_lock = lock_now
        self._prev_rss = rss or self._prev_rss
        self._prev_wall_mono = wall_mono
        return row

    def _rotate_if_needed(self) -> None:
        try:
            if os.path.getsize(self.path) <= ROTATE_BYTES:
                return
        except OSError:
            return
        suffixes = [int(p.rsplit(".", 1)[-1]) for p in glob.glob(self.path + ".*")
                    if p.rsplit(".", 1)[-1].isdigit()]
        os.rename(self.path, f"{self.path}.{max(suffixes, default=0) + 1}")

    def append(self, row: dict) -> bool:
        data = (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        try:
            self._rotate_if_needed()
            with open(self.path, "ab") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            return True
        except (OSError, ValueError):
            self.write_failures += 1
            return False

    def sample_and_write(self) -> Optional[dict]:
        try:
            row = self.sample()
        except Exception:
            self.sample_failures += 1
            return None
        self.append(row)
        with self._state_lock:
            self.latest = row
            self._ring.append(compact(row))
        return row

    # -- loop ---------------------------------------------------------------
    def run(self) -> None:
        next_sample = self.mono() + self.interval_sec
        while not self.stop_event.is_set():
            intended = self.mono() + self.lag_tick_sec
            if self.stop_event.wait(self.lag_tick_sec):
                break
            self.lag.record((self.mono() - intended) * 1000.0)
            if self.mono() >= next_sample:
                next_sample += self.interval_sec
                if next_sample <= self.mono():
                    next_sample = self.mono() + self.interval_sec
                self.sample_and_write()

    # -- status -------------------------------------------------------------
    def status(self, now: Optional[float] = None) -> dict:
        with self._state_lock:
            latest = self.latest
            ring = list(self._ring)
        now = self.clock() if now is None else now
        hour = [r for r in ring if float(r.get("ts") or 0) >= now - 3600]

        def _max(key):
            vals = [r.get(key) for r in hour if r.get(key) is not None]
            return max(vals) if vals else None

        def _sum(key):
            return sum(int(r.get(key) or 0) for r in hour)

        return {
            "schema": STATUS_SCHEMA,
            "file": FILE_NAME,
            "interval_sec": self.interval_sec,
            "rows_this_process": self.seq,
            "write_failures": self.write_failures,
            "sample_failures": self.sample_failures,
            "latest": latest,
            "last_hour": {
                "rows": len(hour),
                "scheduler_lag_max_ms": _max("lag_max"),
                "scheduler_lag_p95_max_ms": _max("lag_p95"),
                "process_cpu_pct_max": _max("cpu_pct"),
                "trade_lock_timed_timeouts": _sum("timed_timeouts"),
                "trade_lock_wait_max_ms": _max("wait_max"),
                "trade_lock_wait_over_budget": _sum("wait_over"),
                "crash_dump_new_rows": _sum("crash_new"),
            },
            "health": evaluate(ring, now),
        }
