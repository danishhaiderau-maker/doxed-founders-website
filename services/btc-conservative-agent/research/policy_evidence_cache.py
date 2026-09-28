"""Disposable SQLite cache for manifest-bound policy evidence results."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from research.policy_evidence_schema import CACHE_SCHEMA_VERSION, canonical_json


DDL = """
CREATE TABLE IF NOT EXISTS cache_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS episode_policy_result (
 generation_key TEXT NOT NULL, opportunity_id TEXT NOT NULL, episode_id TEXT NOT NULL,
 decision_id TEXT NOT NULL, policy_signature TEXT NOT NULL,
 evidence_world TEXT NOT NULL, comparison_cohort_key TEXT NOT NULL,
 lane TEXT, family TEXT, entry_offset_pct TEXT, chase_policy TEXT, exit_family TEXT,
 regime TEXT, side TEXT, split TEXT, ai_direction TEXT, ai_decision TEXT,
 classification TEXT NOT NULL, supported INTEGER NOT NULL,
 filled_qty REAL, gross_pnl_usd REAL, fees_usd REAL, slippage_usd REAL, net_pnl_usd REAL,
 payload_json TEXT NOT NULL,
 PRIMARY KEY (generation_key, opportunity_id, episode_id, decision_id,
              policy_signature, evidence_world, comparison_cohort_key)
);
CREATE INDEX IF NOT EXISTS idx_pe_lookup ON episode_policy_result
 (generation_key, evidence_world, family, entry_offset_pct, chase_policy, exit_family,
  regime, side, split, ai_direction, ai_decision);
CREATE TABLE IF NOT EXISTS query_cache (
 generation_key TEXT NOT NULL, query_hash TEXT NOT NULL, result_json TEXT NOT NULL,
 PRIMARY KEY (generation_key, query_hash)
);
"""


def cache_path(canonical_root: str | Path, generation_key: str) -> Path:
    root = Path(canonical_root).resolve()
    if root.name != "canonical-research-data":
        raise ValueError("CACHE_ROOT_NOT_CANONICAL_RESEARCH_DATA")
    return root / "derived" / "policy-evidence" / generation_key / "results.sqlite"


class PolicyEvidenceCache:
    def __init__(self, canonical_root: str | Path, generation: Mapping[str, str]):
        self.generation = dict(generation)
        self.path = cache_path(canonical_root, self.generation["generation_key"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._archive_incompatible_cache()
        self._initialize()

    def _archive_incompatible_cache(self) -> None:
        """Archive a disposable cache before a schema/generation rebuild.

        Raw ledgers remain authoritative.  The resolved cache path is already
        confined by ``cache_path``; this preserves the old derived database
        rather than mutating or deleting it in place.
        """
        if not self.path.is_file():
            return
        expected = {"cache_schema": CACHE_SCHEMA_VERSION, **self.generation}
        connection = None
        try:
            connection = sqlite3.connect(self.path)
            existing = dict(connection.execute("SELECT key, value FROM cache_meta"))
        except sqlite3.Error:
            existing = {}
        finally:
            if connection is not None:
                connection.close()
        if existing == expected:
            return
        existing_schema = str(existing.get("cache_schema") or "")
        if not existing_schema:
            raise ValueError("INVALID_POLICY_EVIDENCE_CACHE_METADATA")
        # A schema upgrade is a normal rebuild of disposable derived data.
        # Any identity drift within the same schema is not: leave it intact so
        # _initialize fails closed as foreign/stale evidence.
        if existing_schema == CACHE_SCHEMA_VERSION:
            return
        suffix = existing_schema.replace(os.sep, "_")
        destination = self.path.with_name(f"{self.path.name}.archived-{suffix}")
        counter = 1
        while destination.exists():
            destination = self.path.with_name(
                f"{self.path.name}.archived-{suffix}-{counter}"
            )
            counter += 1
        os.replace(self.path, destination)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(DDL)
            expected = {"cache_schema": CACHE_SCHEMA_VERSION, **self.generation}
            existing = dict(connection.execute("SELECT key, value FROM cache_meta"))
            if existing and existing != expected:
                raise ValueError("STALE_OR_FOREIGN_POLICY_EVIDENCE_CACHE")
            connection.executemany(
                "INSERT OR REPLACE INTO cache_meta(key,value) VALUES(?,?)", expected.items()
            )

    def put_rows(self, rows: Iterable[Mapping[str, Any]]) -> int:
        count = 0
        with self._connect() as connection:
            for source in rows:
                row = dict(source)
                connection.execute(
                    """INSERT OR REPLACE INTO episode_policy_result VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        self.generation["generation_key"], row["opportunity_id"], row["episode_id"],
                        row["decision_id"], row["policy_signature"],
                        row["evidence_world"], row["comparison_cohort_key"], row.get("lane"),
                        row.get("family"), row.get("entry_offset_pct"), row.get("chase_policy"),
                        row.get("exit_family"), row.get("regime"), row.get("side"), row.get("split"),
                        row.get("ai_direction"), row.get("ai_decision"),
                        row["classification"], int(bool(row.get("supported"))), row.get("filled_qty"),
                        row.get("gross_pnl_usd"), row.get("fees_usd"), row.get("slippage_usd"),
                        row.get("net_pnl_usd"), canonical_json(row),
                    ),
                )
                count += 1
            if count:
                # Cached answers describe a snapshot of this table. Any write,
                # including replacement of an existing decision, invalidates it.
                connection.execute(
                    "DELETE FROM query_cache WHERE generation_key=?",
                    (self.generation["generation_key"],),
                )
        return count

    def get_query(self, query_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM query_cache WHERE generation_key=? AND query_hash=?",
                (self.generation["generation_key"], query_hash),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put_query(self, query_hash: str, result: Mapping[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO query_cache VALUES(?,?,?)",
                (self.generation["generation_key"], query_hash, canonical_json(result)),
            )

    def _where(self, query: Mapping[str, Any]) -> tuple[str, list[Any]]:
        clauses = ["generation_key=?", "evidence_world=?"]
        params: list[Any] = [self.generation["generation_key"], query["evidence_world"]]
        column_map = {"comparison_cohort_key":"comparison_cohort_key",
                      "opportunity_id":"opportunity_id", "episode_id":"episode_id",
                      "decision_id":"decision_id", "policy_signature":"policy_signature",
                      "lane":"lane", "family":"family", "chase_policy":"chase_policy",
                      "exit_family":"exit_family", "regime":"regime", "side":"side",
                      "split":"split", "ai_direction":"ai_direction",
                      "ai_decision":"ai_decision", "classification":"classification"}
        for field, column in column_map.items():
            values = query.get(field) or []
            if values:
                clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        if query.get("entry_offset_pct") is not None:
            clauses.append("entry_offset_pct=?")
            params.append(query["entry_offset_pct"])
        return " AND ".join(clauses), params

    def cohort_keys(self, query: Mapping[str, Any]) -> set[str]:
        where, params = self._where(query)
        with self._connect() as connection:
            return {
                str(row[0]) for row in connection.execute(
                    "SELECT DISTINCT comparison_cohort_key FROM episode_policy_result WHERE " + where,
                    params,
                )
            }

    def count(self, query: Mapping[str, Any]) -> int:
        where, params = self._where(query)
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM episode_policy_result WHERE " + where, params,
            ).fetchone()[0])

    def select(self, query: Mapping[str, Any]) -> list[dict[str, Any]]:
        where, params = self._where(query)
        params.append(int(query["limit"]))
        sql = "SELECT payload_json FROM episode_policy_result WHERE " + where
        sql += " ORDER BY opportunity_id, episode_id, decision_id, policy_signature LIMIT ?"
        with self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(sql, params)]
