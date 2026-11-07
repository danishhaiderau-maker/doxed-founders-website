"""Fixture-only entrypoint. There is no real-input mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gates import evaluate_fixture, profile_sha256
from .guards import (
    BOUNDS,
    CanaryRejected,
    CapStop,
    DriftStop,
    MonotonicClock,
    ProhibitedAccess,
    commit_receipt,
    enforce_elapsed,
    fixture_path,
    jail_fixture,
    load_lock,
    lock_path,
    pin_files,
    reject_access,
    reject_canaries,
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
        _emit_error("real_input_rejected")
        return 2
    try:
        reject_access(args.access)
        path = jail_fixture(Path(args.fixture) if args.fixture else fixture_path())
        raw = path.read_bytes()
        reject_canaries(raw)
        if path == fixture_path():
            lock = load_lock()
            if sha256_bytes(raw) != lock["fixture_sha256"]:
                raise DriftStop("fixture hash")
        document = json.loads(raw.decode("utf-8"), parse_float=_reject_float, parse_constant=_reject_float)
        clock = MonotonicClock()
        result = evaluate_fixture(document, workers=1, clock=clock)
        receipt = build_receipt(result, clock=clock)
        body = canonical_bytes(receipt)
        if args.receipt:
            commit_receipt(Path(args.receipt), body)
        sys.stdout.write(body.decode("utf-8"))
        return 0
    except CanaryRejected:
        _emit_error("canary_detected")
        return 3
    except ProhibitedAccess:
        _emit_error("prohibited_access")
        return 3
    except CapStop as exc:
        _emit_error(exc.reason)
        return 5
    except (SchemaError, ProfileRejected, DriftStop, json.JSONDecodeError, UnicodeError):
        _emit_error("schema")
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
            "command_elapsed_ms": elapsed_ms,
            "rss_kib": plain["artifacts"]["rss_kib"],
        },
        "unknowns": list(UNKNOWNS),
        "evaluation_status_constant": EVALUATION_STATUS,
    }


def _reject_float(_value):
    raise SchemaError("float rejected")


def _emit_error(code: str) -> None:
    sys.stdout.write(json.dumps({"ok": False, "error": code, "status": "REJECTED"}, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
