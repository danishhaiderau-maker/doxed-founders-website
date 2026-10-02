"""Versioned, machine-readable analyzer export for agents.

Every completed analyzer generation writes::

    <root>/history/<UTC stamp>-<rev12>-<generation12>/summary.json + tables
    <root>/latest/                          (same files; summary.json replaced last)
    <root>/latest_export_id.txt, <root>/analyzer_client.py, <root>/README.md

``root`` is ``DOXXED_ANALYZER_EXPORT_DIR`` (default
``C:\\DoxxedCrypto\\analyzer-exports``). Tables are CSV always and Parquet when
pyarrow is importable; ``summary.json`` carries sha256 for every table file so
readers can prove they loaded one consistent generation. Nothing is ever
deleted here: history accumulates (retention is a separate, explicit decision).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

EXPORT_SCHEMA = "analyzer_export_v1"
DEFAULT_EXPORT_ROOT = r"C:\DoxxedCrypto\analyzer-exports"
FRESHNESS_MAX_AGE_MIN = 45
LATEST_POINTER = "latest_export_id.txt"     # not "LATEST": Windows paths are case-insensitive
TABLES = ("tile_stats", "hypotheses", "hypothesis_trades", "family_tests", "walk_forward", "correlation",
          "sim_parity", "stream_health", "quarantine", "main_rankings", "exit_regret", "exit_regret_trades",
          "taker_counterfactual", "fill_markouts", "research_events", "stream_study_health",
          "event_study_hypotheses", "data_health_streams")
EVENT_STUDY_REPORT = "event_study_report.json"
DATA_HEALTH_REPORT = "data_health_report.json"
COVERAGE_COLUMNS = ("epoch_coverage_share", "in_window_present_share", "first_available", "horizon_hours",
                    "tier_a_rows", "archive_rows", "mirror_rows")
# Table owned by each staged group (stage_group); a group that did not run exports empty tables.
GROUP_TABLES = {
    "main_rankings": ("main_rankings",),
    "stream_studies": ("exit_regret", "exit_regret_trades", "taker_counterfactual", "fill_markouts",
                       "research_events", "stream_study_health"),
}
TILE_VERDICT_COLUMNS = ("n_tested", "p_holm", "q_bh", "corrected_verdict")

_STAGED: dict = {}
_GROUPS: dict = {}


def export_root() -> str:
    return os.environ.get("DOXXED_ANALYZER_EXPORT_DIR") or DEFAULT_EXPORT_ROOT


def stage_strategy_lab(payload: Optional[dict], tables: Optional[dict]) -> None:
    """Hold this generation's strategy-lab output until the export at finalize."""
    _STAGED.clear()
    _STAGED["payload"] = payload or {}
    _STAGED["tables"] = dict(tables or {})


def stage_group(group: str, summary: Optional[dict], tables: Optional[dict]) -> None:
    """Hold a non-lab report group (main rankings, stream studies) for this generation's export."""
    _GROUPS[group] = {"summary": summary or {}, "tables": dict(tables or {})}


def _tile_verdicts(rankings: dict) -> dict:
    rows = (((rankings or {}).get("families") or {}).get("tiles") or {}).get("rows") or []
    return {str(r.get("key")): r for r in rows}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v = float(value)
        return v if math.isfinite(v) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return str(value)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tile_stats(trades: Optional[pd.DataFrame], registry: Optional[dict], lanes) -> pd.DataFrame:
    """Per current tile: closed trades, after-cost PnL and an hour-cluster CI."""
    from strategy_lab.stats import cluster_ci

    rows = []
    t = trades if trades is not None else pd.DataFrame()
    if not t.empty and "trade_id" in t.columns:
        t = t.drop_duplicates(subset=["trade_id"], keep="last")
    lane_col = (t["research_lane"].fillna("").astype(str).str.upper() if "research_lane" in t.columns
                else pd.Series("", index=t.index, dtype=str))
    pnl_all = pd.to_numeric(t["net_pnl_usd"], errors="coerce") if "net_pnl_usd" in t.columns else pd.Series(dtype=float)
    ts_all = pd.to_datetime(t.get("close_ts", t.get("ts")), utc=True, errors="coerce") if len(t) else pd.Series(dtype=object)
    for lane in lanes:
        spec = (registry or {}).get(lane) or {}
        m = (lane_col == lane) & pnl_all.notna() if len(t) else pd.Series(dtype=bool)
        pnl = pnl_all[m] if len(t) else pd.Series(dtype=float)
        ts = ts_all[m] if len(t) else pd.Series(dtype=object)
        secs = ts.map(lambda x: x.timestamp() if pd.notna(x) else np.nan).to_numpy(float) if len(ts) else np.array([])
        ci = cluster_ci(pnl.to_numpy(float), (np.nan_to_num(secs) // 3600).astype(np.int64)) if len(pnl) else {}
        reasons = t.loc[m, "exit_reason"].astype(str).value_counts().to_dict() if len(pnl) and "exit_reason" in t else {}
        rows.append({
            "research_lane": lane,
            "tile_id": spec.get("tile_id"),
            "label": spec.get("label") or lane,
            "default_enabled": spec.get("default_enabled"),
            "paper_only": spec.get("paper_only"),
            "platform_relay_eligible": spec.get("platform_relay_eligible"),
            "exit_family": (spec.get("exit_policy") or {}).get("family"),
            "n": int(len(pnl)),
            "net_pnl_usd": float(pnl.sum()) if len(pnl) else 0.0,
            "mean_usd": float(pnl.mean()) if len(pnl) else None,
            "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
            "ci_lo_usd": ci.get("lo"), "ci_hi_usd": ci.get("hi"), "p_cluster": ci.get("p"),
            "ci_clusters": ci.get("clusters"),
            "first_close": _iso(np.nanmin(secs)) if len(secs) and np.isfinite(secs).any() else None,
            "last_close": _iso(np.nanmax(secs)) if len(secs) and np.isfinite(secs).any() else None,
            "exit_reasons": json.dumps(reasons, sort_keys=True),
        })
    return pd.DataFrame(rows)


def quarantine_table(report_dir: str) -> pd.DataFrame:
    path = os.path.join(report_dir, "trade_cohort_quarantine.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return pd.DataFrame(columns=["trade_id", "research_lane", "epoch_id", "ts", "net_pnl_usd", "reason"])
    return pd.DataFrame(data.get("rows_detail") or [])


def stream_health(data_dir: str, lab: dict, now: float) -> pd.DataFrame:
    from strategy_lab.streams import stream_inventory

    rows = stream_inventory(data_dir, now=now)
    derived = {
        "ai_tranche_log.csv": ((lab.get("ai_calls") or {}).get("end"), "last AI_DECISION row"),
        "market_microstructure_1s.jsonl": (_iso((lab.get("tape") or {}).get("end_ts"))
                                           if (lab.get("tape") or {}).get("end_ts") else None, "last valid BBO second"),
        "cross_venue_tape_1m.jsonl": (_iso(((lab.get("cross_venue") or {}).get("span") or {}).get("end_ts"))
                                      if ((lab.get("cross_venue") or {}).get("span") or {}).get("end_ts") else None,
                                      "last decoded minute"),
    }
    coverage = lab.get("stream_coverage") or {}
    for row in rows:
        last, basis = derived.get(row["stream"], (None, None))
        row["content_last_at"] = last
        row["content_basis"] = basis
        if last:
            lag = now - pd.Timestamp(last).timestamp()
            row["content_lag_sec"] = round(lag, 1)
            if lag > 3600 and row["status"] == "OK":
                row["status"] = "CONTENT_STALE"
        else:
            row["content_lag_sec"] = None
        cov = coverage.get(row["stream"]) or {}
        sources = cov.get("sources") or {}
        for col in COVERAGE_COLUMNS:
            row[col] = sources.get(col) if col.endswith("_rows") else cov.get(col)
    return pd.DataFrame(rows)


def _read_report(report_dir: str, name: str) -> dict:
    try:
        with open(os.path.join(report_dir, name), encoding="utf-8") as handle:
            doc = json.load(handle)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def event_study_summary(report_dir: str) -> tuple:
    """Lockbox counters per pre-registered event-study hypothesis (summary, table)."""
    doc = _read_report(report_dir, EVENT_STUDY_REPORT)
    if not doc:
        return {"status": "MISSING"}, pd.DataFrame()
    rows = []
    for h in doc.get("hypotheses") or []:
        lock = h.get("lockbox") or {}
        disc = h.get("discovery") or {}
        rows.append({"id": h.get("id"), "spec_hash": h.get("spec_hash"), "status": h.get("status"),
                     "metric": h.get("metric"), "min_lockbox_events": h.get("min_lockbox_events"),
                     "lockbox_open": lock.get("open"), "lockbox_scored": lock.get("scored"),
                     "lockbox_start_utc": lock.get("start_utc"), "lockbox_end_utc": lock.get("end_utc"),
                     "lockbox_events_counted": lock.get("events_counted"),
                     "lockbox_events_per_day": lock.get("events_per_day"),
                     "lockbox_days_to_min_sample": lock.get("days_to_min_sample"),
                     "discovery_n_events": disc.get("n_events"),
                     "discovery_events_with_controls": disc.get("events_with_controls")})
    summary = {"status": "OK", "schema": doc.get("schema"), "generated_ts": doc.get("generated_ts"),
               "span": doc.get("span"), "data_status": doc.get("data_status"),
               "registered_utc": (doc.get("registry") or {}).get("registered_utc"),
               "dataset_epoch": doc.get("dataset_epoch"), "hypotheses": rows}
    return summary, pd.DataFrame(rows)


def data_health_summary(report_dir: str) -> tuple:
    """Per-stream data-health verdicts (summary, table)."""
    doc = _read_report(report_dir, DATA_HEALTH_REPORT)
    if not doc:
        return {"status": "MISSING"}, pd.DataFrame()
    keys = ("stream", "source_file", "status", "cadence", "rows", "rows_24h", "first_ts", "last_ts",
            "staleness_sec", "coverage_pct_24h", "lag_vs_mirror_head_sec")
    rows = [{k: s.get(k) for k in keys} for s in doc.get("streams") or [] if isinstance(s, dict)]
    summary = {"status": doc.get("status"), "schema": doc.get("schema"), "generated_ts": doc.get("generated_ts"),
               "window_sec": doc.get("window_sec"), "mirror_head_ts": doc.get("mirror_head_ts"),
               "status_counts": doc.get("status_counts"), "dataset_epoch": doc.get("dataset_epoch"),
               "streams": {r["stream"]: {"status": r["status"], "coverage_pct_24h": r["coverage_pct_24h"],
                                         "staleness_sec": r["staleness_sec"]} for r in rows if r.get("stream")}}
    return summary, pd.DataFrame(rows)


def generation_health(report_dir: str) -> dict:
    """Generation receipt, BLOCKED inputs and ledger reconciliation of this generation."""
    receipt = _read_report(report_dir, "analyzer_generation_receipt.json")
    blockers = _read_report(report_dir, "analyzer_input_blockers.json")
    recon = _read_report(report_dir, "ledger_reconciliation.json")
    return {
        "generation_receipt": {k: receipt.get(k) for k in (
            "level", "complete", "reasons", "failed_required_studies", "failed_optional_studies",
            "integrity_status", "protection_replay_window", "ledger_reconciliation", "generated_at",
        )} if receipt else {"status": "MISSING"},
        "input_blockers": {"level": blockers.get("level"), "counts": blockers.get("counts"),
                           "epoch_id": blockers.get("epoch_id"),
                           "items": [{k: i.get(k) for k in ("input", "status", "reason_code", "reason")}
                                     for i in blockers.get("items") or [] if isinstance(i, dict)]}
        if blockers else {"status": "MISSING"},
        "ledger_reconciliation": {k: recon.get(k) for k in (
            "level", "reasons", "source_data_through", "win_pct_definition", "win_pct_source",
            "analyzer_cohort", "mirror_ledger", "quarantined", "cents_display_drift",
        )} if recon else {"status": "MISSING"},
    }


def _write_table(df: pd.DataFrame, directory: str, name: str) -> dict:
    out = {"rows": int(len(df)), "columns": list(map(str, df.columns))}
    csv_path = os.path.join(directory, f"{name}.csv")
    df.to_csv(csv_path, index=False)
    out["csv"] = {"file": f"{name}.csv", "sha256": _sha256(csv_path)}
    try:
        pq = df.copy()
        for c in pq.columns:
            if pq[c].dtype == object:
                pq[c] = pq[c].map(lambda v: v if v is None or isinstance(v, (str, bool, int, float))
                                  else json.dumps(v, default=_json_default))
        pq_path = os.path.join(directory, f"{name}.parquet")
        pq.to_parquet(pq_path, index=False)
        out["parquet"] = {"file": f"{name}.parquet", "sha256": _sha256(pq_path)}
    except Exception as exc:  # pyarrow missing or unconvertible column: CSV stays authoritative
        out["parquet_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _bundle_files(root: str) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for src, dst in (("client.py", "analyzer_client.py"), ("insights.py", "insights_client.py"),
                     ("EXPORT_README.md", "README.md"),
                     (os.path.join("..", "system_health_alerts.py"), "system_health_alerts.py"),
                     (os.path.join("..", "runtime_uptime.py"), "runtime_uptime.py")):
        s = os.path.join(here, src)
        if os.path.isfile(s):
            tmp = os.path.join(root, dst + ".tmp")
            shutil.copyfile(s, tmp)
            os.replace(tmp, os.path.join(root, dst))


def write_export(*, report_dir: str, data_dir: str, trades: Optional[pd.DataFrame], registry: Optional[dict],
                 lanes, manifest: Optional[dict] = None, now: Optional[float] = None,
                 root: Optional[str] = None) -> dict:
    """Write one generation; returns the summary (also on partial failure)."""
    now = float(now if now is not None else time.time())
    root = root or export_root()
    manifest = manifest
    if manifest is None:
        try:
            with open(os.path.join(report_dir, "report_manifest.json"), encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError):
            manifest = {}
    lab = _STAGED.get("payload") or {}
    lab_tables = _STAGED.get("tables") or {}
    groups = dict(_GROUPS)
    _GROUPS.clear()
    rankings = (groups.get("main_rankings") or {}).get("summary") or {}
    studies = (groups.get("stream_studies") or {}).get("summary") or {}
    tiles = tile_stats(trades, registry, lanes)
    verdicts = _tile_verdicts(rankings)
    for col in TILE_VERDICT_COLUMNS:
        tiles[col] = [(verdicts.get(str(lane)) or {}).get(col) for lane in tiles.get("research_lane", [])] \
            if len(tiles) else []
    event_study, event_table = event_study_summary(report_dir)
    data_health, health_table = data_health_summary(report_dir)
    tables = {
        "tile_stats": tiles,
        "stream_health": stream_health(data_dir, lab, now),
        "quarantine": quarantine_table(report_dir),
        "event_study_hypotheses": event_table,
        "data_health_streams": health_table,
    }
    for group, names in GROUP_TABLES.items():
        staged = (groups.get(group) or {}).get("tables") or {}
        for tname in names:
            df = staged.get(tname)
            tables[tname] = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    for name in ("hypotheses", "hypothesis_trades", "family_tests", "walk_forward", "correlation", "sim_parity"):
        df = lab_tables.get(name)
        tables[name] = df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    rev = str(manifest.get("analyzer_revision") or manifest.get("source_revision") or "unknown")
    gen = str(manifest.get("generation_id") or "nogen")
    stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}-{rev[:12]}-{gen[:12]}"
    os.makedirs(os.path.join(root, "history"), exist_ok=True)
    staging = tempfile.mkdtemp(prefix=f".staging-{name}-", dir=root)
    table_meta = {}
    for tname in TABLES:
        table_meta[tname] = _write_table(tables[tname], staging, tname)
    lab_summary = {k: lab.get(k) for k in ("status", "generated_at", "epoch_id", "epoch_start", "world", "usd_per_bp",
                                           "sizing_basis", "cost_model", "tape", "cross_venue", "ai_calls",
                                           "hypothesis_registry_signature", "registry_defects", "heavy", "timing",
                                           "method", "families", "sim_parity", "hypotheses", "error",
                                           "stream_coverage", "history_sources")}
    summary = {
        "schema": EXPORT_SCHEMA,
        "export_id": name,
        "generated_at": _iso(now),
        "generated_at_ts": now,
        "freshness_policy": {"max_age_min": FRESHNESS_MAX_AGE_MIN,
                             "rule": "refuse when older than max_age_min, when generation_id differs from the "
                                     "analyzer's current report_manifest, or when any table hash mismatches"},
        "generation": {
            "generation_id": manifest.get("generation_id"),
            "generation_started_at": manifest.get("generation_started_at"),
            "analyzer_completed_at": manifest.get("generated_at"),
            "analyzer_revision": manifest.get("analyzer_revision"),
            "source_revision": manifest.get("source_revision"),
            "deployed_revision": manifest.get("deployed_revision"),
            "analyzer_version": manifest.get("analyzer_version"),
            "dataset_epoch": manifest.get("dataset_epoch"),
            "dataset_checksum": manifest.get("dataset_checksum"),
            "manifest_entry_hash": manifest.get("manifest_entry_hash"),
            "tile_registry_signature": manifest.get("tile_registry_signature"),
            "active_tiles": manifest.get("active_tiles"),
            "fee_profile_receipt": manifest.get("fee_profile_receipt"),
            "report_manifest_path": os.path.join(os.path.abspath(report_dir), "report_manifest.json"),
        },
        "tables": table_meta,
        "strategy_lab": lab_summary,
        "main_rankings": {k: rankings.get(k) for k in ("status", "schema", "method", "family_summaries",
                                                       "tile_verdicts", "error", "tile_pool")} if rankings else
        {"status": "NOT_RUN"},
        "stream_studies": {k: studies.get(k) for k in ("status", "schema", "timing", "cache", "errors", "stream_health",
                                                       "exit_regret", "taker_counterfactual",
                                                       "fill_markouts", "research_events",
                                                       "history_sources")} if studies else
        {"status": "NOT_RUN"},
        "stream_coverage": lab.get("stream_coverage") or {},
        "event_study": event_study,
        "data_health": data_health,
        **generation_health(report_dir),
        "dashboard": {"export": "http://127.0.0.1:9001/api/export/latest",
                      "hypotheses": "http://127.0.0.1:9001/api/hypotheses",
                      "streams": "http://127.0.0.1:9001/api/streams/health",
                      "insights": "http://127.0.0.1:9001/api/insights"},
    }
    from strategy_lab.engine import _clean

    summary = _clean(json.loads(json.dumps(summary, default=_json_default)))
    with open(os.path.join(staging, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, allow_nan=False)
    # history copy: staging becomes the immutable history folder
    history_dir = os.path.join(root, "history", name)
    os.replace(staging, history_dir)
    # latest: copy tables first, summary.json last, so a reader never sees a new
    # summary pointing at old tables (hash check catches the reverse race)
    latest = os.path.join(root, "latest")
    os.makedirs(latest, exist_ok=True)
    for fname in sorted(os.listdir(history_dir)):
        if fname == "summary.json":
            continue
        tmp = os.path.join(latest, fname + ".tmp")
        shutil.copyfile(os.path.join(history_dir, fname), tmp)
        os.replace(tmp, os.path.join(latest, fname))
    tmp = os.path.join(latest, "summary.json.tmp")
    shutil.copyfile(os.path.join(history_dir, "summary.json"), tmp)
    os.replace(tmp, os.path.join(latest, "summary.json"))
    tmp = os.path.join(root, LATEST_POINTER + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(name + "\n")
    os.replace(tmp, os.path.join(root, LATEST_POINTER))
    _bundle_files(root)
    return summary
