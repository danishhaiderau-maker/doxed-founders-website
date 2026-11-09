"""EXT2 run supervisor. Paper only. The parent enforces caps and the kill.

The heartbeat thread only proves the worker is alive. Wall-clock caps are
enforced here, outside the worker: 600s per CPU unit, 120s per GPU unit,
and a 4h hard stop. Caps cannot be raised. A GPU fallback in a real run is
a STOP. Receipts do not carry environment dumps or secrets.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
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
_ENV_MARKERS = (
    "BITFINEX_", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "APIKEY",
    "PRIVATE_KEY", "ACCESS_KEY", "CREDENTIAL",
)
_IMPORT_ALLOW = frozenset({
    "__future__", "ast", "collections", "datetime", "gzip", "hashlib", "io", "itertools",
    "json", "math", "os", "pathlib", "signal", "subprocess", "threading", "time",
    "typing", "numpy", "torch", "research",
})
_IMPORT_DENY = frozenset({
    "socket", "requests", "urllib", "http", "ftplib", "smtplib", "websocket",
    "aiohttp", "ssl", "urllib3", "httplib",
})
_RECEIPT_KEYS = frozenset({
    "env", "environ", "environment", "os.environ", "secret", "secrets", "api_key",
    "token", "password", "bitfinex_api_key",
})
CONNECT_ALLOWLIST: tuple[str, ...] = ()


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
        if any(marker in upper for marker in _ENV_MARKERS):
            continue
        clean[key] = value
    return clean


def windows_job_members(supervisor_pid: int, child_pid: int) -> dict[str, Any]:
    if int(supervisor_pid) == int(child_pid):
        raise Ext2Stop("STOP_JOB")
    return {"members": [int(child_pid)], "excludes": [int(supervisor_pid)], "supervisor_outside_job": True}


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


def classify_exit(returncode: int | None, marker_present: bool) -> str:
    if returncode is None:
        return "STOP_UNKNOWN_EXIT"
    if returncode != 0:
        return "STOP_CHILD_EXIT"
    if not marker_present:
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


def assert_roots(data_root: Path, output_root: Path) -> None:
    data = Path(data_root).resolve()
    output = Path(output_root).resolve()
    if output == data or _under(output, data):
        raise Ext2Stop("STOP_OUTPUT_INSIDE_DATA_ROOT")


def guarded_write(path: Path, text: str, *, data_root: Path, output_root: Path) -> None:
    assert_roots(data_root, output_root)
    target = Path(path)
    if _under(target, Path(data_root)):
        raise Ext2Stop("STOP_WRITE_DATA_ROOT")
    if not _under(target, Path(output_root)):
        raise Ext2Stop("STOP_WRITE_OUTSIDE_OUTPUT")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


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


def supervise(command: Sequence[str], *, data_root: Path, output_root: Path,
              disk_free: Callable[[], int], env: Mapping[str, str] | None = None,
              hard_stop_sec: float | None = None, cpu_unit_sec: float | None = None,
              gpu_unit_sec: float | None = None, unit_device: str = "cpu",
              heartbeat_stall_sec: float | None = None, output_cap_bytes: int | None = None,
              require_gpu: bool = False, forbidden_substrings: Sequence[str] = ()) -> dict[str, Any]:
    """Run one worker. The parent owns the caps, the disk floor, and the final kill."""
    data_root = Path(data_root)
    output_root = Path(output_root)
    stages: list[dict[str, Any]] = [{"stage": "supervisor", "device": "cpu", "fallback": False}]
    receipt: dict[str, Any] = {
        "schema": "ext2_supervisor_receipt_v1",
        "live": False,
        "paper_only": True,
        "child_started": False,
        "tree_kill": False,
        "status": "STOP",
        "reason": "STOP",
        "exit_code": None,
        "devices": stages,
        "unit_device": unit_device,
    }
    proc = None
    pgid = None
    try:
        assert_roots(data_root, output_root)
        hard = _cap("STOP_HARD_STOP_EXTENDED", hard_stop_sec, HARD_STOP_SEC)
        cpu_cap = _cap("STOP_CPU_CAP_EXTENDED", cpu_unit_sec, CPU_UNIT_SEC)
        gpu_cap = _cap("STOP_GPU_CAP_EXTENDED", gpu_unit_sec, GPU_UNIT_SEC)
        stall = HEARTBEAT_STALL_SEC if heartbeat_stall_sec is None else float(heartbeat_stall_sec)
        cap_bytes = OUTPUT_CAP_BYTES if output_cap_bytes is None else int(output_cap_bytes)
        unit_cap = gpu_cap if unit_device == "gpu" else cpu_cap
        if int(disk_free()) < DISK_FLOOR_BYTES:
            raise Ext2Stop("STOP_DISK_FLOOR")
        if require_gpu:
            from research.ext2_search import _cuda_available
            if not _cuda_available():
                stages.append({"stage": "exit_lattice", "device": None, "fallback": False, "status": "STOP"})
                raise Ext2Stop("STOP_GPU_MISSING")
        from research.ext2_search import count_combos
        if count_combos() != 181442:
            raise Ext2Stop("STOP_COUNT")
        output_root.mkdir(parents=True, exist_ok=True)
        clean = scrub_env(dict(os.environ if env is None else env))
        proc = subprocess.Popen(
            list(command), env=clean, cwd=str(output_root), start_new_session=os.name != "nt",
        )
        receipt["child_started"] = True
        receipt["child_pid"] = proc.pid
        pgid = None if os.name == "nt" else os.getpgid(proc.pid)
        job = windows_job_members(os.getpid(), proc.pid)
        receipt["job"] = job
        started = time.monotonic()
        beat_path = output_root / HEARTBEAT_NAME
        marker = output_root / COMPLETION_NAME
        reason = None
        while proc.poll() is None:
            now = time.monotonic()
            if now - started >= unit_cap:
                reason = "STOP_UNIT_CAP"
                break
            if now - started >= hard:
                reason = "STOP_HARD_STOP"
                break
            age = None if not beat_path.exists() else time.time() - beat_path.stat().st_mtime
            if age is None and now - started >= stall:
                reason = "STOP_HEARTBEAT"
                break
            if age is not None and age >= stall:
                reason = "STOP_HEARTBEAT"
                break
            if int(disk_free()) < DISK_RUN_FLOOR_BYTES:
                reason = "STOP_DISK_RUN"
                break
            if _dir_size(output_root) > cap_bytes:
                reason = "STOP_OUTPUT_CAP"
                break
            time.sleep(0.05)
        if reason is not None:
            receipt["tree_kill"] = True
            receipt["kill_command"] = kill_tree(proc.pid, pgid)
        proc.wait(timeout=5)
        receipt["exit_code"] = proc.returncode
        if reason is None:
            reason = classify_exit(proc.returncode, marker.is_file())
        if proc.returncode is None:
            reason = "STOP_UNKNOWN_EXIT"
        receipt["reason"] = reason
        receipt["status"] = "COMPLETED" if reason == "COMPLETED" else "STOP"
        receipt["tree_kill"] = True
        receipt["kill_command"] = kill_tree(proc.pid, pgid)
    except Ext2Stop as exc:
        receipt["status"] = "STOP"
        receipt["reason"] = exc.reason
        if proc is not None and proc.poll() is None:
            receipt["tree_kill"] = True
            receipt["kill_command"] = kill_tree(proc.pid, pgid)
            proc.wait(timeout=5)
            receipt["exit_code"] = proc.returncode
    except Exception:
        receipt["status"] = "STOP"
        receipt["reason"] = "STOP_SUPERVISOR"
        if proc is not None and proc.poll() is None:
            receipt["tree_kill"] = True
            kill_tree(proc.pid, pgid)
    receipt["devices"] = stages
    if real_run_device_stop(stages) and receipt["reason"] not in {"STOP_GPU_MISSING", "STOP_GPU_FALLBACK"}:
        fallback = real_run_device_stop(stages)
        if fallback and receipt["status"] == "COMPLETED":
            receipt["status"] = "STOP"
            receipt["reason"] = fallback
    try:
        sealed = seal_receipt(receipt, forbidden_substrings)
        guarded_write(
            output_root / "receipt.json", json.dumps(sealed, sort_keys=True),
            data_root=data_root, output_root=output_root,
        )
    except Ext2Stop as exc:
        receipt["status"] = "STOP"
        receipt["reason"] = exc.reason
        return receipt
    return sealed
