"""Fixture-only entrypoint. There is no real-input mode.

The parent process is the only receipt writer. It admits the canonical fixture
path before any open, then forks one child. The child performs the single
bounded fixture read. Two workers inclusive: parent plus that child.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gates import evaluate_fixture, profile_sha256
from .guards import (
    BOUNDS,
    COMMAND_SECONDS,
    PER_WORKER_VAS_BYTES,
    RSS_MIB,
    CanaryRejected,
    CapStop,
    DriftStop,
    MonotonicClock,
    ProhibitedAccess,
    admit_canonical_path,
    commit_receipt,
    enforce_elapsed,
    fixture_path,
    lock_path,
    pin_files,
    read_canonical_verified,
    reject_access,
    reject_canaries,
    run_contained,
)
from .validate import (
    EVALUATION_STATUS,
    UNKNOWNS,
    ProfileRejected,
    SchemaError,
    canonical_bytes,
    schema_manifest,
    sha256_bytes,
    sha256_obj,
    to_plain,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fixture_cli")
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--real-input", action="store_true")
    parser.add_argument("--fixture", default=None)
    parser.add_argument("--receipt", default=None)
    parser.add_argument("--access", default="none")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    if not args.fixture_only or args.real_input:
        _emit_error("real_input_rejected", status="REJECTED")
        return 2
    try:
        reject_access(args.access)
        requested = Path(args.fixture) if args.fixture else fixture_path()
        admit_canonical_path(requested)
    except ProhibitedAccess:
        _emit_error("prohibited_access", status="REJECTED")
        return 3
    try:
        code, body = run_contained(_worker_main)
    except CapStop as exc:
        _emit_error(exc.reason, status="INCOMPLETE_STOP")
        return 5
    except ProhibitedAccess:
        _emit_error("prohibited_access", status="REJECTED")
        return 3
    text = body.decode("utf-8")
    if code == 5 or '"status": "INCOMPLETE_STOP"' in text or '"status":"INCOMPLETE_STOP"' in text:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        return 5
    if code != 0:
        sys.stdout.write(text if text else _rejected("schema"))
        return code if code else 4
    if args.receipt:
        commit_receipt(Path(args.receipt), body)
    sys.stdout.write(text)
    return 0


def _worker_main(write_fd: int) -> int:
    from .guards import _write_all

    try:
        raw = read_canonical_verified()
        reject_canaries(raw)
        document = json.loads(raw.decode("utf-8"), parse_float=_reject_float, parse_constant=_reject_float)
        clock = MonotonicClock()
        result = evaluate_fixture(document, workers=1, clock=clock)
        receipt = build_receipt(result, clock=clock)
        _write_all(write_fd, canonical_bytes(receipt))
        return 0
    except CanaryRejected:
        _write_all(write_fd, _rejected("canary_detected").encode("utf-8"))
        return 3
    except ProhibitedAccess:
        _write_all(write_fd, _rejected("prohibited_access").encode("utf-8"))
        return 3
    except CapStop as exc:
        _write_all(write_fd, _cap(exc).encode("utf-8"))
        return 5
    except (SchemaError, ProfileRejected, DriftStop, json.JSONDecodeError, UnicodeError):
        _write_all(write_fd, _rejected("schema").encode("utf-8"))
        return 4


def build_receipt(result, *, clock: MonotonicClock) -> dict:
    elapsed_ms = int(clock.elapsed() * 1000)
    enforce_elapsed(elapsed_ms / 1000, kind="command")
    plain = to_plain(result)
    return {
        "receipt_schema": "results_gates_v1_fixture_receipt",
        "receipt_version": "1",
        "fixture_only": True,
        "ok": True,
        "evaluation_status": plain["evaluation_status"],
        "research_done": False,
        "mission_done": False,
        "live": "OFF",
        "compatibility": plain["producer_compatibility"],
        "results": plain,
        "pins": {
            "files": pin_files(),
            "entrypoint": "research/results_gates_v1/fixture_cli.py",
            "entrypoint_args": ["--fixture-only"],
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "profile_sha256": profile_sha256(),
            "schema_manifest_sha256": sha256_obj(schema_manifest()),
            "lock_sha256": sha256_bytes(lock_path().read_bytes()),
            "card_sha256": "c0b239a5b6f8b076d1d4e36535c4a4055d21d9838da18a7cafcb5b1059f3b81b",
            "card_bytes": 9566,
            "design_spec_sha256": {
                "stage_a": "ecb42d094510f7dd507b4ebc5fb2797a2614951614745d1fcfbaa79e44bfb122",
                "stage_b": "025082ee2c98c9d8e8637e6ea1d23f446546b2fc935ecf8119533ccbef2e9f22",
            },
            "design_specs_are_not_implementation_pins": True,
        },
        "resources": {
            "bounds": dict(BOUNDS),
            "enforced": True,
            "cap_hit": False,
            "workers_used": 1,
            "inclusive_workers": 2,
            "topology": "parent_writer_plus_one_child",
            "command_elapsed_ms": elapsed_ms,
            "command_cap_seconds": COMMAND_SECONDS,
            "rss_kib": plain["artifacts"]["rss_kib"],
            "per_worker_vas_ceiling_bytes": PER_WORKER_VAS_BYTES,
            "vas_sum_ceiling_bytes": RSS_MIB * 1024 * 1024,
            "vas_is_rss": False,
            "startup_overshoot": "limit_after_interpreter_start_no_zero_overshoot_claim",
            "receipt_transport": "parent_bounded_pipe",
        },
        "unknowns": list(UNKNOWNS),
        "evaluation_status_constant": EVALUATION_STATUS,
    }


def _reject_float(_value):
    raise SchemaError("float rejected")


def _rejected(code: str) -> str:
    return json.dumps({"error": code, "ok": False, "status": "REJECTED"}, sort_keys=True) + "\n"


def _cap(exc: CapStop) -> str:
    return json.dumps(
        {"error": exc.reason, "ok": False, "status": "INCOMPLETE_STOP"},
        sort_keys=True,
    ) + "\n"


def _emit_error(code: str, *, status: str) -> None:
    sys.stdout.write(json.dumps({"error": code, "ok": False, "status": status}, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
