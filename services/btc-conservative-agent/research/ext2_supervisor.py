"""EXT2 run supervisor. Paper only. The parent enforces caps and the kill.

The heartbeat thread only proves the worker is alive. Caps are enforced here,
outside the worker: 600 s per CPU unit and 120 s per GPU unit (the worker
announces each unit in `unit.json`; the parent times it on its own clock),
a 2 h run-level flag and a 4 h run-level hard stop. Caps cannot be raised.
Card v3 section 6 / section 10 STOP 7 (B13): the worker gets <= 8 threads (thread
env + torch.set_num_threads), BelowNormal priority (Windows priority class on the
process and the Job; nice 10 on POSIX), a 24 GB memory cap (Job Object job-memory
limit on Windows, process-group RSS watchdog on POSIX), new work pauses while
free RAM < 8 GB, and free RAM < 8 GB for more than 10 min is STOP_RAM_FLOOR.
The run root is created new (exclusive); nothing is ever overwritten. A fresh
run nonce is minted at child start; only a COMPLETE marker carrying that
nonce counts. On Windows the child is created suspended, assigned to a
KILL_ON_JOB_CLOSE Job Object, then resumed; membership is measured, not
asserted. Receipts carry no environment dumps or secrets.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research.ext2_costs import Ext2Stop

HARD_STOP_SEC = 4 * 60 * 60
FLAG_SEC = 2 * 60 * 60
CPU_UNIT_SEC = 600
GPU_UNIT_SEC = 120
HEARTBEAT_STALL_SEC = 300
DISK_FLOOR_BYTES = 100 * 1024 ** 3
DISK_RUN_FLOOR_BYTES = 80 * 1024 ** 3
OUTPUT_CAP_BYTES = 5 * 1024 ** 3
COMPLETION_NAME = "COMPLETE"
HEARTBEAT_NAME = "heartbeat"
UNIT_NAME = "unit.json"
WORKER_RECEIPT_NAME = "run_receipt.json"
RECEIPT_NAME = "receipt.json"
SUMS_NAME = "SHA256SUMS.txt"
NONCE_ENV = "EXT2_RUN_NONCE"
CARD_ENV = "EXT2_CARD_SHA12"
CARD_SHA_V3 = "873fe69e0eaec8dfa2a208992778f8633505289f7e932e3e6f0b94768ccf59fe"
ADDENDUM_SHA_V3 = "7a18903b8d0a88cb31706e374f80b23a94a01c2b3e27eb69712deb4c22c2ca7e"
RK_RULING_PR996_SHA = "9ac982877fbcb8f8eb1a5b5569263e4bced6886d2de8ceaf121d3762ec996628"
PUSH3_CARD_SHA = "46f2868d42fdb50507540335f7647bc8665e15ff2510f25c95200d8483ad747a"
# RK D4 (C6): the receipt binds FROZEN files only (never the rolling rulings log).
# None = not yet frozen; a real launch needs every binding filled (STOP_BINDING_MISSING).
FROZEN_BINDINGS: tuple[tuple[str, str | None, str], ...] = (
    ("card_v4_launch", None, "launch card v4 (PENDING: not yet written / board PASS)"),
    ("card_v3", CARD_SHA_V3, "RUN-CARD-EXT2-FIRST-REAL-20261109-v3.md"),
    ("build_spec_addendum_v3", ADDENDUM_SHA_V3, "RUN-CARD-v3-BUILD-SPEC-ADDENDUM-20261109.md"),
    ("rk_ruling_pr996_gate6_h25_receipt", RK_RULING_PR996_SHA,
     "RK-PR996-GATE6-H25-RECEIPT-RULING-20261110.md"),
    ("hm_r2_receipt_merged_head", None, "HM R2 receipt at the merged head (PENDING merge)"),
    ("push_card_3", PUSH3_CARD_SHA, "pr996-consolidated-push-3-20261109.md"),
    ("push_card_4", None, "push-4 card (PENDING: no frozen file yet)"),
)
MAX_THREADS = 8
THREAD_ENV_KEYS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                   "VECLIB_MAXIMUM_THREADS", "TORCH_NUM_THREADS")
RAM_CAP_BYTES = 24 * 1024 ** 3
RAM_PAUSE_FREE_BYTES = 8 * 1024 ** 3
RAM_STOP_SEC = 10 * 60
PAUSE_UNIT_SEC = 12 * 60  # a worker 'ram_pause' unit may last 1.2 x the STOP 7 window
PAUSE_NAME = "pause"
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
POSIX_NICE = 10
AEDT = timezone(timedelta(hours=11), "AEDT")
_ENV_MARKERS = (
    "BITFINEX_", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "APIKEY",
    "PRIVATE_KEY", "ACCESS_KEY", "CREDENTIAL", "KEY", "AUTH", "PASS", "SESSION", "COOKIE",
)
_ENV_KEEP = frozenset({"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "USERPROFILE",
                       "LANG", "LC_ALL", "PYTHONPATH", "PYTHONHASHSEED", "NUMBER_OF_PROCESSORS",
                       "CUDA_VISIBLE_DEVICES", "PROCESSOR_ARCHITECTURE"})
_SECRET_MIN_LEN = 8
_IMPORT_ALLOW = frozenset({
    "__future__", "argparse", "ast", "collections", "ctypes", "dataclasses", "datetime", "gzip", "hashlib", "io",
    "itertools", "json", "math", "os", "pathlib", "signal", "statistics", "subprocess", "sys",
    "threading", "time", "typing", "numpy", "torch", "research",
})
_IMPORT_DENY = frozenset({
    "socket", "requests", "urllib", "http", "ftplib", "smtplib", "websocket",
    "aiohttp", "ssl", "urllib3", "httplib", "importlib", "ccxt", "asyncio",
})
_CALL_DENY = frozenset({"__import__", "import_module", "exec", "eval"})
_RECEIPT_KEYS = frozenset({
    "env", "environ", "environment", "os.environ", "secret", "secrets", "api_key",
    "token", "password", "bitfinex_api_key",
})
CONNECT_ALLOWLIST: tuple[str, ...] = ()
NETWORK_AUDIT_EVENTS = ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.bind",
                        "urllib.Request", "http.client.connect")


def aedt_now() -> str:
    return datetime.now(AEDT).isoformat(timespec="seconds")


def install_network_audit_hook() -> None:
    """Runtime H10 detection inside the worker: any socket connect/resolve raises.
    Detection-only (audit hooks); see the disclosed limitation in the receipt."""
    def hook(event: str, args: Any) -> None:
        if event in NETWORK_AUDIT_EVENTS:
            raise Ext2Stop("STOP_NETWORK")
    sys.addaudithook(hook)


class HeartbeatThread:
    """Liveness only. This thread does not enforce a wall-clock cap."""

    def __init__(self, path: Path, interval_sec: float = 0.05):
        self.path = Path(path)
        self.interval_sec = interval_sec
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ext2-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.path.write_text("alive", encoding="utf-8")
            self._stop.wait(self.interval_sec)


def scrub_env(env: Mapping[str, str]) -> dict[str, str]:
    clean = {}
    for key, value in env.items():
        upper = key.upper()
        if upper not in _ENV_KEEP and any(marker in upper for marker in _ENV_MARKERS):
            continue
        clean[key] = value
    return clean


def scrubbed_values(env: Mapping[str, str]) -> tuple[str, ...]:
    """Values removed by scrub_env (N2): fed automatically to the receipt check."""
    kept = scrub_env(env)
    return tuple(sorted({v for k, v in env.items() if k not in kept and v and len(v) >= _SECRET_MIN_LEN}))


CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_PRIORITY_CLASS = 0x00000020
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
WINDOWS_CHILD_START = ("create_suspended", "assign_job", "resume")


def windows_child_start_order() -> tuple[str, ...]:
    return WINDOWS_CHILD_START


_STRUCTS: list = []


def _job_structs():
    """Built once and cached: ctypes argtypes compare struct classes by identity
    (a fresh class per call broke Thread32First on Windows CI at c45e9e22)."""
    if _STRUCTS:
        return _STRUCTS[0]
    _STRUCTS.append(_build_job_structs())
    return _STRUCTS[0]


def _build_job_structs():
    import ctypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
            ("th32ThreadID", ctypes.c_uint32), ("th32OwnerProcessID", ctypes.c_uint32),
            ("tpBasePri", ctypes.c_int32), ("tpDeltaPri", ctypes.c_int32), ("dwFlags", ctypes.c_uint32),
        ]
    return IoCounters, BasicLimit, ExtendedLimit, ThreadEntry32


def job_limit_flags() -> int:
    """KILL_ON_JOB_CLOSE + PRIORITY_CLASS (BelowNormal for every member) + JOB_MEMORY (24 GB)."""
    return JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PRIORITY_CLASS | JOB_OBJECT_LIMIT_JOB_MEMORY


def fill_job_limits(info, ram_cap_bytes: int = RAM_CAP_BYTES):
    """B13: one place the Job limits are set (unit-tested on every OS with the cached structs)."""
    info.BasicLimitInformation.LimitFlags = job_limit_flags()
    info.BasicLimitInformation.PriorityClass = BELOW_NORMAL_PRIORITY_CLASS
    info.JobMemoryLimit = int(ram_cap_bytes)
    return info


def thread_limit_env(max_threads: int = MAX_THREADS) -> dict[str, str]:
    """Thread caps for the worker's numeric libraries; set by the parent before the
    child imports numpy/torch (they read these at import)."""
    if int(max_threads) < 1 or int(max_threads) > MAX_THREADS:
        raise Ext2Stop("STOP_THREADS_EXTENDED")
    return {key: str(int(max_threads)) for key in THREAD_ENV_KEYS}


def apply_worker_thread_limits(environ: Mapping[str, str] | None = None, torch_module: Any = None) -> dict[str, Any]:
    """Worker side: measure the thread env the parent set and cap torch intra-op threads.
    `ok` is False when any thread env is missing or above MAX_THREADS."""
    env = os.environ if environ is None else environ
    seen = {key: env.get(key) for key in THREAD_ENV_KEYS}
    ok = all(v is not None and v.isdigit() and 1 <= int(v) <= MAX_THREADS for v in seen.values())
    torch = torch_module if torch_module is not None else sys.modules.get("torch")
    torch_threads = None
    if torch is not None:
        torch.set_num_threads(MAX_THREADS)
        torch_threads = int(torch.get_num_threads())
        ok = ok and torch_threads <= MAX_THREADS
    return {"max_threads": MAX_THREADS, "thread_env": seen, "torch_threads": torch_threads, "ok": bool(ok)}


class SystemProbe:
    """Real RAM / priority probe. No psutil dependency (not on the box or CI): Windows
    uses kernel32 GlobalMemoryStatusEx / SetPriorityClass / the Job's peak job memory;
    POSIX uses /proc/meminfo MemAvailable, /proc/*/stat process-group RSS and setpriority.
    Tests inject a fake probe with the same three methods."""

    def available_bytes(self) -> int | None:
        if os.name == "nt":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32)] + [
                    (name, ctypes.c_ulonglong) for name in (
                        "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                        "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]
            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatusEx)]
            k.GlobalMemoryStatusEx.restype = ctypes.c_int
            if not k.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return int(status.ullAvailPhys)
        try:
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        return None

    def tree_rss_bytes(self, pid: int, pgid: int | None, job: Any = None) -> int | None:
        if os.name == "nt":
            return None if job is None else job.peak_job_memory()
        target = pgid if pgid is not None else pid
        page = os.sysconf("SC_PAGE_SIZE")
        total = 0
        for stat in Path("/proc").glob("[0-9]*/stat"):
            try:
                text = stat.read_text(encoding="ascii")
                fields = text[text.rindex(")") + 2:].split()
                if int(fields[2]) == int(target):
                    total += int(fields[21]) * page
            except (OSError, ValueError, IndexError):
                continue
        return total

    def set_below_normal(self, proc: Any) -> dict[str, Any]:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k.SetPriorityClass.restype = wintypes.BOOL
            k.GetPriorityClass.argtypes = [wintypes.HANDLE]
            k.GetPriorityClass.restype = wintypes.DWORD
            ok = bool(k.SetPriorityClass(int(proc._handle), BELOW_NORMAL_PRIORITY_CLASS))
            measured = int(k.GetPriorityClass(int(proc._handle)))
            return {"kind": "windows_priority_class", "requested": "BELOW_NORMAL", "measured": measured,
                    "ok": ok and measured == BELOW_NORMAL_PRIORITY_CLASS, "job_priority_class_limit": True}
        os.setpriority(os.PRIO_PROCESS, proc.pid, POSIX_NICE)
        measured = os.getpriority(os.PRIO_PROCESS, proc.pid)
        return {"kind": "posix_nice", "requested": POSIX_NICE, "measured": measured,
                "ok": measured >= POSIX_NICE}


class ResourceGuard:
    """Card v3 section 6 + section 10 STOP 7 on the parent's (injectable) clock.
    check() -> (pause_new_work, stop_reason_or_None)."""

    def __init__(self, probe: Any, *, ram_stop_sec: float = RAM_STOP_SEC,
                 pause_free_bytes: int = RAM_PAUSE_FREE_BYTES, ram_cap_bytes: int = RAM_CAP_BYTES):
        self.probe = probe
        self.ram_stop_sec = float(ram_stop_sec)
        self.pause_free_bytes = int(pause_free_bytes)
        self.ram_cap_bytes = int(ram_cap_bytes)
        self.low_since: float | None = None
        self.paused = False
        self.pause_events = 0
        self.min_free: int | None = None
        self.max_rss: int | None = None
        self.longest_low_sec = 0.0

    def check(self, now: float, pid: int | None = None, pgid: int | None = None,
              job: Any = None) -> tuple[bool, str | None]:
        free = self.probe.available_bytes()
        if free is None:
            return self.paused, "STOP_RAM_PROBE"
        free = int(free)
        self.min_free = free if self.min_free is None else min(self.min_free, free)
        if free < self.pause_free_bytes:
            if self.low_since is None:
                self.low_since = now
            if not self.paused:
                self.paused = True
                self.pause_events += 1
            low = now - self.low_since
            self.longest_low_sec = max(self.longest_low_sec, low)
            if low > self.ram_stop_sec:
                return True, "STOP_RAM_FLOOR"
        else:
            self.low_since = None
            self.paused = False
        if pid is not None:
            rss = self.probe.tree_rss_bytes(pid, pgid, job)
            if rss is not None:
                self.max_rss = int(rss) if self.max_rss is None else max(self.max_rss, int(rss))
                if int(rss) > self.ram_cap_bytes:
                    return self.paused, "STOP_RAM_CAP"
        return self.paused, None

    def attestation(self) -> dict[str, Any]:
        return {"ram_cap_bytes": self.ram_cap_bytes, "pause_free_bytes": self.pause_free_bytes,
                "ram_stop_sec": self.ram_stop_sec, "pause_events": self.pause_events,
                "min_free_bytes": self.min_free, "max_tree_rss_bytes": self.max_rss,
                "longest_low_ram_sec": round(self.longest_low_sec, 3),
                "ram_cap_enforcement": "windows: Job JOB_MEMORY limit (OS) + peak job memory watchdog; "
                                       "posix: process-group RSS watchdog",
                "stop_7": "free RAM < 8 GB for > 10 min = STOP_RAM_FLOOR"}


def job_struct_sizes() -> dict[str, int]:
    """Expected on 64-bit Windows: basic 64, IO_COUNTERS 48, extended 144, THREADENTRY32 28."""
    import ctypes
    io, basic, ext, thread = _job_structs()
    return {"basic": ctypes.sizeof(basic), "io": ctypes.sizeof(io), "extended": ctypes.sizeof(ext),
            "threadentry32": ctypes.sizeof(thread), "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8}


def _kernel32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    _, _, ext, thread = _job_structs()
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    k.IsProcessInJob.restype = wintypes.BOOL
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateJobObject.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    k.GetCurrentProcess.argtypes = []
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(thread)]
    k.Thread32First.restype = wintypes.BOOL
    k.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(thread)]
    k.Thread32Next.restype = wintypes.BOOL
    k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenThread.restype = wintypes.HANDLE
    k.ResumeThread.argtypes = [wintypes.HANDLE]
    k.ResumeThread.restype = wintypes.DWORD
    k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(ctypes.c_uint32)]
    k.QueryInformationJobObject.restype = wintypes.BOOL
    return k


class WindowsJob:
    """Owns a Job Object handle. close() terminates any member and closes the handle."""

    def __init__(self, kernel, handle):
        self.kernel = kernel
        self.handle = handle
        self.closed = False

    def contains(self, process_handle) -> bool:
        import ctypes
        from ctypes import wintypes
        flag = wintypes.BOOL(False)
        if not self.kernel.IsProcessInJob(process_handle, self.handle, ctypes.byref(flag)):
            raise Ext2Stop("STOP_JOB")
        return bool(flag.value)

    def supervisor_outside(self) -> bool:
        return not self.contains(self.kernel.GetCurrentProcess())

    def peak_job_memory(self) -> int | None:
        """Peak committed job memory (>= current; conservative for the 24 GB watchdog)."""
        import ctypes
        _, _, extended, _ = _job_structs()
        info = extended()
        size = ctypes.c_uint32(0)
        query = getattr(self.kernel, "QueryInformationJobObject", None)
        if query is None or not query(self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                      ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(size)):
            return None
        return int(info.PeakJobMemoryUsed)

    def close(self) -> None:
        if self.closed:
            return
        self.kernel.TerminateJobObject(self.handle, 1)
        self.kernel.CloseHandle(self.handle)
        self.closed = True


def start_worker(command: list[str], *, env: Mapping[str, str], cwd: str, ram_cap_bytes: int = RAM_CAP_BYTES):
    """Windows: create suspended (BelowNormal), assign a KILL_ON_JOB_CLOSE job with the
    BelowNormal priority-class and 24 GB job-memory limits, then resume.
    POSIX: a new session (process group) so the whole tree can be killed."""
    if os.name != "nt":
        return subprocess.Popen(list(command), env=dict(env), cwd=cwd, start_new_session=True), None
    import ctypes

    kernel = _kernel32()
    _, _, extended, thread_entry = _job_structs()
    proc = subprocess.Popen(list(command), env=dict(env), cwd=cwd,
                            creationflags=CREATE_SUSPENDED | BELOW_NORMAL_PRIORITY_CLASS)
    job = None
    try:
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise Ext2Stop("STOP_JOB")
        job = WindowsJob(kernel, handle)
        info = fill_job_limits(extended(), ram_cap_bytes)
        if not kernel.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                              ctypes.byref(info), ctypes.sizeof(info)):
            raise Ext2Stop("STOP_JOB")
        if not kernel.AssignProcessToJobObject(handle, int(proc._handle)):
            raise Ext2Stop("STOP_JOB")
        snap = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            raise Ext2Stop("STOP_JOB")
        resumed = 0
        try:
            entry = thread_entry()
            entry.dwSize = ctypes.sizeof(thread_entry)
            ok = kernel.Thread32First(snap, ctypes.byref(entry))
            while ok:
                if entry.th32OwnerProcessID == proc.pid:
                    thread = kernel.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                    if thread:
                        if kernel.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed += 1
                        kernel.CloseHandle(thread)
                ok = kernel.Thread32Next(snap, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snap)
        if resumed < 1:
            raise Ext2Stop("STOP_JOB")
    except Exception:
        proc.kill()
        if job is not None:
            job.close()
        raise
    return proc, job


def job_attestation(proc, job) -> dict[str, Any]:
    """Measured, never hard-coded. Windows: IsProcessInJob for child and supervisor.
    POSIX: the child's process group differs from the supervisor's."""
    if job is not None:
        child_in = job.contains(int(proc._handle))
        outside = job.supervisor_outside()
        return {"kind": "windows_job_object", "kill_on_job_close": True, "child_in_job": child_in,
                "supervisor_outside_job": bool(outside and child_in)}
    if os.name == "nt":
        return {"kind": "none", "supervisor_outside_job": False, "child_in_job": False}
    child_pgid = os.getpgid(proc.pid)
    return {"kind": "posix_process_group", "child_pgid_differs": child_pgid != os.getpgid(0),
            "supervisor_outside_job": None, "child_in_job": None}


def windows_job_members(supervisor_pid: int, child_pid: int) -> dict[str, Any]:
    """Planning helper only (no attestation): who should be in the job."""
    if int(supervisor_pid) == int(child_pid):
        raise Ext2Stop("STOP_JOB")
    return {"members": [int(child_pid)], "excludes": [int(supervisor_pid)]}


def kill_tree(pid: int, pgid: int | None = None) -> list[str]:
    if os.name == "nt":
        command = ["taskkill", "/F", "/T", "/PID", str(pid)]
        subprocess.run(command, check=False, capture_output=True)
        return command
    target = int(pgid if pgid is not None else pid)
    try:
        os.killpg(target, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return ["killpg", str(target)]


def read_completion(marker: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(marker).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def completion_ok(marker_data: Mapping[str, Any] | None, run_nonce: str, card_sha12: str) -> bool:
    """RK2/A18: only a marker carrying this run's nonce and card counts."""
    if not marker_data:
        return False
    return (marker_data.get("run_nonce") == run_nonce and marker_data.get("card_sha12") == card_sha12
            and bool(marker_data.get("finished_aedt")))


def classify_exit(returncode: int | None, marker_ok: bool) -> str:
    if returncode is None:
        return "STOP_UNKNOWN_EXIT"
    if returncode != 0:
        return "STOP_CHILD_EXIT"
    if not marker_ok:
        return "STOP_NO_COMPLETION_MARKER"
    return "COMPLETED"


def classify_connect(address: str, allowlist: Sequence[str] = CONNECT_ALLOWLIST) -> str:
    if address not in set(allowlist):
        return "STOP_NETWORK"
    return "ALLOWED"


def imported_roots(source: str) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def import_violations(source: str) -> list[str]:
    bad = []
    for name in sorted(imported_roots(source)):
        if name in _IMPORT_DENY or name not in _IMPORT_ALLOW:
            bad.append(name)
    return bad


def audit_files(paths: Sequence[Path]) -> list[str]:
    found = []
    for path in paths:
        for name in import_violations(path.read_text(encoding="utf-8")):
            found.append(f"{path.name}:{name}")
    return found


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


EXTERNAL_DATA_ANCHOR = "external-data"


def external_data_anchor(data_root: Path) -> Path:
    """N4: the guard is anchored on the `external-data` ancestor of the data root
    (or the data root itself when no such ancestor exists)."""
    data = Path(data_root).resolve()
    for candidate in (data, *data.parents):
        if candidate.name == EXTERNAL_DATA_ANCHOR:
            return candidate
    return data


def assert_roots(data_root: Path, output_root: Path) -> None:
    anchor = external_data_anchor(data_root)
    output = Path(output_root).resolve()
    if output == anchor or _under(output, anchor):
        raise Ext2Stop("STOP_OUTPUT_INSIDE_DATA_ROOT")
    if _under(anchor, output):
        raise Ext2Stop("STOP_OUTPUT_CONTAINS_DATA_ROOT")


def create_run_root(output_root: Path) -> Path:
    """RK1: the run root must not exist; it is created exclusively."""
    root = Path(output_root)
    root.parent.mkdir(parents=True, exist_ok=True)
    try:
        root.mkdir(exist_ok=False)
    except FileExistsError:
        raise Ext2Stop("STOP_RUN_ROOT_EXISTS") from None
    return root


def guarded_write(path: Path, text: str | bytes, *, data_root: Path, output_root: Path) -> None:
    """Write a new output file. Mode 'x': an existing file is never overwritten."""
    assert_roots(data_root, output_root)
    target = Path(path)
    if _under(target, external_data_anchor(data_root)):
        raise Ext2Stop("STOP_WRITE_DATA_ROOT")
    if not _under(target, Path(output_root)):
        raise Ext2Stop("STOP_WRITE_OUTSIDE_OUTPUT")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if isinstance(text, bytes):
            with open(target, "xb") as handle:
                handle.write(text)
        else:
            with open(target, "x", encoding="utf-8") as handle:
                handle.write(text)
    except FileExistsError:
        raise Ext2Stop("STOP_OVERWRITE") from None


def write_control(path: Path, text: str) -> None:
    """Control files (heartbeat, unit.json) are replaced atomically; never outputs."""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def seal_receipt(payload: dict[str, Any], forbidden_substrings: Sequence[str] = ()) -> dict[str, Any]:
    def walk(value: Any, key: str | None = None) -> None:
        if key is not None and key.lower() in _RECEIPT_KEYS:
            raise Ext2Stop("STOP_RECEIPT_SECRET")
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, str(child_key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child, key)
    walk(payload)
    encoded = json.dumps(payload, sort_keys=True)
    for secret in forbidden_substrings:
        if secret and secret in encoded:
            raise Ext2Stop("STOP_RECEIPT_SECRET")
    return payload


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def _cap(name: str, value: float | None, default: float) -> float:
    chosen = default if value is None else float(value)
    if chosen <= 0.0 or chosen > default:
        raise Ext2Stop(name)
    return chosen


def real_run_device_stop(stages: Sequence[Mapping[str, Any]]) -> str | None:
    for stage in stages:
        if stage.get("fallback") or stage.get("parity_test"):
            return "STOP_GPU_FALLBACK"
        if stage.get("stage") == "exit_lattice" and stage.get("device") == "cpu":
            return "STOP_GPU_FALLBACK"
    return None


def verify_import_pins(pin_path: Path, service_root: Path) -> None:
    document = json.loads(Path(pin_path).read_text(encoding="utf-8"))
    files = document.get("files")
    if not isinstance(files, dict) or not files:
        raise Ext2Stop("STOP_IMPORT_PIN")
    for rel, expected in files.items():
        target = (Path(service_root) / rel).resolve()
        if not target.is_file():
            raise Ext2Stop("STOP_IMPORT_PIN")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != expected:
            raise Ext2Stop("STOP_IMPORT_PIN")


RECEIPT_REQUIRED_KEYS = (
    "card_sha", "card_sha12", "frozen_bindings", "pr_head", "import_pins", "allowlist_sha", "h1_seal_sha",
    "windows", "folds",
    "undefined_stat_bars", "censored_close_count", "liquidation_count", "path_end_count",
    "cuda_prestart", "dual_c2_projection_hours", "start_aedt", "end_aedt", "wall_sec",
    "child_exit_code", "completion_marker_ok", "run_nonce", "stop_reason", "r10_scrub_attestation",
    "label", "resource_limits", "gate6_disclosure", "f6_eligibility_disclosure",
)
GATE6_DISCLOSURE = "gate 6 nominal-level; gate 7 White RC binding"


def frozen_bindings(overrides: Mapping[str, str | None] | None = None) -> list[dict[str, Any]]:
    """RK D4 list field: every frozen file the run is bound to (sha256 or None = pending)."""
    extra = dict(overrides or {})
    unknown = set(extra) - {name for name, _, _ in FROZEN_BINDINGS}
    if unknown:
        raise Ext2Stop("STOP_BINDING_UNKNOWN")
    out = []
    for name, sha, what in FROZEN_BINDINGS:
        value = extra.get(name, sha)
        if value is not None and (len(str(value)) != 64 or any(c not in "0123456789abcdef" for c in str(value))):
            raise Ext2Stop("STOP_BINDING_SHA")
        out.append({"name": name, "sha256": value, "file": what, "pending": value is None})
    return out


def bindings_complete(bindings: Sequence[Mapping[str, Any]]) -> bool:
    return bool(bindings) and all(b.get("sha256") for b in bindings)


def _read_unit(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def supervise(command: Sequence[str], *, data_root: Path, output_root: Path,
              disk_free: Callable[[], int], env: Mapping[str, str] | None = None,
              hard_stop_sec: float | None = None, flag_sec: float | None = None,
              cpu_unit_sec: float | None = None, gpu_unit_sec: float | None = None,
              heartbeat_stall_sec: float | None = None, output_cap_bytes: int | None = None,
              require_gpu: bool = False, forbidden_substrings: Sequence[str] = (),
              card_sha: str = CARD_SHA_V3, pr_head: str | None = None,
              poll_sec: float = 0.05, cuda_check: Callable[[], bool] | None = None,
              probe: Any = None, ram_stop_sec: float | None = None, ram_cap_bytes: int | None = None,
              clock: Callable[[], float] = time.monotonic,
              binding_overrides: Mapping[str, str | None] | None = None) -> dict[str, Any]:
    """Run one worker. The parent owns the caps, the disk floor, the RAM floor/cap,
    priority, the nonce and the final kill. `probe`/`clock` are injectable for tests."""
    data_root = Path(data_root)
    output_root = Path(output_root)
    started_wall = time.time()
    card_sha12 = str(card_sha)[:12]
    stages: list[dict[str, Any]] = [{"stage": "supervisor", "device": "cpu", "fallback": False}]
    receipt: dict[str, Any] = {key: None for key in RECEIPT_REQUIRED_KEYS}
    receipt.update({
        "schema": "ext2_supervisor_receipt_v2",
        "live": False,
        "paper_only": True,
        "card_sha": card_sha,
        "card_sha12": card_sha12,
        "pr_head": pr_head,
        "gate6_disclosure": GATE6_DISCLOSURE,
        "child_started": False,
        "tree_kill": False,
        "status": "STOP",
        "reason": "STOP",
        "stop_reason": "STOP",
        "exit_code": None,
        "child_exit_code": None,
        "completion_marker_ok": False,
        "devices": stages,
        "units": [],
        "wall_flag_2h": False,
        "start_aedt": aedt_now(),
        "receipt_written": False,
    })
    source_env = dict(os.environ if env is None else env)
    clean = scrub_env(source_env)
    forbidden = tuple(forbidden_substrings) + scrubbed_values(source_env)
    receipt["r10_scrub_attestation"] = {
        "scrubbed_key_count": len(source_env) - len(clean),
        "markers": list(_ENV_MARKERS),
        "receipt_value_check": "scrubbed values (len>=%d) are forbidden substrings" % _SECRET_MIN_LEN,
        "network_detection": "static import audit + runtime sys.addaudithook socket hook in worker; "
                             "C-extension sockets bypass audit hooks (disclosed)",
    }
    proc = None
    job = None
    pgid = None
    root_created = False
    guard = None
    receipt["resource_limits"] = {"max_threads": MAX_THREADS, "ram_cap_bytes": RAM_CAP_BYTES,
                                  "pause_free_bytes": RAM_PAUSE_FREE_BYTES, "ram_stop_sec": RAM_STOP_SEC,
                                  "applied": False}
    try:
        receipt["frozen_bindings"] = frozen_bindings(binding_overrides)
        assert_roots(data_root, output_root)
        hard = _cap("STOP_HARD_STOP_EXTENDED", hard_stop_sec, HARD_STOP_SEC)
        ram_stop = _cap("STOP_RAM_STOP_EXTENDED", ram_stop_sec, RAM_STOP_SEC)
        ram_cap = int(_cap("STOP_RAM_CAP_EXTENDED", ram_cap_bytes, RAM_CAP_BYTES))
        guard = ResourceGuard(probe if probe is not None else SystemProbe(), ram_stop_sec=ram_stop,
                              ram_cap_bytes=ram_cap)
        flag = _cap("STOP_FLAG_EXTENDED", flag_sec, FLAG_SEC)
        caps = {"cpu": _cap("STOP_CPU_CAP_EXTENDED", cpu_unit_sec, CPU_UNIT_SEC),
                "gpu": _cap("STOP_GPU_CAP_EXTENDED", gpu_unit_sec, GPU_UNIT_SEC),
                "pause": ram_stop * PAUSE_UNIT_SEC / RAM_STOP_SEC}
        stall = HEARTBEAT_STALL_SEC if heartbeat_stall_sec is None else float(heartbeat_stall_sec)
        cap_bytes = OUTPUT_CAP_BYTES if output_cap_bytes is None else int(output_cap_bytes)
        if int(disk_free()) < DISK_FLOOR_BYTES:
            raise Ext2Stop("STOP_DISK_FLOOR")
        if guard.probe.available_bytes() is None:
            raise Ext2Stop("STOP_RAM_PROBE")
        if require_gpu:
            from research.ext2_lattice import cuda_available
            check = cuda_check or cuda_available
            if not check():
                stages.append({"stage": "exit_lattice", "device": None, "fallback": False, "status": "STOP"})
                raise Ext2Stop("STOP_GPU_MISSING")
        from research.ext2_search import count_combos
        if count_combos() != 181442:
            raise Ext2Stop("STOP_COUNT")
        create_run_root(output_root)
        root_created = True
        marker = output_root / COMPLETION_NAME
        if marker.exists():
            raise Ext2Stop("STOP_STALE_COMPLETE")
        tmp_dir = output_root / "tmp"
        tmp_dir.mkdir()
        run_nonce = os.urandom(16).hex()
        receipt["run_nonce"] = run_nonce
        clean.update({NONCE_ENV: run_nonce, CARD_ENV: card_sha12,
                      "TEMP": str(tmp_dir), "TMP": str(tmp_dir), "TMPDIR": str(tmp_dir)})
        clean.update(thread_limit_env())
        proc, job = start_worker(list(command), env=clean, cwd=str(output_root))
        receipt["child_started"] = True
        receipt["child_pid"] = proc.pid
        pgid = None if os.name == "nt" else os.getpgid(proc.pid)
        receipt["job"] = job_attestation(proc, job)
        priority = guard.probe.set_below_normal(proc)
        receipt["resource_limits"].update({"applied": True, "thread_env": thread_limit_env(),
                                           "priority": priority})
        if not priority.get("ok"):
            raise Ext2Stop("STOP_PRIORITY")
        pause_path = output_root / PAUSE_NAME
        started = clock()
        beat_path = output_root / HEARTBEAT_NAME
        unit_path = output_root / UNIT_NAME
        reason = None
        unit_key = None
        unit_started = started
        unit_device = "cpu"
        last_slow = 0.0
        while proc.poll() is None:
            now = clock()
            unit = _read_unit(unit_path)
            if unit is not None and (unit.get("seq"), unit.get("device")) != unit_key:
                unit_key = (unit.get("seq"), unit.get("device"))
                unit_started = now
                unit_device = unit.get("device") if unit.get("device") in ("gpu", "pause") else "cpu"
                receipt["units"].append({"seq": unit.get("seq"), "device": unit_device,
                                         "name": str(unit.get("name", ""))[:64]})
            if now - unit_started >= caps[unit_device]:
                reason = {"gpu": "STOP_GPU_UNIT_CAP", "pause": "STOP_PAUSE_CAP"}.get(unit_device, "STOP_UNIT_CAP")
                break
            if now - started >= flag and not receipt["wall_flag_2h"]:
                receipt["wall_flag_2h"] = True
            if now - started >= hard:
                reason = "STOP_HARD_STOP"
                break
            age = None if not beat_path.exists() else time.time() - beat_path.stat().st_mtime
            if (age is None and now - started >= stall) or (age is not None and age >= stall):
                reason = "STOP_HEARTBEAT"
                break
            if now - last_slow >= 1.0 or poll_sec >= 1.0:
                last_slow = now
                if int(disk_free()) < DISK_RUN_FLOOR_BYTES:
                    reason = "STOP_DISK_RUN"
                    break
                if _dir_size(output_root) > cap_bytes:
                    reason = "STOP_OUTPUT_CAP"
                    break
                was_paused = guard.paused
                paused, ram_reason = guard.check(now, proc.pid, pgid, job)
                if ram_reason is not None:
                    reason = ram_reason
                    break
                if paused and not was_paused:
                    write_control(pause_path, json.dumps({"pause": True, "since": aedt_now()}))
                elif was_paused and not paused and pause_path.exists():
                    pause_path.unlink()
            time.sleep(poll_sec)
        if reason is not None:
            receipt["tree_kill"] = True
            receipt["kill_command"] = kill_tree(proc.pid, pgid)
        proc.wait(timeout=10)
        receipt["exit_code"] = proc.returncode
        receipt["child_exit_code"] = proc.returncode
        marker_ok = completion_ok(read_completion(marker), run_nonce, card_sha12)
        receipt["completion_marker_ok"] = marker_ok
        if reason is None:
            reason = classify_exit(proc.returncode, marker_ok)
        receipt["reason"] = reason
        receipt["tree_kill"] = True
        receipt["kill_command"] = kill_tree(proc.pid, pgid)
    except Ext2Stop as exc:
        receipt["reason"] = exc.reason
        if proc is not None and proc.poll() is None:
            receipt["tree_kill"] = True
            receipt["kill_command"] = kill_tree(proc.pid, pgid)
            proc.wait(timeout=10)
            receipt["exit_code"] = proc.returncode
            receipt["child_exit_code"] = proc.returncode
    except Exception as exc:  # noqa: BLE001 - recorded as a STOP, never swallowed silently
        receipt["reason"] = "STOP_SUPERVISOR"
        receipt["supervisor_error"] = type(exc).__name__
        if proc is not None and proc.poll() is None:
            receipt["tree_kill"] = True
            kill_tree(proc.pid, pgid)
    finally:
        if job is not None:
            job.close()
    if guard is not None:
        receipt["resource_limits"].update(guard.attestation())
    receipt["devices"] = stages
    receipt["end_aedt"] = aedt_now()
    receipt["wall_sec"] = round(time.time() - started_wall, 3)
    worker = None
    if root_created:
        worker = _read_unit(output_root / WORKER_RECEIPT_NAME)
    if worker:
        stages.extend(worker.get("devices") or [])
        for key, value in worker.items():
            if key in {"child_exit_code", "exit_code", "completion_marker_ok", "run_nonce", "start_aedt",
                       "end_aedt", "wall_sec", "status", "reason", "job", "devices", "units",
                       "tree_kill", "kill_command", "child_pid", "child_started", "schema", "live",
                       "paper_only", "wall_flag_2h", "receipt_written", "r10_scrub_attestation",
                       "resource_limits", "frozen_bindings", "gate6_disclosure"}:
                continue
            receipt[key] = value
        receipt["worker_run_nonce_ok"] = worker.get("run_nonce") == receipt.get("run_nonce")
        receipt["worker_thread_limits"] = worker.get("thread_limits")
        if receipt["reason"] == "STOP_CHILD_EXIT" and str(worker.get("stop_reason", "")).startswith("STOP"):
            receipt["worker_stop_reason"] = worker["stop_reason"]
    # A smoke worker (synthetic data, labelled SMOKE-NOT-A-RUN) may use the CPU lattice;
    # anything else (including a worker that wrote no receipt) is held to the GPU rule.
    smoke = bool(worker) and worker.get("mode") == "smoke" and not require_gpu
    fallback = None if smoke else real_run_device_stop(stages)
    if fallback and receipt["reason"] == "COMPLETED":
        receipt["reason"] = fallback
    receipt["status"] = "COMPLETED" if receipt["reason"] == "COMPLETED" else "STOP"
    receipt["stop_reason"] = None if receipt["status"] == "COMPLETED" else \
        receipt.get("worker_stop_reason") or receipt["reason"]
    if not root_created:
        return receipt
    try:
        sealed = seal_receipt(receipt, forbidden)
        sealed["receipt_written"] = True
        guarded_write(output_root / RECEIPT_NAME, json.dumps(sealed, sort_keys=True, indent=1),
                      data_root=data_root, output_root=output_root)
        lines = []
        for path in sorted(output_root.rglob("*")):
            if path.is_file() and path.name not in {SUMS_NAME, HEARTBEAT_NAME, UNIT_NAME, PAUSE_NAME} \
                    and "tmp" not in path.relative_to(output_root).parts:
                lines.append(f"{_sha256_file(path)}  {path.relative_to(output_root).as_posix()}")
        guarded_write(output_root / SUMS_NAME, "\n".join(lines) + "\n",
                      data_root=data_root, output_root=output_root)
    except Ext2Stop as exc:
        receipt["status"] = "STOP"
        receipt["reason"] = exc.reason
        receipt["stop_reason"] = exc.reason
        receipt["receipt_written"] = False
        return receipt
    return sealed
