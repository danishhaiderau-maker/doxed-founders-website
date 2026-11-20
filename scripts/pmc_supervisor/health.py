"""Bounded local-only sampling and cached, read-only health. Probes never restart."""
from __future__ import annotations
import copy
import hashlib
import http.client
import json
import os
import shutil
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .policy import Hold, ROOT, DATA, REPORTS, no_reparse

GIB = 1024 ** 3
STREAMS = ("raw/book", "raw/trades", "norm/book", "norm/trades")


def reserve_port(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", port))
        return s
    except OSError:
        s.close()
        raise Hold("PORT_OCCUPIED") from None


def probe_viewer(expected_pid):
    result = {"alive": False, "ready": False, "owner_pid_match": False, "roots_match": False,
              "report_current": False, "reason": "NOT_READY", "fly_failure": False, "expected_pid": expected_pid}
    conn = http.client.HTTPConnection("127.0.0.1", 9001, timeout=1.0)
    try:
        conn.request("GET", "/api/health", headers={"Host": "127.0.0.1:9001", "Connection": "close"})
        response = conn.getresponse()
        blob = response.read(65537)
        if len(blob) > 65536:
            return result
        scan = blob.lower()
        if b"fly" in scan and any(w in scan for w in (b"error", b"timeout", b"timed out", b"exception", b"failed")):
            result["fly_failure"] = True
        if response.status != 200:
            return result
        row = json.loads(blob)
        if not isinstance(row, dict):
            return result
        result["owner_pid_match"] = type(row.get("pid")) is int and row["pid"] == expected_pid
        result["roots_match"] = (row.get("data_root") == DATA and row.get("report_root") == REPORTS)
        result["alive"] = row.get("alive") is True and result["owner_pid_match"]
        freshness = row.get("generation_freshness")
        result["report_current"] = isinstance(freshness, dict) and freshness.get("current") is True
        result["ready"] = bool(row.get("ready") is True and result["alive"] and result["roots_match"] and result["report_current"])
        result["reason"] = "READY" if result["ready"] else "NOT_READY"
    except (OSError, ValueError, http.client.HTTPException, TypeError):
        pass  # Do not serialize even exception class/value from the peer.
    finally:
        conn.close()
    return result


class ReceiptReader:
    """Incremental collector checksum validation; bounded bytes per sample.

    Partial append remains NOT_READY, not silently accepted; resume next sample.
    No historical recursive scans. A backlog is UNKNOWN until fully consumed.
    """
    LIMIT = 256 * 1024
    def __init__(self):
        self.readers = {}

    def read(self, path):
        no_reparse(path)
        st = path.stat()
        key = str(path)
        item = self.readers.setdefault(key, {"id": (st.st_dev, st.st_ino), "offset": 0, "last": None, "counts": {}})
        if item["id"] != (st.st_dev, st.st_ino) or st.st_size < item["offset"]:
            return {"status": "UNKNOWN_IDENTITY", "last": None, "counts": {}}
        consumed, valid = 0, True
        with path.open("rb") as f:
            f.seek(item["offset"])
            while consumed < self.LIMIT:
                pos = f.tell()
                line = f.readline(32769)
                if not line:
                    break
                consumed += len(line)
                if len(line) > 32768 or not line.endswith(b"\n"):
                    valid = False
                    break
                try:
                    obj = json.loads(line)
                    body = {k: v for k, v in obj.items() if k != "record_checksum"}
                    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
                    if hashlib.sha256(raw).hexdigest() != obj.get("record_checksum"):
                        raise ValueError()
                    kind = obj.get("kind")
                    if kind in {"start", "exit"}:
                        item["counts"][kind] = item["counts"].get(kind, 0) + 1
                    item["last"], item["offset"] = obj, pos + len(line)
                except (ValueError, TypeError, AttributeError):
                    valid = False
                    break
        if len(self.readers) > 12:
            # bounded active reader cache; never write/truncate anything on disk
            self.readers.pop(next(iter(self.readers)))
        status = "VALID" if valid and item["offset"] == st.st_size else "UNKNOWN_OR_PARTIAL"
        return {"status": status, "last": item["last"] if status == "VALID" else None, "counts": dict(item["counts"])}


class CollectorSample:
    def __init__(self, root=Path(ROOT)):
        self.root, self.reader = Path(root), ReceiptReader()
        self.scans, self.latest, self.previous_counts = {}, {}, None

    def sample(self, now):
        date = datetime.fromtimestamp(now, timezone.utc).date()
        days = [str(date - timedelta(days=i)) for i in (0, 1)]
        result = {"status": "UNKNOWN", "heartbeat_target_s": 30, "stale_after_s": 90,
                  "coverage_claim": "observed_not_continuous", "streams": {}, "runtime": None,
                  "health": None, "internal_runs": None, "durable_progress": "UNKNOWN"}
        try:
            no_reparse(self.root)
            active = {str(self.root / day / stream) for day in days for stream in STREAMS}
            for key in list(self.scans):
                if key not in active:
                    if self.scans[key] is not None:
                        self.scans[key].close()
                    del self.scans[key]
            for stream in STREAMS:
                candidates, incomplete = [], False
                for day in days:
                    directory = self.root / day / stream
                    if not directory.exists():
                        continue
                    no_reparse(directory)
                    key = str(directory)
                    if self.scans.get(key) is None:
                        self.scans[key] = os.scandir(directory)
                    iterator = self.scans[key]
                    for _ in range(256):
                        try:
                            entry = next(iterator)
                        except StopIteration:
                            iterator.close()
                            self.scans[key] = None
                            break
                        path = no_reparse(Path(entry.path))
                        if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".gz"):
                            continue
                        st = path.stat()
                        old = self.latest.get(key)
                        if old is None or st.st_mtime > old[0]:
                            self.latest[key] = (st.st_mtime, path)
                    if self.scans[key] is not None:
                        incomplete = True
                    if key in self.latest:
                        _, path = self.latest[key]
                        no_reparse(path)
                        st = path.stat()
                        candidates.append((st.st_mtime, st.st_size, path))
                if candidates:
                    mtime, size, path = max(candidates, key=lambda x: x[0])
                    age = now - mtime
                    status = "CLOCK_UNCERTAIN" if age < -5 else "FRESH" if age <= 90 else "STALE"
                    result["streams"][stream] = {"path": str(path.relative_to(self.root)).replace("\\", "/"),
                        "mtime": mtime, "bytes": size, "age_s": age, "status": "UNKNOWN_SCAN" if incomplete else status}
                else:
                    result["streams"][stream] = {"status": "NOT_READY", "path": None, "mtime": None, "bytes": None, "age_s": None}
            runtime = self.reader.read(self.root / "runtime.journal")
            launch = self.reader.read(self.root / "launch.journal")
            result["internal_runs"] = {"status": launch["status"], "counts": launch["counts"],
                                       "budget_scope": "OS_restarts_only_not_PMC_logical_retries"}
            row = runtime["last"]
            result["runtime_status"] = runtime["status"]
            if row and row.get("schema_v") == "pmc_runtime_v1":
                result["runtime"] = {k: row.get(k) for k in ("utc_ns", "run_start_ns", "book_valid", "received_count", "durable_count", "gaps", "checksum_faults")}
                # Strict types: arbitrary payloads never enter cached health.
                if (type(row.get("utc_ns")) is not int or type(row.get("book_valid")) is not bool or
                    any(type(row.get(k)) is not int for k in ("received_count", "durable_count", "gaps", "checksum_faults"))):
                    raise ValueError()
                heartbeat_age = now - row["utc_ns"] / 1e9
                result["runtime"]["age_s"] = heartbeat_age
                counts = (row["received_count"], row["durable_count"])
                if self.previous_counts:
                    result["durable_progress"] = "STALLED" if counts[0] > self.previous_counts[0] and counts[1] <= self.previous_counts[1] else "NO_STALL_OBSERVED"
                self.previous_counts = counts
                for day in days:
                    hp = self.root / day / "health.journal"
                    if not hp.exists():
                        continue
                    receipt = self.reader.read(hp)
                    health = receipt["last"]
                    if health and health.get("schema_v") == "pmc_health_v1":
                        keys = ("now_ns", "liveness_receive_time_ns", "last_receive_time_ns", "book_valid", "trade_quiet", "durable_count", "received_count", "gaps")
                        safe = {k: health.get(k) for k in keys}
                        if all(v is None or type(v) in (bool, int, float) for v in safe.values()):
                            result["health"] = safe
                        break
                current = (-5 <= heartbeat_age <= 90 and row["book_valid"] and row["gaps"] == 0 and
                           row["checksum_faults"] == 0 and 0 < row["durable_count"] <= row["received_count"] and result["durable_progress"] != "STALLED")
                observed = row.get("observed_minutes", {})
                day_start = int(row["utc_ns"] // 1_000_000_000 // 86400) * 86400
                trades = observed.get("trades_feed", []) if isinstance(observed, dict) else []
                confirmed = bool(trades and type(trades[-1]) is int and -5 <= now - (day_start + trades[-1] * 60) <= 90)
                for stream in ("raw/trades", "norm/trades"):
                    if result["streams"][stream]["status"] == "STALE" and confirmed:
                        result["streams"][stream]["status"] = "QUIET/NO_NEW_TRADES"
                book_minutes = observed.get("valid_book_feed", []) if isinstance(observed, dict) else []
                book_confirmed = bool(book_minutes and type(book_minutes[-1]) is int and -5 <= now - (day_start + book_minutes[-1] * 60) <= 90)
                health = result["health"]
                health_current = bool(health and health.get("book_valid") is True and
                    type(health.get("now_ns")) is int and -5 <= now - health["now_ns"] / 1e9 <= 90 and
                    type(health.get("liveness_receive_time_ns")) is int and -5 <= now - health["liveness_receive_time_ns"] / 1e9 <= 90)
                stream_ok = all(s["status"] in {"FRESH", "QUIET/NO_NEW_TRADES"} for s in result["streams"].values())
                result["status"] = "GREEN" if current and stream_ok and health_current and book_confirmed else "AMBER/HOLD"
        except (OSError, Hold, ValueError, TypeError, OverflowError):
            result["status"] = "UNKNOWN"
            result["runtime"] = None
        return result


class DiskSample:
    def __init__(self):
        self.previous = None

    def sample(self, now, free):
        row = {"free_bytes": free, "free_gib": None if free is None else free / GIB,
               "research_reserve_gib": 100, "collector_start_floor_gib": 20,
               "collector_code_floor_mib": 512, "new_research_allowed": free is not None and free >= 100 * GIB,
               "new_collector_allowed": free is not None and free >= 20 * GIB,
               "status": "UNKNOWN" if free is None else "RED" if free < 100 * GIB else "GREEN",
               "projection_basis": "NO_MEASURED_GROWTH", "hours_to_reserve": None, "days_to_reserve": None}
        if self.previous and free is not None:
            wall, before = self.previous
            if now > wall and before > free:
                rate = (before - free) / (now - wall)
                seconds = max(0, free - 100 * GIB) / rate
                row.update(projection_basis="MEASURED_C_FREE_BYTE_DELTA_NOT_PMC_ONLY", hours_to_reserve=seconds / 3600, days_to_reserve=seconds / 86400)
        self.previous = None if free is None else (now, free)
        return row


def disk_free():
    try:
        return shutil.disk_usage("C:\\").free
    except OSError:
        return None


class Cache:
    def __init__(self):
        self.snapshot, self.sampled_mono = {"status": "UNKNOWN", "required_roles": ["pmc", "analyzer_viewer"]}, None

    def publish(self, snapshot, mono):
        # Atomic tuple swap under CPython GIL; handlers never lock the monitor.
        self.snapshot, self.sampled_mono = copy.deepcopy(snapshot), mono

    def read(self, mono=None):
        snap, sampled = self.snapshot, self.sampled_mono
        result = copy.deepcopy(snap)
        age = None if sampled is None else (time.monotonic() if mono is None else mono) - sampled
        result["sample_age_s"] = age
        if age is None or age > 30 or age < 0:
            result["status"], result["sample_state"] = "UNKNOWN", "AMBER/HOLD_STALE"
        return result


class HealthServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False
    request_queue_size = 8

    def __init__(self, cache, *, socket_factory=reserve_port):
        self.cache, self.slots = cache, threading.BoundedSemaphore(8)
        sock = socket_factory(9040)
        super().__init__(("127.0.0.1", 9040), Handler, bind_and_activate=False)
        self.socket.close()
        self.socket = sock
        self.server_address = sock.getsockname()
        self.server_activate()

    def process_request(self, request, client_address):
        request.settimeout(1.0)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # no raw network exception/log leaks


class Handler(BaseHTTPRequestHandler):
    server_version = "DoxxedLaptopSupervisor"
    sys_version = ""
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.headers.get("Host") != "127.0.0.1:9040" or self.path not in ("/", "/health.json", "/health"):
            self.send_error(404)
            return
        body = json.dumps(self.server.cache.read(), sort_keys=True, allow_nan=False).encode("utf-8")
        kind = "application/json"
        if self.path == "/":
            # Static page fetches only local cached JSON. No viewer JS or cloud banners.
            body = b'<!doctype html><title>Doxxed laptop health</title><h1>Paper only; Live OFF</h1><pre id="h"></pre><script>fetch("/health.json").then(r=>r.json()).then(x=>document.getElementById("h").textContent=JSON.stringify(x,null,2))</script>'
            kind = "text/html; charset=utf-8"
        self.send_response(200)
        for k, v in (("Content-Type", kind), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                     ("X-Content-Type-Options", "nosniff"), ("Connection", "close")):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_HEAD = do_GET
    def denied(self):
        self.send_error(405)
    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = denied
