"""Tile performance from the V3 execution ledger, plus proof / deploy / auto-ff receipts."""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from .facts import iso, parse_ts


def tile_stats(store, facts: dict, now: float | None = None) -> pd.DataFrame:
    now = time.time() if now is None else now
    try:
        df = store.frame("""
            SELECT research_lane AS lane, fill_id, max(close_ts) AS close_ts, sum(net_pnl_usd) AS net_usd,
                   any_value(exit_reason) AS exit_reason, any_value(deployed_revision) AS revision
            FROM raw_execution WHERE fill_id IS NOT NULL AND close_ts IS NOT NULL GROUP BY 1, 2""")
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    if df.empty:
        return df
    df["close_epoch"] = [parse_ts(x) for x in df["close_ts"]]
    rt = facts.get("runtime") or {}
    enabled = rt.get("research_lane_enabled") or {}
    roster = set(rt.get("active_tile_lanes") or [])
    rows = []
    for lane, g in df.groupby("lane"):
        for wname, wsec in (("24h", 86400), ("7d", 7 * 86400), ("all", None)):
            w = g if wsec is None else g[g["close_epoch"] >= now - wsec]
            if w.empty:
                continue
            pnl = w["net_usd"].fillna(0).to_numpy(float)
            rows.append({"lane": lane, "window": wname, "closes": int(len(w)), "wins": int((pnl > 0).sum()),
                         "win_rate": float((pnl > 0).mean()), "net_usd": float(pnl.sum()), "mean_usd": float(pnl.mean()),
                         "worst_usd": float(pnl.min()), "best_usd": float(pnl.max()),
                         "last_close": iso(float(np.nanmax(w["close_epoch"]))) if w["close_epoch"].notna().any() else None,
                         "in_roster": lane in roster, "enabled": bool(enabled.get(lane, False)),
                         "exit_reasons": json.dumps(w["exit_reason"].value_counts().head(6).to_dict())})
    return pd.DataFrame(rows)


def receipts(store, facts: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    runs = [r.get("value", r) if isinstance(r, dict) else r for r in (facts.get("deploys") or {}).get("runs") or []]
    runs = sorted([r for r in runs if isinstance(r, dict)], key=lambda r: parse_ts(r.get("createdAt")) or 0, reverse=True)
    proof = facts.get("proof_active") or {}
    latest_row = None
    receipt = proof.get("receipt")
    if receipt:
        from .facts import tail_jsonl  # noqa: PLC0415
        rows = [r for r in tail_jsonl(receipt, 20) if r.get("kind") == "ROW"]
        if rows:
            r = rows[-1]
            latest_row = {"at": r.get("at"), "status": r.get("status"), "failed_checks": r.get("failed_checks"),
                          "checks": {k: {"ok": v.get("ok"), "detail": v.get("detail")} for k, v in (r.get("checks") or {}).items()}}
    return {
        "schema": "self_aware_receipts_v1", "generated_at": iso(now),
        "deploys": [{k: r.get(k) for k in ("databaseId", "status", "conclusion", "headSha", "createdAt", "updatedAt", "url",
                                            "event", "displayTitle")} for r in runs[:8]],
        "proof": {"window_t0": proof.get("t0"), "ends_at": proof.get("ends_at"), "receipt": receipt,
                  "status": proof.get("status"), "latest_row": latest_row,
                  "baseline_git_rev": (proof.get("baseline") or {}).get("git_rev")},
        "auto_ff": (facts.get("autoff") or [])[-8:],
        "manual_interventions": (facts.get("manual") or [])[-8:],
        "tests": {"note": "CI deploy-gate results are the guarded deploy runs above (fly-bot-deploy.yml); "
                          "self-aware unit tests: scripts/test_self_aware.py"},
        "revisions": {"fly": (facts.get("runtime") or {}).get("git_rev"), "v2c_head": facts.get("analyzer_head"),
                      "analyzer_run": (facts.get("analyzer_run") or {}).get("revision"),
                      "analyzer_dashboard": facts.get("analyzer_dashboard_rev"), "self_aware": store.revision},
    }
