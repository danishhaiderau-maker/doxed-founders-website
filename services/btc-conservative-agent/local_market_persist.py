"""Local public market persist: SQLite + optional Parquet + gap log.

Day-1 fresh start after Fly delete. Public Bitfinex book/trades only — no API keys.
Gap log records missing 1-second book (and optional trade) seconds for soak read-back.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

SCHEMA = "local_market_persist_v1"
GAP_LOG_SCHEMA = "local_market_gap_log_v1"
GAP_LOG_NAME = "gap_log.jsonl"
SQLITE_NAME = "local_market.sqlite3"
PARQUET_BOOK_DIR = "parquet" + os.sep + "book_1s"
PARQUET_TRADES_DIR = "parquet" + os.sep + "trades"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class MarketStore:
    """SQLite primary store; Parquet best-effort when pyarrow is installed."""

    def __init__(self, data_dir: str | Path, *, clock=time.time) -> None:
        self.root = Path(data_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.sqlite_path = self.root / SQLITE_NAME
        self.gap_log_path = self.root / GAP_LOG_NAME
        self._conn = _connect(self.sqlite_path)
        self._init_schema()
        self._last_book_sec: Optional[int] = None
        self._last_trade_sec: Optional[int] = None
        self._parquet_ok = self._probe_parquet()

    @staticmethod
    def _probe_parquet() -> bool:
        try:
            import pyarrow  # noqa: F401
            import pyarrow.parquet  # noqa: F401
            return True
        except Exception:
            return False

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
        s = int(row["s"])
        bid = row.get("bid")
        ask = row.get("ask")
        if bid is None and isinstance(row.get("b"), list) and row["b"]:
            bid = float(row["b"][0][0])
        if ask is None and isinstance(row.get("a"), list) and row["a"]:
            ask = float(row["a"][0][0])
        receive_ts = row.get("receive_ts")
        carried = 1 if row.get("carried") else 0
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

    def append_trades_second(self, row: Mapping[str, Any]) -> None:
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
        self._write_gap("BOOK_1S", start, end, missing)

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
        self._write_gap("TRADE_SEC", self._last_trade_sec + 1, s - 1, missing)

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
        self._write_gap(kind, int(start_s), int(end_s), missing, detail=detail)

    def _write_gap(
        self,
        kind: str,
        start_s: int,
        end_s: int,
        missing: int,
        *,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
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
        }
        self.gap_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.gap_log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")

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
        return {
            "schema": SCHEMA,
            "sqlite_path": str(self.sqlite_path),
            "gap_log_path": str(self.gap_log_path),
            "book_1s_rows": int(book_n),
            "trade_rows": int(trade_n),
            "gap_events": int(gap_n),
            "parquet_enabled": bool(self._parquet_ok),
            "last_book_sec": self._last_book_sec,
            "last_trade_sec": self._last_trade_sec,
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
        except Exception:
            self._parquet_ok = False
            return
        target_dir = self.root / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        # One small file per append batch hour bucket (day-1 skeleton).
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
