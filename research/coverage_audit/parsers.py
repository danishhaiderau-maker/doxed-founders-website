"""Streaming archive and record parsers.

Only timestamp, sequence, schema-type, and depth-count fields are retained.
Ignored token text is discarded before the parser returns. Exceptions that
leave this module carry a fixed reason code and no input bytes.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib

from research.coverage_audit.guards import (
    UNIX_MS_MAX,
    UNIX_MS_MIN,
    UNIX_S_MAX,
    UNIX_S_MIN,
    UNIX_US_MAX,
    UNIX_US_MIN,
    AuditStop,
    clean_call,
    exceeds,
    sha256_hex,
    to_day,
)

_WS = (9, 10, 13, 32)
_ISO_LEN = 20


def gzip_tail_is_bad(following: bytes, pad: int) -> bool:
    return pad < len(following) and following[pad : pad + 2] != b"\x1f\x8b"


def gzip_crc_is_bad(computed: int, expected: int) -> bool:
    return computed != expected


def member_name_mismatch(actual: str, pinned: str) -> bool:
    return actual != pinned


def member_hash_mismatch(actual: str, pinned: str) -> bool:
    return actual != pinned


class Event:
    __slots__ = (
        "ts_ms",
        "row",
        "member_index",
        "sequence",
        "receive_ms",
        "publication_ms",
        "vintage_ms",
    )

    def __init__(self, ts_ms: int, row: int, member_index: int):
        self.ts_ms = ts_ms
        self.row = row
        self.member_index = member_index
        self.sequence = None
        self.receive_ms = None
        self.publication_ms = None
        self.vintage_ms = None


class Acceptor:
    def __init__(self, ledger, track_sequence: bool, on_add=None):
        self.ledger = ledger
        self.track_sequence = track_sequence
        self.on_add = on_add
        self.events: list[Event] = []
        self.prev_ts = None
        self.violations = 0
        self.dups = 0
        self.seen: set[tuple] = set()
        self.days: set[str] = set()

    def add(self, event: Event) -> None:
        self.ledger.check_meter()
        key = (event.ts_ms, event.sequence) if self.track_sequence else (event.ts_ms,)
        is_new = key not in self.seen
        if is_new and exceeds(self.ledger.duplicate_keys + 1, self.ledger.p["max_duplicate_keys"]):
            raise AuditStop("DUPLICATE_STORAGE_CAP")
        if exceeds(self.ledger.rows + 1, self.ledger.p["max_rows"]):
            raise AuditStop("ROW_CAP")
        self.ledger.reserve_memory(self.ledger.p["row_memory_bytes"])
        if is_new:
            self.ledger.reserve_memory(self.ledger.p["dup_memory_bytes"])
            self.ledger.duplicate_keys += 1
            self.seen.add(key)
        else:
            self.dups += 1
        day = to_day(event.ts_ms)
        if day not in self.days:
            self.ledger.reserve_memory(self.ledger.p["day_memory_bytes"])
            self.days.add(day)
        if self.prev_ts is not None and event.ts_ms < self.prev_ts:
            self.violations += 1
        self.prev_ts = event.ts_ms
        self.ledger.rows += 1
        self.events.append(event)
        if self.on_add is not None:
            self.on_add(event)


class Fingerprint:
    def __init__(self) -> None:
        self.types: dict[str, str] = {}
        self.rows: dict[str, int] = {}
        self.count_min: dict[str, int] = {}
        self.count_max: dict[str, int] = {}
        self.undeclared_max = 0
        self.event_rows = 0
        self.members: list[dict] = []

    def observe(self, name: str, kind: str, count: int | None = None) -> None:
        if name in self.types and self.types[name] != kind:
            raise AuditStop("SCHEMA_DRIFT")
        self.types[name] = kind
        self.rows[name] = self.rows.get(name, 0) + 1
        if count is not None:
            previous_min = self.count_min.get(name)
            previous_max = self.count_max.get(name)
            self.count_min[name] = count if previous_min is None else min(previous_min, count)
            self.count_max[name] = count if previous_max is None else max(previous_max, count)

    def note_undeclared(self, count: int) -> None:
        if count > self.undeclared_max:
            self.undeclared_max = count

    def note_row(self) -> None:
        self.event_rows += 1


def parse_input(data, feed, pins, ledger, acceptor, fingerprint, logical_id: str) -> None:
    clean_call(
        _parse_input_impl,
        "UNEXPECTED_STOP",
        data,
        feed,
        pins,
        ledger,
        acceptor,
        fingerprint,
        logical_id,
    )


def _parse_input_impl(data, feed, pins, ledger, acceptor, fingerprint, logical_id: str) -> None:
    container = feed["format"]["container"]
    if container == "plain":
        if pins:
            raise AuditStop("ALLOWLIST_INVALID")
        ledger.add_decomp(len(data))
        digest = sha256_hex(data)
        ledger.add_member()
        before = len(acceptor.events)
        _parse_payload(data, feed, ledger, acceptor, fingerprint, 0)
        fingerprint.members.append(
            {
                "member_id": logical_id,
                "index": 0,
                "role": "plain",
                "rows": len(acceptor.events) - before,
                "payload_sha256": digest,
            }
        )
        return
    if container == "gzip":
        _parse_gzip(data, feed, pins, ledger, acceptor, fingerprint)
        return
    if container == "zip":
        _parse_zip(data, feed, pins, ledger, acceptor, fingerprint)
        return
    raise AuditStop("UNSUPPORTED_FORMAT")


def _parse_gzip(data, feed, pins, ledger, acceptor, fingerprint) -> None:
    if not data:
        raise AuditStop("GZIP_TRUNCATED")
    produced = []
    pos = 0
    size = len(data)
    while pos < size:
        if data[pos] == 0:
            if not produced:
                raise AuditStop("GZIP_MAGIC")
            while pos < size and data[pos] == 0:
                pos += 1
            continue
        if len(produced) >= len(pins):
            raise AuditStop("UNLISTED_MEMBER")
        if ledger.members >= ledger.p["max_archive_members"]:
            raise AuditStop("MEMBER_CAP")
        payload, pos = _one_gzip(data, pos, ledger, bool(produced))
        pin = pins[len(produced)]
        if pin.get("kind") != "gzip_member":
            raise AuditStop("ALLOWLIST_INVALID")
        digest = sha256_hex(payload)
        if member_hash_mismatch(digest, pin["sha256"]):
            matched = any(item.get("sha256") == digest for item in pins)
            ledger.release_memory(len(payload))
            payload = b""
            raise AuditStop("MEMBER_ORDER_MISMATCH" if matched else "MEMBER_HASH_MISMATCH")
        ledger.add_member()
        produced.append((pin["member_id"], digest, payload))
    if len(produced) < len(pins):
        raise AuditStop("MEMBER_MISSING")
    for index, (member_id, digest, payload) in enumerate(produced):
        before = len(acceptor.events)
        try:
            _parse_payload(payload, feed, ledger, acceptor, fingerprint, index)
        finally:
            ledger.release_memory(len(payload))
            produced[index] = None
        fingerprint.members.append(
            {
                "member_id": member_id,
                "index": index,
                "role": "gzip_member",
                "rows": len(acceptor.events) - before,
                "payload_sha256": digest,
            }
        )


def _one_gzip(data: bytes, pos: int, ledger, followed: bool) -> tuple[bytes, int]:
    size = len(data)
    if size - pos < 10 or data[pos : pos + 2] != b"\x1f\x8b":
        raise AuditStop("GZIP_TRUNCATED_TAIL" if followed else "GZIP_MAGIC")
    if data[pos + 2] != 8 or data[pos + 3] & 0xE0:
        raise AuditStop("GZIP_CORRUPT")
    flag = data[pos + 3]
    index = pos + 10
    if flag & 4:
        if index + 2 > size:
            raise AuditStop("GZIP_TRUNCATED")
        extra = int.from_bytes(data[index : index + 2], "little")
        index += 2 + extra
        if index > size:
            raise AuditStop("GZIP_TRUNCATED")
    if flag & 8:
        index = _skip_cstring(data, index)
    if flag & 16:
        index = _skip_cstring(data, index)
    if flag & 2:
        index += 2
        if index > size:
            raise AuditStop("GZIP_TRUNCATED")
    decomp_room = ledger.p["max_decompressed_bytes"] - ledger.decompressed
    mem_room = ledger.p["memory_bytes"] - ledger.memory
    if mem_room <= 0:
        raise AuditStop("MEMORY_CAP")
    if decomp_room <= 0:
        raise AuditStop("DECOMPRESSION_CAP")
    allowance = decomp_room if decomp_room < mem_room else mem_room
    owned = 0

    def release_exact(amount: int) -> None:
        nonlocal owned
        if amount == 0:
            return
        if amount > owned:
            raise AuditStop("MEMORY_CAP")
        ledger.release_memory(amount)
        owned -= amount

    ledger.mark_loop(True)
    try:
        try:
            ledger.charge_before_alloc(allowance)
        except AuditStop:
            raise
        owned = allowance
        ledger.check_meter()
        try:
            decompressor = zlib.decompressobj(-15)
            out = decompressor.decompress(data[index:], allowance)
        except AuditStop:
            release_exact(owned)
            raise
        except Exception:
            release_exact(owned)
            raise AuditStop("GZIP_CORRUPT") from None
    finally:
        ledger.mark_loop(False)
    if not decompressor.eof:
        hit_cap = len(out) >= allowance
        out = b""
        release_exact(owned)
        if hit_cap and allowance == mem_room and mem_room < decomp_room:
            raise AuditStop("MEMORY_CAP")
        raise AuditStop("DECOMPRESSION_CAP" if hit_cap else "GZIP_TRUNCATED")
    unused = decompressor.unused_data
    if len(unused) < 8:
        out = b""
        release_exact(owned)
        raise AuditStop("GZIP_TRUNCATED")
    release_exact(allowance - len(out))
    crc, isize = struct.unpack("<II", unused[:8])
    if gzip_crc_is_bad(zlib.crc32(out) & 0xFFFFFFFF, crc) or gzip_crc_is_bad(len(out) & 0xFFFFFFFF, isize):
        release_exact(owned)
        out = b""
        raise AuditStop("GZIP_CRC")
    ledger.add_decomp(len(out))
    following = unused[8:]
    pad = 0
    while pad < len(following) and following[pad] == 0:
        pad += 1
    if gzip_tail_is_bad(following, pad):
        release_exact(owned)
        out = b""
        raise AuditStop("GZIP_TRUNCATED_TAIL")
    next_pos = len(data) - len(unused) + 8 + pad
    return out, next_pos


def _skip_cstring(data: bytes, index: int) -> int:
    size = len(data)
    while index < size and data[index] != 0:
        index += 1
    if index >= size:
        raise AuditStop("GZIP_TRUNCATED")
    return index + 1


def _find_eocd(data: bytes):
    size = len(data)
    if size < 22:
        return None
    start = max(0, size - (22 + 65535))
    cursor = size - 22
    while cursor >= start:
        if data[cursor : cursor + 4] == b"PK\x05\x06":
            return cursor
        cursor -= 1
    return None


def _prescan_zip(data: bytes, ledger) -> int:
    """Prove contiguous local+CD+EOCD coverage and charge the CD before ZipFile."""
    size = len(data)
    if b"PK\x06\x06" in data or b"PK\x06\x07" in data:
        raise AuditStop("ZIP_UNSUPPORTED")
    eocd = _find_eocd(data)
    if eocd is None:
        raise AuditStop("ZIP_TRUNCATED")
    comment_len = int.from_bytes(data[eocd + 20 : eocd + 22], "little")
    if eocd + 22 + comment_len != size:
        raise AuditStop("ZIP_CORRUPT")
    total = int.from_bytes(data[eocd + 10 : eocd + 12], "little")
    cd_size = int.from_bytes(data[eocd + 12 : eocd + 16], "little")
    cd_off = int.from_bytes(data[eocd + 16 : eocd + 20], "little")
    if cd_off + cd_size != eocd or cd_size > size:
        raise AuditStop("ZIP_CORRUPT")
    if exceeds(ledger.members + total, ledger.p["max_archive_members"]):
        raise AuditStop("MEMBER_CAP")
    if exceeds(cd_size, ledger.p["memory_bytes"] - ledger.memory):
        raise AuditStop("MEMORY_CAP")
    cursor = cd_off
    metas = []
    seen = 0
    ledger.mark_loop(True)
    try:
        while seen < total:
            ledger.check_meter()
            if cursor + 46 > cd_off + cd_size or data[cursor : cursor + 4] != b"PK\x01\x02":
                raise AuditStop("ZIP_CORRUPT")
            flag = int.from_bytes(data[cursor + 8 : cursor + 10], "little")
            method = int.from_bytes(data[cursor + 10 : cursor + 12], "little")
            comp = int.from_bytes(data[cursor + 20 : cursor + 24], "little")
            uncomp = int.from_bytes(data[cursor + 24 : cursor + 28], "little")
            name_len = int.from_bytes(data[cursor + 28 : cursor + 30], "little")
            extra_len = int.from_bytes(data[cursor + 30 : cursor + 32], "little")
            comment = int.from_bytes(data[cursor + 32 : cursor + 34], "little")
            local_off = int.from_bytes(data[cursor + 42 : cursor + 46], "little")
            record = 46 + name_len + extra_len + comment
            if cursor + record > cd_off + cd_size:
                raise AuditStop("ZIP_CORRUPT")
            if comp == 0xFFFFFFFF or uncomp == 0xFFFFFFFF or local_off == 0xFFFFFFFF or flag & 0x8:
                raise AuditStop("ZIP_UNSUPPORTED")
            metas.append((local_off, comp, method))
            cursor += record
            seen += 1
        if cursor != cd_off + cd_size or seen != total:
            raise AuditStop("ZIP_CORRUPT")
        expect = 0
        for local_off, comp, method in sorted(metas):
            ledger.check_meter()
            if local_off != expect or local_off + 30 > size or data[local_off : local_off + 4] != b"PK\x03\x04":
                raise AuditStop("ZIP_CORRUPT")
            local_flag = int.from_bytes(data[local_off + 6 : local_off + 8], "little")
            local_method = int.from_bytes(data[local_off + 8 : local_off + 10], "little")
            local_comp = int.from_bytes(data[local_off + 18 : local_off + 22], "little")
            local_name = int.from_bytes(data[local_off + 26 : local_off + 28], "little")
            local_extra = int.from_bytes(data[local_off + 28 : local_off + 30], "little")
            if local_comp != comp or local_method != method:
                raise AuditStop("ZIP_CORRUPT")
            if local_flag & 1:
                raise AuditStop("ZIP_ENCRYPTED")
            if local_flag & 0x8:
                raise AuditStop("ZIP_UNSUPPORTED")
            expect = local_off + 30 + local_name + local_extra + comp
            if expect > cd_off:
                raise AuditStop("ZIP_CORRUPT")
        if expect != cd_off:
            raise AuditStop("ZIP_CORRUPT")
    finally:
        ledger.mark_loop(False)
    ledger.charge_before_alloc(cd_size)
    return cd_size


def _parse_zip(data, feed, pins, ledger, acceptor, fingerprint) -> None:
    cd_size = _prescan_zip(data, ledger)
    archive = None
    try:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data), "r")
            infos = archive.infolist()
        except AuditStop:
            raise
        except Exception:
            raise AuditStop("ZIP_CORRUPT") from None
        if exceeds(ledger.members + len(infos), ledger.p["max_archive_members"]):
            raise AuditStop("MEMBER_CAP")
        if len(infos) < len(pins):
            raise AuditStop("MEMBER_MISSING")
        for index, info in enumerate(infos):
            if index >= len(pins):
                raise AuditStop("UNLISTED_MEMBER")
            pin = pins[index]
            name = info.filename
            if _unsafe_zip_name(name):
                raise AuditStop("PATH_TRAVERSAL_MEMBER")
            if info.flag_bits & 1:
                raise AuditStop("ZIP_ENCRYPTED")
            matched_name = any(item.get("name") == name for item in pins)
            if member_name_mismatch(name, pin.get("name") or ""):
                raise AuditStop("MEMBER_ORDER_MISMATCH" if matched_name else "UNLISTED_MEMBER")
            if pin["kind"] == "directory":
                if not name.endswith("/") or info.file_size != 0:
                    raise AuditStop("ZIP_UNSUPPORTED")
                digest = sha256_hex(b"")
                if member_hash_mismatch(digest, pin["sha256"]):
                    raise AuditStop("MEMBER_HASH_MISMATCH")
                ledger.add_member()
                fingerprint.members.append(
                    {
                        "member_id": pin["member_id"],
                        "index": index,
                        "role": "directory",
                        "rows": 0,
                        "payload_sha256": digest,
                    }
                )
                continue
            if pin["kind"] != "file":
                raise AuditStop("ALLOWLIST_INVALID")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise AuditStop("ZIP_UNSUPPORTED")
            payload = _read_zip_payload(archive, info, ledger)
            digest = sha256_hex(payload)
            if member_hash_mismatch(digest, pin["sha256"]):
                ledger.release_memory(len(payload))
                payload = b""
                raise AuditStop("MEMBER_HASH_MISMATCH")
            ledger.add_member()
            before = len(acceptor.events)
            try:
                _parse_payload(payload, feed, ledger, acceptor, fingerprint, index)
            finally:
                ledger.release_memory(len(payload))
                payload = b""
            fingerprint.members.append(
                {
                    "member_id": pin["member_id"],
                    "index": index,
                    "role": "file",
                    "rows": len(acceptor.events) - before,
                    "payload_sha256": digest,
                }
            )
    finally:
        if archive is not None:
            archive.close()
        ledger.release_memory(cd_size)


def _unsafe_zip_name(name: str) -> bool:
    if not isinstance(name, str) or not name or "\x00" in name:
        return True
    if name.startswith("/") or name.startswith("\\"):
        return True
    return any(part == ".." for part in name.replace("\\", "/").split("/"))


def _read_zip_payload(archive, info, ledger) -> bytes:
    need = info.file_size
    if not isinstance(need, int) or isinstance(need, bool) or need < 0:
        raise AuditStop("ZIP_CORRUPT")
    mem_room = ledger.p["memory_bytes"] - ledger.memory
    decomp_room = ledger.p["max_decompressed_bytes"] - ledger.decompressed
    if exceeds(need, mem_room):
        raise AuditStop("MEMORY_CAP")
    if exceeds(need, decomp_room):
        raise AuditStop("DECOMPRESSION_CAP")
    ledger.charge_before_alloc(need)
    handle = None
    filled = 0
    buf = bytearray(need)
    ledger.mark_loop(True)
    try:
        try:
            handle = archive.open(info, "r")
        except Exception:
            raise AuditStop("ZIP_CORRUPT") from None
        while filled < need:
            ledger.check_meter()
            try:
                blob = handle.read(min(4096, need - filled))
            except Exception:
                raise AuditStop("ZIP_CRC") from None
            if not blob:
                break
            take = len(blob)
            if filled + take > need:
                raise AuditStop("ZIP_CORRUPT")
            buf[filled : filled + take] = blob
            filled += take
        if filled != need:
            raise AuditStop("ZIP_CRC")
        ledger.add_decomp(need)
        return bytes(buf)
    except AuditStop:
        ledger.release_memory(need)
        raise
    finally:
        ledger.mark_loop(False)
        if handle is not None:
            handle.close()


def _parse_payload(data, feed, ledger, acceptor, fingerprint, member_index: int) -> None:
    kind = feed["format"]["kind"]
    if kind in ("header_csv", "headerless_csv"):
        _parse_csv(data, feed, ledger, acceptor, fingerprint, member_index)
        return
    if kind == "jsonl":
        _parse_jsonl(data, feed, ledger, acceptor, fingerprint, member_index)
        return
    if kind == "json_array":
        _parse_json_array(data, feed, ledger, acceptor, fingerprint, member_index)
        return
    raise AuditStop("UNSUPPORTED_FORMAT")


def _split_csv(data: bytes, ledger):
    quote = 34
    delim = 44
    index = 0
    size = len(data)
    ledger.mark_loop(True)
    try:
        while index < size:
            start = index
            fields: list[bytes] = []
            buf = bytearray()
            quoted = False
            opened = False
            while index < size:
                ledger.check_meter()
                if index - start > ledger.p["max_record_bytes"]:
                    raise AuditStop("RECORD_SIZE_CAP")
                if exceeds(len(buf) + 1, ledger.p["memory_bytes"] - ledger.memory):
                    raise AuditStop("MEMORY_CAP")
                char = data[index]
                if quoted:
                    if char == quote:
                        if index + 1 < size and data[index + 1] == quote:
                            buf.append(quote)
                            index += 2
                            continue
                        quoted = False
                        index += 1
                        continue
                    buf.append(char)
                    index += 1
                    continue
                if char == quote and not buf and not opened:
                    quoted = True
                    opened = True
                    index += 1
                    continue
                if char == delim:
                    fields.append(bytes(buf))
                    buf = bytearray()
                    opened = False
                    index += 1
                    continue
                if char in (10, 13):
                    fields.append(bytes(buf))
                    index += 1
                    if char == 13 and index < size and data[index] == 10:
                        index += 1
                    break
                if char == quote:
                    raise AuditStop("CSV_SYNTAX")
                buf.append(char)
                index += 1
            else:
                if quoted:
                    raise AuditStop("CSV_SYNTAX")
                if buf or fields:
                    fields.append(bytes(buf))
            raw_len = index - start
            if exceeds(raw_len, ledger.p["max_record_bytes"]):
                raise AuditStop("RECORD_SIZE_CAP")
            if fields == [b""]:
                continue
            ledger.note_record(raw_len)
            ledger.charge_before_alloc(raw_len)
            try:
                yield fields, raw_len
            finally:
                ledger.release_memory(raw_len)
                fields.clear()
    finally:
        ledger.mark_loop(False)


def _parse_csv(data, feed, ledger, acceptor, fingerprint, member_index: int) -> None:
    records = _split_csv(data, ledger)
    names = [field["name"] for field in feed["fields"]]
    row_number = 0
    if feed["format"]["header"]:
        try:
            header, _raw_len = next(records)
        except StopIteration:
            raise AuditStop("SCHEMA_MISMATCH") from None
        if not _header_matches(header, names):
            raise AuditStop("SCHEMA_MISMATCH")
    for fields, _raw_len in records:
        _consume_csv_row(fields, feed, acceptor, fingerprint, member_index, row_number)
        row_number += 1


def _header_matches(fields: list[bytes], names: list[str]) -> bool:
    if len(fields) != len(names):
        return False
    for got, expected in zip(fields, names):
        try:
            text = got.decode("utf-8")
        except Exception:
            raise AuditStop("ENCODING_ERROR") from None
        matched = text == expected
        text = None
        if not matched:
            return False
    return True


def _consume_csv_row(fields, feed, acceptor, fingerprint, member_index: int, row_number: int) -> None:
    specs = feed["fields"]
    if len(fields) != len(specs):
        raise AuditStop("FIELD_COUNT")
    event = Event(0, row_number, member_index)
    have_ts = False
    for spec, raw in zip(specs, fields):
        role = spec["role"]
        if role == "ignored":
            kind = _discard_text(raw)
            fingerprint.observe(spec["name"], kind)
            continue
        if role == "event_timestamp":
            event.ts_ms = _timestamp(raw, feed["timestamp"]["unit"])
            fingerprint.observe(spec["name"], "timestamp")
            have_ts = True
            continue
        if role in ("receive_timestamp", "publication_timestamp", "vintage_timestamp"):
            if raw == b"":
                fingerprint.observe(spec["name"], "empty")
                continue
            parsed = _timestamp(raw, feed["timestamp"]["unit"])
            _assign_optional(event, role, parsed)
            fingerprint.observe(spec["name"], "timestamp")
            continue
        if role == "sequence":
            event.sequence = _sequence(raw)
            fingerprint.observe(spec["name"], "number")
            continue
        raise AuditStop("UNSUPPORTED_FORMAT")
    if not have_ts:
        raise AuditStop("SCHEMA_MISMATCH")
    fingerprint.note_row()
    acceptor.add(event)


def _assign_optional(event: Event, role: str, value: int) -> None:
    if role == "receive_timestamp":
        event.receive_ms = value
    elif role == "publication_timestamp":
        event.publication_ms = value
    elif role == "vintage_timestamp":
        event.vintage_ms = value


def _discard_text(raw: bytes) -> str:
    if raw == b"":
        return "empty"
    try:
        raw.decode("utf-8")
    except Exception:
        raise AuditStop("ENCODING_ERROR") from None
    return "discarded"


def _timestamp(raw: bytes, unit: str) -> int:
    try:
        text = raw.decode("utf-8")
    except Exception:
        raise AuditStop("ENCODING_ERROR") from None
    try:
        return _timestamp_text(text, unit)
    finally:
        text = None


def _timestamp_text(text: str, unit: str) -> int:
    if unit == "iso_utc":
        if len(text) != _ISO_LEN or text[4] != "-" or text[7] != "-" or text[10] != "T" or text[13] != ":" or text[16] != ":" or text[19] != "Z":
            raise AuditStop("TIMESTAMP_PARSE")
        try:
            stamp = dt_parts(text)
        except AuditStop:
            raise
        except Exception:
            raise AuditStop("TIMESTAMP_PARSE") from None
        return stamp
    if unit == "unix_s":
        return _scaled(text, UNIX_S_MIN, UNIX_S_MAX, 1000)
    if unit == "unix_ms":
        return _scaled(text, UNIX_MS_MIN, UNIX_MS_MAX, 1)
    if unit == "unix_us":
        value = _scaled(text, UNIX_US_MIN, UNIX_US_MAX, 0)
        if value % 1000:
            raise AuditStop("TIMESTAMP_PARSE")
        return value // 1000
    raise AuditStop("UNSUPPORTED_FORMAT")


def dt_parts(text: str) -> int:
    import datetime as dt

    year = int(text[0:4])
    month = int(text[5:7])
    day = int(text[8:10])
    hour = int(text[11:13])
    minute = int(text[14:16])
    second = int(text[17:19])
    stamp = dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.timezone.utc)
    return int(stamp.timestamp()) * 1000


def _scaled(text: str, low: int, high: int, scale: int) -> int:
    if not text or len(text) > 20 or any(char < "0" or char > "9" for char in text):
        raise AuditStop("TIMESTAMP_PARSE")
    if len(text) > 1 and text[0] == "0":
        raise AuditStop("TIMESTAMP_PARSE")
    try:
        number = int(text)
    except Exception:
        raise AuditStop("TIMESTAMP_PARSE") from None
    if number < low or number > high:
        raise AuditStop("TIMESTAMP_UNIT")
    return number if scale == 0 else number * scale


def _sequence(raw: bytes) -> int:
    try:
        text = raw.decode("utf-8")
    except Exception:
        raise AuditStop("ENCODING_ERROR") from None
    try:
        return _sequence_text(text)
    finally:
        text = None


def _sequence_text(text: str) -> int:
    if not text or len(text) > 10 or any(char < "0" or char > "9" for char in text):
        raise AuditStop("SEQUENCE_PARSE")
    if len(text) > 1 and text[0] == "0":
        raise AuditStop("SEQUENCE_PARSE")
    try:
        number = int(text)
    except Exception:
        raise AuditStop("SEQUENCE_PARSE") from None
    if number < 0 or number > 1_000_000_000:
        raise AuditStop("SEQUENCE_PARSE")
    return number


_JSON_LEDGER = [None]


def _json_tick() -> None:
    ledger = _JSON_LEDGER[0]
    if ledger is not None:
        ledger.check_meter()


def _parse_jsonl(data, feed, ledger, acceptor, fingerprint, member_index: int) -> None:
    _JSON_LEDGER[0] = ledger
    ledger.mark_loop(True)
    try:
        _parse_jsonl_body(data, feed, ledger, acceptor, fingerprint, member_index)
    finally:
        ledger.mark_loop(False)
        _JSON_LEDGER[0] = None


def _parse_jsonl_body(data, feed, ledger, acceptor, fingerprint, member_index: int) -> None:
    row_number = 0
    for line in data.split(b"\n"):
        if line.endswith(b"\r"):
            line = line[:-1]
        if line == b"":
            continue
        if len(line) > ledger.p["max_record_bytes"]:
            raise AuditStop("RECORD_SIZE_CAP")
        ledger.note_record(len(line))
        end = _consume_json_object(line, 0, feed, acceptor, fingerprint, member_index, row_number)
        if _skip_ws(line, end) != len(line):
            raise AuditStop("JSON_MALFORMED")
        row_number += 1


def _parse_json_array(data, feed, ledger, acceptor, fingerprint, member_index: int) -> None:
    _JSON_LEDGER[0] = ledger
    ledger.mark_loop(True)
    try:
        _parse_json_array_body(data, feed, ledger, acceptor, fingerprint, member_index)
    finally:
        ledger.mark_loop(False)
        _JSON_LEDGER[0] = None


def _parse_json_array_body(data, feed, ledger, acceptor, fingerprint, member_index: int) -> None:
    index = _skip_ws(data, 0)
    if index >= len(data) or data[index] != 91:
        raise AuditStop("JSON_MALFORMED")
    index += 1
    row_number = 0
    while True:
        index = _skip_ws(data, index)
        if index < len(data) and data[index] == 93:
            index += 1
            break
        start = index
        index = _consume_json_object(data, index, feed, acceptor, fingerprint, member_index, row_number)
        if index - start > ledger.p["max_record_bytes"]:
            raise AuditStop("RECORD_SIZE_CAP")
        ledger.note_record(index - start)
        row_number += 1
        index = _skip_ws(data, index)
        if index < len(data) and data[index] == 44:
            index += 1
            continue
        if index < len(data) and data[index] == 93:
            continue
        raise AuditStop("JSON_MALFORMED")
    if _skip_ws(data, index) != len(data):
        raise AuditStop("JSON_MALFORMED")


def _consume_json_object(data, index, feed, acceptor, fingerprint, member_index, row_number) -> int:
    end, values, undeclared = _parse_object(data, index, feed)
    fingerprint.note_undeclared(undeclared)
    event = _event_from_values(values, feed, fingerprint, member_index, row_number)
    values.clear()
    fingerprint.note_row()
    acceptor.add(event)
    return end


def _event_from_values(values, feed, fingerprint, member_index, row_number) -> Event:
    event = Event(0, row_number, member_index)
    have_ts = False
    for spec in feed["fields"]:
        name = spec["name"]
        if name not in values:
            if spec["required"]:
                raise AuditStop("SCHEMA_MISMATCH")
            continue
        kind, payload, count = values[name]
        if spec["role"] == "event_timestamp":
            event.ts_ms = payload
            have_ts = True
            fingerprint.observe(name, "timestamp")
        elif spec["role"] in ("receive_timestamp", "publication_timestamp", "vintage_timestamp"):
            if kind == "empty" or payload is None:
                fingerprint.observe(name, "empty")
            else:
                _assign_optional(event, spec["role"], payload)
                fingerprint.observe(name, "timestamp")
        elif spec["role"] == "sequence":
            event.sequence = payload
            fingerprint.observe(name, "number")
        elif spec["role"] == "ignored":
            fingerprint.observe(name, kind)
        elif spec["role"] in ("depth_band", "executable_level"):
            fingerprint.observe(name, "array", count)
        else:
            raise AuditStop("UNSUPPORTED_FORMAT")
        values[name] = None
    if not have_ts:
        raise AuditStop("SCHEMA_MISMATCH")
    return event


def _parse_object(data: bytes, index: int, feed: dict):
    index = _skip_ws(data, index)
    if index >= len(data) or data[index] != 123:
        raise AuditStop("JSON_MALFORMED")
    index += 1
    index = _skip_ws(data, index)
    expected = {field["name"]: field for field in feed["fields"]}
    values: dict[str, tuple] = {}
    seen: set[str] = set()
    undeclared = 0
    if index < len(data) and data[index] == 125:
        return index + 1, values, undeclared
    while True:
        index = _skip_ws(data, index)
        index, key = _parse_string(data, index, True)
        if key in seen:
            raise AuditStop("JSON_MALFORMED")
        seen.add(key)
        index = _skip_ws(data, index)
        if index >= len(data) or data[index] != 58:
            raise AuditStop("JSON_MALFORMED")
        index += 1
        index = _skip_ws(data, index)
        spec = expected.get(key)
        if spec is None:
            undeclared += 1
            index = _skip_value(data, index)
        else:
            index, parsed = _kept_value(data, index, spec, feed)
            values[key] = parsed
        key = None
        index = _skip_ws(data, index)
        if index < len(data) and data[index] == 125:
            return index + 1, values, undeclared
        if index >= len(data) or data[index] != 44:
            raise AuditStop("JSON_MALFORMED")
        index += 1


def _kept_value(data, index, spec, feed):
    role = spec["role"]
    if role in ("event_timestamp", "receive_timestamp", "publication_timestamp", "vintage_timestamp"):
        if role != "event_timestamp" and data.startswith(b"null", index):
            return index + 4, ("empty", None, None)
        return _kept_timestamp(data, index, feed["timestamp"]["unit"])
    if role == "sequence":
        return _kept_sequence(data, index)
    if role == "ignored":
        end, kind = _classify_skip(data, index)
        return end, (kind, None, None)
    if role in ("depth_band", "executable_level"):
        end, count = _scan_array(data, index)
        return end, ("array", None, count)
    raise AuditStop("UNSUPPORTED_FORMAT")


def _kept_timestamp(data, index, unit):
    char = data[index]
    if unit == "iso_utc":
        if char != 34:
            raise AuditStop("TIMESTAMP_PARSE")
        end, text = _parse_string(data, index, True)
        try:
            value = _timestamp_text(text, unit)
        finally:
            text = None
        return end, ("timestamp", value, None)
    if char == 34 or char == 45:
        raise AuditStop("TIMESTAMP_PARSE")
    end, number = _read_int(data, index)
    try:
        value = _timestamp_text(str(number), unit)
    finally:
        number = None
    return end, ("timestamp", value, None)


def _kept_sequence(data, index):
    if data[index] == 34 or data[index] == 45:
        raise AuditStop("SEQUENCE_PARSE")
    end, number = _read_int(data, index)
    if number < 0 or number > 1_000_000_000:
        raise AuditStop("SEQUENCE_PARSE")
    return end, ("number", number, None)


def _classify_skip(data, index):
    index = _skip_ws(data, index)
    if index >= len(data):
        raise AuditStop("JSON_MALFORMED")
    char = data[index]
    if char == 34:
        end, text = _parse_string(data, index, True)
        kind = "empty" if text == "" else "discarded"
        text = None
        return end, kind
    end = _skip_value(data, index)
    return end, "discarded"


def _skip_ws(data: bytes, index: int) -> int:
    size = len(data)
    while index < size and data[index] in _WS:
        index += 1
    return index


def _parse_string(data: bytes, index: int, keep: bool):
    if index >= len(data) or data[index] != 34:
        raise AuditStop("JSON_MALFORMED")
    index += 1
    out = bytearray() if keep else None
    size = len(data)
    while index < size:
        _json_tick()
        char = data[index]
        if char == 34:
            if not keep:
                return index + 1, None
            try:
                text = bytes(out).decode("utf-8")
            except Exception:
                raise AuditStop("ENCODING_ERROR") from None
            out = None
            return index + 1, text
        if char == 92:
            if index + 1 >= size:
                raise AuditStop("JSON_MALFORMED")
            escaped = data[index + 1]
            simple = {34: 34, 92: 92, 47: 47, 98: 8, 102: 12, 110: 10, 114: 13, 116: 9}
            if escaped in simple:
                if keep:
                    out.append(simple[escaped])
                index += 2
                continue
            if escaped == 117 and index + 6 <= size:
                try:
                    code = int(data[index + 2 : index + 6].decode("ascii"), 16)
                    if keep:
                        out.extend(chr(code).encode("utf-8"))
                except Exception:
                    raise AuditStop("JSON_MALFORMED") from None
                index += 6
                continue
            raise AuditStop("JSON_MALFORMED")
        if char < 32:
            raise AuditStop("JSON_MALFORMED")
        if keep:
            out.append(char)
        index += 1
        if out is not None and len(out) > 1_000_000:
            raise AuditStop("RECORD_SIZE_CAP")
    raise AuditStop("JSON_MALFORMED")


def _skip_value(data: bytes, index: int) -> int:
    _json_tick()
    index = _skip_ws(data, index)
    if index >= len(data):
        raise AuditStop("JSON_MALFORMED")
    char = data[index]
    if char == 34:
        end, _text = _parse_string(data, index, False)
        return end
    if char == 123:
        return _skip_object(data, index)
    if char == 91:
        end, _count = _scan_array(data, index)
        return end
    if data.startswith(b"true", index):
        return index + 4
    if data.startswith(b"false", index):
        return index + 5
    if data.startswith(b"null", index):
        return index + 4
    if char == 45 or 48 <= char <= 57:
        return _skip_number(data, index)
    raise AuditStop("JSON_MALFORMED")


def _skip_object(data: bytes, index: int) -> int:
    if data[index] != 123:
        raise AuditStop("JSON_MALFORMED")
    index += 1
    index = _skip_ws(data, index)
    if index < len(data) and data[index] == 125:
        return index + 1
    while True:
        index = _skip_ws(data, index)
        index, _key = _parse_string(data, index, False)
        index = _skip_ws(data, index)
        if index >= len(data) or data[index] != 58:
            raise AuditStop("JSON_MALFORMED")
        index = _skip_value(data, index + 1)
        index = _skip_ws(data, index)
        if index < len(data) and data[index] == 125:
            return index + 1
        if index >= len(data) or data[index] != 44:
            raise AuditStop("JSON_MALFORMED")
        index += 1


def _scan_array(data: bytes, index: int) -> tuple[int, int]:
    index = _skip_ws(data, index)
    if index >= len(data) or data[index] != 91:
        raise AuditStop("JSON_MALFORMED")
    index += 1
    index = _skip_ws(data, index)
    if index < len(data) and data[index] == 93:
        return index + 1, 0
    count = 0
    while True:
        _json_tick()
        index = _skip_value(data, index)
        count += 1
        if count > 10000:
            raise AuditStop("RECORD_SIZE_CAP")
        index = _skip_ws(data, index)
        if index < len(data) and data[index] == 93:
            return index + 1, count
        if index >= len(data) or data[index] != 44:
            raise AuditStop("JSON_MALFORMED")
        index += 1


def _read_int(data: bytes, index: int) -> tuple[int, int]:
    end, number = _read_number(data, index)
    return end, number


def _read_number(data: bytes, index: int) -> tuple[int, int]:
    start = index
    size = len(data)
    if index < size and data[index] == 45:
        raise AuditStop("JSON_MALFORMED")
    if index >= size or not (48 <= data[index] <= 57):
        raise AuditStop("JSON_MALFORMED")
    if data[index] == 48 and index + 1 < size and 48 <= data[index + 1] <= 57:
        raise AuditStop("JSON_MALFORMED")
    while index < size and 48 <= data[index] <= 57:
        index += 1
    if index < size and data[index] in (46, 69, 101):
        raise AuditStop("JSON_MALFORMED")
    if index - start > 20:
        raise AuditStop("TIMESTAMP_PARSE")
    try:
        number = int(data[start:index])
    except Exception:
        raise AuditStop("JSON_MALFORMED") from None
    return index, number


def _skip_number(data: bytes, index: int) -> int:
    size = len(data)
    if index < size and data[index] == 45:
        index += 1
    start = index
    while index < size and 48 <= data[index] <= 57:
        index += 1
    if index == start:
        raise AuditStop("JSON_MALFORMED")
    if index < size and data[index] == 46:
        index += 1
        fraction = index
        while index < size and 48 <= data[index] <= 57:
            index += 1
        if index == fraction:
            raise AuditStop("JSON_MALFORMED")
    if index < size and data[index] in (69, 101):
        index += 1
        if index < size and data[index] in (43, 45):
            index += 1
        exponent = index
        while index < size and 48 <= data[index] <= 57:
            index += 1
        if index == exponent:
            raise AuditStop("JSON_MALFORMED")
    return index
