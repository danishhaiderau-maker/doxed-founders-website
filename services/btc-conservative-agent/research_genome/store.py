"""SQLite + JSONL research store — primary DB with append-only mirrors."""
from __future__ import annotations

import json
import hashlib
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
CREATE TABLE IF NOT EXISTS research_generation_segments (
  generation_id TEXT PRIMARY KEY,
  dataset_epoch TEXT NOT NULL,
  deployed_revision TEXT NOT NULL,
  tile_config_signature TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  start_boundaries_json TEXT NOT NULL,
  legacy_unbound_counts_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_ingestion_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  generation_id TEXT NOT NULL,
  status TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  row_count INTEGER,
  opportunity_count INTEGER,
  detail_json TEXT NOT NULL,
  FOREIGN KEY(generation_id) REFERENCES research_generation_segments(generation_id)
);
CREATE INDEX IF NOT EXISTS idx_market_env ON market_genome(environment_id);
CREATE INDEX IF NOT EXISTS idx_decision_mkt ON decision_genome(market_genome_id);
CREATE INDEX IF NOT EXISTS idx_exec_dec ON execution_genome(decision_id);
CREATE INDEX IF NOT EXISTS idx_trade_dec ON trade_genome(decision_id);
CREATE INDEX IF NOT EXISTS idx_events_name ON research_events(event_name);
CREATE INDEX IF NOT EXISTS idx_generation_epoch ON research_generation_segments(dataset_epoch, recorded_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_generation ON research_ingestion_status(generation_id, id);
"""

GENERATION_IDENTITY_SCHEMA = "research_generation_segment_v2"


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

    def reset(self, *, destructive: bool = False, deletion_receipt_path=None,
              quiescent: bool = False, recovery_states=None) -> Dict[str, Any]:
        """Start an empty epoch; payload retention is default, discard explicit."""
        if destructive is not False and destructive is not True:
            raise ValueError("destructive must be an explicit boolean")
        if destructive:
            return self._reset_destructive(
                deletion_receipt_path=deletion_receipt_path,
                quiescent=quiescent, recovery_states=recovery_states,
            )
        removed_bytes = 0
        with self._lock:
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()
                self._conn = None
            quarantine = os.path.join(
                os.path.dirname(self.db_path), "epoch_quarantine",
                datetime.now(timezone.utc).strftime("epoch_%Y%m%dT%H%M%S_%fZ"),
            )
            os.makedirs(quarantine, exist_ok=False)
            preserved = []
            for suffix in ("", "-wal", "-shm", "-journal"):
                path = self.db_path + suffix
                try:
                    if os.path.isfile(path):
                        size = int(os.path.getsize(path))
                        removed_bytes += size
                        target = os.path.join(quarantine, os.path.basename(path))
                        os.replace(path, target)
                        digest = hashlib.sha256()
                        with open(target, "rb") as source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                digest.update(chunk)
                        preserved.append({"path": os.path.basename(path), "size_bytes": size,
                                          "sha256": digest.hexdigest()})
                except OSError:
                    self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                    self._conn.executescript(DDL)
                    self._conn.commit()
                    raise
            with open(os.path.join(quarantine, "quarantine_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"schema": "research_genome_epoch_quarantine_v1", "complete": True,
                           "cutoff_utc": datetime.now(timezone.utc).isoformat(),
                           "files": preserved}, handle, indent=2)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.executescript(DDL)
            self._conn.commit()
        return {"removed_bytes": removed_bytes, "tables": len(LAYER_TABLES) + 1}

    def _reset_destructive(self, *, deletion_receipt_path, quiescent, recovery_states):
        """Explicit old-research discard: close DB, delete exact files, recreate."""
        from pathlib import Path
        from research_exact_deletion import delete_exact_research_files, ResearchDeletionRejected
        if quiescent is not True or not deletion_receipt_path:
            raise ResearchDeletionRejected("EXPLICIT_QUIESCENCE_AND_RECEIPT_REQUIRED")
        with self._lock:
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()
                self._conn = None
            candidates = [Path(self.db_path + suffix).absolute() for suffix in ("", "-wal", "-shm", "-journal")]
            # Do not automatically reopen after failure: the caller must retain
            # pause and reconcile the receipt before any new research writes.
            receipt = delete_exact_research_files(
                root=Path(self.base_dir).absolute(),
                targets=[p for p in candidates if p.exists() or p.is_symlink()],
                allowed_paths=candidates, receipt_path=deletion_receipt_path,
                quiescent=quiescent, recovery_states=recovery_states,
            )
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.executescript(DDL)
            self._conn.commit()
            return {"removed_bytes": receipt["deleted_bytes"], "tables": len(LAYER_TABLES) + 1,
                    "raw_payloads_retained": False, "deletion_receipt": receipt}

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()
                self._conn = None

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

    def _evidence_boundaries_locked(self) -> Dict[str, Dict[str, int]]:
        assert self._conn is not None
        boundaries: Dict[str, Dict[str, int]] = {}
        for table in (*LAYER_TABLES.values(), "research_events"):
            count, max_rowid = self._conn.execute(
                f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM {table}"
            ).fetchone()
            boundaries[table] = {"row_count": int(count), "max_rowid": int(max_rowid)}
        return boundaries

    @staticmethod
    def generation_id(
        *, dataset_epoch: str, deployed_revision: str, tile_config_signature: str
    ) -> str:
        material = "|".join((dataset_epoch, deployed_revision, tile_config_signature))
        return "generation-" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    def record_generation_identity(
        self,
        *,
        dataset_epoch: str,
        deployed_revision: str,
        tile_config_signature: str,
        recorded_at: str | None = None,
    ) -> Dict[str, Any]:
        """Append a revision segment; never attribute earlier rows retroactively."""
        epoch = str(dataset_epoch or "").strip()
        revision = str(deployed_revision or "").strip().lower()
        signature = str(tile_config_signature or "").strip()
        if not epoch or not revision or not signature:
            raise ValueError("GENERATION_IDENTITY_FIELDS_MISSING")
        if len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision):
            raise ValueError("GENOME_DEPLOYED_REVISION_NOT_EXACT_FULL_SHA")
        generation_id = self.generation_id(
            dataset_epoch=epoch, deployed_revision=revision,
            tile_config_signature=signature,
        )
        with self._lock:
            assert self._conn is not None
            current = self._conn.execute(
                """SELECT generation_id, dataset_epoch, deployed_revision,
                          tile_config_signature, schema_version, recorded_at,
                          start_boundaries_json, legacy_unbound_counts_json
                     FROM research_generation_segments WHERE generation_id = ?""",
                (generation_id,),
            ).fetchone()
            keys = (
                "generation_id", "dataset_epoch", "deployed_revision",
                "tile_config_signature", "schema_version", "recorded_at",
                "start_boundaries_json", "legacy_unbound_counts_json",
            )
            if current:
                existing = dict(zip(keys, current))
                if any(existing[name] != value for name, value in (
                    ("dataset_epoch", epoch), ("deployed_revision", revision),
                    ("tile_config_signature", signature),
                    ("schema_version", GENERATION_IDENTITY_SCHEMA),
                )):
                    raise ValueError("GENERATION_IDENTITY_CONFLICT")
                return self._decode_generation(existing)
            boundaries = self._evidence_boundaries_locked()
            stored_segments = self._conn.execute(
                """SELECT generation_id, dataset_epoch, deployed_revision,
                          tile_config_signature, schema_version, recorded_at,
                          start_boundaries_json, legacy_unbound_counts_json
                     FROM research_generation_segments ORDER BY rowid ASC"""
            ).fetchall()
            decoded_segments = [self._decode_generation(dict(zip(keys, row)))
                                for row in stored_segments]
            if decoded_segments:
                latest_boundaries = decoded_segments[-1]["start_boundaries"]
                for table, boundary in boundaries.items():
                    if any(boundary[key] < latest_boundaries[table][key]
                           for key in ("row_count", "max_rowid")):
                        raise ValueError(f"GENERATION_IDENTITY_BOUNDARY_REGRESSION:{table}")
                legacy_counts = decoded_segments[0]["legacy_unbound_counts"]
            else:
                legacy_counts = {
                    name: values["row_count"] for name, values in boundaries.items()
                }
            values = {
                "generation_id": generation_id,
                "dataset_epoch": epoch,
                "deployed_revision": revision,
                "tile_config_signature": signature,
                "schema_version": GENERATION_IDENTITY_SCHEMA,
                "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
                "start_boundaries_json": json.dumps(boundaries, sort_keys=True),
                "legacy_unbound_counts_json": json.dumps(legacy_counts, sort_keys=True),
            }
            self._conn.execute(
                """INSERT INTO research_generation_segments
                   (generation_id, dataset_epoch, deployed_revision, tile_config_signature,
                    schema_version, recorded_at, start_boundaries_json,
                    legacy_unbound_counts_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(values[name] for name in keys),
            )
            self._conn.commit()
        return self._decode_generation(values)

    @staticmethod
    def _decode_generation(values: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(values)
        required = {
            "generation_id", "dataset_epoch", "deployed_revision",
            "tile_config_signature", "schema_version", "recorded_at",
            "start_boundaries_json", "legacy_unbound_counts_json",
        }
        if required - result.keys():
            raise ValueError("GENERATION_IDENTITY_METADATA_FIELDS_MISSING")
        revision = str(result["deployed_revision"] or "").lower()
        if len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision):
            raise ValueError("GENOME_DEPLOYED_REVISION_NOT_EXACT_FULL_SHA")
        expected_id = ResearchStore.generation_id(
            dataset_epoch=str(result["dataset_epoch"] or ""),
            deployed_revision=revision,
            tile_config_signature=str(result["tile_config_signature"] or ""),
        )
        if result["generation_id"] != expected_id:
            raise ValueError("GENERATION_IDENTITY_ID_MISMATCH")
        if result["schema_version"] != GENERATION_IDENTITY_SCHEMA:
            raise ValueError("GENERATION_IDENTITY_SCHEMA_MISMATCH")
        result["deployed_revision"] = revision
        result["start_boundaries"] = json.loads(result.pop("start_boundaries_json"))
        result["legacy_unbound_counts"] = json.loads(result.pop("legacy_unbound_counts_json"))
        expected_tables = {*LAYER_TABLES.values(), "research_events"}
        boundaries = result["start_boundaries"]
        legacy = result["legacy_unbound_counts"]
        if not isinstance(boundaries, dict) or set(boundaries) != expected_tables:
            raise ValueError("GENERATION_IDENTITY_BOUNDARIES_INVALID")
        for table, boundary in boundaries.items():
            if not isinstance(boundary, dict) or set(boundary) != {"row_count", "max_rowid"}:
                raise ValueError(f"GENERATION_IDENTITY_BOUNDARY_INVALID:{table}")
            if any(type(boundary[key]) is not int or boundary[key] < 0
                   for key in ("row_count", "max_rowid")):
                raise ValueError(f"GENERATION_IDENTITY_BOUNDARY_INVALID:{table}")
        if not isinstance(legacy, dict) or not set(legacy).issubset(expected_tables):
            raise ValueError("GENERATION_IDENTITY_LEGACY_COUNTS_INVALID")
        if any(type(value) is not int or value < 0 for value in legacy.values()):
            raise ValueError("GENERATION_IDENTITY_LEGACY_COUNTS_INVALID")
        result["legacy_unbound_total"] = sum(result["legacy_unbound_counts"].values())
        return result

    def record_ingestion_status(
        self,
        *,
        generation_id: str,
        status: str,
        row_count: int | None = None,
        opportunity_count: int | None = None,
        observed_at: str | None = None,
        detail: Dict[str, Any] | None = None,
    ) -> int:
        """Append ingestion progress for an already-identified generation."""
        generation = str(generation_id or "").strip()
        state = str(status or "").strip().upper()
        if not generation or not state:
            raise ValueError("INGESTION_STATUS_FIELDS_MISSING")
        with self._lock:
            assert self._conn is not None
            known = self._conn.execute(
                "SELECT 1 FROM research_generation_segments WHERE generation_id = ?", (generation,)
            ).fetchone()
            if not known:
                raise ValueError("INGESTION_GENERATION_IDENTITY_MISSING")
            cursor = self._conn.execute(
                """INSERT INTO research_ingestion_status
                   (generation_id, status, observed_at, row_count, opportunity_count, detail_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (generation, state, observed_at or datetime.now(timezone.utc).isoformat(),
                 row_count, opportunity_count, json.dumps(detail or {}, sort_keys=True, default=str)),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def generation_identity(self, generation_id: str | None = None) -> Dict[str, Any] | None:
        """Return identity plus the latest ingestion status for DB-backed answers."""
        with self._lock:
            assert self._conn is not None
            if generation_id:
                row = self._conn.execute(
                    """SELECT generation_id, dataset_epoch, deployed_revision,
                              tile_config_signature, schema_version, recorded_at,
                              start_boundaries_json, legacy_unbound_counts_json
                         FROM research_generation_segments WHERE generation_id = ?""",
                    (generation_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """SELECT generation_id, dataset_epoch, deployed_revision,
                              tile_config_signature, schema_version, recorded_at,
                              start_boundaries_json, legacy_unbound_counts_json
                         FROM research_generation_segments ORDER BY rowid DESC LIMIT 1"""
                ).fetchone()
            if not row:
                return None
            keys = (
                "generation_id", "dataset_epoch", "deployed_revision",
                "tile_config_signature", "schema_version", "recorded_at",
                "start_boundaries_json", "legacy_unbound_counts_json",
            )
            result = self._decode_generation(dict(zip(keys, row)))
            result["generation_segment_count"] = int(self._conn.execute(
                "SELECT COUNT(*) FROM research_generation_segments WHERE dataset_epoch = ?",
                (result["dataset_epoch"],),
            ).fetchone()[0])
            ingestion = self._conn.execute(
                """SELECT status, observed_at, row_count, opportunity_count, detail_json
                     FROM research_ingestion_status WHERE generation_id = ?
                     ORDER BY id DESC LIMIT 1""",
                (result["generation_id"],),
            ).fetchone()
        result["last_ingestion"] = None
        if ingestion:
            result["last_ingestion"] = {
                "status": ingestion[0], "observed_at": ingestion[1],
                "row_count": ingestion[2], "opportunity_count": ingestion[3],
                "detail": json.loads(ingestion[4] or "{}"),
            }
        return result

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
