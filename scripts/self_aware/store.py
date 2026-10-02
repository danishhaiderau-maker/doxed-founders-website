"""One embedded DuckDB store: raw data as views over the source files, computed results as tables.

Raw views never copy data; they read the custody mirror, analyzer exports and
laptop-chain files in place, so raw drill-down is always possible and the
store stays small. Every result table has a row in ``provenance`` (source
datasets, time window, code revision, computed_at, schema_version, rows).
Incremental results (per AI call, per edge event) are upserted so labels
survive after the mirror rotates its rolling files.
"""
from __future__ import annotations

import glob as _glob
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

from .config import SCHEMA_VERSION, Paths, code_revision

QUERY_MAX_ROWS = 5000
QUERY_TIMEOUT_SEC = 20.0

_TS = "VARCHAR"


@dataclass(frozen=True)
class RawSource:
    name: str
    root: str  # attribute of Paths
    pattern: str
    kind: str  # json | json_raw | csv | parquet
    columns: dict[str, str] | None = None
    description: str = ""


def _cols(**kw: str) -> dict[str, str]:
    return kw


RAW_SOURCES: tuple[RawSource, ...] = (
    RawSource("raw_tape_1s", "mirror", "market_microstructure_1s.jsonl*", "json",
              _cols(bucket_ts="BIGINT", bid="DOUBLE", ask="DOUBLE", bid_qty="DOUBLE", ask_qty="DOUBLE",
                    buy_qty="DOUBLE", sell_qty="DOUBLE", valid_bbo="BOOLEAN", fresh="BOOLEAN", source_age_sec="DOUBLE"),
              "Bitfinex 1 s L1 tape (rolling on the mirror)"),
    RawSource("raw_tape_1s_archive", "mirror_archive", "market_microstructure_1s.jsonl*", "json",
              _cols(bucket_ts="BIGINT", bid="DOUBLE", ask="DOUBLE", bid_qty="DOUBLE", ask_qty="DOUBLE",
                    buy_qty="DOUBLE", sell_qty="DOUBLE", valid_bbo="BOOLEAN", fresh="BOOLEAN", source_age_sec="DOUBLE"),
              "Pre-2026-09-30 archived 1 s tape"),
    RawSource("raw_cross_venue_1m", "mirror", "cross_venue_tape_1m.jsonl*", "json",
              _cols(minute_ts="BIGINT", n="INTEGER", basis_bp_mean="JSON", latency_ms_median="JSON", meta="JSON",
                    bfx="JSON", venues="JSON"),
              "Cross-venue 1 m tape (Binance/Bybit/OKX vs Bitfinex)"),
    RawSource("raw_ai_tranche", "mirror", "ai_tranche_log.csv", "csv", None, "Per AI call outcome rows (tranche log)"),
    RawSource("raw_ai_tranche_archive", "mirror_archive", "ai_tranche_log.csv", "csv", None, "Archived tranche log"),
    RawSource("raw_ai_input", "mirror", "ai_input_log.jsonl", "json",
              _cols(ts=_TS, ts_epoch="DOUBLE", trade_id=_TS, research_lane=_TS, trigger_reason=_TS,
                    context_fingerprint=_TS, temperature="DOUBLE", context="JSON"),
              "AI call inputs (ai_input_v3)"),
    RawSource("raw_ai_input_archive", "mirror_archive", "ai_input_log.jsonl", "json",
              _cols(ts=_TS, ts_epoch="DOUBLE", trade_id=_TS, research_lane=_TS, trigger_reason=_TS,
                    context_fingerprint=_TS, temperature="DOUBLE", context="JSON"),
              "Archived AI call inputs"),
    RawSource("raw_ai_compact", "mirror", "ai_shadow_compact_prompt.jsonl", "json",
              _cols(shared_ai_call_id=_TS, prompt_id=_TS, observed_at_utc=_TS, git_rev=_TS, model=_TS, served_model=_TS,
                    system_fingerprint=_TS, latency_ms="DOUBLE", side=_TS, call_state=_TS, facts_sha256=_TS,
                    raw_response=_TS, parsed="JSON", facts="JSON"),
              "Shadow compact-prompt calls (prompt id, raw response, parsed)"),
    RawSource("raw_ai_challengers", "mirror", "ai_shadow_challengers.jsonl", "json",
              _cols(row_kind=_TS, shared_ai_call_id=_TS, prompt_id=_TS, deepseek_model=_TS, decision_ts="DOUBLE",
                    sides="JSON", epoch_id=_TS),
              "Shadow challenger sides per AI call"),
    RawSource("raw_execution", "mirror", "v3/ledgers/execution.jsonl", "json",
              _cols(record_id=_TS, event_id=_TS, schema=_TS, research_lane=_TS, fill_id=_TS, fill_ts=_TS, close_ts=_TS,
                    activation_ts=_TS, entry_price="DOUBLE", exit_price="DOUBLE", net_pnl_usd="DOUBLE",
                    exit_reason=_TS, shared_ai_call_id=_TS, epoch_id=_TS, deployed_revision=_TS, opportunity_id=_TS),
              "V3 execution ledger (fills and closes)"),
    RawSource("raw_lifecycle", "mirror", "v3/ledgers/lifecycle.jsonl", "json",
              _cols(record_id=_TS, event_id=_TS, research_lane=_TS, fill_id=_TS, terminal="BOOLEAN", terminal_reason=_TS,
                    terminal_ttl_expired="BOOLEAN", terminal_no_fill="BOOLEAN", terminal_ts="DOUBLE", entry_outcome=_TS,
                    position_state=_TS, outcome_state=_TS, net_pnl_usd="DOUBLE", exit_reason=_TS, signal_ts="DOUBLE",
                    submitted_ts="DOUBLE", executed_direction=_TS, opportunity_id=_TS, epoch_id=_TS,
                    deployed_revision=_TS, shared_ai_call_id=_TS),
              "V3 lifecycle ledger"),
    RawSource("raw_decision", "mirror", "v3/ledgers/decision.jsonl", "json",
              _cols(event_id=_TS, decision_ts="DOUBLE", research_lane=_TS, decision_stage=_TS, policy_decision=_TS,
                    execution_disposition=_TS, exact_reason=_TS, executed_direction=_TS, order_intent_expected="BOOLEAN",
                    deployed_revision=_TS, epoch_id=_TS, opportunity_id=_TS),
              "V3 decision ledger"),
    RawSource("raw_order_intent", "mirror", "v3/ledgers/order_intent.jsonl", "json",
              _cols(event_id=_TS, research_lane=_TS, opportunity_id=_TS, epoch_id=_TS),
              "V3 order intents (1+ GB: ad-hoc drill-down only, never scanned by the engine)"),
    RawSource("raw_alarms", "chain", "health/alarms.jsonl", "json",
              _cols(at=_TS, event=_TS, check=_TS, status=_TS, observed=_TS, threshold=_TS, hint=_TS, runbook=_TS,
                    opened_at=_TS),
              "Laptop watcher alarm events (Alerts section source)"),
    RawSource("raw_verdicts", "chain", "health/verdicts-*.jsonl", "json",
              _cols(at=_TS, verdict=_TS, open_alarms="VARCHAR[]", failing="VARCHAR[]"),
              "Watcher verdict per tick"),
    RawSource("raw_autoff_receipts", "chain", "v2c-auto-ff.receipts.jsonl", "json_raw", None, "v2c auto-ff receipts"),
    RawSource("raw_manual_interventions", "chain", "manual-interventions.jsonl", "json_raw", None,
              "Manual intervention journal (unattended proof)"),
    RawSource("raw_proof_rows", "diagnostics", "unattended-proof-*.jsonl", "json_raw", None, "Unattended proof receipts"),
    RawSource("raw_puller_acks", "puller", "*.jsonl", "json_raw", None, "Segment puller ACK log"),
    RawSource("raw_self_journal", "home", "repair-journal.jsonl", "json_raw", None, "Self-aware repair journal"),
    RawSource("raw_archive", "archive", "**/*.parquet", "parquet", None, "Analysis archive rollups"),
)


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _struct(columns: dict[str, str]) -> str:
    return "{" + ", ".join(f"{_sql_str(k)}: {_sql_str(v)}" for k, v in columns.items()) + "}"


def _reader(kind: str, pattern: str, columns: dict[str, str] | None) -> str:
    p = _sql_str(pattern)
    if kind == "json":
        return (f"read_json({p}, format='newline_delimited', ignore_errors=true, filename=true, "
                f"maximum_object_size=67108864, columns={_struct(columns or {})})")
    if kind == "json_raw":
        return (f"read_json({p}, format='newline_delimited', records=false, ignore_errors=true, filename=true, "
                f"maximum_object_size=67108864)")
    if kind == "csv":
        return f"read_csv({p}, all_varchar=true, encoding='latin-1', ignore_errors=true, filename=true)"
    if kind == "parquet":
        return f"read_parquet({p}, union_by_name=true, filename=true)"
    raise ValueError(kind)


_DENY = re.compile(
    r"\b(read_\w+|glob|sniff_csv|parquet_\w+|query_table|query|getenv|current_setting|duckdb_secrets|"
    r"duckdb_settings|which_secret|load|install|attach|copy|export|import|pragma|set|reset|call|checkpoint)\b",
    re.IGNORECASE)
_PATHY = re.compile(r"'[^']*([\\/:]|\.(csv|json|jsonl|parquet|txt|env|db|duckdb|ps1|py))[^']*'", re.IGNORECASE)


class QueryRejected(ValueError):
    pass


class Store:
    def __init__(self, paths: Paths | None = None, *, path: str | Path | None = None, threads: int = 2,
                 memory_limit: str = "768MB") -> None:
        self.paths = paths or Paths()
        self.path = Path(path) if path else self.paths.store
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path))
        self.con.execute(f"SET threads={int(threads)}")
        self.con.execute(f"SET memory_limit='{memory_limit}'")
        self.con.execute("SET preserve_insertion_order=false")
        self._write_lock = threading.RLock()
        self._view_sigs: dict[str, str] = {}
        self.revision = code_revision()
        self._init_meta()

    # ------------------------------------------------------------------ meta
    def _init_meta(self) -> None:
        with self._write_lock:
            self.con.execute("""
                CREATE TABLE IF NOT EXISTS provenance (
                    result_name VARCHAR PRIMARY KEY, schema_version VARCHAR, computed_at VARCHAR,
                    code_revision VARCHAR, source_datasets JSON, window_start VARCHAR, window_end VARCHAR,
                    row_count BIGINT, compute_ms BIGINT, status VARCHAR, note VARCHAR)""")
            self.con.execute("""
                CREATE TABLE IF NOT EXISTS raw_catalog (
                    view_name VARCHAR PRIMARY KEY, kind VARCHAR, pattern VARCHAR, files BIGINT, bytes BIGINT,
                    newest_mtime VARCHAR, status VARCHAR, description VARCHAR, refreshed_at VARCHAR)""")

    def cursor(self) -> duckdb.DuckDBPyConnection:
        return self.con.cursor()

    def close(self) -> None:
        try:
            self.con.close()
        except duckdb.Error:
            pass

    # ------------------------------------------------------------- raw views
    def refresh_views(self, sources: Iterable[RawSource] = RAW_SOURCES) -> list[dict[str, Any]]:
        out = []
        now = _iso()
        for src in sources:
            root = getattr(self.paths, src.root)
            files = sorted(f for f in _glob.glob(str(Path(root) / src.pattern), recursive=True)
                           if Path(f).is_file() and not f.endswith((".validation.json", ".json", ".tmp", ".partial")))
            size = sum(Path(f).stat().st_size for f in files) if files else 0
            newest = max((Path(f).stat().st_mtime for f in files), default=None)
            status = "OK" if files else "MISSING"
            signature = "|".join(files)
            if files and self._view_sigs.get(src.name) == signature:
                with self._write_lock:
                    self.con.execute("UPDATE raw_catalog SET bytes = ?, newest_mtime = ?, refreshed_at = ? WHERE view_name = ?",
                                     [size, _iso(newest) if newest else None, now, src.name])
                out.append({"view": src.name, "files": len(files), "bytes": size, "status": "OK"})
                continue
            with self._write_lock:
                if files:
                    target = "[" + ", ".join(_sql_str(Path(x).as_posix()) for x in files) + "]"
                    reader = _reader(src.kind, "__P__", src.columns).replace("'__P__'", target)
                    try:
                        self.con.execute(f"CREATE OR REPLACE VIEW {src.name} AS SELECT * FROM {reader}")
                        self._view_sigs[src.name] = signature
                    except duckdb.Error as exc:
                        status = f"ERROR: {str(exc)[:200]}"
                else:
                    self.con.execute(f"DROP VIEW IF EXISTS {src.name}")
                self.con.execute("INSERT OR REPLACE INTO raw_catalog VALUES (?,?,?,?,?,?,?,?,?)",
                                 [src.name, src.kind, (Path(root) / src.pattern).as_posix(), len(files), size,
                                  _iso(newest) if newest else None, status, src.description, now])
            out.append({"view": src.name, "files": len(files), "bytes": size, "status": status})
        if sources is RAW_SOURCES:
            known = {s.name for s in RAW_SOURCES}
            with self._write_lock:
                for (name,) in self.con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal AND "
                                                "view_name LIKE 'raw\\_%' ESCAPE '\\' AND view_name NOT LIKE 'raw\\_export\\_%' "
                                                "ESCAPE '\\'").fetchall():
                    if name not in known:
                        self.con.execute(f"DROP VIEW IF EXISTS {name}")
                        self.con.execute("DELETE FROM raw_catalog WHERE view_name = ?", [name])
        self._refresh_export_views(now)
        return out

    def _refresh_export_views(self, now: str) -> None:
        root = self.paths.exports
        for f in sorted(_glob.glob(str(root / "*.parquet"))):
            name = "raw_export_" + re.sub(r"[^a-z0-9_]", "_", Path(f).stem.lower())
            st = Path(f).stat()
            with self._write_lock:
                try:
                    self.con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet({_sql_str(Path(f).as_posix())})")
                    status = "OK"
                except duckdb.Error as exc:
                    status = f"ERROR: {str(exc)[:200]}"
                self.con.execute("INSERT OR REPLACE INTO raw_catalog VALUES (?,?,?,?,?,?,?,?,?)",
                                 [name, "parquet", Path(f).as_posix(), 1, st.st_size, _iso(st.st_mtime), status,
                                  "Analyzer export (latest)", now])

    # --------------------------------------------------------------- results
    def publish(self, name: str, frame, *, sources: list[str], window: tuple[str | None, str | None] = (None, None),
                compute_ms: int = 0, status: str = "OK", note: str | None = None) -> None:
        """Replace result table ``res_<name>`` and its provenance row atomically."""
        table = f"res_{name}"
        with self._write_lock:
            cur = self.con.cursor()
            try:
                cur.execute("BEGIN TRANSACTION")
                cur.register("_frame", frame)
                cur.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _frame")
                cur.unregister("_frame")
                self._provenance(cur, name, sources, window, len(frame), compute_ms, status, note)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            finally:
                cur.close()

    def upsert(self, name: str, frame, key: str | list[str], *, sources: list[str],
               window: tuple[str | None, str | None] = (None, None), compute_ms: int = 0, note: str | None = None) -> None:
        """Insert-or-replace rows of ``res_<name>`` by key; history beyond source retention is kept."""
        table = f"res_{name}"
        keys = [key] if isinstance(key, str) else list(key)
        with self._write_lock:
            cur = self.con.cursor()
            try:
                cur.execute("BEGIN TRANSACTION")
                cur.register("_frame", frame)
                exists = cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                                     [table]).fetchone()[0]
                if not exists:
                    cur.execute(f"CREATE TABLE {table} AS SELECT * FROM _frame")
                else:
                    have = {r[0] for r in cur.execute(f"DESCRIBE {table}").fetchall()}
                    for row in cur.execute("DESCRIBE _frame").fetchall():
                        if row[0] not in have:
                            cur.execute(f'ALTER TABLE {table} ADD COLUMN "{row[0]}" {row[1]}')
                    cond = " AND ".join(f't."{k}" = f."{k}"' for k in keys)
                    cur.execute(f"DELETE FROM {table} t WHERE EXISTS (SELECT 1 FROM _frame f WHERE {cond})")
                    cur.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _frame")
                total = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                cur.unregister("_frame")
                self._provenance(cur, name, sources, window, total, compute_ms, "OK", note)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            finally:
                cur.close()

    def append(self, name: str, rows: list[dict[str, Any]], *, sources: list[str], note: str | None = None) -> None:
        """Append JSON documents to ``res_<name>`` (at, doc) history tables (findings, digests, runtime)."""
        if not rows:
            return
        table = f"res_{name}"
        with self._write_lock:
            cur = self.con.cursor()
            try:
                cur.execute(f'CREATE TABLE IF NOT EXISTS {table} ("at" VARCHAR, kind VARCHAR, id VARCHAR, doc JSON)')
                cur.executemany(f"INSERT INTO {table} VALUES (?,?,?,?)",
                                [[r.get("at") or _iso(), r.get("kind"), r.get("id"), json.dumps(r, default=str)]
                                 for r in rows])
                total = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                self._provenance(cur, name, sources, (None, None), total, 0, "OK", note)
            finally:
                cur.close()

    def prune_history(self, name: str, keep_days: int) -> None:
        table = f"res_{name}"
        cutoff = _iso(time.time() - keep_days * 86400)
        with self._write_lock:
            try:
                self.con.execute(f'DELETE FROM {table} WHERE "at" < ?', [cutoff])
            except duckdb.CatalogException:
                pass

    def _provenance(self, cur, name, sources, window, rows, compute_ms, status, note) -> None:
        cur.execute("INSERT OR REPLACE INTO provenance VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [name, SCHEMA_VERSION, _iso(), self.revision, json.dumps(sources), window[0], window[1],
                     int(rows), int(compute_ms), status, note])

    def provenance(self, name: str | None = None) -> list[dict[str, Any]] | dict[str, Any] | None:
        cur = self.cursor()
        try:
            if name:
                rows = self._dicts(cur.execute("SELECT * FROM provenance WHERE result_name = ?", [name]))
                if not rows:
                    return None
                row = rows[0]
                row["source_datasets"] = json.loads(row["source_datasets"] or "[]")
                return row
            rows = self._dicts(cur.execute("SELECT * FROM provenance ORDER BY result_name"))
            for row in rows:
                row["source_datasets"] = json.loads(row["source_datasets"] or "[]")
            return rows
        finally:
            cur.close()

    def table_exists(self, name: str) -> bool:
        cur = self.cursor()
        try:
            return bool(cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                                    [name]).fetchone()[0])
        finally:
            cur.close()

    def read(self, sql: str, params: list | None = None) -> list[dict[str, Any]]:
        """Trusted internal read (engine and API handlers)."""
        cur = self.cursor()
        try:
            return self._dicts(cur.execute(sql, params or []))
        finally:
            cur.close()

    def frame(self, sql: str, params: list | None = None):
        cur = self.cursor()
        try:
            return cur.execute(sql, params or []).df()
        finally:
            cur.close()

    def history(self, name: str, *, limit: int = 50, kind: str | None = None, since: str | None = None) -> list[dict]:
        table = f"res_{name}"
        if not self.table_exists(table):
            return []
        where, params = [], []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if since:
            where.append('"at" >= ?')
            params.append(since)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self.read(f'SELECT doc FROM {table} {clause} ORDER BY "at" DESC LIMIT {int(limit)}', params)
        return [json.loads(r["doc"]) for r in rows]

    @staticmethod
    def _dicts(result) -> list[dict[str, Any]]:
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    # ------------------------------------------------------- guarded queries
    def catalog(self) -> dict[str, Any]:
        cur = self.cursor()
        try:
            raw = self._dicts(cur.execute("SELECT * FROM raw_catalog ORDER BY view_name"))
            tables = self._dicts(cur.execute(
                "SELECT table_name, estimated_size AS rows FROM duckdb_tables() WHERE table_name LIKE 'res_%' "
                "ORDER BY table_name"))
        finally:
            cur.close()
        prov = {p["result_name"]: p for p in self.provenance() or []}
        for t in tables:
            t["provenance"] = prov.get(t["table_name"][4:])
        return {"raw_views": raw, "result_tables": tables}

    def query(self, sql: str, *, max_rows: int = QUERY_MAX_ROWS, timeout: float = QUERY_TIMEOUT_SEC) -> dict[str, Any]:
        """Read-only drill-down: one SELECT over registered views/tables, bounded rows and time.

        File-reading table functions, path literals and every non-SELECT
        statement are refused so the endpoint cannot read arbitrary files
        (secrets live elsewhere on this laptop).
        """
        text = (sql or "").strip().rstrip(";").strip()
        if not text:
            raise QueryRejected("empty query")
        if ";" in text:
            raise QueryRejected("one statement only")
        if _DENY.search(text):
            raise QueryRejected(f"function or keyword not allowed: {_DENY.search(text).group(0)}")
        if _PATHY.search(text):
            raise QueryRejected("string literals that look like file paths are not allowed")
        cur = self.cursor()
        try:
            stmts = cur.extract_statements(text)
            if len(stmts) != 1 or stmts[0].type != duckdb.StatementType.SELECT:
                raise QueryRejected("only a single SELECT is allowed")
            limit = max(1, min(int(max_rows), QUERY_MAX_ROWS))
            timer = threading.Timer(timeout, cur.interrupt)
            started = time.time()
            timer.start()
            try:
                result = cur.execute(f"SELECT * FROM ({text}) AS q LIMIT {limit + 1}")
                cols = [d[0] for d in result.description]
                rows = result.fetchall()
            except duckdb.InterruptException as exc:
                raise QueryRejected(f"query exceeded {timeout:.0f}s") from exc
            finally:
                timer.cancel()
            truncated = len(rows) > limit
            return {"columns": cols, "rows": [list(r) for r in rows[:limit]], "row_count": min(len(rows), limit),
                    "truncated": truncated, "elapsed_ms": round((time.time() - started) * 1000)}
        finally:
            cur.close()
