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
    "taker_counterfactuals": "taker_signal_counterfactuals.jsonl",  # RETIRED (R17/R22 writer deleted)
    "shadow_exit_paths": "shadow_exit_paths.jsonl",                # RETIRED (R17/R22 recorder deleted)
    "ai_shadow_challengers": "ai_shadow_challengers.jsonl",
    "runtime_telemetry": "runtime_telemetry_1m.jsonl",
    "dyn_regime_minutes": "dyn_regime_minutes.jsonl",      # Tile 15 DYN-AF shadow classifier
    # RETIRED DISLOC / DISLOC-FOI ledgers (Tiles 6/7 removed 23 Oct 2026, R9): no writer;
    # kept in STREAMS for R5 custody / readable archive. Do not delete files here.
    "disloc_trades": "disloc_trades.jsonl",
    "disloc_foi_trades": "disloc_foi_trades.jsonl",
    "disloc_paused_skips": "disloc_paused_skips.jsonl",
    "disloc_paused_skips_rot1": "disloc_paused_skips.jsonl.1",
    # Strategist data streams (12 Oct, strategist_streams_collector.py)
    "bfx_book_10l": "bfx_book_10l_1s.jsonl",               # Bitfinex 10-level book, 1 s
    "bfx_trades": "bfx_trades.jsonl",                      # every individual Bitfinex public trade
    "venue_bbo_lighter_paradex": "venue_bbo_1m.jsonl",     # Lighter + Paradex BTC bid/ask, 1 s in 1 m rows
    # DATA-GAPS-20261012 (Strategist c, g)
    "bfx_deriv_status_1s": "bfx_deriv_status_1s.jsonl",    # Bitfinex funding / mark / OI, 1 s
    "bfx_ws_events": "bfx_ws_events.jsonl",                # Bitfinex WS events + heartbeats (incl. trades hb), recv time r
    "xvp_shadow_signals": "xvp_shadow_signals.jsonl",      # cross-venue premium shadow triggers/outcomes
}

# Row timestamp keys tried in order (seconds, epoch UTC) for since/until filtering.
TS_KEYS = ("ts", "bucket_ts", "minute_ts", "s", "m", "evaluated_ts", "exit_ts", "signal_ts",
           "anchor_bucket_ts", "fill_ts", "order_ts", "decision_ts", "created_ts",
           "r")  # r: bfx_ws_events receive time (last, so it never shadows another stream's key)
# Key fields whose null rate /api/monitor/streams/health reports (sampled tail rows).
KEY_FIELDS = {
    "market_context": ("minute_ts", "spot", "funding", "oi"),
    "book_tape_1s": ("bucket_ts", "bid", "ask", "fresh"),
    "cross_venue_tape": ("minute_ts", "bfx"),
    "bfx_book_10l": ("s", "b", "a"),
    "bfx_trades": ("s", "tr"),
    "venue_bbo_lighter_paradex": ("m", "lighter", "paradex"),
    "bfx_deriv_status_1s": ("s", "mark", "fund_cur", "oi"),
    "bfx_ws_events": ("r", "ev"),
    "xvp_shadow_signals": ("evaluated_ts", "side", "premium_dev_bp"),
    "liquidations": ("ts", "venue", "liq_side"),
    "runtime_telemetry": ("ts",),
    "dyn_regime_minutes": ("minute_ts", "rv15_bp", "drift15_bp"),
}
# Live freshness limits (seconds). Master values restored (HM FAIL B1 — do not raise).
# Missing from this table AND not in RETIRED_STREAMS → UNKNOWN_THRESHOLD (RED).
# ADD only: liquidations 3600 (sparse event prints).
STALE_AFTER_SEC = {
    "market_context": 300,
    "book_tape_1s": 30,
    "cross_venue_tape": 300,
    "bfx_book_10l": 30,
    "bfx_trades": 600,
    "venue_bbo_lighter_paradex": 300,
    "bfx_deriv_status_1s": 30,
    "bfx_ws_events": 60,  # Bitfinex heartbeats every <=15 s per channel
    "runtime_telemetry": 300,
    "dyn_regime_minutes": 300,
    "liquidations": 3600,
}

# R17 / HM / R22: ticker/L1-priced or dead writers. No STALE_AFTER_SEC entry.
# Value = unix ts after which a new row is resurrection (RED). Quiet retired → RETIRED.
RETIRED_STREAMS: dict[str, float] = {
    # HM/RK 30 Oct: ticker/L1-priced — status RETIRED (writers may still exist; resurrection RED).
    "fill_markouts": 1793359800.0,
    "xvp_shadow_signals": 1793359800.0,
    "ai_shadow_challengers": 1793359800.0,
    # R22 PR-B / R17-stopped writers (custody keys stay in STREAMS).
    "taker_counterfactuals": 1792830060.0,
    "shadow_exit_paths": 1792830060.0,
    # DISLOC / DISLOC-FOI ledgers (tiles removed 23 Oct).
    "disloc_trades": 1792830060.0,
    "disloc_foi_trades": 1792830060.0,
    "disloc_paused_skips": 1792830060.0,
    "disloc_paused_skips_rot1": 1792830060.0,
}

# Watcher / expected.json severity for health status tokens (UNKNOWN_THRESHOLD → RED).
STATUS_SEVERITY = {
    "OK": "GREEN",
    "STALE": "RED",
    "NO_TS": "RED",
    "UNKNOWN_THRESHOLD": "RED",
    "MISSING": "RED",
    "RETIRED": "GREEN",
    "RETIRED_RESURRECTED": "RED",
}

ARCHIVE_NOTE = {
    "local_rotations": "<data>/runtime/<file>.N (N ascending = newer), served by ?part=N and ?since=",
    "tape_7d": "<data>/tape_1s/YYYYMMDDHH.jsonl.gz (book_tape_1s fields, 8-day retention)",
    "segment_store": "<data>/segment-store/v3 (research segment shipper; laptop mirror; laptop job backs up to R2,"
                     " receipt at /api/monitor/archive)",
}


def classify_stream_status(name: str, age, last_ts) -> tuple[str, float | None]:
    """Status precedence: RETIRED_* → NO_TS → UNKNOWN_THRESHOLD → OK/STALE.

    Missing threshold is never OK. ``age`` may be None when there is no last_ts.
    """
    if name in RETIRED_STREAMS:
        retired_since = RETIRED_STREAMS[name]
        if last_ts is not None and float(last_ts) > float(retired_since):
            return "RETIRED_RESURRECTED", None
        return "RETIRED", None
    if age is None:
        return "NO_TS", STALE_AFTER_SEC.get(name)
    limit_age = STALE_AFTER_SEC.get(name)
    if limit_age is None:
        return "UNKNOWN_THRESHOLD", None
    if age <= limit_age:
        return "OK", limit_age
    return "STALE", limit_age


def assert_stream_threshold_coverage() -> None:
    """Every STREAMS name must have a threshold or be RETIRED (set-equality)."""
    live = set(STALE_AFTER_SEC)
    retired = set(RETIRED_STREAMS)
    names = set(STREAMS)
    if live & retired:
        raise AssertionError(f"stream in both STALE_AFTER_SEC and RETIRED_STREAMS: {sorted(live & retired)}")
    missing = names - live - retired
    if missing:
        raise AssertionError(f"stream lacks threshold and RETIRED entry: {sorted(missing)}")
    extra = (live | retired) - names
    if extra:
        raise AssertionError(f"threshold/RETIRED entry for unknown stream: {sorted(extra)}")


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


# --------------------------------------------------------------------------- DATA-GAPS-20261012
def row_ts(row) -> float | None:
    if not isinstance(row, dict):
        return None
    for key in TS_KEYS:
        v = row.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1e9:
            return float(v) / 1000.0 if v > 1e12 else float(v)
    return None


def _line_ts(line: bytes) -> float | None:
    import json
    try:
        return row_ts(json.loads(line))
    except ValueError:
        return None


def parts(data_dir, name: str) -> list[dict]:
    """Rotated parts oldest -> newest, then the current file (part 0)."""
    rel = STREAMS.get(str(name))
    if rel is None:
        raise BadRequest(f"unknown stream {name!r}")
    root = Path(data_dir)
    out = []
    for p in root.glob(rel + ".*"):
        suf = p.name.rsplit(".", 1)[-1]
        if suf.isdigit():
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({"part": int(suf), "file": p.name, "bytes": st.st_size, "mtime": st.st_mtime,
                        "inode": st.st_ino})
    out.sort(key=lambda r: r["part"])
    try:
        st = (root / rel).stat()
        out.append({"part": 0, "file": rel, "bytes": st.st_size, "mtime": st.st_mtime, "inode": st.st_ino,
                    "current": True})
    except OSError:
        pass
    return out


def _edge_ts(path: Path, size: int, first: bool) -> float | None:
    try:
        with path.open("rb") as fh:
            if first:
                for _ in range(50):
                    line = fh.readline()
                    if not line:
                        return None
                    ts = _line_ts(line)
                    if ts is not None:
                        return ts
                return None
            fh.seek(max(0, size - 65536))
            lines = fh.read().splitlines()
            for line in reversed(lines):
                ts = _line_ts(line)
                if ts is not None:
                    return ts
    except OSError:
        return None
    return None


def part_bounds(data_dir, name: str, part: dict) -> dict:
    path = Path(data_dir) / part["file"]
    return {**part, "first_ts": _edge_ts(path, part["bytes"], True),
            "last_ts": _edge_ts(path, part["bytes"], False)}


def offset_for_ts(path: Path, since: float) -> int:
    """Byte offset of the first line with ts >= since (binary search; rows are append-ordered)."""
    with path.open("rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        lo, hi = 0, size
        while hi - lo > 4096:
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()
            pos = fh.tell()
            ts = None
            for _ in range(20):
                line = fh.readline()
                if not line:
                    break
                ts = _line_ts(line)
                if ts is not None:
                    break
            if ts is None or ts >= since:
                hi = mid
            else:
                lo = pos
        fh.seek(lo)
        if lo:
            fh.readline()
        while True:
            pos = fh.tell()
            line = fh.readline()
            if not line:
                return pos
            ts = _line_ts(line)
            if ts is not None and ts >= since:
                return pos


def _clip_until(body: bytes, until: float | None) -> tuple[bytes, bool]:
    if until is None or not body:
        return body, False
    out = []
    for line in body.splitlines(keepends=True):
        ts = _line_ts(line)
        if ts is not None and ts > until:
            return b"".join(out), True
        out.append(line)
    return b"".join(out), False


def read_range(data_dir, name: str, *, since=None, until=None, part=None, cursor=None,
               limit_bytes=None) -> tuple[dict, bytes]:
    """Time-ranged page across rotated parts. Feed back next_part + next_cursor until done."""
    try:
        since = float(since) if since not in (None, "") else None
        until = float(until) if until not in (None, "") else None
        limit = min(int(limit_bytes or DEFAULT_PAGE_BYTES), MAX_PAGE_BYTES)
        part = int(part) if part not in (None, "") else None
        cursor = int(cursor) if cursor not in (None, "") else None
    except (TypeError, ValueError):
        raise BadRequest("since/until must be epoch seconds; part/cursor/limit_bytes integers")
    if limit <= 0:
        raise BadRequest("limit_bytes > 0 required")
    plist = parts(data_dir, name)
    header = {"schema": "monitor_stream_range_v1", "stream": name, "since": since, "until": until}
    if not plist:
        return {**header, "exists": False, "done": True, "rows": 0}, b""
    order = [p["part"] for p in plist]
    if part is None:
        idx = 0
        if since is not None:
            idx = len(plist) - 1
            for i, p in enumerate(plist):
                last = _edge_ts(Path(data_dir) / p["file"], p["bytes"], False)
                if last is not None and last >= since:
                    idx = i
                    break
    else:
        if part not in order:
            raise BadRequest(f"unknown part {part}")
        idx = order.index(part)
    p = plist[idx]
    path = Path(data_dir) / p["file"]
    start = cursor if cursor is not None else (offset_for_ts(path, since) if since is not None else 0)
    with path.open("rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        start = min(max(0, start), size)
        fh.seek(start)
        raw = fh.read(limit)
    cut = raw.rfind(b"\n")
    body = raw[:cut + 1] if cut >= 0 else b""
    if cut < 0 and len(raw) >= limit:
        raise BadRequest("single row larger than limit_bytes; raise limit_bytes")
    nxt = start + len(body)
    body, hit_until = _clip_until(body, until)
    part_eof = nxt >= size
    last_part = idx == len(plist) - 1
    done = hit_until or (part_eof and last_part)
    next_part, next_cursor = (p["part"], nxt)
    if part_eof and not last_part and not hit_until:
        next_part, next_cursor = plist[idx + 1]["part"], 0
    return {**header, "exists": True, "part": p["part"], "file": p["file"], "cursor": start,
            "next_part": None if done else next_part, "next_cursor": None if done else next_cursor,
            "done": done, "rows": body.count(b"\n"), "bytes": len(body),
            "parts": order}, body


# Decision-log / 3factor files that are created lazily on the first qualifying event (so "missing" after a wipe is not
# by itself a fault). Exposed with mtimes so an operator can tell "never written since reset" from "stopped writing".
LAZY_LOG_FILES = (
    "decisions_3factor.csv", "blocked_signals_3factor.csv", "ai_errors_3factor.csv", "pipeline_events_3factor.csv",
    "ai_tranche_log.csv", "setup_log_3factor.csv", "trades_3factor.csv", "approved_but_rejected.jsonl",
    "near_miss.jsonl", "soft_reject_shadow.jsonl",
)


def file_mtimes(data_dir, now: float) -> dict:
    """Never raises: an observability add-on must not take /streams/health down ({} on any failure).

    ``age_sec`` is relative to ``now`` = the moment the response was computed; /api/monitor/streams/health caches that
    response for 15 s, so a served age_sec can lag real age by up to 15 s (use cached_at/server_ts to correct)."""
    try:
        return _file_mtimes(data_dir, now)
    except Exception:  # noqa: BLE001
        return {}


def _file_mtimes(data_dir, now: float) -> dict:
    out = {}
    base = Path(data_dir)
    for rel in LAZY_LOG_FILES:
        for cand in (base / rel, base / "runtime" / rel):
            try:
                st = cand.stat()
            except OSError:
                continue
            out[rel] = {"exists": True, "mtime": st.st_mtime, "age_sec": round(now - st.st_mtime, 1), "bytes": st.st_size}
            break
        else:
            out[rel] = {"exists": False, "mtime": None, "age_sec": None, "bytes": 0}
    return out


def health(data_dir, now: float, sample_rows: int = 200) -> dict:
    """Per stream: first/last ts across parts, age, approx rows, key-field null rate, archive."""
    import json
    out = {}
    for name, rel in STREAMS.items():
        try:
            plist = parts(data_dir, name)
        except BadRequest:
            plist = []
        if not plist:
            if name in RETIRED_STREAMS:
                out[name] = {"file": rel, "exists": False, "status": "RETIRED",
                             "severity": STATUS_SEVERITY["RETIRED"],
                             "stale_after_sec": None,
                             "retired_since_ts": RETIRED_STREAMS[name]}
            else:
                limit = STALE_AFTER_SEC.get(name)
                status = "MISSING" if limit is not None else "UNKNOWN_THRESHOLD"
                out[name] = {"file": rel, "exists": False, "status": status,
                             "severity": STATUS_SEVERITY.get(status, "RED"),
                             "stale_after_sec": limit}
            continue
        first = part_bounds(data_dir, name, plist[0])
        cur = part_bounds(data_dir, name, plist[-1])
        total_bytes = sum(p["bytes"] for p in plist)
        tail = []
        try:
            with (Path(data_dir) / plist[-1]["file"]).open("rb") as fh:
                fh.seek(max(0, plist[-1]["bytes"] - 512 * 1024))
                lines = fh.read().splitlines()[1:]
            for line in lines[-sample_rows:]:
                try:
                    tail.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            pass
        avg = (sum(len(json.dumps(r)) + 1 for r in tail) / len(tail)) if tail else None
        nulls = {}
        for key in KEY_FIELDS.get(name, ()):
            if tail:
                missing = sum(1 for r in tail if isinstance(r, dict) and r.get(key) in (None, "", [], {}))
                nulls[key] = round(missing / len(tail), 4)
        last_ts = cur.get("last_ts")
        age = round(now - last_ts, 1) if last_ts else None
        status, limit_age = classify_stream_status(name, age, last_ts)
        retired_since = RETIRED_STREAMS.get(name)
        out[name] = {
            "file": rel, "exists": True, "status": status, "stale_after_sec": limit_age,
            "severity": STATUS_SEVERITY.get(status, "RED"),
            "first_ts": first.get("first_ts"), "last_ts": last_ts,
            "age_sec": age, "bytes": total_bytes, "parts": len(plist),
            "rows_estimate": int(total_bytes / avg) if avg else None,
            "rows_estimate_basis": "bytes / mean tail row size",
            "key_field_null_rate": nulls, "null_rate_sample_rows": len(tail),
            **({"retired_since_ts": retired_since} if retired_since is not None else {}),
            "archive": {"local_parts": [p["file"] for p in plist],
                        **({"tape_7d": ARCHIVE_NOTE["tape_7d"]} if name == "book_tape_1s" else {}),
                        "segment_store": ARCHIVE_NOTE["segment_store"]},
        }
    return {"schema": "monitor_stream_health_v1", "streams": out, "archive_locations": ARCHIVE_NOTE,
            "status_severity": dict(STATUS_SEVERITY),
            "log_files": file_mtimes(data_dir, now), "server_ts": now}
