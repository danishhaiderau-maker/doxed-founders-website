#!/usr/bin/env python3
"""
Fresh trade accumulator (v9.83+ era) — SQLite DB updated each analyzer iteration (~30 min).

Does NOT backfill historical archives. Only trades at/after collection epoch
(fresh_collection_start or first sync time) are stored.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

ACCUMULATOR_DIR = "research_accumulator"
DB_NAME = "research_trades_v983.db"
STATUS_FILE = "research_accumulator_status.json"
EXPORT_CSV = "trades_accumulated.csv"
SCHEMA_VERSION = "v983_week_collection_v1"
TRADES_CSV = "trades_3factor.csv"

# Regime tag helpers (shared with analyzer reports)
SESSION_HOURS_UTC = (
    (0, 8, "ASIA"),
    (8, 13, "LONDON"),
    (13, 16, "OVERLAP"),
    (16, 22, "NEW_YORK"),
    (22, 24, "ASIA"),
)


def _root() -> Path:
    return Path(os.getenv("RESEARCH_ACCUMULATOR_ROOT", Path(__file__).resolve().parent))


def _db_path(root: Path | None = None) -> Path:
    root = root or _root()
    d = root / ACCUMULATOR_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / DB_NAME


def _status_path(root: Path | None = None) -> Path:
    return (root or _root()) / ACCUMULATOR_DIR / STATUS_FILE


def _export_csv_path(root: Path | None = None) -> Path:
    return (root or _root()) / ACCUMULATOR_DIR / EXPORT_CSV


@contextmanager
def _connect(root: Path | None = None) -> Iterator[sqlite3.Connection]:
    # sqlite3's own context manager commits but never closes; the leaked
    # handle kept the DB locked for the life of the analyzer process.
    conn = sqlite3.connect(_db_path(root), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _meta_get(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def init_db(root: Path | None = None) -> None:
    with _connect(root) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                inserted_at TEXT NOT NULL,
                close_ts TEXT,
                research_lane TEXT,
                net_pnl_usd REAL,
                regime_json TEXT,
                row_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                synced_at TEXT NOT NULL,
                new_rows INTEGER NOT NULL,
                total_rows INTEGER NOT NULL,
                note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_trades_close_ts ON trades(close_ts);
            CREATE INDEX IF NOT EXISTS idx_trades_lane ON trades(research_lane);
            """
        )
        if _meta_get(conn, "schema_version") is None:
            _meta_set(conn, "schema_version", SCHEMA_VERSION)
            _meta_set(conn, "backfill_policy", "none — epoch-only from v9.83 fresh collection")
        conn.commit()


def _parse_ts(raw) -> pd.Timestamp | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    try:
        return pd.to_datetime(raw, utc=True, errors="coerce")
    except Exception:
        return None


def _session_from_ts(ts: pd.Timestamp | None) -> str:
    if ts is None or pd.isna(ts):
        return "UNKNOWN"
    h = int(ts.hour)
    for lo, hi, label in SESSION_HOURS_UTC:
        if lo <= h < hi:
            return label
    return "UNKNOWN"


def _adx_bucket(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "unknown"
    if x < 18:
        return "adx_low"
    if x < 30:
        return "adx_mid"
    return "adx_high"


def _vol_bucket(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "unknown"
    if x < 0.8:
        return "vol_low"
    if x < 1.5:
        return "vol_mid"
    return "vol_high"


def _funding_bucket(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "neutral"
    if x > 0.0001:
        return "funding_positive"
    if x < -0.0001:
        return "funding_negative"
    return "funding_neutral"


def _liquidity_bucket(row: dict) -> str:
    try:
        vr = float(row.get("features_volume_ratio") or row.get("volume_ratio") or 0)
    except (TypeError, ValueError):
        vr = 0.0
    try:
        spread = abs(float(row.get("directional_factor_spread") or row.get("factor_spread") or 0))
    except (TypeError, ValueError):
        spread = 0.0
    if vr >= 1.2 or spread >= 4:
        return "liquidity_high"
    if vr > 0:
        return "liquidity_mid"
    return "liquidity_low"


def compute_regime_tags(row: dict) -> dict:
    ts = _parse_ts(row.get("close_ts") or row.get("ts") or row.get("entry_ts"))
    day_type = "unknown"
    if ts is not None and pd.notna(ts):
        day_type = "weekend" if ts.dayofweek >= 5 else "weekday"
    funding_raw = (
        row.get("funding_rate_pct_8h_at_entry")
        or row.get("funding_rate_pct_8h")
        or row.get("funding_rate")
    )
    vol_raw = row.get("volatility") or row.get("features_volatility")
    tags = {
        "day_type": day_type,
        "session": _session_from_ts(ts),
        "adx": _adx_bucket(row.get("adx_at_entry") or row.get("adx")),
        "volatility": _vol_bucket(vol_raw),
        "funding": _funding_bucket(funding_raw),
        "liquidity": _liquidity_bucket(row),
        "spread_bucket": str(row.get("directional_spread_bucket") or "unknown"),
    }
    tags["regime_key"] = "|".join(
        [
            tags["day_type"],
            tags["session"],
            tags["adx"],
            tags["volatility"],
            tags["funding"],
            tags["liquidity"],
        ]
    )
    return tags


def _data_dir_candidates(root: Path | None = None, data_dir: str | Path | None = None) -> list[Path]:
    """Where the canonical trades CSV and session live, most explicit first.

    The DB lives beside this module, but the evidence lives in the analyzer's
    data mirror (``BTC_AGENT_DATA_DIR``; the analyzer's cwd is its report dir
    inside that mirror).  Reading only the module dir silently found no CSV.
    """
    root = root or _root()
    candidates: list[Path] = []
    for raw in (data_dir, os.getenv("BTC_AGENT_DATA_DIR")):
        if raw:
            candidates.append(Path(raw))
    cwd = Path.cwd()
    candidates.extend([
        cwd, cwd.parent, root,
        root / "doxedcryptofounder" / "services" / "btc-conservative-agent",
    ])
    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def resolve_data_dir(root: Path | None = None, data_dir: str | Path | None = None) -> Path | None:
    for path in _data_dir_candidates(root, data_dir):
        if (path / TRADES_CSV).is_file():
            return path
    return None


def _load_session(root: Path | None = None, data_dir: str | Path | None = None) -> dict:
    for base in _data_dir_candidates(root, data_dir):
        p = base / "research_session.json"
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def current_collection_epoch(session: dict | None) -> tuple[str | None, pd.Timestamp | None, str]:
    """Return (epoch_id, epoch_start, source) for the live collection epoch.

    The collector epoch (``collector_v22_epoch_*``) supersedes the bot process
    start: a long-lived bot keeps its start time across collector epochs.
    """
    session = session or {}
    epoch_id = None
    epoch_ts = None
    for key in sorted(session):
        if key.startswith("collector_") and key.endswith("_epoch_id") and session.get(key):
            ts_key = key[: -len("_id")] + "_ts"
            try:
                candidate_ts = pd.Timestamp(float(session.get(ts_key)), unit="s", tz="UTC")
            except (TypeError, ValueError):
                continue
            if epoch_ts is None or candidate_ts > epoch_ts:
                epoch_id, epoch_ts = str(session[key]), candidate_ts
    if epoch_ts is not None:
        return epoch_id, epoch_ts, "COLLECTOR_EPOCH"
    if session.get("fresh_collection_mode") or session.get("fresh_collection_start_iso"):
        epoch = _parse_ts(session.get("fresh_collection_start_iso"))
        if (epoch is None or pd.isna(epoch)) and session.get("fresh_collection_start_time"):
            try:
                epoch = pd.Timestamp(float(session["fresh_collection_start_time"]), unit="s", tz="UTC")
            except Exception:
                epoch = None
        if epoch is not None and pd.notna(epoch):
            return None, epoch, "FRESH_COLLECTION_START"
    return None, None, "NONE"


def _active_registry_lanes() -> frozenset[str]:
    try:
        from combo_pathway_config import ACTIVE_TILE_ORDER
    except ImportError:
        return frozenset()
    return frozenset(str(lane).upper() for lane in ACTIVE_TILE_ORDER)


def _row_in_current_cohort(row: dict, epoch_id: str | None, epoch: pd.Timestamp, lanes: frozenset[str]) -> str | None:
    """Return None when the row belongs to the current cohort, else a reason code."""
    if lanes and str(row.get("research_lane") or "").strip().upper() not in lanes:
        return "NON_REGISTRY_LANE"
    row_epoch = str(row.get("epoch_id") or row.get("research_collection_id") or "").strip()
    if epoch_id and row_epoch and row_epoch.lower() not in {"nan", "none"}:
        return None if row_epoch == epoch_id else "OTHER_EPOCH"
    rts = _parse_ts(row.get("close_ts") or row.get("ts") or row.get("entry_ts"))
    if rts is not None and pd.notna(rts) and rts < epoch:
        return "BEFORE_EPOCH"
    return None


def _bot_session_epoch(session: dict) -> pd.Timestamp | None:
    if not session:
        return None
    if session.get("bot_start_time"):
        try:
            return pd.Timestamp(float(session["bot_start_time"]), unit="s", tz="UTC")
        except Exception:
            pass
    if session.get("bot_start_iso_utc"):
        raw = str(session["bot_start_iso_utc"]).replace(" UTC", "+00:00").replace("Z", "+00:00")
        return _parse_ts(raw)
    return None


def _is_v983_era_session(session: dict) -> bool:
    label = str(session.get("bot_version") or session.get("analyzer_sync_id") or "")
    return "v9.83" in label or "quality-roster-4-tiles" in label


def _resolve_epoch_start(session: dict | None, root: Path | None = None) -> pd.Timestamp:
    """Collection epoch — no trades before this are ingested (no historical backfill)."""
    session = session or _load_session(root)
    init_db(root)
    current_id, current_ts, current_source = current_collection_epoch(session)
    with _connect(root) as conn:
        stored = _meta_get(conn, "epoch_start_iso")
        if current_ts is not None:
            # Follow the live collector epoch; a persisted epoch from an older
            # session must not pin the accumulator forever.
            stored_ts = _parse_ts(stored) if stored else None
            if (
                stored_ts is None or pd.isna(stored_ts) or stored_ts != current_ts
                or (_meta_get(conn, "epoch_id") or None) != current_id
            ):
                _meta_set(conn, "epoch_start_iso", current_ts.isoformat())
                _meta_set(conn, "epoch_id", current_id or "")
                _meta_set(conn, "epoch_reason", f"re-anchored to current {current_source}")
                if stored:
                    _meta_set(conn, "previous_epoch_start_iso", str(stored))
                conn.commit()
            return current_ts
        total = int(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
        bot_epoch = _bot_session_epoch(session)

        if stored:
            ts = _parse_ts(stored)
            if ts is not None and pd.notna(ts):
                if total == 0 and bot_epoch is not None and bot_epoch < ts:
                    ts = bot_epoch
                    _meta_set(conn, "epoch_start_iso", ts.isoformat())
                    _meta_set(conn, "epoch_reason", "re-anchored to v9.83 bot session start")
                    conn.commit()
                return ts

        epoch = None
        if session.get("fresh_collection_mode") or session.get("fresh_collection_start_iso"):
            epoch = _parse_ts(session.get("fresh_collection_start_iso"))
            if epoch is None and session.get("fresh_collection_start_time"):
                try:
                    epoch = pd.Timestamp(float(session["fresh_collection_start_time"]), unit="s", tz="UTC")
                except Exception:
                    pass
        elif _is_v983_era_session(session) and bot_epoch is not None:
            epoch = bot_epoch
        if epoch is None or pd.isna(epoch):
            epoch = pd.Timestamp.now(tz="UTC")
        _meta_set(conn, "epoch_start_iso", epoch.isoformat())
        _meta_set(
            conn,
            "epoch_reason",
            "v9.83 week collection — session-era only, no archive backfill",
        )
        conn.commit()
        return epoch


def _load_csv_trades(root: Path | None = None, data_dir: str | Path | None = None) -> pd.DataFrame:
    base = resolve_data_dir(root, data_dir)
    if base is None:
        return pd.DataFrame()
    return pd.read_csv(base / TRADES_CSV, low_memory=False)


def _disk_ok(root: Path | None = None, min_mb: int = 500) -> bool:
    """Ensure enough free disk for week collection (CSV + DB + reports)."""
    try:
        usage = shutil.disk_usage(root or _root())
        free_mb = usage.free / (1024 * 1024)
        return free_mb >= min_mb
    except Exception:
        return True


def sync_accumulator(
    session: dict | None = None,
    root: Path | None = None,
    data_dir: str | Path | None = None,
) -> dict:
    """Ingest new closed trades from CSV into SQLite. Called each analyzer iteration."""
    root = root or _root()
    if not _disk_ok(root):
        return {"new": 0, "total": 0, "error": "low_disk_space", "min_free_mb": 500}
    init_db(root)
    # The caller's session may predate the collector-epoch keys; the data
    # mirror's session file is authoritative for keys the caller lacks.
    session = {**_load_session(root, data_dir), **(session or {})}
    epoch = _resolve_epoch_start(session, root)
    epoch_id = current_collection_epoch(session)[0]
    lanes = _active_registry_lanes()
    source_dir = resolve_data_dir(root, data_dir)
    df = _load_csv_trades(root, data_dir)
    if df.empty:
        status = build_status(root=root, session=session)
        status["trades_csv"] = str(source_dir / TRADES_CSV) if source_dir else None
        if source_dir is None:
            status["error"] = "TRADES_CSV_NOT_FOUND"
        _write_status(status, root)
        return {
            "new": 0, "total": status.get("total_trades", 0), "skipped_epoch": 0,
            "epoch": epoch.isoformat(), "epoch_id": epoch_id,
            "trades_csv": status["trades_csv"], "error": status.get("error"),
        }

    if "trade_id" not in df.columns:
        return {"new": 0, "total": 0, "error": "trades CSV missing trade_id"}

    work = df.drop_duplicates(subset=["trade_id"], keep="last").copy()
    skipped_epoch = 0
    skipped_non_registry = 0
    new_rows = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    with _connect(root) as conn:
        existing = {r[0] for r in conn.execute("SELECT trade_id FROM trades").fetchall()}
        for _, row in work.iterrows():
            tid = str(row.get("trade_id") or "").strip()
            if not tid or tid in existing:
                continue
            row_dict = {k: (None if pd.isna(v) else v) for k, v in row.items()}
            exclusion = _row_in_current_cohort(row_dict, epoch_id, epoch, lanes)
            if exclusion == "NON_REGISTRY_LANE":
                skipped_non_registry += 1
                continue
            if exclusion:
                skipped_epoch += 1
                continue
            tags = compute_regime_tags(row_dict)
            try:
                pnl = float(row_dict.get("net_pnl_usd") or row_dict.get("pnl") or 0)
            except (TypeError, ValueError):
                pnl = 0.0
            conn.execute(
                """
                INSERT OR IGNORE INTO trades
                (trade_id, inserted_at, close_ts, research_lane, net_pnl_usd, regime_json, row_json)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    tid,
                    now_iso,
                    str(row_dict.get("close_ts") or row_dict.get("ts") or ""),
                    str(row_dict.get("research_lane") or "UNKNOWN").upper(),
                    pnl,
                    json.dumps(tags),
                    json.dumps(row_dict, default=str),
                ),
            )
            if conn.total_changes:
                new_rows += 1
                existing.add(tid)

        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn.execute(
            "INSERT INTO sync_log(synced_at, new_rows, total_rows, note) VALUES (?,?,?,?)",
            (now_iso, new_rows, total, f"epoch>={epoch.isoformat()}"),
        )
        _meta_set(conn, "last_sync_at", now_iso)
        _meta_set(conn, "last_new_rows", str(new_rows))
        _meta_set(conn, "total_trades", str(total))
        conn.commit()

    export_accumulated_csv(root)
    status = build_status(root=root, session=session)
    status.update({
        "trades_csv": str(source_dir / TRADES_CSV) if source_dir else None,
        "last_skipped_epoch": skipped_epoch,
        "last_skipped_non_registry_lane": skipped_non_registry,
    })
    _write_status(status, root)
    return {
        "new": new_rows,
        "total": status["total_trades"],
        "skipped_epoch": skipped_epoch,
        "skipped_non_registry_lane": skipped_non_registry,
        "epoch": epoch.isoformat(),
        "epoch_id": epoch_id,
        "trades_csv": status["trades_csv"],
        "db_path": str(_db_path(root).resolve()),
    }


def _current_cohort_records(conn: sqlite3.Connection) -> list[dict]:
    epoch = _parse_ts(_meta_get(conn, "epoch_start_iso"))
    epoch_id = _meta_get(conn, "epoch_id") or None
    lanes = _active_registry_lanes()
    rows = conn.execute("SELECT row_json FROM trades ORDER BY close_ts").fetchall()
    records = [json.loads(r["row_json"]) for r in rows]
    if epoch is None or pd.isna(epoch):
        return records
    return [row for row in records if _row_in_current_cohort(row, epoch_id, epoch, lanes) is None]


def load_accumulated_trades_df(root: Path | None = None) -> pd.DataFrame:
    root = root or _root()
    if not _db_path(root).is_file():
        return pd.DataFrame()
    with _connect(root) as conn:
        records = _current_cohort_records(conn)
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def export_accumulated_csv(root: Path | None = None) -> Path | None:
    df = load_accumulated_trades_df(root)
    if df.empty:
        return None
    out = _export_csv_path(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    return out


def build_status(root: Path | None = None, session: dict | None = None) -> dict:
    root = root or _root()
    init_db(root)
    epoch = _resolve_epoch_start(session or {}, root)
    with _connect(root) as conn:
        stored_total = int(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
        current = _current_cohort_records(conn)
        total = len(current)
        epoch_id = _meta_get(conn, "epoch_id") or None
        epoch_reason = _meta_get(conn, "epoch_reason")
        last = conn.execute(
            "SELECT synced_at, new_rows, total_rows FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    lane_totals: dict[str, dict] = {}
    regime_totals: dict[str, int] = {}
    for row in current:
        lane = str(row.get("research_lane") or "UNKNOWN").upper()
        try:
            pnl = float(row.get("net_pnl_usd") or row.get("pnl") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        cell = lane_totals.setdefault(lane, {"n": 0, "pnl": 0.0})
        cell["n"] += 1
        cell["pnl"] += pnl
        regime_key = compute_regime_tags(row)["regime_key"]
        regime_totals[regime_key] = regime_totals.get(regime_key, 0) + 1
    lane_stats = {
        lane: {"n": cell["n"], "pnl": round(cell["pnl"], 2)}
        for lane, cell in sorted(lane_totals.items(), key=lambda item: -item[1]["n"])
    }
    regime_counts = [
        {"rk": key, "n": n}
        for key, n in sorted(regime_totals.items(), key=lambda item: (-item[1], item[0]))[:20]
    ]
    return {
        "schema": "research_accumulator_status_v1",
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "epoch_start_iso": epoch.isoformat(),
        "epoch_id": epoch_id,
        "epoch_reason": epoch_reason,
        "current_cohort": "CURRENT_EPOCH_ACTIVE_REGISTRY_LANES",
        "active_registry_lanes": sorted(_active_registry_lanes()),
        "stored_trades_all_epochs": stored_total,
        "backfill_policy": "none — only trades after epoch",
        "sync_interval_minutes": int(os.getenv("ANALYZER_INTERVAL_MINUTES", "30")),
        "total_trades": total,
        "last_sync": dict(last) if last else None,
        "by_lane": lane_stats,
        "top_regime_cells": [{"regime": r["rk"], "n": r["n"]} for r in regime_counts],
        "db_path": str(_db_path(root).resolve()),
        "export_csv": str(_export_csv_path(root).resolve()) if _export_csv_path(root).is_file() else None,
        "target_trades_for_roster": 200,
        "collection_goal_days": 7,
    }


def _write_status(status: dict, root: Path | None = None) -> None:
    path = _status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2), encoding="utf-8")


def sync_accumulator_from_analyzer_run(session=None, trades=None, root=None, data_dir=None) -> dict:
    """Entry point from analyzer finalize — always reads live CSV for completeness."""
    return sync_accumulator(session=session, root=root, data_dir=data_dir)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Sync research trade accumulator from trades_3factor.csv")
    ap.add_argument("--root", default=str(_root()))
    args = ap.parse_args()
    result = sync_accumulator(root=Path(args.root))
    print(json.dumps(result, indent=2))
