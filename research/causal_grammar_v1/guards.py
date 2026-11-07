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
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return int(usage.ru_maxrss) * 1024


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


def scan_text(text: str) -> None:
    for pattern in LEAK_PATTERNS:
        if pattern in text:
            raise ContractError("PAYLOAD_LEAK")


def scan_obj(obj: Any) -> None:
    scan_text(canonical_text(obj))


def module_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


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
        banned = imports & BANNED_IMPORTS
        if banned:
            raise ProhibitedAccess("PROHIBITED:import")
        allowed = test_allowed if name in test_names else library_allowed
        if not imports <= allowed:
            raise ProhibitedAccess("PROHIBITED:import")
        banned_env = "os" + ".environ"
        banned_get = "get" + "env"
        if banned_env in source or banned_get in source:
            raise ProhibitedAccess("PROHIBITED:env")
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
