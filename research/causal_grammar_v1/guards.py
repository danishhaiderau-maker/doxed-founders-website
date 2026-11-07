"""Caps, path guards, leakage scan, and substantive-byte pins.

Prohibited network, environment, and vault access is refused before any
such operation. Cap exhaustion is INCOMPLETE and does not truncate.
"""

from __future__ import annotations

import ast
import hashlib
import resource
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
    "stdlib_structural_schema_check_is_not_draft_2020_12"
)


def reject_prohibited(kind: str) -> None:
    raise ProhibitedAccess(f"PROHIBITED:{kind}")


def assert_workers(workers: int) -> None:
    if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
        raise CapExhausted("INCOMPLETE:CAP_WORKERS")
    if workers > MAX_CPU_WORKERS:
        raise CapExhausted("INCOMPLETE:CAP_WORKERS")


def assert_event_count(count: int) -> None:
    if count > MAX_EVENTS:
        raise CapExhausted("INCOMPLETE:CAP_EVENTS")


def assert_definition_count(count: int) -> None:
    if count > MAX_DEFINITIONS:
        raise CapExhausted("INCOMPLETE:CAP_DEFINITIONS")


def assert_elapsed(elapsed_seconds: float, *, suite: bool = False) -> None:
    limit = MAX_SUITE_SECONDS if suite else MAX_COMMAND_SECONDS
    code = "INCOMPLETE:CAP_SUITE_SECONDS" if suite else "INCOMPLETE:CAP_COMMAND_SECONDS"
    if elapsed_seconds > limit:
        raise CapExhausted(code)


def assert_receipt_size(size: int) -> None:
    if size > MAX_RECEIPT_BYTES:
        raise CapExhausted("INCOMPLETE:CAP_RECEIPT_BYTES")


def assert_rss(peak_rss_bytes: int) -> None:
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


def preemptive_checkpoint(clock: CommandClock | None = None) -> None:
    """Halt before the next stage and before a success receipt."""
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
    resolved = reject_path(path, allowed_roots)
    return resolved.read_text(encoding="utf-8")


def guarded_read_bytes(path: Path, allowed_roots: list[Path]) -> bytes:
    resolved = reject_path(path, allowed_roots)
    return resolved.read_bytes()


def guarded_write_text(path: Path, text: str, repo_root: Path) -> None:
    for part in path.parts:
        lowered = part.lower()
        if lowered in _BANNED_PARTS or lowered.startswith(_BANNED_PREFIXES):
            raise ProhibitedAccess("PROHIBITED:vault")
    resolved = path.resolve()
    root = repo_root.resolve()
    if resolved == root or _is_relative_to(resolved, root):
        raise ProhibitedAccess("PROHIBITED:receipt-inside-repo")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    assert_receipt_size(len(payload))
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


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
