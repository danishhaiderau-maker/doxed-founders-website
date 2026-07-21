"""Verify a derived Genome rebuild twice on an isolated SQLite backup only."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from research.genome.run_analyzer import run_genome_analyzer
from research.genome.validation import validate_genome_integrity

RAW_TABLES = (
    "research_events",
    "environment_genome",
    "market_genome",
    "decision_genome",
    "execution_genome",
    "lifecycle_genome",
    "trade_genome",
)
DERIVED_TABLES = (
    "genome_library",
    "genome_discovery_memory",
    "genome_evidence_ledger",
)


def _backup(source: str, destination: str) -> None:
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def _counts(db_path: str, tables: tuple[str, ...]) -> dict[str, int]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}


_VOLATILE_DERIVED_KEYS = {
    "first_seen",
    "last_seen",
    "first_observed",
    "last_observed",
    "generated_at",
    "ts",
}


def _normalize_semantic(value):
    if isinstance(value, dict):
        return {
            key: _normalize_semantic(item)
            for key, item in sorted(value.items())
            if key not in _VOLATILE_DERIVED_KEYS
        }
    if isinstance(value, list):
        return [_normalize_semantic(item) for item in value]
    return value


def _payload(value: str):
    try:
        return _normalize_semantic(json.loads(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _table_semantic_hash(conn: sqlite3.Connection, table: str) -> str:
    if table == "genome_library":
        rows = conn.execute(
            "SELECT genome_id, fingerprint_key, observations, trade_count, payload_json "
            "FROM genome_library ORDER BY genome_id"
        ).fetchall()
        records = [[*row[:-1], _payload(row[-1])] for row in rows]
    elif table == "genome_discovery_memory":
        rows = conn.execute(
            "SELECT discovery_id, dna_key, status, payload_json "
            "FROM genome_discovery_memory ORDER BY discovery_id"
        ).fetchall()
        records = [[*row[:-1], _payload(row[-1])] for row in rows]
    elif table == "genome_evidence_ledger":
        rows = conn.execute(
            "SELECT entity_type, entity_id, period_key, payload_json "
            "FROM genome_evidence_ledger ORDER BY entity_type, entity_id, period_key"
        ).fetchall()
        records = [[*row[:-1], _payload(row[-1])] for row in rows]
    else:
        raise ValueError(f"unsupported derived table: {table}")
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _derived_signature(db_path: str) -> dict:
    counts = _counts(db_path, DERIVED_TABLES)
    with closing(sqlite3.connect(db_path)) as conn:
        observations = conn.execute("SELECT COALESCE(SUM(observations), 0) FROM genome_library").fetchone()[0]
        hashes = {f"{table}_sha256": _table_semantic_hash(conn, table) for table in DERIVED_TABLES}
    return {**counts, "genome_observations": int(observations or 0), **hashes}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_staging(staging_db: str, artifacts: str) -> dict:
    raw_before = _counts(staging_db, RAW_TABLES)
    with closing(sqlite3.connect(staging_db)) as conn:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.lower() != "ok":
            raise RuntimeError(f"staging integrity check failed: {integrity}")
        for table in DERIVED_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()

    # One warm-up pass establishes run-delta fields such as previous_ev and
    # new_observations. The following two passes must be semantically equal.
    run_genome_analyzer(db_path=staging_db, out_dir=artifacts, publish_root_artifacts=False)
    warmup = _derived_signature(staging_db)
    raw_after_warmup = _counts(staging_db, RAW_TABLES)
    run_genome_analyzer(db_path=staging_db, out_dir=artifacts, publish_root_artifacts=False)
    first = _derived_signature(staging_db)
    raw_after_first = _counts(staging_db, RAW_TABLES)
    run_genome_analyzer(db_path=staging_db, out_dir=artifacts, publish_root_artifacts=False)
    second = _derived_signature(staging_db)
    raw_after_second = _counts(staging_db, RAW_TABLES)
    if raw_before != raw_after_warmup or raw_before != raw_after_first or raw_before != raw_after_second:
        raise AssertionError("raw research evidence changed during isolated rebuild")
    if first != second:
        raise AssertionError(f"derived rebuild is not idempotent: first={first}, second={second}")
    validation = validate_genome_integrity(staging_db)
    if str(validation.get("verdict") or "").upper() == "FAIL":
        raise AssertionError("staging Genome validation failed")
    return {
        "raw_counts": raw_before,
        "warmup_pass": warmup,
        "first_pass": first,
        "second_pass": second,
        "validation_verdict": validation.get("verdict"),
        "data_quality_warnings": len((validation.get("data_quality") or {}).get("warnings") or []),
    }


def verify(source_db: str) -> dict:
    source_db = os.path.abspath(source_db)
    with tempfile.TemporaryDirectory(prefix="genome-rebuild-verify-") as tmp:
        staging_db = os.path.join(tmp, "research-staging.db")
        artifacts = os.path.join(tmp, "artifacts")
        _backup(source_db, staging_db)
        result = _verify_staging(staging_db, artifacts)
        return {
            "status": "PASS",
            "source_db": source_db,
            "live_database_modified": False,
            **result,
        }


def rebuild_and_publish(source_db: str, backup_dir: str) -> dict:
    """Atomically replace only verified derived Genome tables."""
    source_db = os.path.abspath(source_db)
    backup_root = Path(backup_dir).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_db = str(backup_root / f"research-before-genome-rebuild-{stamp}.db")
    staging_db = str(backup_root / f"research-genome-staging-{stamp}.db")
    artifacts = str(backup_root / f"artifacts-{stamp}")

    _backup(source_db, backup_db)
    with closing(sqlite3.connect(backup_db)) as backup_conn:
        integrity = str(backup_conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.lower() != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
    backup_sha256 = _sha256(backup_db)
    raw_live_before = _counts(source_db, RAW_TABLES)
    _backup(backup_db, staging_db)
    staged = _verify_staging(staging_db, artifacts)
    if staged["raw_counts"] != raw_live_before:
        raise AssertionError("staging raw counts differ from the stopped source database")

    with closing(sqlite3.connect(source_db, timeout=30)) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("ATTACH DATABASE ? AS staging", (staging_db,))
        try:
            conn.execute("BEGIN IMMEDIATE")
            for table in DERIVED_TABLES:
                columns = [
                    str(row[1])
                    for row in conn.execute(f"PRAGMA main.table_info({table})").fetchall()
                ]
                staged_columns = [
                    str(row[1])
                    for row in conn.execute(f"PRAGMA staging.table_info({table})").fetchall()
                ]
                if columns != staged_columns or not columns:
                    raise RuntimeError(f"derived schema mismatch for {table}")
                names = ",".join(f'"{name}"' for name in columns)
                conn.execute(f"DELETE FROM main.{table}")
                conn.execute(
                    f"INSERT INTO main.{table} ({names}) SELECT {names} FROM staging.{table}"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("DETACH DATABASE staging")

    raw_live_after = _counts(source_db, RAW_TABLES)
    derived_live = _derived_signature(source_db)
    if raw_live_after != raw_live_before:
        raise AssertionError("raw research evidence changed during atomic publish")
    if derived_live != staged["second_pass"]:
        raise AssertionError("published derived signature differs from verified staging")
    final_validation = validate_genome_integrity(source_db)
    if str(final_validation.get("verdict") or "").upper() == "FAIL":
        raise AssertionError(f"published validation failed; restore backup {backup_db}")
    # Rebuild twice again from a fresh online backup of the published source.
    # This proves the live result is reproducible from raw evidence, rather
    # than merely matching the first staging database by row counts.
    post_publish = verify(source_db)
    if post_publish["second_pass"] != derived_live:
        raise AssertionError(f"post-publish rebuild differs; restore backup {backup_db}")
    return {
        "status": "PASS",
        "source_db": source_db,
        "live_database_modified": True,
        "backup_db": backup_db,
        "backup_sha256": backup_sha256,
        "staging_db": staging_db,
        **staged,
        "published_signature": derived_live,
        "published_validation_verdict": final_validation.get("verdict"),
        "post_publish_rebuild": post_publish,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="research.db")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--backup-dir", default="research/backups/genome-rebuild")
    args = parser.parse_args()
    result = (
        rebuild_and_publish(args.source, args.backup_dir)
        if args.publish
        else verify(args.source)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
