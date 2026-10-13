"""G1-G20 integrity gaps (HM 12:20 review). Pure evaluator.

The route gathers ``facts`` (each part fails soft to None); ``evaluate``
turns them into one row per gap with value / target / status / source.
Statuses: GREEN, AMBER, RED, NA (not applicable while live is OFF),
PENDING (source not shipped yet). Overall status = worst of GREEN/AMBER/RED.
"""
from __future__ import annotations

import json
import os
from typing import Optional

SCHEMA = "integrity_gaps.v1"
NA_LIVE_OFF = "n/a while live OFF"
_RANK = {"GREEN": 0, "NA": 0, "PENDING": 1, "AMBER": 1, "RED": 2}


def _row(gid, metric, value, target, status, source):
    return {"id": gid, "metric": metric, "value": value, "target": target,
            "status": status, "source": source}


def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _pending(gid, metric, target, source):
    return _row(gid, metric, None, target, "PENDING", source)


def oldest_row_ts(path: str) -> Optional[float]:
    """First row's ts of a jsonl file (rows are append-only, so first = oldest)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            line = fh.readline()
        row = json.loads(line)
    except Exception:
        return None
    for key in ("ts", "s", "timestamp", "run_ts", "signal_ts"):
        v = row.get(key) if isinstance(row, dict) else None
        if isinstance(v, (int, float)):
            return float(v) / 1000.0 if v > 1e12 else float(v)
    return None


def scan_local_oldest(root: str, limit: int = 300) -> dict:
    """G16 Fly side: files whose first row predates the epoch are counted by the caller."""
    out = {}
    try:
        names = sorted(n for n in os.listdir(root) if n.endswith(".jsonl"))[:limit]
    except Exception:
        return out
    for n in names:
        ts = oldest_row_ts(os.path.join(root, n))
        if ts is not None:
            out[n] = ts
    return out


def evaluate(f: dict) -> dict:
    live_armed = f.get("live_armed")
    force_paper = f.get("force_paper")
    live_off = live_armed is False
    epoch = f.get("epoch_ts")
    rows = []

    ok = live_armed is False and force_paper is True
    rows.append(_row("LIVE_OFF", "live_armed / force_paper",
                     {"live_armed": live_armed, "force_paper": force_paper},
                     "live_armed=false, force_paper=true until Danish re-arms",
                     "GREEN" if ok else "RED", "state.live_armed, FORCE_PAPER_MODE"))

    lc = f.get("lifecycle") or {}
    for gid, metric, key, target in (
        ("G1", "stop placed/cancelled times", "stop_times_coverage", "100% of closed live chains"),
        ("G2", "exits mislabelled MANUAL_OR_EXCHANGE_CLOSE", "count_mislabelled", "0 / 24 h"),
        ("G3", "stale NO_ORDER_FOR_INTENT alerts > 1 h", "stale_alerts_over_1h", "0"),
        ("G4", "summary money-risk vs exchange exposure delta", "money_risk_delta", "0"),
    ):
        v = lc.get(key)
        if live_off:
            rows.append(_row(gid, metric, v, target, "NA", NA_LIVE_OFF))
        elif v is None:
            rows.append(_pending(gid, metric, target, "/api/monitor/live/lifecycle (not shipped)"))
        else:
            good = (v >= 1.0) if gid == "G1" else (v == 0)
            rows.append(_row(gid, metric, v, target, "GREEN" if good else "RED",
                             "/api/monitor/live/lifecycle"))

    actor = f.get("onoff_actor") or {}
    untagged = actor.get("untagged")
    rows.append(_row("G5", "on/off rows without actor", untagged, "0",
                     "PENDING" if untagged is None else ("GREEN" if untagged == 0 else "AMBER"),
                     "/api/live-copy/status history[].by"))

    ret = f.get("lifecycle_retention") or {}
    days = ret.get("retained_days")
    rows.append(_row("G6", "lifecycle retention across restarts (days)",
                     {"retained_days": days, "pre_restart_intents": ret.get("pre_restart_intents")},
                     ">= 7 d (or since epoch)",
                     "PENDING" if days is None else (
                         "GREEN" if days >= 7 or ret.get("covers_epoch") else "AMBER"),
                     "lifecycle_journal.jsonl"))

    hop = f.get("hop_windows") or {}
    hs = hop.get("status")
    rows.append(_row("G7", "hop lag windows (last20 + since_process_boot)", hs, "OK or EMPTY_LIVE_OFF",
                     "PENDING" if hs is None else ("GREEN" if hs in ("OK", "EMPTY_LIVE_OFF") else "AMBER"),
                     "/api/monitor/live/copy-status lags"))

    un = f.get("unlinked") or {}
    new24 = un.get("new_24h")
    rows.append(_row("G8", "new unlinked lifecycle rows / 24 h",
                     {"new_24h": new24, "total": un.get("total")}, "0",
                     "PENDING" if new24 is None else ("GREEN" if new24 == 0 else "AMBER"),
                     "lifecycle unlinked_count"))

    for gid, metric, key, target in (
        ("G9", "live decline rate", "decline_rate", "present"),
        ("G10", "fill_detect_lag p95 ms", "fill_detect_lag_p95_ms", "< 1000 ms"),
    ):
        v = (f.get("live_fidelity") or {}).get(key)
        if live_off:
            rows.append(_row(gid, metric, v, target, "NA", NA_LIVE_OFF))
        elif v is None:
            rows.append(_pending(gid, metric, target, "live fidelity"))
        else:
            good = True if gid == "G9" else v < 1000
            rows.append(_row(gid, metric, v, target, "GREEN" if good else "AMBER", "live fidelity"))

    ti = f.get("tile_integrity") or {}
    miss = _get(ti, "regime_coverage", "missing_after_epoch")
    rows.append(_row("G11", "regime_at_signal missing after epoch", miss, "0",
                     "PENDING" if miss is None else ("GREEN" if miss == 0 else "RED"),
                     "/api/monitor/tile-integrity regime-coverage"))

    pf = f.get("paper_fill") or {}
    v1 = pf.get("v1_share")
    depth_ok = pf.get("depth_fields_ok")
    st = "PENDING"
    if v1 is not None:
        st = "GREEN" if v1 >= 1.0 and depth_ok else "AMBER"
    rows.append(_row("G12", "book-priced v1 fills incl. depth_snapshot_signal + depth_snapshot_fill",
                     {"v1_share": v1, "depth_fields_ok": depth_ok,
                      "pending_migration": pf.get("pending_migration")},
                     "100% v1, both depth fields, diff <= 0.1 bp", st,
                     "paper_fill + tile-integrity fill-recompute"))

    off = pf.get("fill_site_violations")
    rows.append(_row("G13", "fill sites reading price/tape (AST)", off, "0 offenders (CI)",
                     "PENDING" if off is None else ("GREEN" if off == 0 else "AMBER"),
                     "paper_fill.fill_site_violations"))

    ps = f.get("paused") or {}
    durable = ps.get("durable_total")
    opens = ps.get("opens_while_paused")
    guarded_pos = ps.get("guarded_deploys_with_paused_gt0")
    if durable is None:
        st = "PENDING"
    elif opens:
        st = "RED"
    elif ps.get("survived_deploy") and (guarded_pos or 0) >= 1:
        st = "GREEN"
    else:
        st = "AMBER"
    rows.append(_row("G14", "PAUSED skips durable across deploy, >=1 guarded deploy PAUSED>0",
                     {"durable_total": durable, "survived_deploy": ps.get("survived_deploy"),
                      "guarded_deploys_with_paused_gt0": guarded_pos, "opens_while_paused": opens},
                     "survives deploy; >= 1 guarded deploy with PAUSED > 0; 0 opens", st,
                     "disloc_paused_skips.jsonl"))

    hb = ti.get("last_run_age_s")
    rows.append(_row("G15", "tile-integrity heartbeat age s", hb, "< 600 s",
                     "PENDING" if hb is None else ("GREEN" if hb < 600 else "RED"),
                     "/api/monitor/checks tile-integrity"))

    w = f.get("wipe") or {}
    fly_old = w.get("fly_files_older_than_epoch")
    r2_old = w.get("r2_objects_older_than_epoch")
    box_old = w.get("box_files_older_than_epoch")
    vals = [fly_old, r2_old, box_old]
    if epoch is None or any(v is None for v in vals):
        st = "PENDING" if not any(vals) else "RED"
    else:
        st = "GREEN" if not any(vals) else "RED"
    rows.append(_row("G16", "rows older than epoch (R2 / Fly / box) + bfx_book_10l archive",
                     {"epoch_ts": epoch, "r2": r2_old, "fly": fly_old, "box": box_old,
                      "fly_offenders": w.get("fly_offenders"),
                      "archive_gaps": w.get("archive_gaps")},
                     "0 everywhere; daily archive, 0 gaps", st,
                     "Fly scan + fresh_start_wipe_receipt.json"))

    rt = f.get("runtime") or {}
    p95 = rt.get("trade_lock_wait_p95_ms")
    mx = rt.get("trade_lock_wait_max_ms")
    lag = rt.get("scheduler_lag_max_ms")
    if p95 is None:
        st = "PENDING"
    elif p95 < 100 and (mx or 0) < 250 and (lag or 0) < 500:
        st = "GREEN"
    else:
        st = "AMBER"
    rows.append(_row("G17", "trade_lock wait/hold + scheduler lag (last hour)",
                     {"trade_lock_wait_p95_ms": p95, "trade_lock_wait_max_ms": mx,
                      "trade_lock_hold_max_ms": rt.get("trade_lock_hold_max_ms"),
                      "scheduler_lag_max_ms": lag, "cpu_pct_max": rt.get("cpu_pct_max")},
                     "wait p95 < 100, max < 250, lag < 500 ms", st,
                     "runtime_telemetry_1m phase_timings.trade_lock_wait / trade_lock_hold"))

    mm = f.get("arming_mismatch")
    rows.append(_row("G18", "Fly vs website arming mismatch", mm, "false",
                     "PENDING" if mm is None else ("GREEN" if mm is False else "RED"),
                     "/api/live-copy/status arming_mismatch"))

    rec = f.get("ledger_reconciliation")
    rows.append(_row("G19", "analyzer vs Fly ledger delta", rec, "0 after epoch",
                     "PENDING" if rec is None else ("GREEN" if rec == 0 else "AMBER"),
                     "/api/monitor/checks ledger"))

    rows.append(_row("G20", "Vercel skips API-only builds", "PR #662 merged", "skipped",
                     "GREEN", "vercel ignore step"))

    worst = max((_RANK[r["status"]] for r in rows), default=0)
    status = {0: "GREEN", 1: "AMBER", 2: "RED"}[worst]
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"schema": SCHEMA, "status": status, "counts": counts,
            "alerts": [r["id"] for r in rows if r["status"] == "RED"], "rows": rows}
