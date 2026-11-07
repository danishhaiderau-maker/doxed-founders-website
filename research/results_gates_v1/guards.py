"""Resource caps, fixture jail, canary rejection and pin checks.

Missing cap enforcement is an acceptance failure. Cap exhaustion is
INCOMPLETE/STOP with no truncation and no ETA.
"""

from __future__ import annotations

import json
import sys
import time
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
        "pathlib",
        "re",
        "resource",
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
    import resource

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


def jail_fixture(path: Path) -> Path:
    root = (package_root() / "fixtures").resolve()
    candidate = path.resolve()
    if not candidate.is_relative_to(root) or candidate.suffix != ".json":
        raise ProhibitedAccess("real_root")
    return candidate


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
    enforce_receipt_bytes(len(body))
    if b'"eta"' in body:
        raise SchemaError("eta")
    path.write_bytes(body)
