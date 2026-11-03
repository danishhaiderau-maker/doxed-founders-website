"""Local public market persist: SQLite + optional Parquet + gap log.

Day-1 fresh start after Fly delete. Public Bitfinex book/trades only — no API keys.

Strategist rules:
* Gap log records collector disconnects **and** laptop sleep.
* Gaps are **never filled** (no synthetic book/trade rows for missing seconds).
* Local series starts at SERIES BREAK; Fly-era data stays closed.

HM B3: disk-free guard + retention/rotation for SQLite and ``gap_log.jsonl``.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from local_fresh_runtime import (
    ensure_disk_headroom,
    ensure_series_break,
    min_disk_free_bytes,
)

SCHEMA = "local_market_persist_v1"
GAP_LOG_SCHEMA = "local_market_gap_log_v1"
GAP_LOG_NAME = "gap_log.jsonl"
SQLITE_NAME = "local_market.sqlite3"
PARQUET_BOOK_DIR = "parquet" + os.sep + "book_1s"
PARQUET_TRADES_DIR = "parquet" + os.sep + "trades"

# Retention defaults (override via env).
DEFAULT_GAP_LOG_MAX_BYTES = 50 * 1024 * 1024  # 50 MiB
# RK (C) / R51 BLOCKER: default 0 = **no SQLite purge** so fresh local data
# cannot be lost before the analyzer reads it. Set a positive seconds value
# only with a lossless archive path. Parquet remains optional (pyarrow).
DEFAULT_SQLITE_RETENTION_SECONDS = 0
DEFAULT_RETENTION_EVERY_N_WRITES = 500
# RK 20:20 (#972 REV7): pruning off with no limit is a FAIL. Cap store growth
# fail-closed + alert while purge is disabled (override via env).
DEFAULT_STORE_MAX_BYTES = 20 * 1024 * 1024 * 1024  # 20 GiB

_LOG = logging.getLogger("local_market_persist")


class StoreGrowthCapError(RuntimeError):
    """Raised when local store size exceeds the fail-closed growth cap (purge off)."""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class DiskFullError(RuntimeError):
    """Raised when free disk is below the local-fresh minimum."""


class MarketStore:
    """SQLite primary store; Parquet best-effort when pyarrow is installed.

    Never synthesizes missing seconds — gaps are logged only.
    """

    def __init__(self, data_dir: str | Path, *, clock=time.time) -> None:
        self.root = Path(data_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        # P5: series-start stamp uses the same clock as the store (tests + soak).
        self.series_break = ensure_series_break(self.root, now=float(self.clock()))
        self.sqlite_path = self.root / SQLITE_NAME
        self.gap_log_path = self.root / GAP_LOG_NAME
        self._conn = _connect(self.sqlite_path)
        self._init_schema()
        self._last_book_sec: Optional[int] = None
        self._last_trade_sec: Optional[int] = None
        self._writes_since_retention = 0
        self._parquet_ok = self._probe_parquet()
        self._disk_guard()

    @staticmethod
    def _probe_parquet() -> bool:
        try:
            import pyarrow  # noqa: F401
            import pyarrow.parquet  # noqa: F401
            return True
        except Exception as exc:  # noqa: BLE001 — optional dependency
            _LOG.warning(
                "LOCAL_FRESH_IMPORT_FAILED pyarrow (parquet disabled): %s: %s",
                type(exc).__name__,
                exc,
            )
            return False

    def _store_bytes(self) -> int:
        total = 0
        for path in (self.sqlite_path, self.gap_log_path):
            try:
                if path.is_file():
                    total += int(path.stat().st_size)
            except OSError:
                pass
        # WAL/SHM siblings (if present) count toward the growth cap.
        for suffix in ("-wal", "-shm"):
            sibling = Path(str(self.sqlite_path) + suffix)
            try:
                if sibling.is_file():
                    total += int(sibling.stat().st_size)
            except OSError:
                pass
        return total

    def store_max_bytes(self) -> int:
        return _env_int("LOCAL_FRESH_STORE_MAX_BYTES", DEFAULT_STORE_MAX_BYTES)

    def _disk_guard(self) -> None:
        ok, free, minimum = ensure_disk_headroom(self.root)
        if not ok:
            raise DiskFullError(
                f"DISK_FREE_BELOW_MIN free={free} min={minimum} path={self.root}"
            )
        # RK 20:20: while SQLite purge is off, enforce a fail-closed growth cap.
        purge_on = (
            _env_int(
                "LOCAL_FRESH_SQLITE_RETENTION_SECONDS", DEFAULT_SQLITE_RETENTION_SECONDS
            )
            > 0
        )
        if purge_on:
            return
        used = self._store_bytes()
        cap = self.store_max_bytes()
        if cap > 0 and used >= cap:
            _LOG.error(
                "LOCAL_FRESH_STORE_GROWTH_CAP_EXCEEDED used=%s cap=%s path=%s",
                used,
                cap,
                self.root,
            )
            raise StoreGrowthCapError(
                f"STORE_GROWTH_CAP_EXCEEDED used={used} cap={cap} path={self.root}"
            )
        # Alert at 80% so operators can archive (lossless Parquet) before hard fail.
        if cap > 0 and used >= int(cap * 0.8):
            _LOG.warning(
                "LOCAL_FRESH_STORE_GROWTH_ALERT used=%s cap=%s pct=%.1f path=%s",
                used,
                cap,
                100.0 * used / cap,
                self.root,
            )

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS book_1s (
              s INTEGER PRIMARY KEY,
              bid REAL,
              ask REAL,
              receive_ts REAL,
              carried INTEGER DEFAULT 0,
              payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS trades (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              s INTEGER NOT NULL,
              trade_id INTEGER,
              mts_ms INTEGER,
              amount REAL,
              price REAL,
              receive_ts REAL
            );
            CREATE INDEX IF NOT EXISTS idx_trades_s ON trades(s);
            CREATE TABLE IF NOT EXISTS gap_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              start_s INTEGER NOT NULL,
              end_s INTEGER NOT NULL,
              missing_seconds INTEGER NOT NULL,
              ts REAL NOT NULL,
              detail_json TEXT
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def append_book_second(self, row: Mapping[str, Any]) -> None:
        self._disk_guard()
        s = int(row["s"])
        bid = row.get("bid")
        ask = row.get("ask")
        if bid is None and isinstance(row.get("b"), list) and row["b"]:
            bid = float(row["b"][0][0])
        if ask is None and isinstance(row.get("a"), list) and row["a"]:
            ask = float(row["a"][0][0])
        receive_ts = row.get("receive_ts")
        carried = 1 if row.get("carried") else 0
        # Strategist: never fill gaps — only persist the observed second.
        payload = json.dumps(dict(row), separators=(",", ":"), default=str)
        self._conn.execute(
            "INSERT OR REPLACE INTO book_1s(s, bid, ask, receive_ts, carried, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (s, bid, ask, receive_ts, carried, payload),
        )
        self._conn.commit()
        self._note_book_gap(s)
        self._last_book_sec = s
        self._maybe_parquet_book(row)
        self._maybe_retention()

    def append_trades_second(self, row: Mapping[str, Any]) -> None:
        self._disk_guard()
        s = int(row["s"])
        receive_ts = row.get("receive_ts")
        trades = row.get("tr") or []
        for item in trades:
            if not isinstance(item, (list, tuple)) or len(item) < 4:
                continue
            self._conn.execute(
                "INSERT INTO trades(s, trade_id, mts_ms, amount, price, receive_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (s, int(item[0]), int(item[1]), float(item[2]), float(item[3]), receive_ts),
            )
        self._conn.commit()
        self._note_trade_gap(s)
        self._last_trade_sec = s
        self._maybe_parquet_trades(row)
        self._maybe_retention()

    def _note_book_gap(self, s: int) -> None:
        if self._last_book_sec is None:
            return
        if s <= self._last_book_sec:
            return
        missing = s - self._last_book_sec - 1
        if missing <= 0:
            return
        start = self._last_book_sec + 1
        end = s - 1
        # Never insert synthetic book rows for [start, end].
        self._write_gap("BOOK_1S", start, end, missing, detail={"filled": False})

    def _note_trade_gap(self, s: int) -> None:
        # Trade seconds are sparse by nature; only log multi-second holes after a
        # prior print second when the hole is >= 2 s (same hole rule as book).
        if self._last_trade_sec is None:
            return
        if s <= self._last_trade_sec:
            return
        missing = s - self._last_trade_sec - 1
        if missing < 2:
            return
        self._write_gap(
            "TRADE_SEC",
            self._last_trade_sec + 1,
            s - 1,
            missing,
            detail={"filled": False},
        )

    def log_explicit_gap(
        self,
        kind: str,
        start_s: int,
        end_s: int,
        *,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        missing = max(0, int(end_s) - int(start_s) + 1)
        if missing <= 0:
            return
        meta = dict(detail or {})
        meta.setdefault("filled", False)
        self._write_gap(kind, int(start_s), int(end_s), missing, detail=meta)

    def _write_gap(
        self,
        kind: str,
        start_s: int,
        end_s: int,
        missing: int,
        *,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._disk_guard()
        ts = float(self.clock())
        detail_json = json.dumps(dict(detail or {}), separators=(",", ":"))
        self._conn.execute(
            "INSERT INTO gap_events(kind, start_s, end_s, missing_seconds, ts, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, start_s, end_s, missing, ts, detail_json),
        )
        self._conn.commit()
        event = {
            "schema": GAP_LOG_SCHEMA,
            "kind": kind,
            "start_s": start_s,
            "end_s": end_s,
            "missing_seconds": missing,
            "ts": ts,
            "detail": dict(detail or {}),
            "filled": False,
        }
        self.gap_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.gap_log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        self._maybe_retention()

    def missing_book_seconds(self, start_s: int, end_s: int) -> list[int]:
        rows = self._conn.execute(
            "SELECT s FROM book_1s WHERE s BETWEEN ? AND ? ORDER BY s",
            (int(start_s), int(end_s)),
        ).fetchall()
        present = {int(r["s"]) for r in rows}
        return [s for s in range(int(start_s), int(end_s) + 1) if s not in present]

    def stats(self) -> dict[str, Any]:
        book_n = self._conn.execute("SELECT COUNT(*) AS n FROM book_1s").fetchone()["n"]
        trade_n = self._conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        gap_n = self._conn.execute("SELECT COUNT(*) AS n FROM gap_events").fetchone()["n"]
        gap_bytes = self.gap_log_path.stat().st_size if self.gap_log_path.is_file() else 0
        sqlite_bytes = self.sqlite_path.stat().st_size if self.sqlite_path.is_file() else 0
        ok, free, minimum = ensure_disk_headroom(self.root)
        return {
            "schema": SCHEMA,
            "sqlite_path": str(self.sqlite_path),
            "gap_log_path": str(self.gap_log_path),
            "book_1s_rows": int(book_n),
            "trade_rows": int(trade_n),
            "gap_events": int(gap_n),
            "gap_log_bytes": int(gap_bytes),
            "sqlite_bytes": int(sqlite_bytes),
            "parquet_enabled": bool(self._parquet_ok),
            "sqlite_purge_enabled": _env_int(
                "LOCAL_FRESH_SQLITE_RETENTION_SECONDS", DEFAULT_SQLITE_RETENTION_SECONDS
            )
            > 0,
            "local_epoch_id": (self.series_break or {}).get("local_epoch_id"),
            "series_start_ts": (self.series_break or {}).get("series_start_ts"),
            "last_book_sec": self._last_book_sec,
            "last_trade_sec": self._last_trade_sec,
            "series_break_ts": (self.series_break or {}).get("series_break_ts"),
            "gaps_never_filled": True,
            "disk_free_bytes": free,
            "disk_min_free_bytes": minimum,
            "disk_headroom_ok": ok,
            "store_bytes": int(sqlite_bytes + gap_bytes),
            "store_max_bytes": self.store_max_bytes(),
            "store_growth_cap_enforced": not bool(
                _env_int(
                    "LOCAL_FRESH_SQLITE_RETENTION_SECONDS",
                    DEFAULT_SQLITE_RETENTION_SECONDS,
                )
                > 0
            ),
        }

    def _maybe_retention(self) -> None:
        self._writes_since_retention += 1
        every = _env_int("LOCAL_FRESH_RETENTION_EVERY_N_WRITES", DEFAULT_RETENTION_EVERY_N_WRITES)
        if self._writes_since_retention < every:
            return
        self._writes_since_retention = 0
        self.run_retention()

    def run_retention(self) -> dict[str, Any]:
        """Rotate/prune gap_log and old SQLite rows. Never deletes trade evidence outside retention window."""
        report: dict[str, Any] = {"gap_log": None, "sqlite": None}
        report["gap_log"] = self._rotate_gap_log()
        report["sqlite"] = self._prune_sqlite()
        return report

    def _rotate_gap_log(self) -> dict[str, Any]:
        max_bytes = _env_int("LOCAL_FRESH_GAP_LOG_MAX_BYTES", DEFAULT_GAP_LOG_MAX_BYTES)
        if not self.gap_log_path.is_file():
            return {"rotated": False, "reason": "absent"}
        size = self.gap_log_path.stat().st_size
        if size <= max_bytes:
            return {"rotated": False, "bytes": size, "max_bytes": max_bytes}
        # Keep the trailing half of the file (newest lines).
        data = self.gap_log_path.read_bytes()
        keep = data[len(data) // 2 :]
        # Align to next newline so we do not split a JSONL record.
        nl = keep.find(b"\n")
        if nl >= 0:
            keep = keep[nl + 1 :]
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
        archive = self.gap_log_path.with_name(f"gap_log.{stamp}.jsonl")
        os.replace(self.gap_log_path, archive)
        self.gap_log_path.write_bytes(keep)
        return {
            "rotated": True,
            "archive": str(archive),
            "kept_bytes": len(keep),
            "prior_bytes": size,
            "max_bytes": max_bytes,
        }

    def _prune_sqlite(self) -> dict[str, Any]:
        retain_s = _env_int(
            "LOCAL_FRESH_SQLITE_RETENTION_SECONDS", DEFAULT_SQLITE_RETENTION_SECONDS
        )
        # RK (C): retain_s <= 0 → no DELETE (analyzer must see all fresh rows).
        if retain_s <= 0:
            used = self._store_bytes()
            cap = self.store_max_bytes()
            return {
                "cutoff_s": None,
                "book_deleted": 0,
                "trade_deleted": 0,
                "gap_deleted": 0,
                "retain_seconds": retain_s,
                "purge": False,
                "reason": "LOCAL_FRESH_SQLITE_PURGE_DISABLED",
                "min_disk_free_bytes": min_disk_free_bytes(),
                "store_bytes": used,
                "store_max_bytes": cap,
                "store_growth_cap_enforced": True,
                "alert": bool(cap > 0 and used >= int(cap * 0.8)),
            }
        now_s = int(self.clock())
        cutoff = now_s - retain_s
        cur = self._conn.cursor()
        book_del = cur.execute("DELETE FROM book_1s WHERE s < ?", (cutoff,)).rowcount
        trade_del = cur.execute("DELETE FROM trades WHERE s < ?", (cutoff,)).rowcount
        # Keep gap_events for soak read-back; only drop ancient ones beyond 2x retention.
        gap_del = cur.execute(
            "DELETE FROM gap_events WHERE end_s < ?", (cutoff - retain_s,)
        ).rowcount
        self._conn.commit()
        try:
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        return {
            "cutoff_s": cutoff,
            "book_deleted": int(book_del or 0),
            "trade_deleted": int(trade_del or 0),
            "gap_deleted": int(gap_del or 0),
            "retain_seconds": retain_s,
            "purge": True,
            "min_disk_free_bytes": min_disk_free_bytes(),
        }

    def _maybe_parquet_book(self, row: Mapping[str, Any]) -> None:
        if not self._parquet_ok:
            return
        self._append_parquet_row(PARQUET_BOOK_DIR, row)

    def _maybe_parquet_trades(self, row: Mapping[str, Any]) -> None:
        if not self._parquet_ok:
            return
        self._append_parquet_row(PARQUET_TRADES_DIR, row)

    def _append_parquet_row(self, relative_dir: str, row: Mapping[str, Any]) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "LOCAL_FRESH_IMPORT_FAILED pyarrow during append: %s: %s",
                type(exc).__name__,
                exc,
            )
            self._parquet_ok = False
            return
        target_dir = self.root / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        s = int(row.get("s") or 0)
        hour = s - (s % 3600) if s else 0
        path = target_dir / f"part-{hour}.parquet"
        table = pa.Table.from_pylist([dict(row)])
        if path.is_file():
            existing = pq.read_table(path)
            table = pa.concat_tables([existing, table])
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".part.", suffix=".parquet", dir=str(target_dir))
            os.close(fd)
            pq.write_table(table, tmp)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def ingest_jsonl_book(store: MarketStore, path: str | Path, *, limit: int = 0) -> int:
    """Replay strategist ``bfx_book_10l_1s.jsonl`` into the store. Returns rows ingested."""
    n = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            store.append_book_second(row)
            n += 1
            if limit and n >= limit:
                break
    return n


def ingest_jsonl_trades(store: MarketStore, path: str | Path, *, limit: int = 0) -> int:
    n = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            store.append_trades_second(row)
            n += 1
            if limit and n >= limit:
                break
    return n


def detect_gaps_from_seconds(seconds: Sequence[int]) -> list[tuple[int, int, int]]:
    """Return (start, end, missing_count) holes where consecutive present secs differ by >= 2."""
    if len(seconds) < 2:
        return []
    ordered = sorted(int(s) for s in seconds)
    gaps = []
    for prev, cur in zip(ordered, ordered[1:]):
        if cur - prev >= 2:
            start, end = prev + 1, cur - 1
            gaps.append((start, end, end - start + 1))
    return gaps
