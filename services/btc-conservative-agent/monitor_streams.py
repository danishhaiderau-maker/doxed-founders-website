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
    "dyn_regime_minutes": "dyn_regime_minutes.jsonl",      # Tile 15 DYN-AF shadow classifier
    # Retired DISLOC / DISLOC-FOI ledgers (Tiles 6/7 removed 23 Oct 2026, R9): no writer remains; listed so the
    # historical files stay readable and archived (R5 custody). Do not delete.
    "disloc_trades": "disloc_trades.jsonl",                # Tile 13 DISLOC paper orders/fills/cancels/closes
    "disloc_foi_trades": "disloc_foi_trades.jsonl",        # Tile 14 DISLOC-FOI paper ledger
    "disloc_paused_skips": "disloc_paused_skips.jsonl",            # former G14 PAUSED-skip ledger
    "disloc_paused_skips_rot1": "disloc_paused_skips.jsonl.1",     # its one rotation (archived too)
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
# Continuous streams only; event streams (xvp, liquidations, ledgers) may be quiet legitimately.
STALE_AFTER_SEC = {"market_context": 300, "book_tape_1s": 30, "cross_venue_tape": 300, "bfx_book_10l": 30,
                   "bfx_trades": 600, "venue_bbo_lighter_paradex": 300, "bfx_deriv_status_1s": 30,
                   "bfx_ws_events": 60,  # Bitfinex heartbeats every <=15 s per channel
                   "runtime_telemetry": 300, "dyn_regime_minutes": 300}
ARCHIVE_NOTE = {
    "local_rotations": "<data>/runtime/<file>.N (N ascending = newer), served by ?part=N and ?since=",
    "tape_7d": "<data>/tape_1s/YYYYMMDDHH.jsonl.gz (book_tape_1s fields, 8-day retention)",
    "segment_store": "<data>/segment-store/v3 (research segment shipper; laptop mirror; laptop job backs up to R2,"
                     " receipt at /api/monitor/archive)",
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


# ---- #808 tape_freeze: how often the 1 s tape repeats a stale ticker quote while trades print outside it ------------
# Monitoring only: never gates, never writes, nothing kept after the call. Detects the #807 gap (triggers and the
# limit-touch test still read ticker bid/ask), it does not fix it.
TAPE_FREEZE_STREAM = "book_tape_1s"            # market_microstructure_1s.jsonl (+ rotated parts)
TAPE_FREEZE_WINDOW_S = 3600
TAPE_FREEZE_MAX_BYTES = 4 * 1024 * 1024        # hard read cap per call; `truncated` reports when it stopped the read
TAPE_FREEZE_PAGE_BYTES = 512 * 1024            # one page in memory at a time
TAPE_FREEZE_OUTSIDE_BP = 2.5                   # a print > 2.5 bp beyond the quoted bid/ask (~$20 at 80k) is "outside"
TAPE_FREEZE_MAX_GAP_S = 2.0                    # consecutive rows further apart than this are not compared
TAPE_FREEZE_CACHE_S = 300.0                    # bot.py caches tape_freeze separately from the 15 s health cache


def _fnum(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _tape_freeze_null(reason: str, base: dict) -> dict:
    return {**base, "reason": reason, "unchanged_bbo_pct": None, "source_ts_unchanged_pct": None,
            "frozen_s": None, "frozen_s_per_h": None, "longest_frozen_run_s": None,
            "longest_unchanged_run_s": None, "outside_print_count": None}


def tape_freeze(data_dir, now: float, window_s: float = TAPE_FREEZE_WINDOW_S,
                max_bytes: int = TAPE_FREEZE_MAX_BYTES, page_bytes: int = TAPE_FREEZE_PAGE_BYTES) -> dict:
    """Stream the last ``window_s`` of the 1 s tape (across rotated parts) with O(1) state.

    Only rows with valid_bbo True count. A row is compared with the previous valid row when they are at most
    TAPE_FREEZE_MAX_GAP_S apart:
      unchanged    bid and ask identical to the previous valid row
      source_same  source_ts (ticker timestamp) identical: no new ticker message, vs. a message repeating the quote
      outside      trade_high > ask*(1+bp) or trade_low < bid*(1-bp), bp = TAPE_FREEZE_OUTSIDE_BP (any valid row)
      frozen       unchanged AND outside: the quote did not move although the market traded through it
    Never raises; missing/empty tape -> null values with a reason (never 0 %)."""
    import json
    since = float(now) - float(window_s)
    base = {"schema": "tape_freeze_v1", "stream": TAPE_FREEZE_STREAM, "file": STREAMS[TAPE_FREEZE_STREAM],
            "window_s": float(window_s), "since": since, "until": float(now), "outside_bp": TAPE_FREEZE_OUTSIDE_BP,
            "max_gap_s": TAPE_FREEZE_MAX_GAP_S, "max_bytes": int(max_bytes), "computed_at": float(now),
            "rows_seen": 0, "valid_rows": 0, "compared_pairs": 0, "coverage": None, "bytes_read": 0,
            "truncated": False, "monitoring_only": True}
    try:
        if not parts(data_dir, TAPE_FREEZE_STREAM):
            return _tape_freeze_null("TAPE_MISSING", base)
        rows_seen = valid_rows = pairs = unchanged = source_same = outside = frozen = 0
        run_frozen = run_unchanged = best_frozen = best_unchanged = 0
        prev = None                      # (bucket_ts, bid, ask, source_ts) of the previous valid row
        bytes_read, part, cursor, truncated = 0, None, None, False
        while True:
            remaining = int(max_bytes) - bytes_read
            if remaining <= 0:
                truncated = True
                break
            try:
                header, body = read_range(data_dir, TAPE_FREEZE_STREAM, since=since, until=now, part=part,
                                          cursor=cursor, limit_bytes=min(int(page_bytes), remaining))
            except BadRequest:
                if remaining < int(page_bytes):      # a row did not fit in what is left of the cap
                    truncated = True
                    break
                raise
            bytes_read += header.get("bytes") or 0
            for line in body.splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = _fnum(row.get("bucket_ts")) if isinstance(row, dict) else None
                if ts is None or ts < since or ts > now:
                    continue
                rows_seen += 1
                bid, ask = _fnum(row.get("bid")), _fnum(row.get("ask"))
                if row.get("valid_bbo") is not True or bid is None or ask is None or bid <= 0 or ask < bid:
                    prev, run_frozen, run_unchanged = None, 0, 0
                    continue
                valid_rows += 1
                src = _fnum(row.get("source_ts"))
                hi, lo = _fnum(row.get("trade_high")), _fnum(row.get("trade_low"))
                k = TAPE_FREEZE_OUTSIDE_BP / 1e4
                is_out = (hi is not None and hi > ask * (1 + k)) or (lo is not None and lo < bid * (1 - k))
                outside += int(bool(is_out))
                if prev is not None and 0 < ts - prev[0] <= TAPE_FREEZE_MAX_GAP_S:
                    pairs += 1
                    same = bid == prev[1] and ask == prev[2]
                    unchanged += int(same)
                    source_same += int(src is not None and src == prev[3])
                    run_unchanged = run_unchanged + 1 if same else 0
                    is_frozen = same and is_out
                    frozen += int(is_frozen)
                    run_frozen = run_frozen + 1 if is_frozen else 0
                else:
                    run_frozen = run_unchanged = 0
                best_frozen, best_unchanged = max(best_frozen, run_frozen), max(best_unchanged, run_unchanged)
                prev = (ts, bid, ask, src)
            body = None
            if header.get("done"):
                break
            part, cursor = header.get("next_part"), header.get("next_cursor")
        base.update(rows_seen=rows_seen, valid_rows=valid_rows, compared_pairs=pairs, bytes_read=bytes_read,
                    truncated=truncated, coverage=round(rows_seen / float(window_s), 4) if window_s else None)
        if rows_seen == 0:
            return _tape_freeze_null("TAPE_EMPTY_IN_WINDOW", base)
        if valid_rows == 0:
            return _tape_freeze_null("NO_VALID_BBO_ROWS", base)
        out = {**base, "reason": None if pairs else "NO_COMPARABLE_PAIRS",
               "unchanged_bbo_pct": round(100.0 * unchanged / pairs, 3) if pairs else None,
               "source_ts_unchanged_pct": round(100.0 * source_same / pairs, 3) if pairs else None,
               "frozen_s": frozen if pairs else None,
               "frozen_s_per_h": round(frozen * 3600.0 / pairs, 2) if pairs else None,
               "frozen_s_per_h_basis": "frozen seconds per hour of compared valid seconds",
               "longest_frozen_run_s": best_frozen if pairs else None,
               "longest_unchanged_run_s": best_unchanged if pairs else None,
               "outside_print_count": outside}
        return out
    except Exception as exc:  # noqa: BLE001 - observability must not take /streams/health down
        return _tape_freeze_null(f"ERROR:{type(exc).__name__}", base)


_COMPUTE = object()


def health(data_dir, now: float, sample_rows: int = 200, tape_freeze_result=_COMPUTE) -> dict:
    """Per stream: first/last ts across parts, age, approx rows, key-field null rate, archive.

    ``tape_freeze_result``: bot.py passes its separately cached (300 s) tape_freeze dict; computed when omitted."""
    import json
    out = {}
    for name, rel in STREAMS.items():
        try:
            plist = parts(data_dir, name)
        except BadRequest:
            plist = []
        if not plist:
            out[name] = {"file": rel, "exists": False, "status": "MISSING"}
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
        limit_age = STALE_AFTER_SEC.get(name)
        status = "OK" if age is not None and (limit_age is None or age <= limit_age) else (
            "STALE" if age is not None else "NO_TS")
        out[name] = {
            "file": rel, "exists": True, "status": status, "stale_after_sec": limit_age, "first_ts": first.get("first_ts"), "last_ts": last_ts,
            "age_sec": age, "bytes": total_bytes, "parts": len(plist),
            "rows_estimate": int(total_bytes / avg) if avg else None,
            "rows_estimate_basis": "bytes / mean tail row size",
            "key_field_null_rate": nulls, "null_rate_sample_rows": len(tail),
            "archive": {"local_parts": [p["file"] for p in plist],
                        **({"tape_7d": ARCHIVE_NOTE["tape_7d"]} if name == "book_tape_1s" else {}),
                        "segment_store": ARCHIVE_NOTE["segment_store"]},
        }
    if tape_freeze_result is _COMPUTE:
        tape_freeze_result = tape_freeze(data_dir, now)
    return {"schema": "monitor_stream_health_v1", "streams": out, "archive_locations": ARCHIVE_NOTE,
            "log_files": file_mtimes(data_dir, now), "tape_freeze": tape_freeze_result, "server_ts": now}
