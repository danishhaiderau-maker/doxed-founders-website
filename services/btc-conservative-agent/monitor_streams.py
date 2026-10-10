"""Read-only paginated pulls of append-only research JSONL streams.

Lets the research mirror (Grok Strategist) fetch the new data streams over
HTTPS with the monitor token instead of an SSH/sftp tunnel. The cursor is a
byte offset into the stream; pages always end on a complete line, so
``next_cursor`` can be fed straight back. Never writes, never takes a bot lock.
"""
from __future__ import annotations

import os
from pathlib import Path

SCHEMA = "monitor_stream_page_v1"
DEFAULT_PAGE_BYTES = 2 * 1024 * 1024
MAX_PAGE_BYTES = 8 * 1024 * 1024

# name -> file (relative to the bot data dir)
STREAMS = {
    "market_context": "market_context_1m.jsonl",          # funding / OI / spot basis
    "liquidations": "liquidations.jsonl",
    "cross_venue_tape": "cross_venue_tape_1m.jsonl",       # spot + other venues
    "book_tape_1s": "market_microstructure_1s.jsonl",      # 1 s Bitfinex book/trade tape
    "fill_markouts": "fill_markouts.jsonl",
    "taker_counterfactuals": "taker_signal_counterfactuals.jsonl",
    "shadow_exit_paths": "shadow_exit_paths.jsonl",
    "ai_shadow_challengers": "ai_shadow_challengers.jsonl",
    "runtime_telemetry": "runtime_telemetry_1m.jsonl",
}


class BadRequest(ValueError):
    pass


def catalog(data_dir: str | os.PathLike) -> dict:
    root = Path(data_dir)
    out = {}
    for name, rel in STREAMS.items():
        path = root / rel
        try:
            st = path.stat()
            out[name] = {"file": rel, "bytes": st.st_size, "mtime": st.st_mtime,
                         "inode": st.st_ino}
        except OSError:
            out[name] = {"file": rel, "bytes": None}
    return {"schema": "monitor_stream_catalog_v1", "streams": out,
            "page": {"cursor": "byte offset; 0 = start, use next_cursor",
                     "default_bytes": DEFAULT_PAGE_BYTES, "max_bytes": MAX_PAGE_BYTES}}


def read_page(data_dir: str | os.PathLike, name: str, cursor=None, limit_bytes=None,
              inode=None) -> tuple[dict, bytes]:
    """(header, body). body is raw JSONL bytes ending on a newline."""
    rel = STREAMS.get(str(name))
    if rel is None:
        raise BadRequest(f"unknown stream {name!r}")
    try:
        start = int(cursor or 0)
        limit = int(limit_bytes or DEFAULT_PAGE_BYTES)
    except (TypeError, ValueError):
        raise BadRequest("cursor/limit_bytes must be integers")
    if start < 0 or limit <= 0:
        raise BadRequest("cursor >= 0 and limit_bytes > 0 required")
    limit = min(limit, MAX_PAGE_BYTES)
    path = Path(data_dir) / rel
    header = {"schema": SCHEMA, "stream": name, "file": rel, "cursor": start}
    try:
        fh = path.open("rb")
    except OSError:
        return {**header, "exists": False, "next_cursor": start, "eof": True, "rows": 0}, b""
    with fh:
        st = os.fstat(fh.fileno())
        rotated = (inode is not None and str(inode) != str(st.st_ino)) or start > st.st_size
        if rotated:
            # The file was rotated/rewritten; restart from 0 and say so.
            start = 0
        fh.seek(start)
        raw = fh.read(limit)
    cut = raw.rfind(b"\n")
    if cut < 0:
        body = b""
        if len(raw) >= limit:
            raise BadRequest("single row larger than limit_bytes; raise limit_bytes")
    else:
        body = raw[:cut + 1]
    nxt = start + len(body)
    return {**header, "exists": True, "rotated": rotated, "inode": st.st_ino,
            "size": st.st_size, "next_cursor": nxt, "eof": nxt >= st.st_size,
            "rows": body.count(b"\n"), "bytes": len(body)}, body
