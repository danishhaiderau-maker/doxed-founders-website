"""Callable fixture-only entrypoint.

Accepts a manufactured fixture whose bytes match the lock, and one explicit
receipt path. There is no real-input mode. The process does not read
environment variables, vaults, or market-data roots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from .engine import replay
from .guards import DEFAULT_LIMITS, CapStop, ResourceObservation, enforce_observation

PACKAGE = Path(__file__).resolve().parent
LOCK_PATH = PACKAGE / "dependencies.lock.json"
FIXTURE_KEY = "research/event_replay_v1/fixtures/lifecycle.json"


def _load_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _fail(code: str) -> int:
    print(code, file=sys.stderr)
    return 3


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--real-input" in argv or "--live" in argv or "--network" in argv:
        return _fail("REAL_INPUT_MODE_REJECTED")
    if "--fixture-only" not in argv:
        return _fail("FIXTURE_ONLY_REQUIRED")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--workers", type=int, default=1)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return _fail("FIXTURE_ONLY_ARGS")
    receipt = Path(args.receipt)
    resolved = str(receipt.resolve())
    if resolved.startswith("/vault") or resolved.startswith("/etc"):
        return _fail("PROHIBITED_REAL_ROOT")
    started = time.monotonic()
    try:
        raw = Path(args.fixture).read_bytes()
    except OSError:
        return _fail("FIXTURE_UNREADABLE")
    lock = _load_lock()
    pinned = lock["files"][FIXTURE_KEY]["sha256"]
    if hashlib.sha256(raw).hexdigest() != pinned:
        return _fail("FIXTURE_PIN_MISMATCH")
    try:
        fixture = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return _fail("FIXTURE_JSON_INVALID")
    try:
        result = replay(fixture, workers=args.workers, limits=DEFAULT_LIMITS)
    except CapStop as exc:
        print(str(exc), file=sys.stderr)
        return 2
    elapsed = time.monotonic() - started
    receipt_body = {
        "kind": "event_replay_v1.synthetic_receipt",
        "compatibility": "BLOCKED",
        "fixture_only": "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED",
        "authoritative_base": lock["authoritative_base"],
        "head": None,
        "head_status": "RECORDED_OUTSIDE_RUNTIME_AFTER_COMMIT",
        "live": "OFF",
        "research_complete": False,
        "profitability_claim": False,
        "findings_claim": False,
        "r57_actual_sweep": False,
        "realistic_v1": None,
        "result": result,
        "resource": result["resource"],
    }
    blob = json.dumps(receipt_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        enforce_observation(
            ResourceObservation(
                workers=args.workers,
                rss_bytes=result["resource"]["rss_bytes"],
                command_seconds=elapsed,
                suite_seconds=elapsed,
                receipt_bytes=len(blob),
                events=result["resource"]["events"],
            )
        )
    except CapStop as exc:
        print(str(exc), file=sys.stderr)
        return 2
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_bytes(blob)
    print(
        json.dumps(
            {
                "status": "OK",
                "compatibility": "BLOCKED",
                "receipt_bytes": len(blob),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
