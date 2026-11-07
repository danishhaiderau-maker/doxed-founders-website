"""Resource caps, fixture jail, canary rejection and pin checks.

Missing cap enforcement is an acceptance failure. Cap exhaustion is
INCOMPLETE/STOP with no truncation and no ETA.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import signal
import sys
import time
import resource
from pathlib import Path

from .validate import (
    CanaryRejected,
    CapStop,
    DriftStop,
    ProfileRejected,
    ProhibitedAccess,
    SchemaError,
    sha256_bytes,
)

CPU_WORKERS = 2
RSS_MIB = 512
COMMAND_SECONDS = 120
SUITE_SECONDS = 600
RECEIPT_BYTES = 8 * 1024 * 1024
MAX_STRATEGIES = 256
VAS_SUM_BYTES = RSS_MIB * 1024 * 1024
PER_WORKER_VAS_BYTES = VAS_SUM_BYTES // CPU_WORKERS
STREAM_CHUNK = 65536
_IN_CHILD = False
_suite_arm = None
_armed_deadline = None
_evidence_deadline = None
_halt_runner = None
_active_child = None
_outer_deadline_expired = False
_evidence_expired = False
_proof_latch = False
_injection_mark = False


class SuiteHalt(BaseException):
    """Unwinds the frame that was running when the suite deadline fired.

    BaseException so except Exception and except CapStop do not resume that frame.
    """

    def __init__(self) -> None:
        super().__init__("suite_seconds")

BOUNDS = {
    "cpu_workers": CPU_WORKERS,
    "rss_mib": RSS_MIB,
    "command_seconds": COMMAND_SECONDS,
    "suite_seconds": SUITE_SECONDS,
    "receipt_bytes": RECEIPT_BYTES,
    "fixture_strategies": MAX_STRATEGIES,
}

CANARY_MARKERS = (
    "CANARY_SECRET_",
    "CANARY_RAW_",
    "SYNTHETIC_SECRET_CANARY",
    "RAW_PAYLOAD_CANARY",
)

PACKAGE_FILES = (
    "__init__.py",
    "README.md",
    "coverage.py",
    "dashboard_projection.py",
    "dependencies.lock.json",
    "fixture_cli.py",
    "fixtures/family-traces.json",
    "folds.py",
    "gates.py",
    "guards.py",
    "metrics.py",
    "packages.py",
    "statistics.py",
    "tests/test_acceptance.py",
    "validate.py",
)

LIBRARY_IMPORTS = frozenset(
    {
        "__future__",
        "argparse",
        "collections",
        "decimal",
        "fractions",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "re",
        "resource",
        "select",
        "signal",
        "sys",
        "time",
        "types",
    }
)

TEST_IMPORTS = LIBRARY_IMPORTS | frozenset(
    {"ast", "contextlib", "copy", "io", "research", "tempfile", "unittest"}
)


def package_root() -> Path:
    return Path(__file__).resolve().parent


def fixture_path() -> Path:
    return package_root() / "fixtures" / "family-traces.json"


def lock_path() -> Path:
    return package_root() / "dependencies.lock.json"


def enforce_workers(workers: int) -> None:
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1 or workers > CPU_WORKERS:
        raise CapStop("cpu_workers")


def enforce_strategy_count(count: int) -> None:
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise CapStop("fixture_strategies")
    if count > MAX_STRATEGIES:
        raise CapStop("fixture_strategies")


def enforce_receipt_bytes(size: int) -> None:
    if size > RECEIPT_BYTES:
        raise CapStop("receipt_bytes")


def enforce_elapsed(seconds: float, *, kind: str) -> None:
    limit = COMMAND_SECONDS if kind == "command" else SUITE_SECONDS
    if kind not in {"command", "suite"}:
        raise CapStop("clock")
    if seconds > limit:
        raise CapStop(f"{kind}_seconds")


def rss_kib() -> int:
    if sys.platform != "linux":
        raise CapStop("rss_platform_unsupported")
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def enforce_rss() -> int:
    observed = rss_kib()
    if observed > RSS_MIB * 1024:
        raise CapStop("rss_mib")
    return observed


class MonotonicClock:
    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._t0


def reject_access(kind: str) -> None:
    if kind != "none":
        raise ProhibitedAccess(kind)


def walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk_strings(key)
            yield from walk_strings(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from walk_strings(value)


def reject_canaries(payload) -> None:
    if isinstance(payload, bytes):
        text = payload.decode("utf-8")
        blobs = (text,)
    else:
        blobs = walk_strings(payload)
    for blob in blobs:
        for marker in CANARY_MARKERS:
            if marker in blob:
                raise CanaryRejected("canary_detected")


def admit_canonical_path(path: Path) -> Path:
    """Refuse any unpinned path before a fixture open or read."""
    allowed = fixture_path().resolve(strict=False)
    candidate = path.resolve(strict=False)
    if candidate != allowed:
        raise ProhibitedAccess("unpinned_alternate_path")
    return allowed


def jail_fixture(path: Path) -> Path:
    return admit_canonical_path(path)


def install_worker_vas_ceiling() -> None:
    """Cap this process at half of the 512 MiB sum so parent+child ceilings add to 512 MiB.

    VAS is a conservative upper bound, not RSS. The limit is installed after
    interpreter startup; delivery of a later kill can overshoot. There is no
    CPU-time cap and no claim of zero overshoot.
    """
    if sys.platform != "linux":
        raise CapStop("rss_platform_unsupported")
    ceiling = PER_WORKER_VAS_BYTES
    try:
        resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
    except (ValueError, OSError) as exc:
        raise CapStop("rss_mib") from exc


def read_canonical_verified() -> bytes:
    """One bounded read of the canonical fixture. Hash those bytes before return.

    The caller parses the returned buffer. This function does not decode JSON
    and does not read the fixture a second time.
    """
    path = fixture_path()
    hasher_calls = 0
    chunks = []
    total = 0
    digest_builder = hashlib.sha256()
    with path.open("rb") as handle:
        hasher_calls += 1
        while True:
            block = handle.read(STREAM_CHUNK)
            if not block:
                break
            total += len(block)
            if total > PER_WORKER_VAS_BYTES:
                raise CapStop("rss_mib")
            digest_builder.update(block)
            chunks.append(block)
    read_canonical_verified.opens = hasher_calls
    body = b"".join(chunks)
    if digest_builder.hexdigest() != load_lock()["fixture_sha256"]:
        raise DriftStop("fixture hash")
    return body


def _raise_command_cap(_signum, _frame):
    raise CapStop("command_seconds")


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def cap_line(exc: CapStop) -> str:
    return json.dumps(
        {"error": exc.reason, "ok": False, "status": exc.status},
        sort_keys=True,
    ) + "\n"


def read_pipe_bounded(fd: int, pid: int, *, timeout_s: int, clock=None) -> bytes:
    if timeout_s != COMMAND_SECONDS:
        raise CapStop("clock")
    ticker = clock or MonotonicClock()
    chunks = []
    total = 0
    while True:
        if _outer_deadline_expired or _evidence_expired:
            raise SuiteHalt()
        expired = ticker.elapsed() > COMMAND_SECONDS
        try:
            ready, _, _ = select.select([fd], [], [], 0 if expired else 0.2)
        except InterruptedError:
            continue
        if not ready:
            if expired:
                _signal_child(pid)
                raise CapStop("command_seconds")
            continue
        block = os.read(fd, STREAM_CHUNK)
        if not block:
            break
        total += len(block)
        if total > RECEIPT_BYTES:
            _signal_child(pid)
            raise CapStop("receipt_bytes")
        chunks.append(block)
    return b"".join(chunks)


def _signal_child(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _block_sigalrm():
    return signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})


def _restore_mask(previous) -> None:
    signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def _reap_once(pid: int, *, options: int) -> tuple[int, int]:
    while True:
        try:
            return os.waitpid(pid, options)
        except InterruptedError:
            continue


def release_owned(pid: int):
    """Masked reap of a published child. A live child is killed before the blocking reap."""
    global _active_child
    if pid is None or _active_child != pid:
        return None
    previous = _block_sigalrm()
    status = 0
    try:
        if _active_child != pid:
            return None
        try:
            waited, status = _reap_once(pid, options=os.WNOHANG)
        except ChildProcessError:
            if _active_child == pid:
                _active_child = None
            return None
        if waited == 0:
            _signal_child(pid)
            try:
                _waited, status = _reap_once(pid, options=0)
            except ChildProcessError:
                status = 0
        if _active_child == pid:
            _active_child = None
        return status
    finally:
        _restore_mask(previous)


def _kill_unpublished(pid: int) -> None:
    _signal_child(pid)
    try:
        _reap_once(pid, options=0)
    except ChildProcessError:
        return


def _child_enter(read_fd: int, write_fd: int, child_main) -> None:
    """Child signal state, then work, then os._exit. Never returns to the parent."""
    global _IN_CHILD
    _IN_CHILD = True
    code = 1
    try:
        os.close(read_fd)
        signal.signal(signal.SIGALRM, _raise_command_cap)
        signal.setitimer(signal.ITIMER_REAL, 0.0, 0.0)
        signal.alarm(COMMAND_SECONDS)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM})
        try:
            code = int(child_main(write_fd))
        except CapStop as exc:
            _write_all(write_fd, cap_line(exc).encode("utf-8"))
            code = 5
        except MemoryError:
            _write_all(write_fd, cap_line(CapStop("rss_mib")).encode("utf-8"))
            code = 5
    except BaseException:
        code = 1
    finally:
        try:
            os.close(write_fd)
        except OSError:
            pass
        os._exit(code)


def _fork_published(read_fd: int, write_fd: int, child_main) -> int:
    """Block SIGALRM from a successful fork until the parent publishes the pid."""
    global _active_child
    previous = _block_sigalrm()
    pid = None
    published = False
    try:
        pid = os.fork()
        if pid == 0:
            _child_enter(read_fd, write_fd, child_main)
        _active_child = pid
        published = True
        return pid
    except BaseException:
        if pid not in (None, 0) and not published:
            _kill_unpublished(pid)
        raise
    finally:
        if pid != 0:
            _restore_mask(previous)


def _on_suite_timer(_signum, _frame) -> None:
    apply_suite_stop()


def _expiry_kind() -> str | None:
    now = time.monotonic()
    if _armed_deadline is not None and now >= _armed_deadline:
        return "process"
    if _evidence_deadline is not None and now >= _evidence_deadline:
        return "evidence"
    return None


def apply_suite_stop() -> None:
    """Kill and reap a published child once, then halt the registered runner."""
    global _active_child, _injection_mark, _proof_latch, _outer_deadline_expired, _evidence_expired
    pid = _active_child
    if pid is not None:
        _signal_child(pid)
        try:
            _reap_once(pid, options=0)
        except ChildProcessError:
            pass
        if _active_child == pid:
            _active_child = None
    kind = _expiry_kind()
    injected = _injection_mark
    _injection_mark = False
    if injected and kind is None:
        _proof_latch = True
    elif kind == "evidence":
        _evidence_expired = True
    else:
        _outer_deadline_expired = True
    halt = _halt_runner
    if halt is not None:
        halt()
    raise SuiteHalt()


def arm_suite_deadline(halt_runner) -> None:
    """One parent ITIMER_REAL of SUITE_SECONDS. A second call does not move it."""
    global _suite_arm, _armed_deadline, _halt_runner
    global _evidence_deadline, _outer_deadline_expired, _evidence_expired, _proof_latch, _injection_mark
    if _IN_CHILD:
        raise CapStop("cpu_workers")
    if _suite_arm is not None:
        return
    prior_handler = signal.getsignal(signal.SIGALRM)
    prior_timer = signal.getitimer(signal.ITIMER_REAL)
    _halt_runner = halt_runner
    _armed_deadline = time.monotonic() + SUITE_SECONDS
    _evidence_deadline = None
    _outer_deadline_expired = False
    _evidence_expired = False
    _proof_latch = False
    _injection_mark = False
    signal.signal(signal.SIGALRM, _on_suite_timer)
    signal.setitimer(signal.ITIMER_REAL, float(SUITE_SECONDS), 0.0)
    _suite_arm = (prior_handler, prior_timer)


def disarm_suite_deadline() -> None:
    """Release any owned child, then restore the handler and timer saved at arm."""
    global _suite_arm, _halt_runner
    try:
        pid = _active_child
        if pid is not None:
            release_owned(pid)
    finally:
        if _suite_arm is None:
            return
        prior_handler, prior_timer = _suite_arm
        signal.setitimer(signal.ITIMER_REAL, 0.0, 0.0)
        signal.signal(signal.SIGALRM, prior_handler)
        signal.setitimer(signal.ITIMER_REAL, prior_timer[0], prior_timer[1])
        _suite_arm = None
        _halt_runner = None


def bind_halt_runner(callback):
    global _halt_runner
    previous = _halt_runner
    _halt_runner = callback
    return previous


def restore_halt_target(previous) -> None:
    """Put the outer stop back. A real or practical deadline then invokes it and raises."""
    global _halt_runner, _proof_latch
    _halt_runner = previous
    if _outer_deadline_expired or _evidence_expired:
        pid = _active_child
        if pid is not None:
            release_owned(pid)
        if previous is not None:
            previous()
        if sys.exc_info()[0] is None:
            raise SuiteHalt()
        return
    _proof_latch = False


def mark_injected_signal() -> None:
    """Marks the next SIGALRM as proof only while the real and practical deadlines are still ahead."""
    global _injection_mark
    if _suite_arm is None:
        raise CapStop("clock")
    if _expiry_kind() is not None:
        return
    _injection_mark = True


def declare_deadline_due() -> None:
    """Practical monotonic deadline. Does not change SUITE_SECONDS or the installed itimer."""
    global _evidence_deadline
    if _suite_arm is None:
        raise CapStop("clock")
    _evidence_deadline = time.monotonic() - 1.0


def run_contained(child_main) -> tuple[int, bytes]:
    """Parent plus one child. No third process. Parent reads the only receipt pipe."""
    if _IN_CHILD:
        raise CapStop("cpu_workers")
    install_worker_vas_ceiling()
    read_fd, write_fd = os.pipe()
    try:
        pid = _fork_published(read_fd, write_fd, child_main)
    except BaseException:
        os.close(read_fd)
        try:
            os.close(write_fd)
        except OSError:
            pass
        raise
    os.close(write_fd)
    status = 0
    try:
        body = read_pipe_bounded(read_fd, pid, timeout_s=COMMAND_SECONDS)
    finally:
        os.close(read_fd)
        released = release_owned(pid)
        if released is not None:
            status = released
    if os.WIFSIGNALED(status):
        raise CapStop("rss_mib")
    exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
    return exit_code, body


def vas_ceiling_probe() -> str:
    """Child allocation at the per-worker ceiling. Parent does not allocate it."""
    def child(write_fd: int) -> int:
        try:
            blob = bytearray(PER_WORKER_VAS_BYTES)
            del blob
            _write_all(write_fd, b"ALLOCATED")
        except MemoryError:
            _write_all(write_fd, b"MEMORY_ERROR")
        except CapStop:
            _write_all(write_fd, b"CAP")
        return 0

    _code, message = run_contained(child)
    return message.decode("utf-8")


def load_lock() -> dict:
    data = json.loads(lock_path().read_text(encoding="utf-8"))
    if data.get("schema") != "results_gates_v1_dependency_lock" or data.get("version") != "1":
        raise DriftStop("lock schema")
    if data.get("third_party") != [] or data.get("stdlib_only") is not True:
        raise DriftStop("lock dependencies")
    if data.get("bounds") != BOUNDS:
        raise DriftStop("lock bounds")
    return data


def check_profile_pin(profile_hash: str, profile_id: str) -> None:
    lock = load_lock()
    if profile_id != lock["profile_id"]:
        raise ProfileRejected("unapproved profile")
    if profile_hash != lock["profile_sha256"]:
        raise ProfileRejected("profile hash drift")
    if lock["real_evaluation_permitted"] is not False:
        raise ProfileRejected("real evaluation is not permitted")


def list_package_files() -> list[str]:
    root = package_root()
    found = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        found.append(path.relative_to(root).as_posix())
    found.sort()
    if found != sorted(PACKAGE_FILES):
        raise DriftStop("package file set drift")
    return found


def pin_files() -> dict:
    root = package_root()
    files = list_package_files()
    return {
        rel: sha256_bytes((root / rel).read_bytes())
        for rel in files
    }


def commit_receipt(path: Path, body: bytes) -> None:
    if _outer_deadline_expired or _evidence_expired:
        raise SuiteHalt()
    enforce_receipt_bytes(len(body))
    if b'"eta"' in body:
        raise SchemaError("eta")
    path.write_bytes(body)
