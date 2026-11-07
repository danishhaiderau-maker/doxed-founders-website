"""Explicit manifest entry. No discovery and no environment configuration."""

from __future__ import annotations

import json
import sys

from research.coverage_audit.auditor import run_audit
from research.coverage_audit.guards import TINY_SYNTHETIC_PROFILE, canonical_bytes, sha256_hex
from research.coverage_audit.real_file_reader import PinError, PinnedRoot

_EXIT = {"COMPLETE": 0, "UNKNOWN": 3, "INCOMPLETE_FAIL": 4}


def main(argv) -> tuple[int, dict]:
    if list(argv)[:1] != ["--manifest"] or len(argv) != 2:
        return _fail()
    try:
        root, registry, allowlist, files, base, clock, seed = _manifest(argv[1])
        pinned = PinnedRoot(root)
        registry_bytes = pinned.open_read(registry).readback()
        allowlist_bytes = pinned.open_read(allowlist).readback()
        snapshot = json.loads(allowlist_bytes.decode("utf-8"))
        sources = {key: pinned.open_read(rel) for key, rel in files.items()}
    except (PinError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return _fail()
        report = run_audit(
        registry_bytes=registry_bytes,
        allowlist=snapshot if isinstance(snapshot, dict) else {},
        allowlist_sha256=sha256_hex(canonical_bytes(snapshot)),
        sources=sources,
        base_commit=base,
        clock_unix_ms=clock,
        seed=seed,
        profile=dict(TINY_SYNTHETIC_PROFILE),
    )
    sys.stdout.write(canonical_bytes(report).decode("ascii"))
    return _EXIT.get(report["status"], 4), report


def _manifest(path: str):
    root_holder = PinnedRoot(_parent(path))
    raw = root_holder.open_read(_name(path)).readback()
    document = json.loads(raw.decode("utf-8"))
    keys = {"root", "registry", "allowlist", "files", "base_commit", "clock_unix_ms", "seed", "profile_id"}
    if set(document) != keys or document["profile_id"] != TINY_SYNTHETIC_PROFILE["profile_id"]:
        raise PinError("rejected")
    files = document["files"]
    if not isinstance(files, dict) or not files:
        raise PinError("rejected")
    clock = document["clock_unix_ms"]
    seed = document["seed"]
    base = document["base_commit"]
    if not isinstance(clock, int) or isinstance(clock, bool) or clock < 0:
        raise PinError("rejected")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise PinError("rejected")
    if not isinstance(base, str) or len(base) != 40:
        raise PinError("rejected")
    return (
        document["root"],
        document["registry"],
        document["allowlist"],
        files,
        base,
        clock,
        seed,
    )


def _parent(path: str) -> str:
    if not isinstance(path, str) or "/" not in path:
        raise PinError("rejected")
    parent, _name = path.rsplit("/", 1)
    if parent == "" or _name == "":
        raise PinError("rejected")
    return parent


def _name(path: str) -> str:
    return path.rsplit("/", 1)[1]


def _fail() -> tuple[int, dict]:
    report = run_audit(
        registry_bytes=b"",
        allowlist={},
        allowlist_sha256="",
        sources={},
        base_commit="0" * 40,
        clock_unix_ms=0,
        seed=0,
    )
    report["reasons"] = sorted(set(report["reasons"]) | {"CLI_ARGS"})
    report["status"] = "INCOMPLETE_FAIL"
    sys.stdout.write(canonical_bytes(report).decode("ascii"))
    return 4, report


if __name__ == "__main__":
    code, _report = main(sys.argv[1:])
    raise SystemExit(code)
