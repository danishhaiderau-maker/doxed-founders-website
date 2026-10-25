"""R5 keep sets (HM C7; RK 23 Oct: "keep only for open lifecycles or prereg tests").

One read-only helper shared by the bot's r5_retention_once and the segment
shipper's r5_segment_pass:

* ``open_lifecycles(runtime_root)`` - lifecycles still open in the
  lifecycle_pipeline index (``dirty_lifecycle`` = not yet terminal/bundled,
  ``qualification_retry`` = qualification pending). Returns their
  identity ids, the market-segment shas their ledger
  rows reference (the ``cap_market_segments`` referenced set), and the
  earliest open-lifecycle start.
* ``prereg_window_start()`` - the FADE3 prereg window start, from
  ``R5_PREREG_KEEP_SINCE_TS`` (unix seconds). Unset today: FADE3 prereg reads
  only bfx_book rows and trade prints, never bundles or segments.
* ``segment_keep_seqs(store_root, prefix, since_ts)`` - shipped segment seqs
  whose manifest window ends at/after ``since_ts`` (binary search over the
  seq-ordered manifests, so only ~log2(n) manifests are read).

Keep sets only narrow AGE retention below the cap; the cap pass still goes
oldest first regardless (R5: no custody/keep gate at the cap). Read-only:
the index is opened ``mode=ro``; nothing here writes or deletes.
PRIME RULE: neutral (no fill/exit/tile input).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

OPEN_MAX_KEYS = 500
OPEN_MAX_ROWS = 5000
PREREG_ENV = "R5_PREREG_KEEP_SINCE_TS"
_KEY_FIELDS = ("collection_epoch_id", "episode_id", "policy_signature", "research_lane")


def identity_id(key: dict[str, str]) -> str:
    """Same as lifecycle_bundles.LifecycleKey.identity_id (canonical JSON sha256)."""
    material = json.dumps({k: key[k] for k in _KEY_FIELDS}, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True)
    return "lifecycle-identity-" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _index_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"


def _row_ts(row: dict[str, Any]) -> float | None:
    for name in ("observed_ts", "ts", "signal_ts"):
        try:
            value = float(row.get(name))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _segment_shas(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for field, value in row.items():
        name = str(field)
        refs = value if name.endswith("segment_refs") and isinstance(value, list) else (
            [value] if name.endswith("segment_ref") and isinstance(value, dict) else [])
        for ref in refs:
            if isinstance(ref, dict):
                sha = str(ref.get("sha256") or "").lower()
                if len(sha) == 64:
                    out.add(sha)
    return out


def open_lifecycles(runtime_root: str | os.PathLike, *, max_keys: int = OPEN_MAX_KEYS,
                    max_rows: int = OPEN_MAX_ROWS) -> dict[str, Any]:
    """Open lifecycles from the pipeline index. Never raises; empty when no index."""
    root = Path(runtime_root)
    out: dict[str, Any] = {"open_count": 0, "identity_ids": set(),
                           "segment_shas": set(), "earliest_start_ts": None, "truncated": False, "error": None}
    database = _index_path(root)
    if not database.is_file():
        return out
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}"
        return out
    try:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        selects = [f"SELECT {', '.join(_KEY_FIELDS)} FROM {t}"
                   for t in ("dirty_lifecycle", "qualification_retry") if t in tables]
        if not selects:
            return out
        keys = connection.execute(" UNION ".join(selects) + " LIMIT ?", (int(max_keys) + 1,)).fetchall()
        out["truncated"] = len(keys) > max_keys
        keys = keys[:max_keys]
        out["open_count"] = len(keys)
        rows_read = 0
        handles: dict[str, Any] = {}
        earliest = None
        try:
            for values in keys:
                key = dict(zip(_KEY_FIELDS, (str(v) for v in values)))
                ident = identity_id(key)
                out["identity_ids"].add(ident)
                if "lifecycle_event" not in tables or rows_read >= max_rows:
                    out["truncated"] = out["truncated"] or rows_read >= max_rows
                    continue
                events = connection.execute(
                    "SELECT ledger, byte_offset, row_length FROM lifecycle_event WHERE collection_epoch_id=? "
                    "AND episode_id=? AND policy_signature=? AND research_lane=? ORDER BY ledger, byte_offset LIMIT ?",
                    (*key.values(), int(max_rows - rows_read))).fetchall()
                for ledger, offset, length in events:
                    rows_read += 1
                    handle = handles.get(ledger)
                    if handle is None:
                        try:
                            handle = (root / "v3" / "ledgers" / f"{ledger}.jsonl").open("rb")
                        except OSError:
                            continue
                        handles[ledger] = handle
                    try:
                        handle.seek(int(offset))
                        row = json.loads(handle.read(int(length)).decode("utf-8"))
                    except (OSError, ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    ts = _row_ts(row)
                    if ts is not None and (earliest is None or ts < earliest):
                        earliest = ts
                    out["segment_shas"] |= _segment_shas(row)
        finally:
            for handle in handles.values():
                handle.close()
        out["earliest_start_ts"] = earliest
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}"
    finally:
        connection.close()
    return out


def prereg_window_start(environ: dict[str, str] | None = None) -> float | None:
    raw = (environ if environ is not None else os.environ).get(PREREG_ENV, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def keep_since_ts(open_info: dict[str, Any] | None, environ: dict[str, str] | None = None) -> float | None:
    starts = [v for v in ((open_info or {}).get("earliest_start_ts"), prereg_window_start(environ))
              if v is not None]
    return min(starts) if starts else None


def _window_end(man_dir: Path, token: str) -> float | None:
    try:
        return float(json.loads((man_dir / f"{token}.json").read_text("utf-8")).get("window_end"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def segment_keep_seqs(store_root: str | os.PathLike, prefix: str, since_ts: float | None, *,
                      shipped_seq: int | None = None) -> set[int]:
    """Seqs whose segment window ends at/after ``since_ts`` (covers the open window)."""
    if since_ts is None:
        return set()
    base = Path(store_root).joinpath(*prefix.split("/"))
    seg_dir, man_dir = base / "seg", base / "man"
    try:
        tokens = sorted((int(t), t) for t in (n.removesuffix(".tar.gz") for n in os.listdir(seg_dir)
                                               if n.endswith(".tar.gz")) if t.isdigit())
    except OSError:
        return set()
    if shipped_seq is not None:
        tokens = [item for item in tokens if item[0] <= int(shipped_seq)]
    lo, hi = 0, len(tokens)
    while lo < hi:  # first seq whose window_end >= since_ts; unreadable manifests count as "keep"
        mid = (lo + hi) // 2
        end = _window_end(man_dir, tokens[mid][1])
        if end is not None and end < since_ts:
            lo = mid + 1
        else:
            hi = mid
    return {seq for seq, _ in tokens[lo:]}


def summary(open_info: dict[str, Any], since_ts: float | None, keep_seqs: set[int] | None = None) -> dict:
    return {"open_lifecycles": open_info.get("open_count", 0), "referenced_segments": len(open_info.get("segment_shas") or ()),
            "keep_since_ts": since_ts, "keep_seqs": len(keep_seqs or ()),
            "truncated": bool(open_info.get("truncated")), "error": open_info.get("error"),
            "at": round(time.time(), 3)}
