"""Incremental, digest-verified row index over append-only V3 JSONL ledgers.

The V3 collector appends canonical JSON (``sort_keys``, compact separators), so
for almost every row the canonical serialization a reader would recompute is
the raw line itself. Re-parsing the ~1.5 GB ``order_intent`` ledger (~200 KB of
``entry_children`` per row) only to re-serialize it cost minutes and ~4 GB per
analyzer pass and grew with the ledger.

This index records, per complete line, its length and whether the raw bytes are
already canonical (or the canonical bytes when they are not, or that readers
skip the line). Each pass re-reads the file once, verifies every previously
indexed byte against stored SHA-256 block digests *before* any cached row is
used, and parses only the bytes appended since the last pass. A rewrite, a
truncation or a corrupt cache falls back to parsing, so results are always
identical to parsing the whole file.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from array import array
from pathlib import Path
from typing import Iterator

SCHEMA = "v3_ledger_row_index_v1"
INDEX_DIR_ENV = "ANALYZER_LEDGER_INDEX_DIR"
BLOCK_BYTES = 8 * 1024 * 1024
KIND_CANONICAL = 0
KIND_OBJECT = 1
KIND_SKIP = 2
_MAGIC = b"V3LRIDX1"


def canonical_row_bytes(raw: bytes) -> tuple[int, bytes | None]:
    """Classify one complete line exactly as the streaming V3 readers parse it."""
    try:
        value = json.loads(raw.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return KIND_SKIP, None
    if not isinstance(value, dict):
        return KIND_SKIP, None
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if canonical == raw[:-1]:
        return KIND_CANONICAL, None
    return KIND_OBJECT, canonical


class _Index:
    def __init__(self) -> None:
        self.cursor = 0
        self.lengths = array("q")
        self.kinds = bytearray()
        self.alt: dict[int, bytes] = {}
        self.blocks: list[str] = []
        self.tail = hashlib.sha256().hexdigest()


def default_index_dir(path: Path) -> Path | None:
    override = os.environ.get(INDEX_DIR_ENV, "").strip()
    if override:
        return Path(override)
    # <data>/v3/ledgers/<name>.jsonl -> <data>/analyzer/ledger_index
    analyzer = path.parent.parent.parent / "analyzer"
    return analyzer / "ledger_index" if analyzer.is_dir() else None


def _index_file(path: Path, cache_dir: Path) -> Path:
    key = hashlib.sha256(str(path.resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{path.stem}-{key}.lri"


def _load(file: Path, source: Path) -> _Index | None:
    try:
        data = file.read_bytes()
        if data[:8] != _MAGIC:
            return None
        (header_len,) = struct.unpack_from("<I", data, 8)
        header = json.loads(data[12:12 + header_len].decode("utf-8"))
        body = memoryview(data)[12 + header_len:]
        if (header.get("schema") != SCHEMA or header.get("source") != str(source.resolve())
                or int(header.get("block_bytes") or 0) != BLOCK_BYTES
                or hashlib.sha256(body).hexdigest() != header.get("body_sha256")):
            return None
        rows = int(header["rows"])
        index = _Index()
        index.lengths.frombytes(bytes(body[:rows * 8]))
        index.kinds = bytearray(body[rows * 8:rows * 9])
        blob = body[rows * 9:]
        for ordinal, start, length in header["alt"]:
            index.alt[int(ordinal)] = bytes(blob[start:start + length])
        index.cursor = int(header["cursor"])
        index.blocks = [str(value) for value in header["blocks"]]
        index.tail = str(header["tail"])
        if (len(index.lengths) != rows or len(index.kinds) != rows or sum(index.lengths) != index.cursor
                or len(index.blocks) != index.cursor // BLOCK_BYTES
                or any(index.kinds[o] != KIND_OBJECT for o in index.alt)
                or sum(1 for kind in index.kinds if kind == KIND_OBJECT) != len(index.alt)):
            return None
        return index
    except (OSError, ValueError, KeyError, TypeError, IndexError, struct.error):
        return None


def _store(file: Path, source: Path, index: _Index) -> None:
    blob = bytearray()
    alt = []
    for ordinal in sorted(index.alt):
        payload = index.alt[ordinal]
        alt.append([ordinal, len(blob), len(payload)])
        blob.extend(payload)
    body = index.lengths.tobytes() + bytes(index.kinds) + bytes(blob)
    header = json.dumps({
        "schema": SCHEMA, "source": str(source.resolve()), "rows": len(index.lengths), "cursor": index.cursor,
        "block_bytes": BLOCK_BYTES, "blocks": index.blocks, "tail": index.tail, "alt": alt,
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }, separators=(",", ":")).encode("utf-8")
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(f"{file.name}.{os.getpid()}.tmp")
    tmp.write_bytes(_MAGIC + struct.pack("<I", len(header)) + header + body)
    os.replace(tmp, file)


def _segments(old_cursor: int, limit: int) -> Iterator[tuple[int, int]]:
    """Read boundaries: every block edge plus the previously indexed cursor."""
    edges = set(range(BLOCK_BYTES, limit, BLOCK_BYTES))
    edges.add(limit)
    if 0 < old_cursor < limit:
        edges.add(old_cursor)
    position = 0
    for edge in sorted(edges):
        yield position, edge
        position = edge


def iter_canonical_rows(path: str | Path, *, byte_limit: int | None = None,
                        cache_dir: str | Path | None = None, stats: dict | None = None) -> Iterator[bytes]:
    """Yield, in file order, the canonical JSON bytes of each object row.

    Equivalent to ``json.dumps(json.loads(line.decode("utf-8", "replace")),
    sort_keys=True, separators=(",", ":")).encode()`` for every complete line
    within ``byte_limit`` that decodes to a JSON object; other lines are skipped
    and a trailing line without a newline ends the scan.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return
    limit = size if byte_limit is None else max(0, min(int(byte_limit), size))
    if limit <= 0:
        return
    directory = Path(cache_dir) if cache_dir is not None else default_index_dir(path)
    index_file = _index_file(path, directory) if directory is not None else None
    old = _load(index_file, path) if index_file is not None else None
    if old is not None and old.cursor > limit:
        old = None
    known = old is not None
    old_cursor = old.cursor if old is not None else 0
    new = _Index()
    counters = {"rows": 0, "reused_rows": 0, "parsed_rows": 0, "index_valid": known,
                "index_file": str(index_file) if index_file else None}

    block_hash = hashlib.sha256()
    tail_hash = hashlib.sha256()
    pending = bytearray()
    pending_start = 0
    row = 0

    def take(length: int) -> bytes:
        nonlocal pending_start
        raw = bytes(pending[:length])
        del pending[:length]
        pending_start += length
        return raw

    def emit_known(verified_to: int) -> Iterator[bytes]:
        nonlocal row
        while row < len(old.lengths) and pending_start + old.lengths[row] <= verified_to:
            length = old.lengths[row]
            kind = old.kinds[row]
            raw = take(length)
            new.lengths.append(length)
            new.kinds.append(kind)
            if kind == KIND_OBJECT:
                new.alt[len(new.lengths) - 1] = old.alt[row]
                yield old.alt[row]
            elif kind == KIND_CANONICAL:
                yield raw[:-1]
            row += 1
            counters["reused_rows"] += 1

    def emit_parsed() -> Iterator[bytes]:
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                return
            raw = take(newline + 1)
            kind, canonical = canonical_row_bytes(raw)
            new.lengths.append(len(raw))
            new.kinds.append(kind)
            counters["parsed_rows"] += 1
            if kind == KIND_OBJECT:
                new.alt[len(new.lengths) - 1] = canonical
                yield canonical
            elif kind == KIND_CANONICAL:
                yield raw[:-1]

    with path.open("rb") as handle:
        for start, end in _segments(old_cursor, limit):
            data = handle.read(end - start)
            short = len(data) != end - start
            block_hash.update(data)
            pending.extend(data)
            at_block_edge = not short and end % BLOCK_BYTES == 0
            if at_block_edge:
                new.blocks.append(block_hash.hexdigest())
                block_hash = hashlib.sha256()
            if known and end <= old_cursor and not short:
                if at_block_edge:
                    ok = new.blocks[-1] == old.blocks[end // BLOCK_BYTES - 1]
                    tail_hash = hashlib.sha256()
                else:
                    tail_hash.update(data)
                    ok = end != old_cursor or tail_hash.hexdigest() == old.tail
                if ok:
                    if at_block_edge or end == old_cursor:
                        yield from emit_known(end)
                    continue
                known = False
                counters["index_valid"] = False
            elif known and row < len(old.lengths):
                known = False
                counters["index_valid"] = False
            # Rows not proven by the index (new bytes, or bytes after a mismatch).
            yield from emit_parsed()
            if short:
                break

    new.cursor = pending_start
    counters["rows"] = len(new.lengths)
    if stats is not None:
        stats.update(counters)
    if index_file is None or new.cursor <= 0:
        return
    if old is not None and counters["index_valid"] and old.cursor == new.cursor:
        return
    full = new.cursor // BLOCK_BYTES
    del new.blocks[full:]
    with path.open("rb") as handle:
        handle.seek(full * BLOCK_BYTES)
        new.tail = hashlib.sha256(handle.read(new.cursor - full * BLOCK_BYTES)).hexdigest()
    try:
        _store(index_file, path, new)
    except OSError:
        pass
