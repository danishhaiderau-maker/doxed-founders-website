"""Bounded-memory validation for relay_lifecycle_evidence_v1 snapshots."""
from __future__ import annotations

import json
import mmap
from pathlib import Path

_ROOT_VALUES = {"schema", "generatedAt", "generatingRevision", "runIdentity", "agentSlug", "userId"}


def _ws(data, pos):
    while pos < len(data) and data[pos] in b" \t\r\n":
        pos += 1
    return pos


def _end(data, pos):
    pos = _ws(data, pos)
    if pos >= len(data):
        raise ValueError("missing JSON value")
    first = data[pos]
    if first == 34:
        escaped = False
        cur = pos + 1
        while cur < len(data):
            byte = data[cur]
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                return cur + 1
            cur += 1
        raise ValueError("unterminated string")
    if first in (123, 91):
        stack, string, escaped, cur = [first], False, False, pos + 1
        while cur < len(data):
            byte = data[cur]
            if string:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    string = False
            elif byte == 34:
                string = True
            elif byte in (123, 91):
                stack.append(byte)
            elif byte in (125, 93):
                expected = 123 if byte == 125 else 91
                if not stack or stack.pop() != expected:
                    raise ValueError("mismatched delimiter")
                if not stack:
                    return cur + 1
            cur += 1
        raise ValueError("unterminated container")
    cur = pos
    while cur < len(data) and data[cur] not in b",]} \t\r\n":
        cur += 1
    if cur == pos:
        raise ValueError("invalid scalar")
    return cur


def _decode(data, start, end):
    return json.loads(data[start:end])


def _record(record, event_ids):
    if not isinstance(record, dict) or not all(
        record.get(key) for key in ("canonicalTradeId", "lifecycleId", "participantId")
    ) or not isinstance(record.get("events"), list):
        return False, "RECORD_INVALID"
    for event in record["events"]:
        if not isinstance(event, dict) or not all(event.get(key) for key in ("id", "eventType", "createdAt")):
            return False, "EVENT_INVALID"
        event_id = str(event["id"])
        if event_id in event_ids:
            return False, "DUPLICATE_EVENT"
        event_ids.add(event_id)
    return True, "OK"


def _records(data, pos):
    pos = _ws(data, pos)
    if pos >= len(data) or data[pos] != 91:
        finish = _end(data, pos)
        _decode(data, pos, finish)
        return finish, 0, False, "RECORDS_INVALID"
    pos = _ws(data, pos + 1)
    count, event_ids, first_error = 0, set(), None
    if pos < len(data) and data[pos] == 93:
        return pos + 1, count, True, "OK"
    while True:
        finish = _end(data, pos)
        valid, code = _record(_decode(data, pos, finish), event_ids)
        if not valid and first_error is None:
            first_error = code
        count += 1
        pos = _ws(data, finish)
        if pos >= len(data):
            raise ValueError("unterminated records")
        if data[pos] == 93:
            return pos + 1, count, first_error is None, first_error or "OK"
        if data[pos] != 44:
            raise ValueError("invalid records separator")
        pos = _ws(data, pos + 1)


def validate_streaming(path: Path):
    """Return (valid, code, metadata), decoding at most one record at a time."""
    metadata, seen, count, records_error = {}, set(), None, None
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        pos = _ws(data, 0)
        if pos >= len(data) or data[pos] != 123:
            raise ValueError("root is not object")
        pos = _ws(data, pos + 1)
        if pos < len(data) and data[pos] == 125:
            pos += 1
        else:
            while True:
                key_end = _end(data, pos)
                key = _decode(data, pos, key_end)
                if not isinstance(key, str) or key in seen:
                    raise ValueError("invalid or duplicate root key")
                seen.add(key)
                pos = _ws(data, key_end)
                if pos >= len(data) or data[pos] != 58:
                    raise ValueError("missing colon")
                pos = _ws(data, pos + 1)
                if key == "records":
                    pos, count, valid, code = _records(data, pos)
                    if not valid:
                        records_error = code
                else:
                    finish = _end(data, pos)
                    decoded = _decode(data, pos, finish)
                    if key in _ROOT_VALUES:
                        metadata[key] = decoded
                    pos = finish
                pos = _ws(data, pos)
                if pos >= len(data):
                    raise ValueError("unterminated root")
                if data[pos] == 125:
                    pos += 1
                    break
                if data[pos] != 44:
                    raise ValueError("invalid root separator")
                pos = _ws(data, pos + 1)
        if _ws(data, pos) != len(data):
            raise ValueError("trailing JSON")
    metadata["records"] = count
    if metadata.get("schema") != "relay_lifecycle_evidence_v1":
        return False, "SCHEMA_INVALID", metadata
    if not all(metadata.get(key) for key in ("generatedAt", "generatingRevision", "runIdentity")):
        return False, "PROVENANCE_INCOMPLETE", metadata
    if metadata.get("agentSlug") != "conservative-btc" or not metadata.get("userId"):
        return False, "SCOPE_INVALID", metadata
    if count is None:
        return False, "RECORDS_INVALID", metadata
    if records_error:
        return False, records_error, metadata
    return True, "OK", metadata
