"""Read-only JSON views of the agent export for the :9001 dashboard."""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

from strategy_lab.export import FRESHNESS_MAX_AGE_MIN, export_root

MAX_ROWS = 5000


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_summary(root: Optional[str]) -> tuple:
    latest = os.path.join(root or export_root(), "latest")
    try:
        with open(os.path.join(latest, "summary.json"), encoding="utf-8") as handle:
            return latest, json.load(handle)
    except (OSError, ValueError):
        return latest, None


def freshness(summary: Optional[dict], latest: str, report_root: Optional[str], now: Optional[float] = None) -> dict:
    """Same refusal rules as ``analyzer_client.load_latest`` (minus the live probe)."""
    now = float(now if now is not None else time.time())
    if not summary:
        return {"current": False, "reasons": ["EXPORT_MISSING"]}
    reasons = []
    age_min = (now - float(summary.get("generated_at_ts") or 0)) / 60.0
    limit = (summary.get("freshness_policy") or {}).get("max_age_min", FRESHNESS_MAX_AGE_MIN)
    if age_min > float(limit):
        reasons.append("EXPORT_TOO_OLD")
    for name, meta in (summary.get("tables") or {}).items():
        info = meta.get("csv") or {}
        fp = os.path.join(latest, info.get("file", f"{name}.csv"))
        if not os.path.isfile(fp) or _sha256(fp) != info.get("sha256"):
            reasons.append(f"TABLE_HASH_MISMATCH:{name}")
    gen = summary.get("generation") or {}
    manifest_gen = None
    if report_root:
        try:
            with open(os.path.join(report_root, "report_manifest.json"), encoding="utf-8") as handle:
                manifest_gen = json.load(handle).get("generation_id")
        except (OSError, ValueError):
            manifest_gen = None
        if manifest_gen and manifest_gen != gen.get("generation_id"):
            reasons.append("GENERATION_MISMATCH")
    return {"current": not reasons, "reasons": reasons, "age_min": round(age_min, 2), "max_age_min": limit,
            "export_generation_id": gen.get("generation_id"), "analyzer_generation_id": manifest_gen}


def _table_rows(latest: str, summary: dict, name: str, limit: int) -> dict:
    import pandas as pd

    meta = (summary.get("tables") or {}).get(name)
    if meta is None:
        return {"error": f"unknown table {name}", "tables": sorted(summary.get("tables") or {})}
    fp = os.path.join(latest, meta["csv"]["file"])
    df = pd.read_csv(fp) if meta.get("rows") else pd.DataFrame(columns=meta.get("columns") or [])
    rows = json.loads(df.head(max(int(limit), 0)).to_json(orient="records"))
    return {"table": name, "rows_total": int(len(df)), "rows_returned": len(rows), "rows": rows}


def export_latest(root: Optional[str] = None, report_root: Optional[str] = None, table: Optional[str] = None,
                  limit: int = MAX_ROWS) -> dict:
    latest, summary = _load_summary(root)
    fresh = freshness(summary, latest, report_root)
    if summary is None:
        return {"status": "EXPORT_MISSING", "freshness": fresh, "export_dir": latest}
    out = {"status": "OK" if fresh["current"] else "STALE", "freshness": fresh, "export_dir": latest}
    if table:
        out.update(_table_rows(latest, summary, table, min(int(limit), MAX_ROWS)))
    else:
        out["summary"] = summary
    return out


def hypotheses(root: Optional[str] = None, report_root: Optional[str] = None) -> dict:
    latest, summary = _load_summary(root)
    fresh = freshness(summary, latest, report_root)
    if summary is None:
        return {"status": "EXPORT_MISSING", "freshness": fresh}
    lab = summary.get("strategy_lab") or {}
    fam = _table_rows(latest, summary, "family_tests", MAX_ROWS) if "family_tests" in (summary.get("tables") or {}) else {}
    return {"status": "OK" if fresh["current"] else "STALE", "freshness": fresh,
            "export_id": summary.get("export_id"), "generation": summary.get("generation"),
            "method": lab.get("method"), "cost_model": lab.get("cost_model"), "sizing_basis": lab.get("sizing_basis"),
            "hypotheses": lab.get("hypotheses") or [], "families": lab.get("families") or [],
            "family_tests": fam.get("rows") or [], "sim_parity": lab.get("sim_parity")}


def streams_health(root: Optional[str] = None, report_root: Optional[str] = None) -> dict:
    latest, summary = _load_summary(root)
    fresh = freshness(summary, latest, report_root)
    if summary is None:
        return {"status": "EXPORT_MISSING", "freshness": fresh}
    rows = _table_rows(latest, summary, "stream_health", MAX_ROWS).get("rows") or []
    bad = [r["stream"] for r in rows if r.get("status") not in ("OK", None)]
    partial = [r["stream"] for r in rows if r.get("analyzer_usage") in ("ACTIVE_FILE_ONLY", "IGNORED", "HEALTH_ONLY")]
    return {"status": "OK" if fresh["current"] else "STALE", "freshness": fresh,
            "export_id": summary.get("export_id"), "streams": rows, "unhealthy": bad,
            "not_fully_analysed": partial}
