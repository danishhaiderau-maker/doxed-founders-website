"""Guarded parent owner for the optional lifecycle evidence pipeline.

This module deliberately has no dependency on ``bot.py``.  The trading runtime
may later supply small pressure/overlap probes, but a failed or wedged evidence
worker can never pause or terminate trading.  Source cleanup is never exposed
as an option and every accepted child result must explicitly deny it.
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from lifecycle_pipeline_worker import create_request, verify_result


logger = logging.getLogger(__name__)

RUNTIME_SCHEMA = "lifecycle_pipeline_runtime_status_v1"
MIN_BACKOFF_SEC = 180.0
MAX_BACKOFF_SEC = 1800.0
DEFAULT_INTERVAL_SEC = 180.0
BACKLOG_INTERVAL_SEC = 1.0
DEFAULT_WALL_TIMEOUT_SEC = 75.0
DEFAULT_CPU_LIMIT_SEC = 60
DEFAULT_RSS_LIMIT_BYTES = 512 * 1024 * 1024


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # On Windows ``os.kill(pid, 0)`` is not a POSIX existence probe; Python
        # routes non-console signals through TerminateProcess, so probing the
        # current owner can terminate the bot itself with exit code zero.
        # Query a minimal process handle instead and never mutate the target.
        try:
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [
                ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong,
            ]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetExitCodeProcess.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
            ]
            kernel32.GetExitCodeProcess.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.GetLastError.restype = ctypes.c_ulong
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, int(pid)
            )
            if not handle:
                # ERROR_INVALID_PARAMETER is the documented nonexistent-PID
                # result. Access denied or a transient query failure is not
                # proof that an owner died, so retain its ownership fail-closed.
                return int(kernel32.GetLastError()) != 87
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return int(exit_code.value) == still_active
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _minimal_worker_environment(source_revision: str | None = None) -> dict[str, str]:
    """Return an allowlist-only environment with trusted provenance only."""
    allowed = (
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
        "LANG", "LC_ALL",
    )
    environment = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    revision = str(source_revision or "").strip().lower()
    if len(revision) == 40 and all(char in "0123456789abcdef" for char in revision):
        # Construct this from the already-validated owner input.  Never inherit
        # the parent environment wholesale: it may contain exchange/database
        # credentials, while the worker only needs exact provenance parity.
        environment["SOURCE_GIT_REV"] = revision
    return environment


def _posix_limits(cpu_limit_sec: int, rss_limit_bytes: int) -> Callable[[], None] | None:
    if os.name != "posix":
        return None

    def apply() -> None:
        import resource

        os.nice(10)
        cpu = max(1, int(cpu_limit_sec))
        rss = max(64 * 1024 * 1024, int(rss_limit_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (rss, rss))

    return apply


class LifecyclePipelineRuntime:
    """Exactly-one parent scheduler for a bounded credential-free subprocess."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        source_revision: str,
        work_root: str | Path | None = None,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        wall_timeout_sec: float = DEFAULT_WALL_TIMEOUT_SEC,
        cpu_limit_sec: int = DEFAULT_CPU_LIMIT_SEC,
        rss_limit_bytes: int = DEFAULT_RSS_LIMIT_BYTES,
        pressure_probe: Callable[[], bool | Mapping[str, Any]] | None = None,
        overlap_probe: Callable[[], bool | str | Sequence[str]] | None = None,
        overlap_paths: Sequence[str | Path] = (),
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        python_executable: str | Path | None = None,
        worker_path: str | Path | None = None,
    ) -> None:
        data_lexical = Path(os.path.abspath(str(data_root)))
        self.data_root = data_lexical.resolve(strict=True)
        if data_lexical != self.data_root or data_lexical.is_symlink() or not self.data_root.is_dir():
            raise ValueError("DATA_ROOT_LINKED_OR_INVALID")
        work_lexical = Path(os.path.abspath(str(work_root or self.data_root / "v3" / "lifecycle_worker")))
        work_lexical.mkdir(parents=True, exist_ok=True)
        self.work_root = work_lexical.resolve(strict=True)
        if work_lexical != self.work_root or work_lexical.is_symlink() or not self.work_root.is_dir():
            raise ValueError("WORK_ROOT_LINKED_OR_INVALID")
        try:
            self.work_root.relative_to(self.data_root)
        except ValueError as exc:
            raise ValueError("WORK_ROOT_OUTSIDE_DATA_ROOT") from exc
        self.source_revision = str(source_revision or "").lower()
        if len(self.source_revision) not in (40, 64) or any(
            char not in "0123456789abcdef" for char in self.source_revision
        ):
            raise ValueError("SOURCE_REVISION_NOT_FULL_HEX")
        self.interval_sec = max(1.0, float(interval_sec))
        self.wall_timeout_sec = max(1.0, float(wall_timeout_sec))
        self.cpu_limit_sec = max(1, int(cpu_limit_sec))
        self.rss_limit_bytes = max(64 * 1024 * 1024, int(rss_limit_bytes))
        self.pressure_probe = pressure_probe or (lambda: False)
        self.overlap_probe = overlap_probe or (lambda: False)
        self.overlap_paths = tuple(Path(path) for path in overlap_paths)
        self.clock = clock
        self.monotonic = monotonic
        self.python_executable = str(python_executable or sys.executable)
        self.worker_path = Path(worker_path or Path(__file__).with_name("lifecycle_pipeline_worker.py")).resolve(strict=True)
        self.owner_path = self.work_root / "pipeline-runtime-owner.json"
        self.owner_token = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._status: dict[str, Any] = {
            "schema": RUNTIME_SCHEMA,
            "source_revision": self.source_revision,
            "running": False,
            "owner": False,
            "active": False,
            "failure_count": 0,
            "backoff_sec": 0.0,
            "next_run_unix": None,
            "last_outcome": "NEVER_RUN",
            "last_error": None,
            "last_error_code": None,
            "last_worker_failure": None,
            "last_worker_failure_unix": None,
            "last_result": None,
            "last_success_unix": None,
            "pressure": False,
            "emergency": False,
            "overlap_code": None,
            "source_cleanup_authorized": False,
            "resource_limits": {
                "parent_wall_timeout_enforced": True,
                "cpu_rlimit_enforced": os.name == "posix",
                "rss_rlimit_enforced": os.name == "posix",
                "windows_below_normal_priority": os.name == "nt",
                "cpu_limit_sec": self.cpu_limit_sec,
                "rss_limit_bytes": self.rss_limit_bytes,
            },
        }

    def _claim_owner(self) -> bool:
        payload = {
            "schema": "lifecycle_pipeline_runtime_owner_v1",
            "pid": os.getpid(), "owner_token": self.owner_token,
            "created_unix": self.clock(),
        }
        raw = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
        for _attempt in range(2):
            try:
                descriptor = os.open(self.owner_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                try:
                    existing = json.loads(self.owner_path.read_text(encoding="utf-8"))
                    pid = int(existing.get("pid") or 0)
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    return False  # corrupt ownership evidence fails closed
                if _pid_alive(pid):
                    return False
                try:
                    self.owner_path.unlink()
                except OSError:
                    return False
                continue
            try:
                os.write(descriptor, raw)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return True
        return False

    def _release_owner(self) -> None:
        try:
            payload = json.loads(self.owner_path.read_text(encoding="utf-8"))
            if payload.get("owner_token") == self.owner_token:
                self.owner_path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass

    def _overlap_reason(self) -> str | None:
        try:
            observed = self.overlap_probe()
        except Exception as exc:
            return f"OVERLAP_PROBE_FAILED:{type(exc).__name__}"
        if isinstance(observed, str) and observed:
            return observed
        if isinstance(observed, Sequence) and not isinstance(observed, (str, bytes)) and observed:
            return ",".join(str(item) for item in observed)
        if observed is True:
            return "CALLER_REPORTED_SYNC_OR_INVENTORY_OVERLAP"
        active_paths = [str(path) for path in self.overlap_paths if path.exists()]
        return "ACTIVE_OVERLAP_PATH:" + ",".join(active_paths) if active_paths else None

    def _pressure(self) -> tuple[bool, bool, str | None]:
        try:
            observed = self.pressure_probe()
        except Exception as exc:
            return False, True, f"PRESSURE_PROBE_FAILED:{type(exc).__name__}"
        if isinstance(observed, Mapping):
            pressure = bool(observed.get("pressure"))
            emergency = bool(observed.get("emergency") or observed.get("critical"))
            return pressure, emergency, None
        return bool(observed), False, None

    def _record_failure(
        self, outcome: str, error: str, *, worker_failure: Mapping[str, Any] | None = None,
    ) -> None:
        retained_worker_failure = None
        if isinstance(worker_failure, Mapping):
            retained_worker_failure = {
                "error_class": str(worker_failure.get("error_class") or "")[:80],
                "error_code": str(worker_failure.get("error_code") or "")[:80],
            }
        with self._lock:
            failures = int(self._status["failure_count"]) + 1
            backoff = min(MAX_BACKOFF_SEC, MIN_BACKOFF_SEC * (2 ** (failures - 1)))
            self._status.update({
                "active": False, "failure_count": failures,
                "backoff_sec": backoff, "next_run_unix": self.clock() + backoff,
                "last_outcome": outcome, "last_error": str(error)[:1000],
                "last_error_code": str(
                    (worker_failure or {}).get("error_code")
                    or (worker_failure or {}).get("error_class") or outcome
                )[:80],
                "source_cleanup_authorized": False,
            })
            if retained_worker_failure is not None:
                self._status.update({
                    "last_worker_failure": retained_worker_failure,
                    "last_worker_failure_unix": self.clock(),
                })

    def _record_skip(self, outcome: str, reason: str, *, delay: float = 30.0) -> None:
        with self._lock:
            self._status.update({
                "active": False, "next_run_unix": self.clock() + max(1.0, delay),
                "last_outcome": outcome, "last_error": reason,
                "last_error_code": str(outcome)[:80],
                "source_cleanup_authorized": False,
            })

    def _record_success(self, receipt: Mapping[str, Any]) -> None:
        pipeline = receipt.get("pipeline") if isinstance(receipt.get("pipeline"), Mapping) else {}
        scan = pipeline.get("scan") if isinstance(pipeline.get("scan"), Mapping) else {}
        pending_dirty = scan.get("pending_dirty_lifecycles")
        sources_caught_up = scan.get("caught_up") is True
        backlog_pending = (
            isinstance(pending_dirty, int) and not isinstance(pending_dirty, bool)
            and pending_dirty > 0
        ) or not sources_caught_up
        summary = {
            "generated_at": receipt.get("generated_at"),
            "candidate_count": pipeline.get("candidate_count"),
            "bundle_count": pipeline.get("bundle_count"),
            "transfer_ready_count": pipeline.get("transfer_ready_count"),
            "transfer_bundle_count": pipeline.get("transfer_bundle_count"),
            "completion_appended_count": pipeline.get("completion_appended_count"),
            "pressure_mode": pipeline.get("pressure_mode"),
            "emergency_closure_mode": bool(pipeline.get("emergency_closure_mode")),
            "pending_dirty_lifecycles": pending_dirty,
            "promoted_qualification_retries": scan.get("promoted_qualification_retries"),
            "rows_scanned": scan.get("rows_scanned"),
            "bytes_indexed": scan.get("bytes_indexed"),
            "caught_up": sources_caught_up,
            "stage_counts": dict(pipeline.get("stage_counts") or {}),
            "blocker_counts": dict(pipeline.get("blocker_counts") or {}),
            "backlog_pending": backlog_pending,
        }
        with self._lock:
            self._status.update({
                "active": False, "failure_count": 0, "backoff_sec": 0.0,
                # A successful bounded batch is not completion while indexed
                # identities or unread ledger bytes remain. Continue promptly;
                # _run_once rechecks sync overlap and resource pressure before
                # every child, preserving the existing fail-closed fences.
                "next_run_unix": self.clock() + (
                    BACKLOG_INTERVAL_SEC
                    if backlog_pending and not pipeline.get("emergency_closure_mode")
                    else self.interval_sec
                ),
                "last_outcome": "SUCCESS", "last_error": None,
                "last_error_code": None,
                "last_worker_failure": None,
                "last_result": summary, "last_success_unix": self.clock(),
                "overlap_code": None, "source_cleanup_authorized": False,
            })

    def _launch(self, command: list[str]) -> subprocess.Popen:
        kwargs: dict[str, Any] = {
            "cwd": str(self.worker_path.parent),
            "env": _minimal_worker_environment(self.source_revision),
            "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "posix":
            kwargs.update(start_new_session=True, preexec_fn=_posix_limits(self.cpu_limit_sec, self.rss_limit_bytes))
        elif os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        return subprocess.Popen(command, **kwargs)

    def _terminate(self, process: subprocess.Popen) -> None:
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2.0)
            except Exception:
                pass

    def _run_once(self) -> bool:
        """Execute one guarded cycle; all failures are converted to status."""
        with self._lock:
            if self._process is not None:
                self._record_skip("DUPLICATE_ACTIVE_SKIPPED", "worker already active")
                return False
        overlap = self._overlap_reason()
        if overlap:
            with self._lock:
                self._status["overlap_code"] = str(overlap)[:160]
            self._record_skip("OVERLAP_SKIPPED", overlap)
            return False
        pressure, emergency, pressure_error = self._pressure()
        with self._lock:
            self._status.update({
                "pressure": bool(pressure), "emergency": bool(emergency),
                "overlap_code": None,
            })
        if pressure_error:
            self._record_skip(
                "PRESSURE_SKIPPED", pressure_error,
                delay=MIN_BACKOFF_SEC,
            )
            return False
        launch: Mapping[str, Any] | None = None
        try:
            launch = create_request(
                self.data_root, self.work_root, source_revision=self.source_revision,
                pressure_mode=bool(pressure or emergency),
                emergency_closure_mode=bool(emergency),
                max_lifecycles=1 if pressure or emergency else 5,
                max_runtime_sec=min(120.0, max(1.0, self.wall_timeout_sec - 1.0)),
            )
            command = [
                self.python_executable, str(self.worker_path),
                "--request", str(launch["request_path"]),
                "--result", str(launch["result_path"]),
                "--nonce", str(launch["nonce"]),
            ]
            process = self._launch(command)
            with self._lock:
                self._process = process
                self._status.update({"active": True, "last_outcome": "RUNNING", "last_error": None})
            try:
                return_code = process.wait(timeout=self.wall_timeout_sec)
            except subprocess.TimeoutExpired:
                self._terminate(process)
                self._record_failure("TIMEOUT", "parent wall-clock timeout exceeded")
                return False
            finally:
                with self._lock:
                    self._process = None
            if return_code != 0:
                worker_failure = None
                try:
                    receipt = verify_result(
                        launch["request_path"], launch["result_path"], launch["nonce"]
                    )
                    if receipt.get("status") == "FAILED":
                        worker_failure = receipt.get("failure")
                except BaseException:
                    pass
                if isinstance(worker_failure, Mapping):
                    logger.error(
                        "[LIFECYCLE PIPELINE] worker failed class=%s code=%s",
                        str(worker_failure.get("error_class") or "UNKNOWN")[:80],
                        str(worker_failure.get("error_code") or "UNKNOWN")[:80],
                    )
                self._record_failure(
                    "WORKER_FAILED", f"worker exit code {return_code}",
                    worker_failure=worker_failure,
                )
                return False
            receipt = verify_result(
                launch["request_path"], launch["result_path"], launch["nonce"]
            )
            if receipt.get("source_revision") != self.source_revision:
                raise ValueError("WORKER_RESULT_SOURCE_REVISION_MISMATCH")
            self._record_success(receipt)
            return True
        except BaseException as exc:
            # This owner is optional research infrastructure.  Never propagate
            # into a caller that may own the trading loop.
            self._record_failure("PARENT_GUARD_FAILURE", f"{type(exc).__name__}:{exc}")
            return False
        finally:
            # Result details are summarized in bounded status.  Per-attempt
            # nonce files are never allowed to accumulate on the Fly volume,
            # including timeouts, corrupt results, and interrupted children.
            if launch is not None:
                for field in ("request_path", "result_path"):
                    try:
                        Path(launch[field]).unlink(missing_ok=True)
                    except (OSError, KeyError, TypeError):
                        pass

    def _loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    due = self._status.get("next_run_unix")
                delay = 0.0 if due is None else max(0.0, float(due) - self.clock())
                if self._stop_event.wait(min(delay, 1.0)):
                    break
                if delay > 0:
                    continue
                self._run_once()
        except BaseException as exc:
            self._record_failure("OWNER_LOOP_FAILED", f"{type(exc).__name__}:{exc}")
        finally:
            with self._lock:
                self._status.update({"running": False, "active": False})
            self._release_owner()

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if not self._claim_owner():
                self._status.update({
                    "running": False, "owner": False,
                    "last_outcome": "DUPLICATE_OWNER_REJECTED",
                })
                return False
            self._stop_event.clear()
            self._status.update({
                "running": True, "owner": True, "next_run_unix": self.clock(),
                "last_outcome": "STARTED", "last_error": None,
            })
            self._thread = threading.Thread(
                target=self._loop, name="lifecycle-pipeline-runtime", daemon=True
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop_event.set()
        with self._lock:
            process = self._process
            thread = self._thread
        if process is not None:
            self._terminate(process)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self._release_owner()
            with self._lock:
                self._status.update({"running": False, "owner": False, "active": False})
        return stopped

    def status(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._status))


_default_runtime: LifecyclePipelineRuntime | None = None
_default_lock = threading.Lock()


def start(data_root: str | Path, **kwargs: Any) -> bool:
    """Start the process-global owner.  A second owner is rejected."""
    global _default_runtime
    with _default_lock:
        if _default_runtime is not None and _default_runtime.status()["running"]:
            return False
        candidate = LifecyclePipelineRuntime(data_root, **kwargs)
        if not candidate.start():
            return False
        _default_runtime = candidate
        return True


def stop(timeout: float = 5.0) -> bool:
    global _default_runtime
    with _default_lock:
        runtime = _default_runtime
    if runtime is None:
        return True
    stopped = runtime.stop(timeout)
    if stopped:
        with _default_lock:
            if _default_runtime is runtime:
                _default_runtime = None
    return stopped


def status() -> dict[str, Any]:
    with _default_lock:
        runtime = _default_runtime
    if runtime is None:
        return {
            "schema": RUNTIME_SCHEMA, "running": False, "owner": False,
            "active": False, "last_outcome": "NOT_STARTED",
            "source_revision": None,
            "source_cleanup_authorized": False,
        }
    return runtime.status()
