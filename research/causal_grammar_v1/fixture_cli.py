"""Fixture-only entrypoint. There is no real-input mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research.causal_grammar_v1.canonical import canonical_bytes
from research.causal_grammar_v1.contracts_v1 import (
    AUTHORIZED_BASE,
    COVERAGE_SCHEMA,
    ENGINEERING_CLEAR_ID,
    LOCK_SCHEMA,
    POLICY_KEYS,
    PROPOSAL_BYTES,
    PROPOSAL_SHA256,
    RECEIPT_SCHEMA,
    TEMPORAL_REAL_N,
    TEMPORAL_REAL_TESTED,
    CapExhausted,
    ContractError,
    ProhibitedAccess,
    blocked_compatibility,
)
from research.causal_grammar_v1.enumerate import enumerate_fixture
from research.causal_grammar_v1.features import features_at
from research.causal_grammar_v1.grammar import evaluate_signals
from research.causal_grammar_v1.guards import (
    CommandClock,
    assert_receipt_size,
    assert_rss,
    assert_workers,
    audit_sources,
    guarded_read_text,
    guarded_write_text,
    load_package_sources,
    peak_rss_bytes,
    pin_substantive_bytes,
    scan_obj,
)
from research.causal_grammar_v1.normalized_adapter import normalize_manifest

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]
FIXTURES = PACKAGE_ROOT / "fixtures"
TEST_FILES = {"tests/test_acceptance.py"}


def assert_pin_head(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ContractError("PIN_HEAD_MALFORMED")
    return value


def load_json_file(path: Path) -> dict:
    try:
        payload = json.loads(guarded_read_text(path, [FIXTURES]))
    except json.JSONDecodeError:
        raise ContractError("MALFORMED_JSON") from None
    if not isinstance(payload, dict):
        raise ContractError("MALFORMED_JSON")
    return payload


def run_fixture(
    spec: dict,
    manifest: dict,
    *,
    pin_base: str,
    pin_head: str,
    workers: int,
    command: list[str],
    clock: CommandClock,
) -> dict:
    assert_workers(workers)
    if pin_base != AUTHORIZED_BASE:
        raise ContractError("PIN_BASE_MISMATCH")
    assert_pin_head(pin_head)
    sources = load_package_sources(PACKAGE_ROOT)
    imports = audit_sources(sources, test_names=TEST_FILES)
    definitions = enumerate_fixture(spec)
    normalized = normalize_manifest(manifest)
    feature_rows = features_at(normalized["records"], spec["feature"])
    signals = evaluate_signals(definitions, normalized["records"])
    clock.assert_open()
    substantive = pin_substantive_bytes(PACKAGE_ROOT)
    compatibility = blocked_compatibility(spec["producer_spec_references"])
    complete = all(set(POLICY_KEYS) <= set(definition["policy"]) for definition in definitions)
    receipt = {
        "receipt_schema": RECEIPT_SCHEMA,
        "engineering_clear_id": ENGINEERING_CLEAR_ID,
        "proposal_sha256": PROPOSAL_SHA256,
        "proposal_bytes": PROPOSAL_BYTES,
        "entrypoint": "research/causal_grammar_v1/fixture_cli.py",
        "command": command,
        "pin_base": pin_base,
        "pin_head": pin_head,
        "pin_head_source": "cli_argument",
        "runtime": {
            "python": sys.version.split()[0],
            "implementation": sys.implementation.name,
        },
        "imports": imports,
        "dependencies_lock_sha256": substantive["dependencies.lock.json"],
        "lock_schema": LOCK_SCHEMA,
        "schema_sha256": substantive["contracts-v1.schema.json"],
        "fixtures_sha256": {
            "fixtures/tiny-spec.json": substantive["fixtures/tiny-spec.json"],
            "fixtures/committed-events.json": substantive["fixtures/committed-events.json"],
        },
        "guard_sha256": substantive["guards.py"],
        "launcher_sha256": substantive["fixture_cli.py"],
        "substantive_bytes_sha256": substantive,
        "compatibility": compatibility,
        "coverage": {
            "schema": COVERAGE_SCHEMA,
            "strategy_count": len(definitions),
            "strategy_ids": [definition["strategy_id"] for definition in definitions],
            "each_has_complete_policy": complete,
            "synthetic_strategy_count": len(definitions),
            "research_n": None,
            "dates": None,
            "max_hold": None,
            "cost_threshold": None,
            "temporal_real_n": TEMPORAL_REAL_N,
            "temporal_real_tested": TEMPORAL_REAL_TESTED,
            "outside_posted_n": True,
            "accepted_findings": "NONE",
            "profitability": "NO_EVIDENCE",
            "mission": "NOT_DONE",
        },
        "definitions": definitions,
        "normalized_record_count": len(normalized["records"]),
        "rejections": normalized["rejections"],
        "features": feature_rows,
        "signals": signals,
        "resources": {
            "cpu_workers": workers,
            "peak_rss_bytes": peak_rss_bytes(),
            "elapsed_seconds": clock.elapsed(),
            "event_count": len(manifest["segment"]["members"]),
            "definition_count": len(definitions),
            "receipt_bytes": 0,
            "cap_status": "WITHIN_CAPS",
        },
    }
    for _ in range(4):
        payload = canonical_bytes(receipt)
        size = len(payload)
        if receipt["resources"]["receipt_bytes"] == size:
            break
        receipt["resources"]["receipt_bytes"] = size
    else:
        raise ContractError("RECEIPT_SIZE_UNSTABLE")
    assert_receipt_size(len(canonical_bytes(receipt)))
    assert_rss(receipt["resources"]["peak_rss_bytes"])
    scan_obj(receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fixture_cli")
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--pin-base", required=True)
    parser.add_argument("--pin-head", required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    if not args.fixture_only:
        print("FIXTURE_ONLY_REQUIRED", file=sys.stderr)
        return 2
    clock = CommandClock()
    command = [
        "fixture_cli.py",
        "--fixture-only",
        "--spec",
        "fixtures/tiny-spec.json",
        "--events",
        "fixtures/committed-events.json",
        "--pin-base",
        args.pin_base,
        "--pin-head",
        args.pin_head,
        "--workers",
        str(args.workers),
    ]
    try:
        spec_path = Path(args.spec)
        events_path = Path(args.events)
        if spec_path.name != "tiny-spec.json" or events_path.name != "committed-events.json":
            raise ContractError("UNPINNED_FIXTURE")
        spec = load_json_file(spec_path)
        manifest = load_json_file(events_path)
        receipt = run_fixture(
            spec,
            manifest,
            pin_base=args.pin_base,
            pin_head=args.pin_head,
            workers=args.workers,
            command=command,
            clock=clock,
        )
        guarded_write_text(Path(args.receipt), canonical_bytes(receipt).decode("utf-8"), REPO_ROOT)
    except CapExhausted as exc:
        print(exc.code, file=sys.stderr)
        return 4
    except (ProhibitedAccess, ContractError) as exc:
        print(exc.code, file=sys.stderr)
        return 3
    print(
        f"FIXTURE_ONLY status=OK strategies={receipt['coverage']['strategy_count']} compatibility=BLOCKED"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
