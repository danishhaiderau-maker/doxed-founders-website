"""SQLite + JSONL research store — primary DB with append-only mirrors."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LAYER_TABLES = {
    "environment": "environment_genome",
    "market": "market_genome",
    "decision": "decision_genome",
    "execution": "execution_genome",
    "lifecycle": "lifecycle_genome",
    "trade": "trade_genome",
}

DDL = """
CREATE TABLE IF NOT EXISTS environment_genome (
  environment_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_genome (
  market_genome_id TEXT PRIMARY KEY,
  environment_id TEXT,
  ts TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_genome (
  decision_id TEXT PRIMARY KEY,
  market_genome_id TEXT,
  ts TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_genome (
  execution_id TEXT PRIMARY KEY,
  decision_id TEXT,
  ts TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifecycle_genome (
  lifecycle_id TEXT PRIMARY KEY,
  execution_id TEXT,
  ts TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_genome (
  trade_id TEXT PRIMARY KEY,
  decision_id TEXT,
  ts TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bus_seq INTEGER,
  event_name TEXT,
  event_version INTEGER,
  ts TEXT,
  payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_env ON market_genome(environment_id);
CREATE INDEX IF NOT EXISTS idx_decision_mkt ON decision_genome(market_genome_id);
CREATE INDEX IF NOT EXISTS idx_exec_dec ON execution_genome(decision_id);
CREATE INDEX IF NOT EXISTS idx_trade_dec ON trade_genome(decision_id);
CREATE INDEX IF NOT EXISTS idx_events_name ON research_events(event_name);
"""


class ResearchStore:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.db_path = os.path.join(base_dir, "research.db")
        self.mirror_dir = os.path.join(base_dir, "research", "genome")
        os.makedirs(self.mirror_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.executescript(DDL)
            self._conn.commit()

    def append_event(self, event: Dict[str, Any]) -> None:
        payload = json.dumps(event, default=str)
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                """INSERT INTO research_events (bus_seq, event_name, event_version, ts, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event.get("bus_seq"),
                    event.get("event_name"),
                    event.get("event_version"),
                    event.get("ts") or datetime.now(timezone.utc).isoformat(),
                    payload,
                ),
            )
            self._conn.commit()

    def upsert_layer(self, layer: str, row_id: str, payload: Dict[str, Any], parent_col: str = "", parent_id: str = "") -> None:
        table = LAYER_TABLES.get(layer)
        if not table:
            raise ValueError(f"Unknown layer: {layer}")
        ts = payload.get("ts") or datetime.now(timezone.utc).isoformat()
        body = json.dumps(payload, default=str)
        with self._lock:
            assert self._conn is not None
            if layer == "lifecycle":
                self._conn.execute(
                    f"""INSERT OR REPLACE INTO {table} (lifecycle_id, execution_id, ts, payload_json)
                        VALUES (?, ?, ?, ?)""",
                    (row_id, parent_id or payload.get("execution_id"), ts, body),
                )
            elif layer == "environment":
                self._conn.execute(
                    f"""INSERT OR REPLACE INTO {table} (environment_id, ts, payload_json) VALUES (?, ?, ?)""",
                    (row_id, ts, body),
                )
            elif layer == "market":
                self._conn.execute(
                    f"""INSERT OR REPLACE INTO {table} (market_genome_id, environment_id, ts, payload_json)
                        VALUES (?, ?, ?, ?)""",
                    (row_id, parent_id or payload.get("environment_id"), ts, body),
                )
            elif layer == "decision":
                self._conn.execute(
                    f"""INSERT OR REPLACE INTO {table} (decision_id, market_genome_id, ts, payload_json)
                        VALUES (?, ?, ?, ?)""",
                    (row_id, parent_id or payload.get("market_genome_id"), ts, body),
                )
            elif layer == "execution":
                self._conn.execute(
                    f"""INSERT OR REPLACE INTO {table} (execution_id, decision_id, ts, payload_json)
                        VALUES (?, ?, ?, ?)""",
                    (row_id, parent_id or payload.get("decision_id"), ts, body),
                )
            elif layer == "trade":
                self._conn.execute(
                    f"""INSERT OR REPLACE INTO {table} (trade_id, decision_id, ts, payload_json)
                        VALUES (?, ?, ?, ?)""",
                    (row_id, parent_id or payload.get("decision_id"), ts, body),
                )
            self._conn.commit()
        self._append_jsonl(f"{table}.jsonl", payload)

    def _append_jsonl(self, filename: str, row: Dict[str, Any]) -> None:
        path = os.path.join(self.mirror_dir, filename)
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except OSError as exc:
            logger.warning("[GENOME] JSONL mirror write failed %s: %s", path, exc)

    def stats(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        with self._lock:
            assert self._conn is not None
            for layer, table in LAYER_TABLES.items():
                cur = self._conn.execute(f"SELECT COUNT(*) FROM {table}")
                counts[layer] = int(cur.fetchone()[0])
            cur = self._conn.execute("SELECT COUNT(*) FROM research_events")
            counts["events"] = int(cur.fetchone()[0])
        return counts
