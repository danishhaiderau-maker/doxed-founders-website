"""Explicit-argument CLI. Process environment is not read."""

from __future__ import annotations

import argparse
import io
import json
import sys

from research.coverage_audit.auditor import run_audit
from research.coverage_audit.guards import FILE_READ_CAP, AuditStop, canonical_bytes

_EXIT = {"COMPLETE": 0, "UNKNOWN": 3, "INCOMPLETE_FAIL": 4}


def main(argv, *, registry_bytes=None, allowlist_obj=None, sources=None, meter=None) -> tuple[int, dict]:
    """Run from an explicit argv list.

    registry_bytes, allowlist_obj, sources, and meter are in-process injections.
    They are not read from the environment. Path arguments are opened only when
    the corresponding object was not injected.
    """
    try:
        args = _parse(argv)
    except AuditStop:
        report = run_audit(
            registry_bytes=b"",
            allowlist={},
            allowlist_sha256="",
            sources={},
            base_commit="0" * 40,
            clock_unix_ms=0,
            seed=0,
            meter=meter,
        )
        report["reasons"] = sorted(set(report["reasons"]) | {"CLI_ARGS"})
        report["status"] = "INCOMPLETE_FAIL"
        _emit(report)
        return 4, report
    try:
        if registry_bytes is None:
            registry_bytes = _read_capped(args.registry)
        if allowlist_obj is None:
            allowlist_obj = json.loads(_read_capped(args.allowlist).decode("utf-8"))
    except Exception:
        report = run_audit(
            registry_bytes=registry_bytes if isinstance(registry_bytes, (bytes, bytearray)) else b"",
            allowlist={},
            allowlist_sha256="",
            sources={},
            base_commit=args.base_commit,
            clock_unix_ms=_int_or_zero(args.clock_unix_ms),
            seed=_int_or_zero(args.seed),
            meter=meter,
        )
        _emit(report)
        return _EXIT.get(report["status"], 4), report
    if args.resource_profile != "tiny-synthetic-v1":
        report = run_audit(
            registry_bytes=registry_bytes,
            allowlist=allowlist_obj if isinstance(allowlist_obj, dict) else {},
            allowlist_sha256=args.allowlist_sha256,
            sources={},
            base_commit=args.base_commit,
            clock_unix_ms=_int_or_zero(args.clock_unix_ms),
            seed=_int_or_zero(args.seed),
            profile={"profile_id": "not-a-pinned-profile"},
            meter=meter,
        )
        _emit(report)
        return _EXIT.get(report["status"], 4), report
    report = run_audit(
        registry_bytes=registry_bytes,
        allowlist=allowlist_obj if isinstance(allowlist_obj, dict) else {},
        allowlist_sha256=args.allowlist_sha256,
        sources=sources if isinstance(sources, dict) else {},
        base_commit=args.base_commit,
        clock_unix_ms=_int_or_zero(args.clock_unix_ms),
        seed=_int_or_zero(args.seed),
        meter=meter,
    )
    _emit(report)
    return _EXIT.get(report["status"], 4), report


def _parse(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.error = _reject
    parser.add_argument("--registry")
    parser.add_argument("--allowlist")
    parser.add_argument("--allowlist-sha256")
    parser.add_argument("--base-commit")
    parser.add_argument("--clock-unix-ms")
    parser.add_argument("--seed")
    parser.add_argument("--resource-profile")
    sink = io.StringIO()
    previous = sys.stderr
    sys.stderr = sink
    try:
        try:
            args = parser.parse_args(list(argv))
        except (AuditStop, SystemExit, argparse.ArgumentError):
            raise AuditStop("CLI_ARGS") from None
    finally:
        sys.stderr = previous
        sink.close()
    required = (
        args.registry,
        args.allowlist,
        args.allowlist_sha256,
        args.base_commit,
        args.clock_unix_ms,
        args.seed,
        args.resource_profile,
    )
    if any(item is None or item == "" for item in required):
        raise AuditStop("CLI_ARGS")
    return args


def _reject(message):
    del message
    raise AuditStop("CLI_ARGS")


def _read_capped(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            data = handle.read(FILE_READ_CAP + 1)
    except Exception:
        raise AuditStop("REGISTRY_INVALID") from None
    if len(data) > FILE_READ_CAP:
        raise AuditStop("REGISTRY_INVALID")
    return data


def _int_or_zero(text: str) -> int:
    if not isinstance(text, str) or not text.isdigit():
        return -1
    try:
        return int(text)
    except Exception:
        return -1


def _emit(report: dict) -> None:
    sys.stdout.write(canonical_bytes(report).decode("ascii"))


def emitted_bytes(report: dict) -> bytes:
    return canonical_bytes(report)
