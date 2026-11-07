"""Normalize manufactured envelopes into A's causal records.

Linkage of each member is checked on its own. A matching whole-segment
digest does not prove member linkage. Uncommitted tails stay unusable.
Nullable timestamps are preserved and never replaced.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from research.causal_grammar_v1.canonical import canonical_bytes, member_core, sha256_hex
from research.causal_grammar_v1.contracts_v1 import (
    COMPATIBILITY_LABEL,
    DOMAIN,
    FIXTURE_CLOCK_ORIGIN,
    MANIFEST_KEYS,
    MANIFEST_SCHEMA,
    MEMBER_KEYS,
    NORMALIZED_SCHEMA,
    SEGMENT_KEYS,
    ContractError,
    blocked_compatibility,
    is_finite_number,
    is_int,
    producer_refs_match,
    require_keys,
)
from research.causal_grammar_v1.guards import assert_event_count, scan_obj

_ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ms_to_utc(value_ms: int | None) -> str | None:
    if value_ms is None:
        return None
    if not is_int(value_ms) or value_ms < 0:
        raise ContractError("INVALID_TIMESTAMP")
    moment = _ORIGIN + timedelta(milliseconds=value_ms)
    millis = moment.microsecond // 1000
    rendered = moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}Z"
    if value_ms == 0 and rendered != FIXTURE_CLOCK_ORIGIN:
        raise ContractError("INVALID_TIMESTAMP")
    return rendered


def source_timestamp_ms(member: dict) -> int | None:
    return member["source_timestamp_ms"]


def assert_finite_value(value: object) -> None:
    if not is_finite_number(value):
        raise ContractError("NONFINITE:value")


def assert_member_digest(actual_hex: str, declared_hex: str) -> None:
    if actual_hex != declared_hex:
        raise ContractError("MEMBER_DIGEST_MISMATCH")


def assert_complete_slice(slice_bytes: bytes, payload: bytes) -> None:
    if len(slice_bytes) != len(payload):
        raise ContractError("TRUNCATED_MEMBER")
    if slice_bytes != payload:
        raise ContractError("MEMBER_PAYLOAD_MISMATCH")


def _nullable_time(value: object, field: str) -> int | None:
    if value is None:
        return None
    if not is_int(value) or value < 0:
        raise ContractError(f"INVALID_TIMESTAMP:{field}")
    return value


def _validate_member_values(member: dict) -> None:
    assert_finite_value(member["value"])
    if member["series_id"] not in DOMAIN:
        raise ContractError("UNSUPPORTED_DOMAIN")
    if member["unit"] != "fixture_unit":
        raise ContractError("UNSUPPORTED_SPEC")
    if not isinstance(member["member_id"], str) or not member["member_id"]:
        raise ContractError("MISSING_FIELD:member_id")
    if not isinstance(member["connection_epoch"], str) or not member["connection_epoch"]:
        raise ContractError("MISSING_FIELD:connection_epoch")
    if member["release_status"] not in {"first", "revision"}:
        raise ContractError("INVALID_RELEASE")
    if member["release_status"] == "first" and member["revision_of"] is not None:
        raise ContractError("INVALID_RELEASE")
    if member["release_status"] == "revision" and not isinstance(member["revision_of"], str):
        raise ContractError("INVALID_RELEASE")
    if not is_int(member["record_index"]) or member["record_index"] < 0:
        raise ContractError("RECORD_INDEX_SEQUENCE")
    if not is_int(member["receive_monotonic_ns"]) or member["receive_monotonic_ns"] < 0:
        raise ContractError("INVALID_TIMESTAMP:receive_monotonic_ns")
    for field in (
        "source_timestamp_ms",
        "publication_time_ms",
        "bar_close_time_ms",
        "receive_time_ms",
    ):
        _nullable_time(member[field], field)
    start = member["book_valid_from_ms"]
    end = member["book_valid_to_ms"]
    if not is_int(start) or start < 0:
        raise ContractError("INVALID_BOOK_INTERVAL")
    if end is not None and (not is_int(end) or end <= start):
        raise ContractError("INVALID_BOOK_INTERVAL")
    if not isinstance(member["linkage"], str):
        raise ContractError("LINKAGE_MISMATCH")
    if not isinstance(member["member_digest"], str):
        raise ContractError("MEMBER_DIGEST_MISMATCH")
    if not is_int(member["byte_start"]) or not is_int(member["byte_end"]):
        raise ContractError("TRUNCATED_MEMBER")


def _validate_envelope(manifest: dict) -> None:
    require_keys(manifest, MANIFEST_KEYS, "manifest")
    if manifest["manifest_schema"] != MANIFEST_SCHEMA:
        raise ContractError("UNKNOWN_VERSION")
    if manifest["fixture_only"] is not True:
        raise ContractError("NOT_FIXTURE_ONLY")
    if manifest["compatibility"] != "NOT_ESTABLISHED" or manifest["label"] != COMPATIBILITY_LABEL:
        raise ContractError("COMPATIBILITY_NOT_ESTABLISHED")
    if not isinstance(manifest["manifest_id"], str) or not manifest["manifest_id"]:
        raise ContractError("MISSING_FIELD:manifest_id")
    producer_refs_match(manifest["producer_spec_references"])


def _decode_segment(encoded: object) -> bytes:
    if not isinstance(encoded, str):
        raise ContractError("UNDECODABLE_SEGMENT")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error):
        raise ContractError("UNDECODABLE_SEGMENT") from None


def verify_committed_prefix(segment: dict, committed: list[dict]) -> list[dict]:
    raw = _decode_segment(segment["segment_bytes_b64"])
    if sha256_hex(raw) != segment["segment_digest"]:
        raise ContractError("SEGMENT_DIGEST_MISMATCH")
    extent = segment["prefix_extent"]
    if not is_int(extent) or extent < 0 or extent > len(raw):
        raise ContractError("TRUNCATED_PREFIX")
    prefix = raw[:extent]
    if sha256_hex(prefix) != segment["prefix_digest"]:
        raise ContractError("PREFIX_DIGEST_MISMATCH")
    if not isinstance(segment["segment_id"], str) or not segment["segment_id"]:
        raise ContractError("MISSING_FIELD:segment_id")
    ordered = sorted(committed, key=lambda member: member["record_index"])
    cursor = 0
    for expected_index, member in enumerate(ordered):
        if member["record_index"] != expected_index:
            raise ContractError("RECORD_INDEX_SEQUENCE")
        linkage = f"{segment['segment_id']}:{member['member_id']}:{member['record_index']}"
        if member["linkage"] != linkage:
            raise ContractError("LINKAGE_MISMATCH")
        start = member["byte_start"]
        end = member["byte_end"]
        if start != cursor or end <= start or end > extent:
            raise ContractError("TRUNCATED_MEMBER")
        payload = canonical_bytes(member_core(member))
        slice_bytes = raw[start:end]
        assert_complete_slice(slice_bytes, payload)
        assert_member_digest(sha256_hex(slice_bytes), member["member_digest"])
        cursor = end
    if cursor != extent:
        raise ContractError("PREFIX_COVERAGE")
    return ordered


def to_record(manifest: dict, segment: dict, member: dict) -> dict:
    source_ms = source_timestamp_ms(member)
    publication_ms = member["publication_time_ms"]
    bar_ms = member["bar_close_time_ms"]
    receive_ms = member["receive_time_ms"]
    times = (source_ms, publication_ms, bar_ms, receive_ms)
    if any(value is None for value in times):
        availability = "UNKNOWN"
        availability_ms = None
        usable = False
    else:
        availability = "AVAILABLE"
        availability_ms = max(times)
        usable = True
    event_before_receive = None
    if source_ms is not None and receive_ms is not None:
        event_before_receive = source_ms < receive_ms
    compatibility = blocked_compatibility(manifest["producer_spec_references"])
    return {
        "schema": NORMALIZED_SCHEMA,
        "schema_version": "v1",
        "source": "synthetic",
        "series_id": member["series_id"],
        "provenance": {
            "fixture_only": True,
            "label": compatibility["label"],
            "compatibility": compatibility["compatibility"],
            "compatibility_status": compatibility["compatibility_status"],
            "reason": compatibility["reason"],
            "manifest_id": manifest["manifest_id"],
            "producer_spec_references": {
                "auditor_coverage_report": compatibility["auditor_coverage_report_spec_ref"],
                "collector": list(compatibility["collector_spec_refs"]),
                "implementation_pins": compatibility["implementation_pins"],
            },
            "input_schema": MANIFEST_SCHEMA,
            "output_schema": NORMALIZED_SCHEMA,
        },
        "linkage": member["linkage"],
        "segment_id": segment["segment_id"],
        "member_id": member["member_id"],
        "record_index": member["record_index"],
        "timestamps": {
            "source_utc": ms_to_utc(source_ms),
            "publication_utc": ms_to_utc(publication_ms),
            "bar_close_utc": ms_to_utc(bar_ms),
            "receive_utc": ms_to_utc(receive_ms),
            "source_ms": source_ms,
            "publication_ms": publication_ms,
            "bar_close_ms": bar_ms,
            "receive_ms": receive_ms,
        },
        "receive_monotonic_ns": member["receive_monotonic_ns"],
        "connection_epoch": member["connection_epoch"],
        "release_status": member["release_status"],
        "revision_of": member["revision_of"],
        "availability": availability,
        "availability_ms": availability_ms,
        "ordering": {
            "receive_ms": receive_ms,
            "receive_monotonic_ns": member["receive_monotonic_ns"],
            "event_before_receive": event_before_receive,
        },
        "validity": {
            "book_valid_from_ms": member["book_valid_from_ms"],
            "book_valid_to_ms": member["book_valid_to_ms"],
            "well_formed": True,
        },
        "value": member["value"],
        "unit": member["unit"],
        "usable": usable,
    }


def _receive_sort_key(record: dict) -> tuple:
    receive_ms = record["timestamps"]["receive_ms"]
    mono = record["receive_monotonic_ns"]
    return (
        receive_ms if receive_ms is not None else 2**62,
        mono if mono is not None else 2**62,
        record["linkage"],
    )


def normalize_manifest(manifest: dict) -> dict:
    trace = ["ENVELOPE_BEGIN"]
    _validate_envelope(manifest)
    trace.append("ENVELOPE_OK")
    segment = manifest["segment"]
    require_keys(segment, SEGMENT_KEYS, "segment")
    members = segment["members"]
    if not isinstance(members, list):
        raise ContractError("MISSING_FIELD:members")
    assert_event_count(len(members))
    rejections: list[dict] = []
    committed: list[dict] = []
    for member in members:
        if not isinstance(member, dict):
            raise ContractError("MISSING_FIELD:member")
        require_keys(member, MEMBER_KEYS, "member")
        if member["committed"] is False:
            linkage = member["linkage"] if isinstance(member["linkage"], str) else None
            rejections.append({"code": "UNCOMMITTED_TAIL", "linkage": linkage})
            trace.append("UNCOMMITTED_TAIL")
            continue
        if member["committed"] is not True:
            raise ContractError("MISSING_FIELD:committed")
        _validate_member_values(member)
        committed.append(member)
        trace.append("MEMBER_FIELDS_OK")
    verified = verify_committed_prefix(segment, committed)
    records = [to_record(manifest, segment, member) for member in verified]
    records.sort(key=_receive_sort_key)
    trace.append("NORMALIZE_OK")
    result = {"records": records, "rejections": rejections, "trace": trace}
    scan_obj(result)
    return result
