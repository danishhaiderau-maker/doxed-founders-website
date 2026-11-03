"""Read-only analyzer path for the day-1 local market store (RK C / R51).

Opens ``local_market.sqlite3`` and ``gap_log.jsonl`` without writes. Does not
import bot trading paths. Analyzer / research loaders may call
``open_local_fresh_store_readonly`` to inspect book/trades/gaps after SERIES BREAK.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Optional

from local_fresh_runtime import SERIES_BREAK_NAME, read_series_break
from local_market_persist import GAP_LOG_NAME, SQLITE_NAME

SCHEMA = "local_fresh_analyzer_read_v1"


class LocalFreshStoreReadError(RuntimeError):
    """Raised when the local store cannot be opened read-only."""


class LocalFreshStoreReadonly:
    """Read-only view of a local-fresh data directory."""

    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir).expanduser().resolve()
        self.sqlite_path = self.root / SQLITE_NAME
        self.gap_log_path = self.root / GAP_LOG_NAME
        self.series_break = read_series_break(self.root)
        if not self.sqlite_path.is_file():
            raise LocalFreshStoreReadError(
                f"LOCAL_FRESH_SQLITE_MISSING: {self.sqlite_path}"
            )
        # URI mode + immutable-ish read-only: mode=ro refuses writes.
        uri = f"file:{self.sqlite_path.as_posix()}?mode=ro"
        try:
            self._conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        except sqlite3.Error as exc:
            raise LocalFreshStoreReadError(
                f"LOCAL_FRESH_SQLITE_OPEN_RO_FAILED: {exc}"
            ) from exc
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "LocalFreshStoreReadonly":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def identity(self) -> dict[str, Any]:
        sb = self.series_break or {}
        return {
            "schema": SCHEMA,
            "data_dir": str(self.root),
            "sqlite_path": str(self.sqlite_path),
            "gap_log_path": str(self.gap_log_path),
            "series_break_present": bool(self.series_break),
            "local_epoch_id": sb.get("local_epoch_id"),
            "series_start_ts": sb.get("series_start_ts"),
            "series_start_utc": sb.get("series_start_utc"),
            "series_break_ts": sb.get("series_break_ts"),
            "read_only": True,
            "writes_forbidden": True,
        }

    def count_rows(self) -> dict[str, int]:
        book_n = self._conn.execute("SELECT COUNT(*) AS n FROM book_1s").fetchone()["n"]
        trade_n = self._conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        gap_n = self._conn.execute("SELECT COUNT(*) AS n FROM gap_events").fetchone()["n"]
        return {
            "book_1s_rows": int(book_n),
            "trade_rows": int(trade_n),
            "gap_events": int(gap_n),
        }

    def iter_book_seconds(
        self, *, start_s: Optional[int] = None, end_s: Optional[int] = None
    ) -> Iterator[dict[str, Any]]:
        sql = "SELECT s, bid, ask, receive_ts, carried, payload_json FROM book_1s"
        params: list[Any] = []
        clauses: list[str] = []
        if start_s is not None:
            clauses.append("s >= ?")
            params.append(int(start_s))
        if end_s is not None:
            clauses.append("s <= ?")
            params.append(int(end_s))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY s"
        for row in self._conn.execute(sql, params):
            yield {
                "s": int(row["s"]),
                "bid": row["bid"],
                "ask": row["ask"],
                "receive_ts": row["receive_ts"],
                "carried": int(row["carried"] or 0),
                "payload_json": row["payload_json"],
            }

    def iter_trades(
        self, *, start_s: Optional[int] = None, end_s: Optional[int] = None
    ) -> Iterator[dict[str, Any]]:
        sql = (
            "SELECT id, s, trade_id, mts_ms, amount, price, receive_ts FROM trades"
        )
        params: list[Any] = []
        clauses: list[str] = []
        if start_s is not None:
            clauses.append("s >= ?")
            params.append(int(start_s))
        if end_s is not None:
            clauses.append("s <= ?")
            params.append(int(end_s))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY s, id"
        for row in self._conn.execute(sql, params):
            yield {
                "id": int(row["id"]),
                "s": int(row["s"]),
                "trade_id": row["trade_id"],
                "mts_ms": row["mts_ms"],
                "amount": row["amount"],
                "price": row["price"],
                "receive_ts": row["receive_ts"],
            }

    def iter_gap_log_lines(self, *, max_lines: int = 10_000) -> Iterator[dict[str, Any]]:
        if not self.gap_log_path.is_file():
            return
        n = 0
        with open(self.gap_log_path, encoding="utf-8") as handle:
            for line in handle:
                if n >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield {"schema": "local_fresh_gap_log_bad_line", "raw": line[:200]}
                n += 1

    def snapshot(self) -> dict[str, Any]:
        out = self.identity()
        out["counts"] = self.count_rows()
        out[SERIES_BREAK_NAME.replace(".json", "")] = self.series_break
        return out


def open_local_fresh_store_readonly(data_dir: str | Path) -> LocalFreshStoreReadonly:
    """Canonical entry for analyzer / research read-only access."""
    return LocalFreshStoreReadonly(data_dir)


def summarize_local_fresh_store(data_dir: str | Path) -> dict[str, Any]:
    with open_local_fresh_store_readonly(data_dir) as store:
        return store.snapshot()
