"""Fixture policy definitions and deterministic fixture signals.

Ordered-pair semantics are fixture choices, not approved real policies.
"""

from __future__ import annotations

from research.causal_grammar_v1.canonical import ordered_id, single_id, unordered_id
from research.causal_grammar_v1.contracts_v1 import (
    POLICY_KEYS,
    SIGNAL_SCHEMA,
    STRATEGY_SCHEMA,
    ContractError,
)


def build_definition(kind: str, members: list[str], strategy_id: str, spec: dict) -> dict:
    policy = {key: None for key in POLICY_KEYS}
    ordered = kind == "ordered_pair"
    if kind == "single":
        policy["signal_rule"] = "sign_of_value"
        policy["zero_is_unsuccessful_marginal"] = True
        policy["event_consumption"] = "consume_one"
    elif kind in {"unordered_pair", "unordered_triple"}:
        unordered = spec["unordered_policy"]
        policy["window_ms"] = unordered["window_ms"]
        policy["signal_rule"] = "conjunction"
        policy["retain_pure_interactions"] = True
        policy["require_all_members"] = True
        policy["marginal_success_irrelevant"] = True
        policy["event_consumption"] = "require_all_members"
    elif kind == "ordered_pair":
        policy.update(spec["ordered_policy"])
        policy["signal_rule"] = "ordered_availability"
    else:
        raise ContractError("UNSUPPORTED_SPEC")
    if set(policy) != set(POLICY_KEYS):
        raise ContractError("INCOMPLETE_POLICY")
    return {
        "schema": STRATEGY_SCHEMA,
        "strategy_id": strategy_id,
        "kind": kind,
        "members": list(members),
        "ordered": ordered,
        "policy": policy,
    }


def strategy_id_for(kind: str, members: list[str]) -> str:
    if kind == "single":
        return single_id(members[0])
    if kind == "ordered_pair":
        return ordered_id(members[0], members[1])
    return unordered_id(members)


def record_sort_key(record: dict) -> tuple:
    return (
        record["availability_ms"],
        record["receive_monotonic_ns"],
        record["linkage"],
    )


def duplicate_seen(seen: set[str], linkage: str) -> bool:
    if linkage in seen:
        return True
    seen.add(linkage)
    return False


def classify_delay(delay: int, policy: dict) -> str:
    if delay < 0:
        raise ContractError("INVALID_ORDER")
    if delay == 0:
        return "tie"
    lag = policy["lag_ms"]
    expiry = policy["expiry_ms"]
    if delay < lag or (delay == lag and not policy["lag_inclusive"]):
        return "too_early"
    if delay > expiry or (delay == expiry and not policy["expiry_inclusive"]):
        return "expire"
    return "fire"


def consume_pending(pending: dict | None, kind: str) -> dict | None:
    if kind in {"fire", "expire"}:
        return None
    return pending


def _blank_evidence() -> dict:
    return {
        "linkage": None,
        "antecedent_linkage": None,
        "consequent_linkage": None,
        "delay_ms": None,
        "linkages": [],
    }


def _signal(
    strategy_id: str,
    decision_ms: int,
    code: str,
    *,
    pure_interaction: bool | None,
    marginal_outcome: str | None,
    evidence: dict,
) -> dict:
    return {
        "schema": SIGNAL_SCHEMA,
        "strategy_id": strategy_id,
        "decision_ms": decision_ms,
        "signal": code,
        "pure_interaction": pure_interaction,
        "marginal_outcome": marginal_outcome,
        "evidence": evidence,
    }


def _sign_signal(defn: dict, record: dict) -> dict:
    value = record["value"]
    if value > 0:
        code, outcome = "LONG", "successful"
    elif value < 0:
        code, outcome = "SHORT", "successful"
    elif value == 0:
        code, outcome = "FLAT", "unsuccessful"
    else:
        raise ContractError("NONFINITE:value")
    evidence = _blank_evidence()
    evidence["linkage"] = record["linkage"]
    return _signal(
        defn["strategy_id"],
        record["availability_ms"],
        code,
        pure_interaction=None,
        marginal_outcome=outcome,
        evidence=evidence,
    )


def evaluate_single(defn: dict, records: list[dict]) -> list[dict]:
    series_id = defn["members"][0]
    seen: set[str] = set()
    signals: list[dict] = []
    for record in sorted(records, key=record_sort_key):
        if record["series_id"] != series_id:
            continue
        if duplicate_seen(seen, record["linkage"]):
            continue
        signals.append(_sign_signal(defn, record))
    return signals


def evaluate_unordered(defn: dict, records: list[dict]) -> list[dict]:
    earliest: dict[str, dict] = {}
    for record in sorted(records, key=record_sort_key):
        series_id = record["series_id"]
        if series_id in defn["members"] and series_id not in earliest:
            earliest[series_id] = record
    if any(member not in earliest for member in defn["members"]):
        return []
    chosen = [earliest[member] for member in defn["members"]]
    times = [record["availability_ms"] for record in chosen]
    window_ms = defn["policy"]["window_ms"]
    if window_ms is None or max(times) - min(times) > window_ms:
        return []
    evidence = _blank_evidence()
    evidence["linkages"] = [record["linkage"] for record in chosen]
    return [
        _signal(
            defn["strategy_id"],
            max(times),
            "CONJUNCTION",
            pure_interaction=all(record["value"] == 0 for record in chosen),
            marginal_outcome=None,
            evidence=evidence,
        )
    ]


def evaluate_ordered(defn: dict, records: list[dict]) -> list[dict]:
    policy = defn["policy"]
    if policy["order_clock"] != "availability_ms":
        raise ContractError("UNSUPPORTED_SPEC")
    if policy["ties"] != "simultaneous_does_not_fire":
        raise ContractError("UNSUPPORTED_SPEC")
    antecedent = defn["members"][0]
    consequent = defn["members"][1]
    pending = None
    seen: set[str] = set()
    signals: list[dict] = []
    for record in sorted(records, key=record_sort_key):
        if duplicate_seen(seen, record["linkage"]):
            continue
        series_id = record["series_id"]
        if series_id == antecedent:
            if pending is None:
                pending = record
            continue
        if series_id != consequent or pending is None:
            continue
        delay = record["availability_ms"] - pending["availability_ms"]
        kind = classify_delay(delay, policy)
        if kind == "fire":
            evidence = _blank_evidence()
            evidence["antecedent_linkage"] = pending["linkage"]
            evidence["consequent_linkage"] = record["linkage"]
            evidence["delay_ms"] = delay
            signals.append(
                _signal(
                    defn["strategy_id"],
                    record["availability_ms"],
                    "ORDERED_FIRE",
                    pure_interaction=None,
                    marginal_outcome=None,
                    evidence=evidence,
                )
            )
        pending = consume_pending(pending, kind)
    return signals


def evaluate_signals(definitions: list[dict], records: list[dict]) -> list[dict]:
    usable = [
        record
        for record in records
        if record.get("usable") is True and record.get("availability") == "AVAILABLE"
    ]
    signals: list[dict] = []
    for definition in definitions:
        kind = definition["kind"]
        if kind == "single":
            signals.extend(evaluate_single(definition, usable))
        elif kind in {"unordered_pair", "unordered_triple"}:
            signals.extend(evaluate_unordered(definition, usable))
        elif kind == "ordered_pair":
            signals.extend(evaluate_ordered(definition, usable))
        else:
            raise ContractError("UNSUPPORTED_SPEC")
    signals.sort(
        key=lambda signal: (
            signal["decision_ms"],
            signal["strategy_id"],
            signal["evidence"]["linkage"] or "",
            signal["evidence"]["antecedent_linkage"] or "",
            tuple(signal["evidence"]["linkages"]),
        )
    )
    return signals
