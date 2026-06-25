"""Persistent Genome Library — living entities in research.db (not regenerated each run)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LIBRARY_DDL = """
CREATE TABLE IF NOT EXISTS genome_library (
  genome_id TEXT PRIMARY KEY,
  fingerprint_key TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  observations INTEGER NOT NULL DEFAULT 0,
  trade_count INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_genome_fp ON genome_library(fingerprint_key);

CREATE TABLE IF NOT EXISTS genome_discovery_memory (
  discovery_id TEXT PRIMARY KEY,
  dna_key TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discovery_dna ON genome_discovery_memory(dna_key);
"""


class GenomeLibraryStore:
    """Genome Memory — persist and update genome entities across analyzer cycles."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.executescript(LIBRARY_DDL)
            conn.commit()
            conn.close()

    def load_all_genomes(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = []
            for row in conn.execute("SELECT payload_json FROM genome_library ORDER BY last_seen DESC"):
                try:
                    rows.append(json.loads(row["payload_json"]))
                except (TypeError, json.JSONDecodeError):
                    continue
            conn.close()
        return rows

    def upsert_genome(self, genome_id: str, fingerprint_key: str, body: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT payload_json FROM genome_library WHERE genome_id = ?",
                (genome_id,),
            ).fetchone()
            if existing:
                prev = json.loads(existing["payload_json"])
                body["genome_id"] = genome_id
                body["first_seen"] = prev.get("first_seen") or now
                body["observations"] = int(prev.get("observations") or 0) + int(body.get("new_observations") or 1)
                body.setdefault("trade_count", int(prev.get("trade_count") or 0))
            else:
                body["genome_id"] = genome_id
                body["first_seen"] = now
                body["observations"] = int(body.get("new_observations") or 1)
            body["last_seen"] = now
            body["fingerprint_key"] = fingerprint_key
            payload = json.dumps(body, default=str)
            conn.execute(
                """INSERT OR REPLACE INTO genome_library
                   (genome_id, fingerprint_key, first_seen, last_seen, observations, trade_count, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    genome_id,
                    fingerprint_key,
                    body["first_seen"],
                    body["last_seen"],
                    body["observations"],
                    int(body.get("trade_count") or 0),
                    payload,
                ),
            )
            conn.commit()
            conn.close()
        return body

    def load_discoveries(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            for row in conn.execute("SELECT dna_key, payload_json FROM genome_discovery_memory"):
                try:
                    out[row["dna_key"]] = json.loads(row["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
            conn.close()
        return out

    def save_discovery(self, discovery: Dict[str, Any]) -> None:
        dna_key = str(discovery.get("dna_key") or "")
        disc_id = str(discovery.get("discovery_id") or dna_key)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            existing = conn.execute(
                "SELECT first_seen FROM genome_discovery_memory WHERE discovery_id = ?",
                (disc_id,),
            ).fetchone()
            first = existing[0] if existing else (discovery.get("first_observed") or now)
            discovery["first_observed"] = first
            discovery["last_observed"] = now
            payload = json.dumps(discovery, default=str)
            conn.execute(
                """INSERT OR REPLACE INTO genome_discovery_memory
                   (discovery_id, dna_key, first_seen, last_seen, status, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (disc_id, dna_key, first, now, discovery.get("status") or "NEW", payload),
            )
            conn.commit()
            conn.close()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            genomes = conn.execute("SELECT COUNT(*) FROM genome_library").fetchone()[0]
            discoveries = conn.execute("SELECT COUNT(*) FROM genome_discovery_memory").fetchone()[0]
            conn.close()
        return {"genomes": int(genomes), "discoveries": int(discoveries)}
