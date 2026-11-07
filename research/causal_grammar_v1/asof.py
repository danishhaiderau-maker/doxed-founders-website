"""Causal eligibility. Source time alone is never sufficient."""

from __future__ import annotations

from research.causal_grammar_v1.contracts_v1 import is_int


def causal_times_ok(record: dict, cutoff_ms: int) -> bool:
    timestamps = record["timestamps"]
    for key in ("source_ms", "publication_ms", "bar_close_ms", "receive_ms"):
        value = timestamps[key]
        if value is None or not is_int(value) or value > cutoff_ms:
            return False
    return True


def book_covers(record: dict, cutoff_ms: int) -> bool:
    validity = record["validity"]
    start = validity["book_valid_from_ms"]
    end = validity["book_valid_to_ms"]
    if not is_int(start) or cutoff_ms < start:
        return False
    if end is not None and cutoff_ms >= end:
        return False
    return True


def is_eligible(record: dict, cutoff_ms: int) -> bool:
    if record.get("availability") != "AVAILABLE" or record.get("usable") is not True:
        return False
    if not causal_times_ok(record, cutoff_ms):
        return False
    if not book_covers(record, cutoff_ms):
        return False
    return True


def vintage_key(record: dict) -> tuple:
    return (
        record["timestamps"]["publication_ms"],
        record["receive_monotonic_ns"],
    )


def visible_at(records: list[dict], cutoff_ms: int) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for record in records:
        if not is_eligible(record, cutoff_ms):
            continue
        root = record["revision_of"] or record["linkage"]
        groups.setdefault(root, []).append(record)
    chosen: list[dict] = []
    for group in groups.values():
        group.sort(key=vintage_key)
        chosen.append(group[-1])
    chosen.sort(
        key=lambda record: (
            record["availability_ms"],
            record["receive_monotonic_ns"],
            record["linkage"],
        )
    )
    return chosen


def asof_latest(records: list[dict], series_id: str, cutoff_ms: int) -> dict | None:
    visible = [record for record in visible_at(records, cutoff_ms) if record["series_id"] == series_id]
    if not visible:
        return None
    return visible[-1]
