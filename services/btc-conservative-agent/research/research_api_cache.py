"""Materialized research API cache: every mix-and-match / genome result precomputed per generation into SQLite.

``materialize`` is called once per genome cycle and atomically replaces ``research_api_cache.sqlite3``. The analyzer
dashboard serves ``/api/research`` (index) and ``/api/research/<dataset>`` straight from it with sorting, equality
filters, substring search and pagination; nothing is computed on request.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "research_api_cache_v1"
DB_NAME = "research_api_cache.sqlite3"
SUMMARY_NAME = "research_api_summary.json"
MAX_LIMIT = 1000
_KEY = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")

# name -> (description, default sort key, default order). Every dataset listed here must be materialized.
DATASETS: dict[str, tuple[str, str, str]] = {
    "top_100_policies": ("Top 100 genome policies by out-of-sample (holdout) net $ under REALISTIC_V1, with totals and "
                         "train rank. FAILED_HOLDOUT rows stay visible.", "net_oos_usd", "desc"),
    "policy_totals": ("Totals for every REALISTIC_V1 genome policy: fills, wins/losses, net $ all / in-sample / OOS, "
                      "avg $ and bp per trade, max drawdown, trades/day", "net_oos_usd", "desc"),
    "family_totals": ("Per family/ingredient group: policy count and the group's top policy by OOS net $ with totals",
                      "net_oos_usd", "desc"),
    "family_table": ("Per family/ingredient group: best train-selected policy with 70/30 holdout plus nested walk-forward "
                     "within the group", "nested_wf_ev_bp", "desc"),
    "mix_match_structures": ("Mix-and-match selection procedures per cohort: nested walk-forward OOS (UTC day and fine "
                             "blocks), full-data rule, deflated Sharpe", "nested_oos_fine.ev_bp", "desc"),
    "most_probable": ("Top 3 most probable strategies per cohort (ranked by fine-block nested-OOS lower CI)",
                      "rank", "asc"),
    "ingredient_verdicts": ("Does each ingredient / regime filter help out of sample? nested OOS with vs without",
                            "delta_bp", "desc"),
    "marginal_effects": ("Descriptive in-sample effect of each regime/context axis value (labelled in-sample)",
                         "median_policy_ev_delta_bp", "desc"),
    "regime_map": ("Regime cell x family group: nested OOS EV of the train-best policy (regime map heatmap source)",
                   "oos_ev_bp", "desc"),
    "meta_policies": ("Regime-switching meta-policies (regime -> policy learned on prior blocks; stand aside when none "
                      "positive) with nested OOS record", "nested_oos.ev_bp", "desc"),
    "in_sample_top": ("Best in-sample combos (LABELLED IN-SAMPLE - overfit by construction; forward-tested)",
                      "in_sample.ev_bp", "desc"),
    "forward_tracker": ("Frozen candidates scored only on episodes after their freeze (FORWARD_RULES_V1 verdicts)",
                        "forward.net_pnl_usd", "desc"),
    "forward_batches": ("Forward-tracker freeze batches and verdict counts", "batch_id", "desc"),
    "live_paper_by_lane": ("Live paper per lane: total closes, wins/losses, net $, avg, drawdown (LIVE_PAPER cohort)",
                           "net_pnl_usd", "desc"),
    "walk_forward": ("Genome walk-forward by UTC day per AI class (existing genome selection)", "cohort", "asc"),
    "mix_match_summary": ("Per cohort: episodes, policies, coverage of context axes, multiple-testing counts, warning",
                          "cohort", "asc"),
}


def _columns(rows: list[Mapping[str, Any]]) -> list[str]:
    cols: dict[str, None] = {}
    for r in rows[:500]:
        for k, v in r.items():
            cols[k] = None
            if isinstance(v, Mapping):
                for k2, v2 in v.items():
                    if not isinstance(v2, (Mapping, list)):
                        cols[f"{k}.{k2}"] = None
    return list(cols)


def materialize(out_dir: Path, datasets: Mapping[str, list[Mapping[str, Any]]], *, generation: str,
                generated_at: str, summary_extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (DB_NAME + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE datasets (name TEXT PRIMARY KEY, description TEXT, rows INTEGER, columns TEXT, "
                "default_sort TEXT, default_order TEXT, sha256 TEXT)")
    con.execute("CREATE TABLE rows (dataset TEXT, idx INTEGER, data TEXT, PRIMARY KEY (dataset, idx))")
    info = {}
    for name, (desc, sort, order) in DATASETS.items():
        rows = list(datasets.get(name) or [])
        blobs = [json.dumps(r, default=str, separators=(",", ":")) for r in rows]
        sha = hashlib.sha256("\n".join(blobs).encode("utf-8")).hexdigest()
        cols = _columns(rows)
        con.execute("INSERT INTO datasets VALUES (?,?,?,?,?,?,?)", (name, desc, len(rows), json.dumps(cols), sort, order, sha))
        con.executemany("INSERT INTO rows VALUES (?,?,?)", [(name, i, b) for i, b in enumerate(blobs)])
        info[name] = {"rows": len(rows), "sha256": sha[:16], "default_sort": sort, "default_order": order}
    for k, v in {"schema": SCHEMA, "generation": generation, "generated_at": generated_at,
                 "materialized_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}.items():
        con.execute("INSERT INTO meta VALUES (?,?)", (k, str(v)))
    con.commit()
    con.close()
    os.replace(tmp, out_dir / DB_NAME)
    summary = {"schema": SCHEMA, "generation": generation, "generated_at": generated_at,
               "materialized_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "db": str(out_dir / DB_NAME),
               "datasets": info} | dict(summary_extra or {})
    stmp = out_dir / (SUMMARY_NAME + ".tmp")
    stmp.write_text(json.dumps(summary, default=str, indent=1), encoding="utf-8")
    os.replace(stmp, out_dir / SUMMARY_NAME)
    return summary


def _connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=2)


def _meta(con: sqlite3.Connection) -> dict[str, str]:
    return dict(con.execute("SELECT key, value FROM meta").fetchall())


def _age(generated_at: str | None) -> float | None:
    try:
        return round(time.time() - calendar.timegm(time.strptime(generated_at or "", "%Y-%m-%dT%H:%M:%SZ")), 1)
    except (TypeError, ValueError):
        return None


def index(db: Path, base_url: str = "/api/research") -> dict[str, Any]:
    started = time.perf_counter()
    try:
        con = _connect(db)
    except sqlite3.Error as exc:
        return {"schema": SCHEMA, "status": "UNAVAILABLE", "reason": str(exc)[:200], "db": str(db)}
    try:
        meta = _meta(con)
        ds = con.execute("SELECT name, description, rows, default_sort, default_order FROM datasets ORDER BY name").fetchall()
    finally:
        con.close()
    return {"schema": SCHEMA, "status": "OK", "generation": meta.get("generation"), "generated_at": meta.get("generated_at"),
            "age_sec": _age(meta.get("generated_at")),
            "query_params": {"sort": "column (dotted for nested, e.g. nested_oos.ev_bp)", "order": "asc|desc",
                             "limit": f"1..{MAX_LIMIT} (default 100)", "offset": "pagination offset",
                             "q": "case-insensitive substring over the row JSON", "<column>": "equality filter, e.g. cohort=AI_DECISION"},
            "datasets": [{"name": n, "url": f"{base_url}/{n}", "description": d, "rows": r, "default_sort": s,
                          "default_order": o} for n, d, r, s, o in ds],
            "other_apis": {"/api/genome-grid": "full genome grid report (Top 100 by train EV, axes, walk-forward)",
                           "/api/sections/health": "analyzer section health incl. research content checks"},
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}


def query(db: Path, dataset: str, params: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
    started = time.perf_counter()
    try:
        con = _connect(db)
    except sqlite3.Error as exc:
        return 503, {"schema": SCHEMA, "status": "UNAVAILABLE", "reason": str(exc)[:200]}
    try:
        meta = _meta(con)
        row = con.execute("SELECT columns, default_sort, default_order, rows FROM datasets WHERE name=?", (dataset,)).fetchone()
        if row is None:
            return 404, {"schema": SCHEMA, "status": "UNKNOWN_DATASET", "dataset": dataset,
                         "datasets": [r[0] for r in con.execute("SELECT name FROM datasets ORDER BY name")]}
        cols, dsort, dorder, total_rows = json.loads(row[0]), row[1], row[2], row[3]
        sort = params.get("sort") or dsort
        order = (params.get("order") or dorder or "desc").lower()
        if not _KEY.match(sort or "") or order not in ("asc", "desc"):
            return 400, {"schema": SCHEMA, "status": "BAD_SORT", "sort": sort, "order": order}
        try:
            limit = max(1, min(MAX_LIMIT, int(params.get("limit") or 100)))
            offset = max(0, int(params.get("offset") or 0))
        except ValueError:
            return 400, {"schema": SCHEMA, "status": "BAD_PAGINATION"}
        where, args = ["dataset = ?"], [dataset]
        reserved = {"sort", "order", "limit", "offset", "q"}
        for key, val in params.items():
            if key in reserved:
                continue
            if not _KEY.match(key) or key not in cols:
                return 400, {"schema": SCHEMA, "status": "BAD_FILTER", "filter": key, "columns": cols}
            where.append("CAST(json_extract(data, ?) AS TEXT) = ?")
            args += ["$." + key, str(val)]
        if params.get("q"):
            where.append("lower(data) LIKE ?")
            args.append("%" + str(params["q"]).lower() + "%")
        cond = " AND ".join(where)
        total = con.execute(f"SELECT count(*) FROM rows WHERE {cond}", args).fetchone()[0]
        key = "json_extract(data, ?)"
        sql = (f"SELECT data FROM rows WHERE {cond} ORDER BY ({key} IS NULL), {key} {order.upper()}, idx ASC "
               "LIMIT ? OFFSET ?")
        data = [json.loads(r[0]) for r in con.execute(sql, args + ["$." + sort, "$." + sort, limit, offset])]
    finally:
        con.close()
    return 200, {"schema": SCHEMA, "status": "OK", "dataset": dataset, "generation": meta.get("generation"),
                 "generated_at": meta.get("generated_at"), "age_sec": _age(meta.get("generated_at")),
                 "total": total, "dataset_rows": total_rows, "offset": offset, "limit": limit, "sort": sort,
                 "order": order, "columns": cols, "rows": data,
                 "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}