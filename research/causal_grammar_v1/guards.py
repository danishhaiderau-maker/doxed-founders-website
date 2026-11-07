"""Caps, path guards, leakage scan, and substantive-byte pins.

Prohibited network, environment, and vault access is refused before any
such operation. Cap exhaustion is INCOMPLETE and does not truncate.
"""

from __future__ import annotations

import ast
import hashlib
import resource
import signal
import time
from pathlib import Path
from typing import Any

from research.causal_grammar_v1.canonical import canonical_text

from research.causal_grammar_v1.contracts_v1 import (
    BANNED_IMPORTS,
    LEAK_PATTERNS,
    MAX_COMMAND_SECONDS,
    MAX_CPU_WORKERS,
    MAX_DEFINITIONS,
    MAX_EVENTS,
    MAX_PEAK_RSS_BYTES,
    MAX_RECEIPT_BYTES,
    MAX_SUITE_SECONDS,
    PACKAGE_FILES,
    CapExhausted,
    ContractError,
    ProhibitedAccess,
)

_BANNED_PARTS = {"vault", "secrets", ".ssh"}
_BANNED_PREFIXES = (".env",)
_BANNED_SUFFIXES = (".pem", ".key")
_PROCESS_STARTED = time.perf_counter()
_DYNAMIC_IMPORT_SENTINEL = "__dynamic_import__"
ENFORCEMENT_DISCLOSURE = (
    "preemptive_checkpoints;"
    "intra_stage_overshoot_possible;"
    "not_zero_overshoot;"
    "rss_linux_kibibytes_times_1024;"
    "conservative_upper_bound_self_plus_children_maxrss;"
    "no_cpu_seconds_cap;"
    "no_setrlimit_no_cgroup;"
    "under_limit_run_is_not_ceiling_proof;"
    "stdlib_structural_schema_check_is_not_draft_2020_12;"
    "vas_recorded_pair_not_rss;"
    "hard_limit_not_raised;"
    "reserved_268435456_uninstalled_no_child;"
    "one_itimer_real;"
    "no_child_spawned"
)

_VAS_TARGET = 268435456
_BACKSTOP_SECONDS = 0.05
_active_clock = None
_expiry: dict | None = None
_observation = False
_refused: set[int] = set()
_rss_latch = ""
_suppress_success = False
_recorded_pair: tuple[int, int] | None = None
_deliveries = 0
_handler_installed = False
_limit_installed = False


def reject_prohibited(kind: str) -> None:
    raise ProhibitedAccess(f"PROHIBITED:{kind}")


def assert_workers(workers: int) -> None:
    enforce_before_work()
    if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
        raise CapExhausted("INCOMPLETE:CAP_WORKERS")
    if workers > MAX_CPU_WORKERS:
        raise CapExhausted("INCOMPLETE:CAP_WORKERS")


def assert_event_count(count: int) -> None:
    enforce_before_work()
    if count > MAX_EVENTS:
        raise CapExhausted("INCOMPLETE:CAP_EVENTS")


def assert_definition_count(count: int) -> None:
    enforce_before_work()
    if count > MAX_DEFINITIONS:
        raise CapExhausted("INCOMPLETE:CAP_DEFINITIONS")


def assert_elapsed(elapsed_seconds: float, *, suite: bool = False) -> None:
    enforce_before_work()
    limit = MAX_SUITE_SECONDS if suite else MAX_COMMAND_SECONDS
    code = "INCOMPLETE:CAP_SUITE_SECONDS" if suite else "INCOMPLETE:CAP_COMMAND_SECONDS"
    if elapsed_seconds > limit:
        raise CapExhausted(code)


def assert_receipt_size(size: int) -> None:
    enforce_before_work()
    if size > MAX_RECEIPT_BYTES:
        raise CapExhausted("INCOMPLETE:CAP_RECEIPT_BYTES")


def assert_rss(peak_rss_bytes: int) -> None:
    enforce_before_work()
    if peak_rss_bytes > MAX_PEAK_RSS_BYTES:
        raise CapExhausted("INCOMPLETE:CAP_RSS")


def peak_rss_bytes() -> int:
    """Conservative parent+children upper bound. Linux ru_maxrss is kibibytes.

    Summing the two maxima can over-count. A checkpoint can fire only between
    stages, so one stage may overshoot until the next checkpoint. That is not
    a zero-overshoot claim and it is not a cgroup or setrlimit gate.
    """
    own = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    children = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    return (own + children) * 1024


def process_elapsed() -> float:
    return time.perf_counter() - _PROCESS_STARTED


def suite_remaining() -> float:
    return MAX_SUITE_SECONDS - (time.perf_counter() - _PROCESS_STARTED)


def command_remaining(clock: CommandClock | None) -> float | None:
    if clock is None:
        return None
    return MAX_COMMAND_SECONDS - clock.elapsed()


def expiry_set() -> bool:
    return _expiry is not None


def rss_latched() -> bool:
    return _rss_latch != ""


def delivery_count() -> int:
    return _deliveries


def timer_delay() -> float:
    return float(signal.getitimer(signal.ITIMER_REAL)[0])


def recorded_limit_pair() -> tuple[int, int] | None:
    return _recorded_pair


def suppress_success() -> bool:
    return _suppress_success


def note_due(code: str, clock: CommandClock | None = None) -> None:
    """Sticky due transition. Does not raise."""
    global _expiry
    if code == "INCOMPLETE:CAP_SUITE_SECONDS":
        _expiry = {"code": code, "clock": None}
    elif clock is not None:
        _refused.add(id(clock))
    signal.setitimer(signal.ITIMER_REAL, _BACKSTOP_SECONDS, _BACKSTOP_SECONDS)


def _arm_snapshot(delay: float) -> None:
    signal.setitimer(signal.ITIMER_REAL, delay, delay)


def arm_earliest() -> None:
    """Before-work arm. May raise. Cleanup does not call this."""
    if _rss_latch:
        raise CapExhausted(_rss_latch)
    if _expiry is not None:
        raise CapExhausted(_expiry["code"])
    remaining = suite_remaining()
    active = command_remaining(_active_clock)
    if remaining <= 0:
        note_due("INCOMPLETE:CAP_SUITE_SECONDS")
        raise CapExhausted("INCOMPLETE:CAP_SUITE_SECONDS")
    if active is not None and active <= 0:
        note_due("INCOMPLETE:CAP_COMMAND_SECONDS", _active_clock)
        raise CapExhausted("INCOMPLETE:CAP_COMMAND_SECONDS")
    earliest = remaining if active is None else min(remaining, active)
    _arm_snapshot(earliest)


def enforce_before_work() -> None:
    if _observation:
        return
    arm_earliest()


def discover_suite_on_cleanup() -> None:
    """Nonraising cleanup. One remaining read. Does not call arm_earliest."""
    global _active_clock, _suppress_success
    if _expiry is not None:
        return
    _active_clock = None
    remaining = suite_remaining()
    if remaining <= 0:
        note_due("INCOMPLETE:CAP_SUITE_SECONDS")
        _suppress_success = True
        return
    _arm_snapshot(remaining)


def begin_command(clock: CommandClock) -> None:
    global _active_clock
    enforce_before_work()
    _active_clock = clock


def end_observation() -> None:
    global _observation
    if _expiry is not None:
        return
    _observation = False
    arm_earliest()


def arm_observation() -> None:
    global _observation
    _observation = True
    _arm_snapshot(_BACKSTOP_SECONDS)


def mark_memory_error() -> None:
    global _rss_latch
    _rss_latch = "INCOMPLETE:CAP_RSS"
    if _active_clock is not None:
        _refused.add(id(_active_clock))
    raise CapExhausted("INCOMPLETE:CAP_RSS")


def decide_due(suite_seconds: float, command_seconds: float | None) -> str:
    """Local wiring. Does not assign process latches."""
    if suite_seconds <= 0:
        return "INCOMPLETE:CAP_SUITE_SECONDS"
    if command_seconds is not None and command_seconds <= 0:
        return "INCOMPLETE:CAP_COMMAND_SECONDS"
    return "ARM"


def classify_limit_pair(soft: int, hard: int) -> str:
    """Local wiring. Does not call setrlimit or assign the process latch."""
    if soft > hard:
        return "LATCH"
    if hard == resource.RLIM_INFINITY or hard > _VAS_TARGET:
        return "LOWER_TO_TARGET"
    return "ACCEPT"


def _on_alarm(signum: int, frame: object) -> None:
    global _deliveries
    del signum, frame
    _deliveries += 1
    if _expiry is not None:
        signal.setitimer(signal.ITIMER_REAL, _BACKSTOP_SECONDS, _BACKSTOP_SECONDS)
        raise SystemExit(4)
    remaining = suite_remaining()
    active = command_remaining(_active_clock)
    if remaining <= 0 or (active is not None and active <= 0):
        code = "INCOMPLETE:CAP_SUITE_SECONDS" if remaining <= 0 else "INCOMPLETE:CAP_COMMAND_SECONDS"
        note_due(code, None if remaining <= 0 else _active_clock)
        raise SystemExit(4)
    if _observation:
        signal.setitimer(signal.ITIMER_REAL, _BACKSTOP_SECONDS, _BACKSTOP_SECONDS)
        raise SystemExit(4)
    arm_earliest()


def install_address_limit() -> None:
    """Once, at import. Does not raise. Does not raise a hard limit."""
    global _recorded_pair, _rss_latch, _limit_installed
    if _limit_installed:
        return
    _limit_installed = True
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    except (OSError, ValueError):
        _rss_latch = "INCOMPLETE:CAP_RSS"
        return
    if not isinstance(soft, int) or not isinstance(hard, int):
        _rss_latch = "INCOMPLETE:CAP_RSS"
        return
    if hard == resource.RLIM_INFINITY or hard > _VAS_TARGET:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (_VAS_TARGET, _VAS_TARGET))
            read_soft, read_hard = resource.getrlimit(resource.RLIMIT_AS)
        except (OSError, ValueError, OverflowError):
            _rss_latch = "INCOMPLETE:CAP_RSS"
            return
        if read_soft != _VAS_TARGET or read_hard != _VAS_TARGET:
            _rss_latch = "INCOMPLETE:CAP_RSS"
            return
        _recorded_pair = (_VAS_TARGET, _VAS_TARGET)
        return
    if soft > hard:
        _rss_latch = "INCOMPLETE:CAP_RSS"
        return
    try:
        read_soft, read_hard = resource.getrlimit(resource.RLIMIT_AS)
    except (OSError, ValueError):
        _rss_latch = "INCOMPLETE:CAP_RSS"
        return
    if read_soft != soft or read_hard != hard:
        _rss_latch = "INCOMPLETE:CAP_RSS"
        return
    _recorded_pair = (soft, hard)


def install_suite_protection() -> None:
    global _handler_installed
    if _handler_installed:
        return
    signal.signal(signal.SIGALRM, _on_alarm)
    _handler_installed = True
    install_address_limit()
    discover_suite_on_cleanup()


def preemptive_checkpoint(clock: CommandClock | None = None) -> None:
    """Halt before the next stage and before a success receipt."""
    enforce_before_work()
    assert_elapsed(process_elapsed(), suite=True)
    if clock is not None:
        clock.assert_open()
    assert_rss(peak_rss_bytes())


def reject_path(path: Path, allowed_roots: list[Path]) -> Path:
    for part in path.parts:
        lowered = part.lower()
        if lowered in _BANNED_PARTS or lowered.startswith(_BANNED_PREFIXES) or lowered.endswith(
            _BANNED_SUFFIXES
        ):
            raise ProhibitedAccess("PROHIBITED:vault")
    resolved = path.resolve()
    roots = [root.resolve() for root in allowed_roots]
    if not any(resolved == root or _is_relative_to(resolved, root) for root in roots):
        raise ProhibitedAccess("PROHIBITED:real-root")
    return resolved


def guarded_read_text(path: Path, allowed_roots: list[Path]) -> str:
    enforce_before_work()
    resolved = reject_path(path, allowed_roots)
    return resolved.read_text(encoding="utf-8")


def guarded_read_bytes(path: Path, allowed_roots: list[Path]) -> bytes:
    resolved = reject_path(path, allowed_roots)
    return resolved.read_bytes()


def guarded_write_text(path: Path, text: str, repo_root: Path) -> None:
    if _observation or (_active_clock is not None and id(_active_clock) in _refused):
        raise SystemExit(4)
    enforce_before_work()
    for part in path.parts:
        lowered = part.lower()
        if lowered in _BANNED_PARTS or lowered.startswith(_BANNED_PREFIXES):
            raise ProhibitedAccess("PROHIBITED:vault")
    resolved = path.resolve()
    root = repo_root.resolve()
    if resolved == root or _is_relative_to(resolved, root):
        raise ProhibitedAccess("PROHIBITED:receipt-inside-repo")
    try:
        payload = text.encode("utf-8")
    except MemoryError:
        mark_memory_error()
    assert_receipt_size(len(payload))
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(payload)


def _leak_found(text: str) -> bool:
    for pattern in LEAK_PATTERNS:
        if pattern in text:
            return True
    return False


def scan_text(text: str) -> None:
    if _leak_found(text):
        raise ContractError("PAYLOAD_LEAK")


def outer_scan_text(text: str) -> None:
    """Independent of scan_text. Tests must not replace this function."""
    if _leak_found(text):
        raise ProhibitedAccess("PROHIBITED:outer-leak")


def scan_obj(obj: Any) -> None:
    scan_text(canonical_text(obj))


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id + "." + func.attr
    return None


def _walk_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name not in {"__import__", "importlib.import_module"}:
                continue
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found.add(str(node.args[0].value).split(".")[0])
            else:
                found.add(_DYNAMIC_IMPORT_SENTINEL)
    return found


def module_imports(source: str) -> set[str]:
    return _walk_imports(source)


def _source_touches_env(source: str) -> bool:
    banned_env = "os" + ".environ"
    banned_get = "get" + "env"
    return banned_env in source or banned_get in source


def _imports_are_prohibited(found: set[str]) -> bool:
    return bool(found & BANNED_IMPORTS) or _DYNAMIC_IMPORT_SENTINEL in found


def outer_containment_scan(sources: dict[str, str]) -> None:
    """Always-on outer layer. Does not call audit_sources or reject_prohibited."""
    for source in sources.values():
        if _imports_are_prohibited(_walk_imports(source)):
            raise ProhibitedAccess("PROHIBITED:outer-import")
        if _source_touches_env(source):
            raise ProhibitedAccess("PROHIBITED:outer-env")


def schema_record_consistent(schema: dict) -> None:
    """Stdlib structural check. Not a Draft 2020-12 validator."""
    try:
        record = schema["$defs"]["normalized_record"]
        required = record["required"]
        properties = record["properties"]
    except (KeyError, TypeError):
        raise ContractError("SCHEMA_INCONSISTENT") from None
    if record.get("additionalProperties") is not False:
        raise ContractError("SCHEMA_INCONSISTENT")
    if not isinstance(required, list) or not isinstance(properties, dict):
        raise ContractError("SCHEMA_INCONSISTENT")
    for name in required:
        if name not in properties:
            raise ContractError("SCHEMA_INCONSISTENT")


def assert_record_matches_schema_structure(record: dict, schema: dict) -> None:
    schema_record_consistent(schema)
    properties = schema["$defs"]["normalized_record"]["properties"]
    required = schema["$defs"]["normalized_record"]["required"]
    for name in required:
        if name not in record:
            raise ContractError("SCHEMA_INCONSISTENT")
    for name in record:
        if name not in properties:
            raise ContractError("SCHEMA_INCONSISTENT")


def audit_sources(sources: dict[str, str], *, test_names: set[str]) -> dict[str, list[str]]:
    library_allowed = {
        "__future__",
        "argparse",
        "ast",
        "base64",
        "datetime",
        "hashlib",
        "json",
        "math",
        "pathlib",
        "resource",
        "signal",
        "sys",
        "time",
        "typing",
        "research",
    }
    test_allowed = library_allowed | {"tempfile", "unittest"}
    detected: dict[str, list[str]] = {}
    for name, source in sources.items():
        imports = module_imports(source)
        detected[name] = sorted(imports)
        if _imports_are_prohibited(imports):
            reject_prohibited("import")
        allowed = test_allowed if name in test_names else library_allowed
        ordinary = imports - {_DYNAMIC_IMPORT_SENTINEL}
        if not ordinary <= allowed:
            reject_prohibited("import")
        if _source_touches_env(source):
            reject_prohibited("env")
    return detected


def pin_substantive_bytes(package_root: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for relative in PACKAGE_FILES:
        payload = guarded_read_bytes(package_root / relative, [package_root])
        pins[relative] = hashlib.sha256(payload).hexdigest()
    return pins


def load_package_sources(package_root: Path) -> dict[str, str]:
    sources: dict[str, str] = {}
    for relative in PACKAGE_FILES:
        if relative.endswith(".py"):
            sources[relative] = guarded_read_text(package_root / relative, [package_root])
    return sources


class CommandClock:
    def __init__(self) -> None:
        self.started = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def assert_open(self) -> None:
        assert_elapsed(self.elapsed())


install_suite_protection()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
