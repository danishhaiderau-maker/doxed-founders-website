"""Compiled, bounded-memory validation for relay evidence snapshots."""
from __future__ import annotations

from pathlib import Path

import ijson
from ijson.common import ObjectBuilder


BACKEND = ijson.backend
_ROOT_VALUES = {"schema", "generatedAt", "generatingRevision", "runIdentity", "agentSlug", "userId"}
_SCALARS = {"null", "boolean", "integer", "double", "number", "string"}


def _events(handle):
    try:
        yield from ijson.parse(handle)
    except ijson.JSONError as exc:
        raise ValueError("invalid JSON") from exc


def _validate_record(record, event_ids):
    if not isinstance(record, dict) or not all(
        record.get(key) for key in ("canonicalTradeId", "lifecycleId", "participantId")
    ) or not isinstance(record.get("events"), list):
        return "RECORD_INVALID"
    for event in record["events"]:
        if not isinstance(event, dict) or not all(event.get(key) for key in ("id", "eventType", "createdAt")):
            return "EVENT_INVALID"
        event_id = str(event["id"])
        if event_id in event_ids:
            return "DUPLICATE_EVENT"
        event_ids.add(event_id)
    return None


def validate_streaming(path: Path):
    """Validate one record at a time using ijson's compiled YAJL backend."""
    metadata, seen_keys, event_ids = {}, set(), set()
    record_count, records_seen, records_error = 0, False, None
    current_root_key = None
    builder = None
    builder_depth = 0
    builder_kind = None

    with path.open("rb") as handle:
        for prefix, event, value in _events(handle):
            if prefix == "" and event == "start_map":
                continue
            if prefix == "" and event == "map_key":
                current_root_key = value
                if value in seen_keys:
                    raise ValueError("duplicate root key")
                seen_keys.add(value)
                continue
            if prefix == "" and event == "end_map":
                continue

            if prefix == "records" and event == "start_array":
                records_seen = True
                continue
            if prefix == "records" and event == "end_array":
                continue
            if prefix == "records" and event != "start_array":
                # A scalar/map records value is valid JSON but invalid schema.
                records_seen = False
                continue

            if prefix == "records.item" and builder is None:
                builder, builder_kind = ObjectBuilder(), "record"
                builder_depth = 0
            elif current_root_key in _ROOT_VALUES and prefix == current_root_key and builder is None:
                builder, builder_kind = ObjectBuilder(), current_root_key
                builder_depth = 0

            if builder is not None:
                builder.event(event, value)
                if event in ("start_map", "start_array"):
                    builder_depth += 1
                elif event in ("end_map", "end_array"):
                    builder_depth -= 1
                complete = (event in _SCALARS and builder_depth == 0) or (
                    event in ("end_map", "end_array") and builder_depth == 0
                )
                if complete:
                    built = builder.value
                    if builder_kind == "record":
                        error = _validate_record(built, event_ids)
                        if records_error is None and error is not None:
                            records_error = error
                        record_count += 1
                    else:
                        metadata[builder_kind] = built
                    builder = builder_kind = None
                continue

    metadata["records"] = record_count
    if metadata.get("schema") != "relay_lifecycle_evidence_v1":
        return False, "SCHEMA_INVALID", metadata
    if not all(metadata.get(key) for key in ("generatedAt", "generatingRevision", "runIdentity")):
        return False, "PROVENANCE_INCOMPLETE", metadata
    if metadata.get("agentSlug") != "conservative-btc" or not metadata.get("userId"):
        return False, "SCOPE_INVALID", metadata
    if not records_seen:
        return False, "RECORDS_INVALID", metadata
    if records_error:
        return False, records_error, metadata
    return True, "OK", metadata
