"""EXT2 run supervisor. Paper only. The parent enforces caps and the kill.

The heartbeat thread only proves the worker is alive. Caps are enforced here,
outside the worker: 600 s per CPU unit and 120 s per GPU unit (the worker
announces each unit in `unit.json`; the parent times it on its own clock),
a 2 h run-level flag and a 4 h run-level hard stop. Caps cannot be raised.
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
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
WINDOWS_CHILD_START = ("create_suspended", "assign_job", "resume")


def windows_child_start_order() -> tuple[str, ...]:
    return WINDOWS_CHILD_START


def _job_structs():
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

    def close(self) -> None:
        if self.closed:
            return
        self.kernel.TerminateJobObject(self.handle, 1)
        self.kernel.CloseHandle(self.handle)
        self.closed = True


def start_worker(command: list[str], *, env: Mapping[str, str], cwd: str):
    """Windows: create suspended, assign a KILL_ON_JOB_CLOSE job, then resume.
    POSIX: a new session (process group) so the whole tree can be killed."""
    if os.name != "nt":
        return subprocess.Popen(list(command), env=dict(env), cwd=cwd, start_new_session=True), None
    import ctypes

    kernel = _kernel32()
    _, _, extended, thread_entry = _job_structs()
    proc = subprocess.Popen(list(command), env=dict(env), cwd=cwd, creationflags=CREATE_SUSPENDED)
    job = None
    try:
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise Ext2Stop("STOP_JOB")
        job = WindowsJob(kernel, handle)
        info = extended()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
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
    "card_sha", "card_sha12", "pr_head", "import_pins", "allowlist_sha", "h1_seal_sha", "windows", "folds",
    "undefined_stat_bars", "censored_close_count", "liquidation_count", "path_end_count",
    "cuda_prestart", "dual_c2_projection_hours", "start_aedt", "end_aedt", "wall_sec",
    "child_exit_code", "completion_marker_ok", "run_nonce", "stop_reason", "r10_scrub_attestation",
    "label",
)


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
              poll_sec: float = 0.05, cuda_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Run one worker. The parent owns the caps, the disk floor, the nonce and the final kill."""
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
    try:
        assert_roots(data_root, output_root)
        hard = _cap("STOP_HARD_STOP_EXTENDED", hard_stop_sec, HARD_STOP_SEC)
        flag = _cap("STOP_FLAG_EXTENDED", flag_sec, FLAG_SEC)
        caps = {"cpu": _cap("STOP_CPU_CAP_EXTENDED", cpu_unit_sec, CPU_UNIT_SEC),
                "gpu": _cap("STOP_GPU_CAP_EXTENDED", gpu_unit_sec, GPU_UNIT_SEC)}
        stall = HEARTBEAT_STALL_SEC if heartbeat_stall_sec is None else float(heartbeat_stall_sec)
        cap_bytes = OUTPUT_CAP_BYTES if output_cap_bytes is None else int(output_cap_bytes)
        if int(disk_free()) < DISK_FLOOR_BYTES:
            raise Ext2Stop("STOP_DISK_FLOOR")
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
        proc, job = start_worker(list(command), env=clean, cwd=str(output_root))
        receipt["child_started"] = True
        receipt["child_pid"] = proc.pid
        pgid = None if os.name == "nt" else os.getpgid(proc.pid)
        receipt["job"] = job_attestation(proc, job)
        started = time.monotonic()
        beat_path = output_root / HEARTBEAT_NAME
        unit_path = output_root / UNIT_NAME
        reason = None
        unit_key = None
        unit_started = started
        unit_device = "cpu"
        last_slow = 0.0
        while proc.poll() is None:
            now = time.monotonic()
            unit = _read_unit(unit_path)
            if unit is not None and (unit.get("seq"), unit.get("device")) != unit_key:
                unit_key = (unit.get("seq"), unit.get("device"))
                unit_started = now
                unit_device = "gpu" if unit.get("device") == "gpu" else "cpu"
                receipt["units"].append({"seq": unit.get("seq"), "device": unit_device,
                                         "name": str(unit.get("name", ""))[:64]})
            if now - unit_started >= caps[unit_device]:
                reason = "STOP_GPU_UNIT_CAP" if unit_device == "gpu" else "STOP_UNIT_CAP"
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
                       "paper_only", "wall_flag_2h", "receipt_written", "r10_scrub_attestation"}:
                continue
            receipt[key] = value
        receipt["worker_run_nonce_ok"] = worker.get("run_nonce") == receipt.get("run_nonce")
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
            if path.is_file() and path.name not in {SUMS_NAME, HEARTBEAT_NAME, UNIT_NAME} \
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
