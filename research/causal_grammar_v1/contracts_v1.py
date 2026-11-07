"""A's fixture-only contracts. Not a producer schema.

CoverageReport, ReplayTrace, ResearchResults, and the collector raw-event
schema stay owned by their producers. Pins below are spec references only.
Compatibility is BLOCKED until approved implementation bytes are pinned.
"""

from __future__ import annotations

from typing import Any

ENGINEERING_CLEAR_ID = "20261107-1945-STAGE-A"
AUTHORIZED_BASE = "40087a65f2157f8c6701ea3670adc5c29a28c87d"
PROPOSAL_SHA256 = "ecb42d094510f7dd507b4ebc5fb2797a2614951614745d1fcfbaa79e44bfb122"
PROPOSAL_BYTES = 9060

NORMALIZED_SCHEMA = "causal_grammar.normalized_record.v1"
FEATURE_SCHEMA = "causal_grammar.feature.v1"
STRATEGY_SCHEMA = "causal_grammar.strategy_definition.v1"
SIGNAL_SCHEMA = "causal_grammar.fixture_signal.v1"
SPEC_SCHEMA = "causal_grammar.fixture_spec.v1"
MANIFEST_SCHEMA = "causal_grammar.manufactured_input.v1"
RECEIPT_SCHEMA = "causal_grammar.receipt.v1"
MUTATION_SCHEMA = "causal_grammar.mutation_kill.v1"
COVERAGE_SCHEMA = "causal_grammar.fixture_coverage.v1"
LOCK_SCHEMA = "causal_grammar.dependencies.lock.v1"

COMPATIBILITY_LABEL = "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED"
COMPATIBILITY_STATUS = "BLOCKED"
TEMPORAL_REAL_N = "NOT_IMPLEMENTED"
TEMPORAL_REAL_TESTED = "NOT_TESTED"

AUDITOR_COVERAGE_SPEC_REF = "3272c6425b59"
COLLECTOR_SPEC_REFS = ("1439354678ef", "7bee784c7e13")

DOMAIN = ("A", "B", "C")

MAX_CPU_WORKERS = 2
MAX_PEAK_RSS_BYTES = 512 * 1024 * 1024
MAX_COMMAND_SECONDS = 120
MAX_SUITE_SECONDS = 600
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 2048
MAX_DEFINITIONS = 256

FIXTURE_CLOCK_ORIGIN = "2026-01-01T00:00:00.000Z"

PAYLOAD_FIELDS = (
    "member_id",
    "record_index",
    "series_id",
    "source_timestamp_ms",
    "publication_time_ms",
    "bar_close_time_ms",
    "receive_time_ms",
    "receive_monotonic_ns",
    "connection_epoch",
    "release_status",
    "revision_of",
    "value",
    "unit",
    "book_valid_from_ms",
    "book_valid_to_ms",
)

MANIFEST_KEYS = frozenset(
    {
        "manifest_schema",
        "fixture_only",
        "compatibility",
        "label",
        "manifest_id",
        "producer_spec_references",
        "segment",
    }
)
SEGMENT_KEYS = frozenset(
    {
        "segment_id",
        "segment_bytes_b64",
        "segment_digest",
        "prefix_extent",
        "prefix_digest",
        "members",
    }
)
MEMBER_KEYS = frozenset(
    set(PAYLOAD_FIELDS)
    | {
        "committed",
        "linkage",
        "byte_start",
        "byte_end",
        "member_digest",
    }
)
PRODUCER_REF_KEYS = frozenset(
    {
        "auditor_coverage_report",
        "collector",
        "implementation_pins",
        "compatibility_status",
    }
)

NORMALIZED_REQUIRED = (
    "schema",
    "schema_version",
    "source",
    "series_id",
    "provenance",
    "linkage",
    "segment_id",
    "member_id",
    "record_index",
    "timestamps",
    "receive_monotonic_ns",
    "connection_epoch",
    "release_status",
    "revision_of",
    "availability",
    "availability_ms",
    "ordering",
    "validity",
    "value",
    "unit",
    "usable",
)

TIMESTAMP_KEYS = (
    "source_utc",
    "publication_utc",
    "bar_close_utc",
    "receive_utc",
    "source_ms",
    "publication_ms",
    "bar_close_ms",
    "receive_ms",
)

POLICY_KEYS = (
    "lag_ms",
    "lag_inclusive",
    "expiry_ms",
    "expiry_inclusive",
    "reset",
    "ties",
    "event_consumption",
    "retrigger",
    "duplicate_events",
    "order_clock",
    "window_ms",
    "signal_rule",
    "zero_is_unsuccessful_marginal",
    "retain_pure_interactions",
    "require_all_members",
    "marginal_success_irrelevant",
)

ORDERED_POLICY = {
    "lag_ms": 100,
    "lag_inclusive": True,
    "expiry_ms": 1000,
    "expiry_inclusive": False,
    "reset": "on_consequent_or_expiry",
    "ties": "simultaneous_does_not_fire",
    "event_consumption": "consume_antecedent_once",
    "retrigger": "after_reset_only",
    "duplicate_events": "ignore_exact_duplicate_linkage",
    "order_clock": "availability_ms",
}

FAMILY_KEYS = frozenset(
    {"singles", "unordered_pairs", "unordered_triples", "ordered_pairs"}
)

PACKAGE_FILES = (
    "__init__.py",
    "contracts_v1.py",
    "contracts-v1.schema.json",
    "normalized_adapter.py",
    "asof.py",
    "features.py",
    "grammar.py",
    "canonical.py",
    "enumerate.py",
    "fixture_cli.py",
    "guards.py",
    "dependencies.lock.json",
    "fixtures/tiny-spec.json",
    "fixtures/committed-events.json",
    "tests/test_acceptance.py",
    "README.md",
)

BANNED_IMPORTS = frozenset(
    {
        "socket",
        "urllib",
        "requests",
        "http",
        "ftplib",
        "smtplib",
        "subprocess",
        "ctypes",
        "pickle",
        "os",
    }
)

LEAK_PATTERNS = ("sk_live_", "AKIA", "BEGIN PRIVATE", "CANARY-SECRET-")


class ContractError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __str__(self) -> str:
        return self.code


class CapExhausted(ContractError):
    pass


class ProhibitedAccess(ContractError):
    pass


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or isinstance(value, str):
        return False
    if is_int(value):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    return False


def require_keys(obj: dict[str, Any], keys: frozenset[str] | tuple[str, ...], label: str) -> None:
    if not isinstance(obj, dict):
        raise ContractError(f"MISSING_FIELD:{label}")
    allowed = set(keys)
    for key in obj:
        if key not in allowed:
            raise ContractError(f"UNKNOWN_FIELD:{key}")
    for key in keys:
        if key not in obj:
            raise ContractError(f"MISSING_FIELD:{key}")


def producer_refs_match(refs: dict[str, Any]) -> None:
    require_keys(refs, PRODUCER_REF_KEYS, "producer_spec_references")
    if refs.get("auditor_coverage_report") != AUDITOR_COVERAGE_SPEC_REF:
        raise ContractError("COMPATIBILITY_NOT_ESTABLISHED")
    if list(refs.get("collector") or []) != list(COLLECTOR_SPEC_REFS):
        raise ContractError("COMPATIBILITY_NOT_ESTABLISHED")
    if refs.get("compatibility_status") != COMPATIBILITY_STATUS:
        raise ContractError("COMPATIBILITY_NOT_ESTABLISHED")


def blocked_compatibility(refs: dict[str, Any]) -> dict[str, Any]:
    pins = refs.get("implementation_pins")
    reason = (
        "APPROVED_IMPLEMENTATION_PINS_ABSENT"
        if pins is None
        else "APPROVED_IMPLEMENTATION_BYTES_ABSENT"
    )
    return {
        "label": COMPATIBILITY_LABEL,
        "compatibility": "NOT_ESTABLISHED",
        "compatibility_status": COMPATIBILITY_STATUS,
        "reason": reason,
        "auditor_coverage_report_spec_ref": AUDITOR_COVERAGE_SPEC_REF,
        "collector_spec_refs": list(COLLECTOR_SPEC_REFS),
        "implementation_pins": pins if pins is None else "PRESENT_UNAPPROVED",
        "fixture_stubs": COMPATIBILITY_LABEL,
    }
