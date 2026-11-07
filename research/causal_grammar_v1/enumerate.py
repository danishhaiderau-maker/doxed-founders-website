"""Compile exactly the explicit fixture families. Never prunes."""

from __future__ import annotations

from research.causal_grammar_v1.canonical import canonicalize_members
from research.causal_grammar_v1.contracts_v1 import (
    COMPATIBILITY_LABEL,
    DOMAIN,
    FAMILY_KEYS,
    ORDERED_POLICY,
    SPEC_SCHEMA,
    ContractError,
    producer_refs_match,
)
from research.causal_grammar_v1.grammar import build_definition, strategy_id_for
from research.causal_grammar_v1.guards import assert_definition_count

REQUIRED_BOUNDS = {
    "max_cpu_workers": 2,
    "peak_rss_bytes": 536870912,
    "per_command_seconds": 120,
    "total_suite_seconds": 600,
    "receipt_bytes": 8388608,
    "max_events": 2048,
    "max_definitions": 256,
    "real_temporal_enumeration": "NOT_IMPLEMENTED",
}

REQUIRED_UNORDERED = {
    "window_ms": 5000,
    "require_all_members": True,
    "retain_pure_interactions": True,
    "marginal_success_irrelevant": True,
}

REQUIRED_SINGLE = {
    "positive_signal_gt": 0,
    "negative_signal_lt": 0,
    "zero_is_unsuccessful_marginal": True,
}

REQUIRED_FEATURE = {
    "name": "prior_mean",
    "unit": "fixture_unit",
    "lookback_observations": 2,
    "lookback_unit": "count_of_strictly_prior_eligible",
    "min_periods": 2,
    "series_boundary": "same_series_id_only",
    "interpolation": "prohibited",
    "forward_fill": "prohibited",
    "warmup": "unknown_if_insufficient",
    "cutoffs_ms": [1000, 1500, 2000],
}

SPEC_KEYS = frozenset(
    {
        "spec_schema",
        "fixture_only",
        "compatibility",
        "label",
        "spec_id",
        "domain",
        "bounds",
        "families",
        "ordered_policy",
        "unordered_policy",
        "single_policy",
        "feature",
        "unsupported_must_fail",
        "prune_by_marginal_performance",
        "producer_spec_references",
    }
)


def validate_spec(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise ContractError("UNSUPPORTED_SPEC")
    unknown = set(spec) - SPEC_KEYS
    if unknown:
        raise ContractError("UNKNOWN_FIELD:" + sorted(unknown)[0])
    for key in SPEC_KEYS:
        if key not in spec:
            raise ContractError(f"MISSING_FIELD:{key}")
    if spec["spec_schema"] != SPEC_SCHEMA:
        raise ContractError("UNKNOWN_VERSION")
    if spec["fixture_only"] is not True:
        raise ContractError("NOT_FIXTURE_ONLY")
    if spec["compatibility"] != "NOT_ESTABLISHED" or spec["label"] != COMPATIBILITY_LABEL:
        raise ContractError("COMPATIBILITY_NOT_ESTABLISHED")
    if spec["domain"] != list(DOMAIN):
        raise ContractError("UNSUPPORTED_DOMAIN")
    if spec["bounds"] != REQUIRED_BOUNDS:
        raise ContractError("UNSUPPORTED_SPEC")
    if spec["ordered_policy"] != ORDERED_POLICY:
        raise ContractError("UNSUPPORTED_SPEC")
    if spec["unordered_policy"] != REQUIRED_UNORDERED:
        raise ContractError("UNSUPPORTED_SPEC")
    if spec["single_policy"] != REQUIRED_SINGLE:
        raise ContractError("UNSUPPORTED_SPEC")
    if spec["feature"] != REQUIRED_FEATURE:
        raise ContractError("UNSUPPORTED_SPEC")
    if spec["unsupported_must_fail"] is not True or spec["prune_by_marginal_performance"] is not False:
        raise ContractError("UNSUPPORTED_SPEC")
    if not isinstance(spec["spec_id"], str) or not spec["spec_id"]:
        raise ContractError("UNSUPPORTED_SPEC")
    producer_refs_match(spec["producer_spec_references"])
    families = spec["families"]
    if not isinstance(families, dict):
        raise ContractError("UNSUPPORTED_SPEC")
    family_unknown = set(families) - FAMILY_KEYS
    if family_unknown:
        raise ContractError("UNKNOWN_FIELD:" + sorted(family_unknown)[0])
    for key in FAMILY_KEYS:
        if key not in families or not isinstance(families[key], list):
            raise ContractError("UNSUPPORTED_SPEC")


def _check_member(member: object) -> str:
    if not isinstance(member, str) or member not in DOMAIN:
        raise ContractError("UNSUPPORTED_DOMAIN")
    return member


def enumerate_fixture(spec: dict) -> list[dict]:
    validate_spec(spec)
    families = spec["families"]
    entry_count = (
        len(families["singles"])
        + len(families["unordered_pairs"])
        + len(families["unordered_triples"])
        + len(families["ordered_pairs"])
    )
    assert_definition_count(entry_count)
    definitions: list[dict] = []
    seen: set[str] = set()

    def emit(kind: str, members: list[str]) -> None:
        canonical_members = canonicalize_members(kind, members)
        strategy_id = strategy_id_for(kind, canonical_members)
        if strategy_id in seen:
            raise ContractError("DUPLICATE_STRATEGY")
        seen.add(strategy_id)
        definitions.append(build_definition(kind, canonical_members, strategy_id, spec))

    for member in families["singles"]:
        emit("single", [_check_member(member)])
    for pair in families["unordered_pairs"]:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ContractError("UNSUPPORTED_SPEC")
        emit("unordered_pair", [_check_member(member) for member in pair])
    for triple in families["unordered_triples"]:
        if not isinstance(triple, list) or len(triple) != 3:
            raise ContractError("UNSUPPORTED_SPEC")
        emit("unordered_triple", [_check_member(member) for member in triple])
    for pair in families["ordered_pairs"]:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ContractError("UNSUPPORTED_SPEC")
        emit("ordered_pair", [_check_member(member) for member in pair])
    return definitions
