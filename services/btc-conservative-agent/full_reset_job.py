"""Background full-reset job support: isolated heavy work, heartbeat guard, status files.

The in-process guarded reset used to hash tens of GB and scan ~400k entries inside one
blocking HTTP request, starving the bot. The heavy, bot-independent calls
(``execute_research_reset`` and target sampling) now run in a SEPARATE low-priority
process; the bot thread that drives the stage machine only waits on it. Progress is
published through small atomic status files and served read-only by
``GET /api/monitor/full-reset``; the route never blocks on the job.

Nothing here deletes anything itself, resumes the pause, or edits pinned deleter code.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

JOB_DIR_PARTS = ("full-reset-receipts", "_job")
STATUS_FILE = "status.json"
PROGRESS_FILE = "progress.json"
HEARTBEAT_FILE = "heartbeat.json"
THROTTLE_AGE_S = 5.0
ABORT_AFTER_S = 30.0
STALE_STATUS_S = 600.0
# Stages from which files/inventory may already have been mutated (HM: reports must say so).
DELETION_STAGES = frozenset({"PAYLOAD_DELETION", "PHYSICAL_SCOPE_DELETION", "V22_SEAL_RETIREMENT",
                             "GENOME_RESET", "AUTHORITY_RETIREMENT", "EMPTY_WAL_REBIND", "PUBLISH_NEW_EPOCH"})
HEARTBEAT_PERIOD_S = 1.0
TASKS = ("execute", "sample")


class HeartbeatAbort(RuntimeError):
    """Bot heartbeat stayed above the limit for ABORT_AFTER_S; fail closed, resumable."""


class IsolatedTaskError(RuntimeError):
    def __init__(self, kind, message, extra=None):
        super().__init__(message)
        self.kind, self.extra = kind, extra or {}


def job_dir(runtime_root) -> Path:
    return Path(runtime_root).joinpath(*JOB_DIR_PARTS)


def _atomic_write(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(body, handle, sort_keys=True, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class JobStatus:
    """Bot-side status writer (single writer: the job thread)."""

    def __init__(self, runtime_root, operation_id, *, mode="full-reset-no-archive"):
        self.dir = job_dir(runtime_root)
        self.body = {"schema": "full_reset_job_status_v1", "operation_id": operation_id, "mode": mode,
                     "state": "RUNNING", "stage": "STARTING", "outcome": None, "blockers": [],
                     "started_at": time.time(), "updated_at": time.time(), "pid": os.getpid(),
                     "epoch_stamped": False, "deletion_started": False}
        self.flush()

    def flush(self):
        self.body["updated_at"] = time.time()
        _atomic_write(self.dir / STATUS_FILE, self.body)

    def stage(self, name):
        self.body["stage"] = name
        if name in DELETION_STAGES:
            self.body["deletion_started"] = True
        self.flush()

    def finish(self, outcome, *, blockers=(), epoch_stamped=False, error=None):
        self.body.update(state="DONE" if outcome == "COMPLETE" else "FAILED", outcome=outcome,
                         blockers=list(blockers), epoch_stamped=bool(epoch_stamped), error=error)
        self.flush()


def read_status(runtime_root, *, live_armed=False, now=None) -> dict:
    """Read-only view for GET /api/monitor/full-reset; only reads small files."""
    now = time.time() if now is None else now
    directory = job_dir(runtime_root)
    status = _read_json(directory / STATUS_FILE)
    if status is None:
        return {"schema": "full_reset_job_view_v1", "state": "IDLE", "outcome": None, "alert": None}
    view = dict(status)
    progress = _read_json(directory / PROGRESS_FILE) or {}
    view["progress"] = {key: progress.get(key) for key in (
        "phase", "scope_name", "completed_targets", "total_targets", "fingerprinted_bytes",
        "scanned_entries", "throttled", "heartbeat_age_s", "updated_at")}
    view["bytes_done"] = progress.get("fingerprinted_bytes")
    view["counts"] = {"completed_targets": progress.get("completed_targets"),
                      "total_targets": progress.get("total_targets")}
    age = now - float(status.get("updated_at") or 0)
    view["status_age_s"] = round(age, 1)
    alert = None
    if status.get("state") == "RUNNING" and age > STALE_STATUS_S:
        view["state"], view["outcome"] = "FAILED", "INTERRUPTED"
        view["blockers"] = list(status.get("blockers") or []) + ["JOB_STATUS_STALE"]
        alert = "partial reset: job status stale while RUNNING (interrupted)"
    elif status.get("state") == "FAILED":
        alert = f"reset failed: {status.get('outcome')}"
    if live_armed and view.get("state") == "RUNNING":
        alert = "reset running while LIVE ARMED"
    view["alert"] = {"level": "RED", "reason": alert} if alert else None
    return view


_RECEIPT_DIRS = ("full-reset-receipts", "research_reset_receipts")
_RECEIPT_JSON_MAX = 256 * 1024
_RECEIPT_MAX_FILES = 200
_RECEIPT_MAX_DIRS = 50
_RECEIPT_STR_MAX = 200
_RECEIPT_MASK = re.compile(r"token|secret|key|confirm", re.IGNORECASE)


def _shape_value(key, value):
    if _RECEIPT_MASK.search(str(key)):
        return "<masked>"
    if isinstance(value, str):
        return value[:_RECEIPT_STR_MAX]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"{type(value).__name__}[{len(value)}]"


def _walk_files(base):
    """Lazy depth-first file walk; never materialises or sorts the whole tree."""
    for entry in os.scandir(base):
        try:
            if entry.is_dir(follow_symlinks=False):
                yield from _walk_files(entry.path)
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path)
        except OSError:
            continue


def read_receipt_summary(runtime_root) -> dict:
    """Read-only summary of the reset receipt directories (names, sizes, shape of small JSON receipts).

    Bounded: at most 50 receipt directories and 200 files each (lazy walk, islice), JSON only under 256 KiB,
    string values cut to 200 chars, keys matching token|secret|key|confirm masked. No writes, no deletion."""
    root = Path(runtime_root)
    out = {"schema": "full_reset_receipt_summary_v1", "directories": [],
           "note": "progress targets/bytes count every scope's fingerprinted payload; receipt files describe each pass"}
    for name in _RECEIPT_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        try:
            subs = [Path(e.path) for e in itertools.islice((e for e in os.scandir(base) if e.is_dir(follow_symlinks=False)), _RECEIPT_MAX_DIRS)]
        except OSError:
            continue
        for sub in sorted(subs):
            files, total = [], 0
            walker = _walk_files(sub)
            batch = list(itertools.islice(walker, _RECEIPT_MAX_FILES + 1))
            truncated = len(batch) > _RECEIPT_MAX_FILES
            for path in batch[:_RECEIPT_MAX_FILES]:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                total += size
                entry = {"file": str(path.relative_to(root)), "bytes": size}
                if path.suffix == ".json" and size <= _RECEIPT_JSON_MAX:
                    doc = _read_json(path)
                    if isinstance(doc, dict):
                        entry["shape"] = {str(k): _shape_value(k, v) for k, v in itertools.islice(doc.items(), 40)}
                files.append(entry)
            out["directories"].append({"dir": f"{name}/{sub.name}", "files_listed": len(files),
                                       "bytes_listed": total, "truncated": truncated, "files": files})
    return out


class HeartbeatPublisher(threading.Thread):
    """Runs in the bot while a job is active. Its own liveness measures starvation."""

    def __init__(self, runtime_root, get_heartbeat_ts):
        super().__init__(name="full-reset-heartbeat", daemon=True)
        self.path = job_dir(runtime_root) / HEARTBEAT_FILE
        self.get = get_heartbeat_ts
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            try:
                _atomic_write(self.path, {"written_at": time.time(), "heartbeat_ts": float(self.get() or 0)})
            except Exception:  # noqa: BLE001 - best effort; a stale file makes the worker throttle
                pass
            self._halt.wait(HEARTBEAT_PERIOD_S)

    def stop(self):
        self._halt.set()


class Guard:
    """Worker-side guard: throttle above THROTTLE_AGE_S, abort after ABORT_AFTER_S sustained."""

    def __init__(self, runtime_root, *, sleep=time.sleep, clock=time.time,
                 throttle_age=THROTTLE_AGE_S, abort_after=ABORT_AFTER_S, require_file=True):
        self.dir = job_dir(runtime_root)
        self.sleep, self.clock = sleep, clock
        self.throttle_age, self.abort_after, self.require_file = throttle_age, abort_after, require_file
        self.throttled_total = 0.0
        self.last_age = 0.0

    def heartbeat_age(self):
        beat = _read_json(self.dir / HEARTBEAT_FILE)
        if beat is None:
            return float("inf") if self.require_file else 0.0
        now = self.clock()
        return max(now - float(beat.get("written_at") or 0), now - float(beat.get("heartbeat_ts") or 0))

    def tick(self):
        age = self.heartbeat_age()
        self.last_age = age
        if age <= self.throttle_age:
            return
        began = self.clock()
        while True:
            self.sleep(1.0)
            age = self.heartbeat_age()
            self.last_age = age
            waited = self.clock() - began
            self.throttled_total = waited
            if age <= self.throttle_age:
                return
            if waited >= self.abort_after:
                raise HeartbeatAbort(f"bot heartbeat age {age:.1f}s above {self.throttle_age}s for {waited:.0f}s")


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def run_isolated(runtime_root, task, payload, *, status=None, poll_s=1.0, timeout_s=None, python=None):
    """Run one heavy task in a separate low-priority process and return its JSON result."""
    if task not in TASKS:
        raise ValueError("unknown isolated task")
    directory = job_dir(runtime_root)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time() * 1000)}-{os.getpid()}"
    task_path, result_path = directory / f"task-{stamp}.json", directory / f"result-{stamp}.json"
    _atomic_write(task_path, {"task": task, "payload": _jsonable(payload), "runtime_root": str(runtime_root)})
    command = [python or sys.executable, str(Path(__file__).resolve()), "worker", str(task_path), str(result_path)]
    proc = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parent), stdin=subprocess.DEVNULL,
                            preexec_fn=_die_with_parent)
    began = time.time()
    try:
        while proc.poll() is None:
            if timeout_s is not None and time.time() - began > timeout_s:
                proc.kill()
                proc.wait()
                raise IsolatedTaskError("Timeout", "isolated task timed out")
            if status is not None:
                status.flush()
            time.sleep(poll_s)
        result = _read_json(result_path)
        if result is None:
            raise IsolatedTaskError("WorkerDied", f"isolated worker exited {proc.returncode} without a result")
        if "error" in result:
            err = result["error"]
            raise IsolatedTaskError(err.get("type", "Error"), err.get("message", ""), err)
        return result["result"]
    finally:
        for path in (task_path, result_path):
            try:
                path.unlink()
            except OSError:
                pass


def _die_with_parent():
    """Linux PR_SET_PDEATHSIG: if the bot dies the worker dies too (fail closed, resumable)."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)
    except Exception:  # noqa: BLE001 - best effort, non-Linux dev machines
        pass


def _lower_priority():
    try:
        os.nice(19)
    except OSError:
        pass
    for command in (["ionice", "-c3", "-p", str(os.getpid())],):
        try:
            subprocess.run(command, check=False, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass


def _worker(task_path, result_path):
    _lower_priority()
    spec = json.loads(Path(task_path).read_text())
    root, payload = spec["runtime_root"], spec["payload"]
    directory = job_dir(root)
    guard = Guard(root, require_file=payload.get("require_heartbeat", True),
                  abort_after=float(os.environ.get("FULL_RESET_JOB_ABORT_AFTER_S", ABORT_AFTER_S)))
    import research_reset_inventory
    scanned = {"n": 0}

    def hook():
        scanned["n"] += research_reset_inventory.SCAN_YIELD_EVERY
        guard.tick()
        _atomic_write(directory / PROGRESS_FILE, {
            "phase": "INVENTORY_SCAN", "scanned_entries": scanned["n"], "throttled": guard.throttled_total,
            "heartbeat_age_s": round(guard.last_age, 2), "updated_at": time.time()})

    research_reset_inventory.SCAN_YIELD_HOOK = hook
    try:
        if spec["task"] == "sample":
            from research_reset_target_stability import sample_targets
            result = sample_targets(payload["root"], payload["proof"], payload["scope_names"])
        else:
            from research_reset_execution import execute_research_reset
            from research_reset_progress import make_reset_progress_callback
            advisory = make_reset_progress_callback(
                payload["root"], attempt_id=payload["attempt_id"], reset_id=payload["reset_id"],
                scope_name=payload.get("scope_name")) if payload.get("attempt_id") else None

            def progress(event):
                guard.tick()
                if advisory is not None:
                    advisory(event)
                _atomic_write(directory / PROGRESS_FILE, {
                    "phase": event.get("phase"), "scope_name": payload.get("scope_name"),
                    "completed_targets": event.get("completed_targets"), "total_targets": event.get("total_targets"),
                    "fingerprinted_bytes": event.get("fingerprinted_bytes"), "throttled": guard.throttled_total,
                    "heartbeat_age_s": round(guard.last_age, 2), "updated_at": time.time()})

            kwargs = dict(payload["kwargs"])
            result = execute_research_reset(progress_callback=progress, **kwargs)
        _atomic_write(Path(result_path), {"result": _jsonable(result)})
        return 0
    except BaseException as exc:  # noqa: BLE001 - report every failure to the parent, fail closed
        extra = {"type": type(exc).__name__, "message": str(exc)}
        unstable = getattr(exc, "unstable_targets", None)
        if unstable:
            extra["unstable_targets"] = _jsonable(unstable)
        stability = getattr(exc, "target_stability", None)
        if stability:
            extra["target_stability"] = _jsonable(stability)
        _atomic_write(Path(result_path), {"error": extra})
        return 1


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "worker":
        sys.exit(_worker(sys.argv[2], sys.argv[3]))
    raise SystemExit("usage: full_reset_job.py worker <task.json> <result.json>")


# --- bot-side hooks used by the reset stage machine (kept here so bot.py stays thin) ---
ACTIVE = None  # {"status": JobStatus, "root": Path, "operation_id": str} while a background job runs
ACTIVE_LOCK = threading.Lock()


def note(stage_name):
    active = ACTIVE
    if active is not None:
        try:
            active["status"].stage(stage_name)
        except Exception:  # noqa: BLE001 - status is advisory; never changes reset behaviour
            pass


def _isolated_error(exc):
    from research_exact_deletion import ResearchDeletionRejected
    if exc.kind == "ResearchDeletionRejected":
        error = ResearchDeletionRejected(str(exc))
        if exc.extra.get("unstable_targets"):
            error.unstable_targets = exc.extra["unstable_targets"]
        if exc.extra.get("target_stability"):
            error.target_stability = exc.extra["target_stability"]
        return error
    return RuntimeError(f"RESET_ISOLATED_{exc.kind}: {exc}")


def heavy_sample(root, proof, scope_names, direct):
    """Target sampling: isolated worker while a background job runs, else the direct in-process call."""
    active = ACTIVE
    if active is None:
        return direct(root, proof, scope_names)
    try:
        raw = run_isolated(active["root"], "sample", {
            "root": str(root), "proof": proof, "scope_names": list(scope_names)}, status=active["status"])
    except IsolatedTaskError as exc:
        raise _isolated_error(exc)
    return {path: tuple(row) for path, row in raw.items()}


def heavy_execute(direct, _root, _attempt_id, _reset_id, _progress_scope, /, **kwargs):
    """execute_research_reset: isolated while a background job runs, else direct with the advisory callback."""
    active = ACTIVE
    if active is None:
        from research_reset_progress import make_reset_progress_callback
        return direct(**kwargs, progress_callback=make_reset_progress_callback(
            _root, attempt_id=_attempt_id, reset_id=_reset_id, scope_name=_progress_scope))
    try:
        return run_isolated(active["root"], "execute", {
            "root": str(_root), "attempt_id": _attempt_id, "reset_id": _reset_id, "scope_name": _progress_scope,
            "kwargs": kwargs}, status=active["status"])
    except IsolatedTaskError as exc:
        raise _isolated_error(exc)
