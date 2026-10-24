"""G1-G20 integrity gaps (HM 12:20 review). Pure evaluator.

The route gathers ``facts`` (each part fails soft to None); ``evaluate``
turns them into one row per gap with value / target / status / source.
Statuses: GREEN, AMBER, RED, NA (not applicable while live is OFF),
PENDING (source not shipped yet). Overall status = worst of GREEN/AMBER/RED.
"""
from __future__ import annotations

import json
import os
import re
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


_DATA_SUFFIXES = (".jsonl", ".json", ".csv", ".tgz", ".gz", ".tar", ".sqlite", ".sqlite3", ".db", ".npz", ".pkl")


# The pinned data epoch id (fly.toml DATA_EPOCH_ID; frozen by test_research_freeze). Its boundary receipts are read at
# boot (bot._data_epoch_boundary_status, epoch_boundary_rotation.run_boundary), so they are current-epoch state.
PINNED_EPOCH_ID = "ce-20261004-v31-freeze21b"

_RETAINED_RE = tuple(re.compile(x) for x in (
    r"runtime/v3/receipts/(?:emergency|owner|recovery)[^/]*/.*",
    r"runtime/live_copy_[^/]+\.json",
    r"live_copy_tile_eligibility\.json",  # volume-root live-switch config (BOT_DATA_DIR), retained
    r"runtime/data_epoch\.json",
    r"runtime/crash_dump\.json",
    r"runtime/clean-epoch-receipts/.*",
    r"runtime/data_epoch_boundary/[^/]+\.compaction\.json",
    r"runtime/data_epoch_boundary/" + re.escape(PINNED_EPOCH_ID) + r"\.(?:v3|research_events_v22)\.json",
    # read at boot (bot.py) and the idempotency gate for archive_pre_epoch_ledgers: pinned id only (HM 18 Oct)
    r"runtime/data_epoch_boundary/" + re.escape(PINNED_EPOCH_ID) + r"\.pre_epoch_archive\.json",
    r"quarantine/v3_completeness_markers/.*",
))
_RETAINED_DIR_RE = tuple(re.compile(x) for x in (
    r"runtime/v3/receipts/(?:emergency|owner|recovery)[^/]*",
    r"quarantine/v3_completeness_markers",
))


_ACTIVE_RESET_FILE = "runtime/research_reset_receipts/ACTIVE_RESET.json"
SIDECAR_NAME = "deletion.json.retained.jsonl"
SIDECAR_AMBER_BYTES = 200 * 1024 * 1024


def sidecar_size(root: str, active_reset: str | None = None) -> dict:
    """Size of the active reset's deletion sidecar (read-only). AMBER above 200 MB; it never blocks a wipe and the next
    reset clears it (a compact sidecar format is a later, non-urgent PR)."""
    rid = active_reset or active_reset_id(root)
    if not rid:
        return {"active_reset": None, "bytes": None, "status": "PENDING"}
    try:
        size = os.stat(os.path.join(root, "runtime", "research_reset_receipts", rid, SIDECAR_NAME)).st_size
    except OSError:
        return {"active_reset": rid, "bytes": 0, "status": "GREEN"}
    return {"active_reset": rid, "bytes": size, "mb": round(size / 1048576, 1), "amber_above_mb": 200,
            "status": "AMBER" if size > SIDECAR_AMBER_BYTES else "GREEN"}


def active_reset_id(root: str) -> str | None:
    """The reset id bot.py currently reads from ACTIVE_RESET.json (24 lowercase hex), else None. Read-only."""
    try:
        with open(os.path.join(root, *_ACTIVE_RESET_FILE.split("/")), "r", encoding="utf-8") as fh:
            rid = json.load(fh).get("reset_id")
    except Exception:  # noqa: BLE001
        return None
    return rid if isinstance(rid, str) and re.fullmatch(r"[0-9a-f]{24}", rid) else None


def retained_by_design(rel: str, active_reset: str | None = None) -> bool:
    """HM post-wipe 18 Oct: runtime state kept by design, never an old-data offender in G16.

    Anchored fullmatch on the path relative to the scan root: v3 emergency/owner/recovery receipts, runtime
    live_copy_*.json and quarantine/v3_completeness_markers/. A parent directory name or a basename elsewhere
    never exempts a path. The pinned keep_manifest (and its hash) is deliberately NOT changed."""
    rel = rel.replace("\\", "/").lstrip("/")
    if active_reset and rel == _ACTIVE_RESET_FILE:
        return True  # the pointer to the active reset; rewritten by the next reset (id-validated: only exists when active_reset is valid)
    if active_reset and rel == f"runtime/research_reset_receipts/{active_reset}/binding.json":
        return True  # the ACTIVE reset anchor only (bot.py reads it to resume/verify); an old reset dir is still flagged
    if active_reset and rel == f"runtime/research_reset_receipts/{active_reset}/{SIDECAR_NAME}":
        return True  # HM 19 Oct: the deletion sidecar of the ACTIVE reset only (id-matched like binding.json); next reset clears it
    return any(r.fullmatch(rel) for r in _RETAINED_RE)


def retained_dir_by_design(rel_dir: str) -> bool:
    rel_dir = rel_dir.replace("\\", "/").strip("/")
    return any(r.fullmatch(rel_dir) for r in _RETAINED_DIR_RE)


def scan_local_oldest(root: str, budget_s: float = 8.0, keep=None) -> dict:
    """G16 Fly side: oldest-data ts of every data file under root (recursive, no file cap).

    jsonl (and .jsonl.N) -> first row ts; every other data file incl. .tgz -> mtime. Files and whole
    directories on the pinned KEEP manifest are counted under ``kept`` and never become offenders.
    Returns {"files": {rel: ts}, "kept": {rel: ts}, "kept_dirs": [...], "scanned", "complete"}.
    """
    import time as _t
    import keep_manifest as km
    keep = km.FLY if keep is None else keep
    t0 = _t.monotonic()
    active_reset = active_reset_id(root)
    files, kept, kept_dirs, scanned, complete = {}, {}, [], 0, True
    sizes: dict = {}
    for d, dirs, names in os.walk(root):
        rel_d = os.path.relpath(d, root).replace(os.sep, "/")
        rel_d = "" if rel_d == "." else rel_d
        for sub in list(dirs):
            rd = f"{rel_d}/{sub}" if rel_d else sub
            if km.kept_dir(rd, keep) or retained_dir_by_design(rd):
                kept_dirs.append(rd)
                dirs.remove(sub)
        for n in names:
            if not (n.endswith(_DATA_SUFFIXES) or ".jsonl." in n):
                continue
            if _t.monotonic() - t0 > budget_s:
                complete = False
                break
            scanned += 1
            path = os.path.join(d, n)
            rel = f"{rel_d}/{n}" if rel_d else n
            ts = oldest_row_ts(path) if (n.endswith(".jsonl") or ".jsonl." in n) else None
            if ts is None:
                try:
                    ts = os.path.getmtime(path)
                except OSError:
                    continue
            (kept if (km.is_kept(rel, keep) or retained_by_design(rel, active_reset)) else files)[rel] = ts
            try:
                sizes[rel] = os.path.getsize(path)
            except OSError:
                pass
        if not complete:
            break
    return {"files": files, "kept": kept, "kept_dirs": sorted(kept_dirs), "scanned": scanned, "complete": complete,
            "sizes": sizes}


def prefix_of(rel: str, depth: int = 3) -> str:
    """runtime/<dir>/<dir> style prefix used for by-prefix G16 counts (shallower for short paths)."""
    parts = rel.replace("\\", "/").strip("/").split("/")
    return "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "."


def by_prefix(files: dict, sizes: dict, epoch: float | None = None, slack: float = 60.0, depth: int = 3) -> dict:
    """{prefix: {"files": n, "bytes": b}} over ALL files (or only those older than epoch). Uncapped: the true total."""
    out: dict = {}
    for rel, ts in files.items():
        if epoch is not None and not ts < float(epoch) - slack:
            continue
        row = out.setdefault(prefix_of(rel, depth), {"files": 0, "bytes": 0})
        row["files"] += 1
        row["bytes"] += int(sizes.get(rel) or 0)
    return dict(sorted(out.items(), key=lambda kv: kv[1]["files"], reverse=True))


_V3_GROWTH_CACHE: dict = {"at": 0.0, "doc": None}


def v3_growth(root: str, now: float | None = None, budget_s: float = 4.0, ttl: float = 1800.0) -> dict:
    """Files-per-day growth check for runtime/v3 (+ disk used/free): per top-level v3 dir total files, bytes and files
    written in the last 24 h. Cached for ttl seconds; a budget-limited walk marks the result incomplete."""
    import shutil
    import time as _t
    now = _t.time() if now is None else now
    if _V3_GROWTH_CACHE["doc"] is not None and now - _V3_GROWTH_CACHE["at"] < ttl:
        return _V3_GROWTH_CACHE["doc"]
    t0, complete, dirs = _t.monotonic(), True, {}
    base = os.path.join(root, "runtime", "v3")
    for d, _subs, names in os.walk(base):
        top = os.path.relpath(d, base).split(os.sep)[0]
        top = "." if top == "." else top
        row = dirs.setdefault(top, {"files": 0, "bytes": 0, "files_24h": 0})
        for n in names:
            try:
                st = os.stat(os.path.join(d, n))
            except OSError:
                continue
            row["files"] += 1
            row["bytes"] += st.st_size
            if now - st.st_mtime < 86400:
                row["files_24h"] += 1
        if _t.monotonic() - t0 > budget_s:
            complete = False
            break
    try:
        du = shutil.disk_usage(root)
        disk = {"used_bytes": du.used, "free_bytes": du.free, "total_bytes": du.total,
                "used_pct": round(100.0 * du.used / du.total, 1) if du.total else None}
    except OSError:
        disk = None
    doc = {"complete": complete, "dirs": dict(sorted(dirs.items(), key=lambda kv: kv[1]["files_24h"], reverse=True)),
           "files_24h_total": sum(v["files_24h"] for v in dirs.values()), "disk": disk, "at": now}
    _V3_GROWTH_CACHE.update(at=now, doc=doc)
    return doc


VERIFY_AFTER_SEC = 24 * 3600


def apply_lifecycle(out: dict, path: str, now: float) -> dict:
    """open -> fixed -> verified per gap: fixed once GREEN, verified after 24 h continuously GREEN.

    Any non-GREEN reading resets the gap to open. NA rows are marked n/a. State persists in ``path``.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            st = json.load(fh)
    except Exception:
        st = {}
    for r in out.get("rows") or []:
        g = st.setdefault(r["id"], {})
        if r["status"] == "NA":
            r["lifecycle"] = "n/a"
            continue
        if r["status"] == "GREEN":
            g.setdefault("green_since", now)
            age = now - float(g["green_since"])
            r["lifecycle"] = "verified" if age >= VERIFY_AFTER_SEC else "fixed"
            r["green_for_s"] = round(age)
            if r["lifecycle"] == "verified":
                g.setdefault("verified_at", now)
        else:
            g.pop("green_since", None)
            g.pop("verified_at", None)
            r["lifecycle"] = "open"
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    out["lifecycle_counts"] = {k: sum(1 for r in out.get("rows") or [] if r.get("lifecycle") == k)
                               for k in ("open", "fixed", "verified", "n/a")}
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

    # G14 measured the DISLOC PAUSED-skip ledger (disloc_paused_skips.jsonl); its only writer, disloc_engine, was
    # removed with Tiles 6/7 on 23 Oct 2026 (R9). The row stays (stable ids) as NA, never PENDING forever.
    rows.append(_row("G14", "PAUSED skips durable across deploy (DISLOC; retired)", None,
                     "n/a: Tiles 6/7 removed 23 Oct 2026", "NA", "retired: disloc_engine removed"))

    hb = ti.get("last_run_age_s")
    rows.append(_row("G15", "tile-integrity heartbeat age s", hb, "< 600 s",
                     "PENDING" if hb is None else ("GREEN" if hb < 600 else "RED"),
                     "/api/monitor/checks tile-integrity"))

    w = f.get("wipe") or {}
    fly_old = w.get("fly_files_older_than_epoch")
    r2_old = w.get("r2_objects_older_than_epoch")          # wipe job's own receipt
    r2_ind = w.get("r2_independent_older_than_epoch")      # box deep check's own R2 listing
    box_old = w.get("box_files_older_than_epoch")
    vals = [fly_old, r2_old, r2_ind, box_old]
    disagree = r2_old is not None and r2_ind is not None and r2_old != r2_ind
    try:
        import keep_manifest as _km
        _sha = _km.MANIFEST_SHA256
    except Exception:
        _sha = None
    keep_mismatch = [k for k in ("receipt_keep_manifest_sha256", "r2_independent_keep_manifest_sha256")
                     if w.get(k) is not None and w.get(k) != _sha]
    if w.get("r2_independent_error"):
        keep_mismatch.append("r2_independent_error")
    disagree = disagree or bool(keep_mismatch)
    if any(v for v in vals) or disagree:
        st = "RED"
    elif epoch is None or any(v is None for v in vals) or w.get("fly_scan_complete") is False:
        st = "PENDING"
    else:
        st = "GREEN"
    rows.append(_row("G16", "rows older than epoch (R2 / Fly / box) + bfx_book_10l archive",
                     {"epoch_ts": epoch, "r2_receipt": r2_old, "r2_independent": r2_ind,
                      "keep": w.get("keep"), "keep_manifest_mismatch": keep_mismatch,
                      "r2_independent_error": w.get("r2_independent_error"),
                      "r2_independent_kept": w.get("r2_independent_kept"),
                      "r2_independent_at": w.get("r2_independent_at"), "fly": fly_old, "box": box_old,
                      "fly_scanned": w.get("fly_scanned"), "fly_scan_complete": w.get("fly_scan_complete"),
                      "fly_offenders": w.get("fly_offenders"), "retire_plan": w.get("retire_plan"), "by_prefix": w.get("by_prefix"),
                      "kept_by_prefix": w.get("kept_by_prefix"), "v3_growth": w.get("v3_growth"), "archive_gaps": w.get("archive_gaps")},
                     "0 everywhere; receipt == independent R2 listing; daily archive, 0 gaps", st,
                     "Fly /app/data scan + fresh_start_wipe_receipt.json + box R2 listing; KEEP per pinned manifest"))

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

    v = f.get("vercel") or {}
    if not v or v.get("status") == "no credential":
        st = "PENDING"
    elif v.get("errors_recent"):
        st = "RED"
    elif v.get("ok") and (v.get("canceled_recent") or 0) >= 1:
        st = "GREEN"
    else:
        st = "AMBER"
    rows.append(_row("G20", "Vercel skips API-only builds (ignored = CANCELED), no ERROR builds",
                     {k: v.get(k) for k in ("ok", "canceled_recent", "errors_recent", "latest", "latest_ready")},
                     ">= 1 skipped, 0 errors, latest READY", st, "box platform receipt (Vercel API)"))

    sc = w.get("sidecar")
    rows.append(_row("G21", "deletion.json.retained.jsonl size (active reset sidecar; exempt from G16, never blocks a wipe)",
                     sc, "<= 200 MB (AMBER above)", "PENDING" if not isinstance(sc, dict) else sc.get("status", "PENDING"),
                     "Fly /app/data runtime/research_reset_receipts/<active>/"))

    worst = max((_RANK[r["status"]] for r in rows), default=0)
    status = {0: "GREEN", 1: "AMBER", 2: "RED"}[worst]
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"schema": SCHEMA, "status": status, "counts": counts, "epoch_ts": epoch,
            "alerts": [r["id"] for r in rows if r["status"] == "RED"], "rows": rows}
