"""One-call agent insights feed: system health, alert history, live Fly bot, transfer/ACK, deploy queue, analyzer export.

    import insights_client                       # C:\\DoxxedCrypto\\analyzer-exports\\insights_client.py
    snap = insights_client.snapshot()
    snap["status"]        # COMPLETE (every component fresh) or PARTIAL
    snap["refused"]       # [{"component", "status", "reason"}] for every stale/unavailable component
    snap["components"]["fly_bot"]["data"]["tiles"]
    snap["active_alerts"]                        # currently open RED/AMBER alerts
    snap["components"]["alerts"]["data"]["alerts"]  # alert history, active first then newest first

Also served uncached at ``http://127.0.0.1:9001/api/insights``. Every
component carries ``status`` OK / STALE / UNAVAILABLE, ``as_of``, ``age_sec``
and ``max_age_sec``. A STALE or UNAVAILABLE component has ``data = None``:
old data is refused, never returned as if it were current. ``transfer`` can
also be DEGRADED (fresh data kept, but pulls are failing or the applied seq
came from the puller's state.json) or UNKNOWN (no applied seq anywhere). Read-only: no
file is written, nothing on Fly or Bitfinex is touched.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

SCHEMA = "agent_insights_v1"
STATE_DIR = os.environ.get("DOXXED_LAPTOP_CHAIN_STATE") or r"C:\DoxxedCrypto\laptop-chain"
HEALTH_ENDPOINT = os.environ.get("DOXXED_HEALTH_URL") or "http://127.0.0.1:9011/api/system-health"
FLY_STATUS_URL = os.environ.get("DOXXED_FLY_STATUS_URL") or "https://doxed-btc-bot.fly.dev/api/status"
WALL_PATH = os.environ.get("DOXXED_WALL_PATH") or r"C:\DoxxedCrypto\btc-v31-current\diagnostics\WALL-STATUS-FLY.md"

HEALTH_MAX_AGE_SEC = 15 * 60          # system_health THRESHOLDS["watcher_stale_sec"]
FLY_SNAPSHOT_MAX_AGE_SEC = 5 * 60     # laptop fly_runtime snapshot fallback when the live call fails
TRANSFER_MAX_AGE_SEC = 15 * 60
AI_SUCCESS_MAX_AGE_SEC = 15 * 60
EXPORT_MAX_AGE_SEC = 45 * 60          # analyzer export freshness policy
ARCHIVE_MAX_AGE_SEC = 12 * 3600       # system_health THRESHOLDS["archive_snapshot_red_sec"]
RETENTION_DIR = os.environ.get("DOXXED_BOT_DATA_RETENTION_DIR") or r"C:\DoxxedCrypto\bot-data-retention"
WALL_TAIL_LINES = 400

OK, STALE, UNAVAILABLE = "OK", "STALE", "UNAVAILABLE"
DEGRADED, UNKNOWN = "DEGRADED", "UNKNOWN"   # fresh but failing (data kept) / fresh but unverifiable (data None)
PULLER_STATE_PATH = os.environ.get("DOXXED_PULLER_STATE") or r"C:\DoxxedCrypto\fly-mirror-segments\.puller\state.json"
TRANSFER_LOCK_BUSY_TOLERANCE = 3     # consecutive LOCK_BUSY pulls (promotion/parity hold the lock) before DEGRADED


def _now() -> float:
    return time.time()


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(value) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    text = str(value).strip().replace("Z", "+00:00")
    m = re.match(r"^(.*\.\d{6})\d+(.*)$", text)   # PowerShell 7-digit fractions
    if m:
        text = m.group(1) + m.group(2)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "doxxed-insights/1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def _component(status: str, *, data=None, as_of: Optional[float] = None, max_age: Optional[float] = None,
               reason: Optional[str] = None, source: Optional[str] = None, now: Optional[float] = None) -> dict:
    now = now if now is not None else _now()
    return {
        "status": status,
        "reason": reason,
        "source": source,
        "as_of": _iso(as_of),
        "age_sec": round(now - as_of, 1) if as_of else None,
        "max_age_sec": max_age,
        "data": data if status in (OK, DEGRADED) else None,
    }


def _fresh(data, *, as_of: Optional[float], max_age: float, source: str, now: float, what: str) -> dict:
    if as_of is None:
        return _component(STALE, as_of=None, max_age=max_age, source=source, now=now,
                          reason=f"{what} has no timestamp; refusing undated data")
    if now - as_of > max_age:
        return _component(STALE, as_of=as_of, max_age=max_age, source=source, now=now,
                          reason=f"{what} is {int(now - as_of)}s old (limit {int(max_age)}s)")
    return _component(OK, data=data, as_of=as_of, max_age=max_age, source=source, now=now)


# --------------------------------------------------------------------------- health
def health_component(now: float, timeout: float = 10.0) -> dict:
    report, source, errors = None, None, []
    try:
        report, source = _get_json(HEALTH_ENDPOINT, timeout), "endpoint"
    except Exception as exc:  # endpoint down: fall back to the published file
        errors.append(f"endpoint: {type(exc).__name__}: {exc}")
    if report is None:
        path = os.path.join(STATE_DIR, "health", "system-health-latest.json")
        try:
            report, source = _read_json(path), "file"
        except Exception as exc:
            errors.append(f"file: {type(exc).__name__}: {exc}")
    if report is None:
        return _component(UNAVAILABLE, reason="; ".join(errors), max_age=HEALTH_MAX_AGE_SEC, now=now)
    as_of = _ts(report.get("generated_ts")) or _ts(report.get("generated_at"))
    failing = [{k: f.get(k) for k in ("id", "status", "observed", "hint", "runbook")}
               for f in report.get("failing") or []]
    data = {"verdict": report.get("verdict"), "counts": report.get("counts"), "failing": failing,
            "open_alarms": report.get("open_alarms"), "source_errors": report.get("source_errors"),
            "checks": {c.get("id"): {"status": c.get("status"), "observed": c.get("observed")}
                       for c in report.get("checks") or []}}
    return _fresh(data, as_of=as_of, max_age=HEALTH_MAX_AGE_SEC, source=source, now=now, what="system health")


# --------------------------------------------------------------------------- alert history
ALERTS_LIMIT = 100


def _load_alerts_module():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (here, os.path.dirname(here)):
        if path not in sys.path:
            sys.path.append(path)
    import system_health_alerts  # export-root copy, or services/btc-conservative-agent
    return system_health_alerts


def alerts_component(now: float) -> dict:
    """Alert history (active first, then newest first) read directly from the watcher's alarm log."""
    alerts = _load_alerts_module()
    health = os.path.join(STATE_DIR, "health")
    path = os.path.join(health, "alarms.jsonl")
    if not os.path.isfile(path) and not os.path.isfile(os.path.join(health, "system-health-latest.json")):
        return _component(UNAVAILABLE, reason=f"{path} missing and the watcher has never published", now=now)
    history = alerts.history_from_file(path, os.path.join(health, "system-health-latest.json"),
                                       now=now, limit=ALERTS_LIMIT)
    data = {k: history[k] for k in ("counts", "retention", "events", "oldest_event_at", "timezone")}
    data["active"] = history["active"]
    data["alerts"] = history["alerts"]
    # The log is read live from disk, so it is current by construction; a quiet log is not stale data.
    return _component(OK, data=data, as_of=now, max_age=0, source=path, now=now)


# --------------------------------------------------------------------------- fly bot
def _fly_data(status: dict, tile_stats: Optional[list], now: float) -> dict:
    sp = status.get("strategy_progress") or {}
    lanes_exec = sp.get("combo_lane_execution") or {}
    stats = {str(r.get("research_lane")): r for r in tile_stats or []}
    tiles = []
    for spec in status.get("active_tiles") or []:
        lane = spec.get("lane")
        ex = lanes_exec.get(lane) or {}
        st = stats.get(str(lane)) or {}
        win = st.get("win_rate")
        tiles.append({
            "lane": lane, "label": spec.get("label"), "id_prefix": spec.get("id_prefix"),
            "lifecycle_state": spec.get("lifecycle_state"), "paper_only": spec.get("paper_only"),
            "relay_eligible": spec.get("relay_eligible"), "accepting": ex.get("accepting"),
            "active": ex.get("active"), "completed": ex.get("completed"), "queued": ex.get("queued"),
            "win_pct": round(100.0 * float(win), 1) if isinstance(win, (int, float)) else None,
            "closed_trades": st.get("n"), "net_pnl_usd": st.get("net_pnl_usd"),
            "corrected_verdict": st.get("corrected_verdict"),
            "win_pct_source": "analyzer export tile_stats" if st else "not in analyzer export",
        })
    ai_ts = _ts(status.get("last_ai_success_at"))
    ai_age = round(now - ai_ts, 1) if ai_ts else None
    return {
        "rev": status.get("git_rev"), "process": status.get("status"),
        "paused": status.get("execution_paused"),
        "open_positions": sp.get("open_positions"), "pending_orders": sp.get("pending_orders"),
        "last_ai_success_at": status.get("last_ai_success_at"), "ai_success_age_sec": ai_age,
        "ai_success_stale": ai_age is None or ai_age > AI_SUCCESS_MAX_AGE_SEC,
        "bitfinex_live_enabled": status.get("bitfinex_live_enabled"),
        "tiles": tiles,
        "uptime": _uptime_view(status.get("uptime"), now),
    }


def _uptime_view(fly_uptime, now: float) -> dict:
    """Fly's uninterrupted-runtime block plus the laptop-side 48h proof progress."""
    out = dict(fly_uptime) if isinstance(fly_uptime, dict) else {
        "available": False, "uninterrupted_label": "Fly uptime unavailable",
        "note": "Fly /api/status has no uptime block yet (ships with the next guarded deploy)"}
    try:
        _load_alerts_module()
        import runtime_uptime  # export-root copy, or services/btc-conservative-agent
        out["proof"] = runtime_uptime.read_proof_progress(STATE_DIR, now)
    except ImportError as exc:
        out["proof"], out["proof_error"] = None, f"runtime_uptime not importable: {exc}"
    return out


def fly_component(now: float, tile_stats: Optional[list], timeout: float = 20.0) -> dict:
    try:
        status = _get_json(FLY_STATUS_URL, timeout)
        return _component(OK, data=_fly_data(status, tile_stats, now), as_of=now, max_age=0,
                          source=FLY_STATUS_URL, now=now)
    except Exception as exc:
        live_error = f"{type(exc).__name__}: {exc}"
    path = os.path.join(STATE_DIR, "fly_runtime_snapshot_v1.json")
    try:
        snap = _read_json(path)
    except Exception as exc:
        return _component(UNAVAILABLE, reason=f"live /api/status failed ({live_error}); snapshot unreadable "
                                              f"({type(exc).__name__})", max_age=FLY_SNAPSHOT_MAX_AGE_SEC, now=now)
    status = snap.get("status") if isinstance(snap.get("status"), dict) else snap
    as_of = _ts(snap.get("observedAt") or snap.get("observed_at")) or os.path.getmtime(path)
    comp = _fresh(_fly_data(status, tile_stats, now), as_of=as_of, max_age=FLY_SNAPSHOT_MAX_AGE_SEC,
                  source=path, now=now, what="laptop Fly runtime snapshot")
    comp["reason"] = f"live /api/status failed ({live_error})" + (f"; {comp['reason']}" if comp["reason"] else "")
    return comp


# --------------------------------------------------------------------------- transfer / ACK
def _puller_state() -> dict:
    try:
        state = _read_json(PULLER_STATE_PATH)
    except Exception:
        return {}
    return state if isinstance(state, dict) else {}


def transfer_component(now: float, health: Optional[dict]) -> dict:
    try:
        pull = _read_json(os.path.join(STATE_DIR, "segment-pull.status.json"))
    except Exception as exc:
        return _component(UNAVAILABLE, reason=f"segment-pull.status.json: {type(exc).__name__}: {exc}",
                          max_age=TRANSFER_MAX_AGE_SEC, now=now)
    try:
        head = _read_json(os.path.join(STATE_DIR, "fly_segment_head_snapshot_v1.json"))
    except Exception:
        head = {}
    checks = ((health or {}).get("data") or {}).get("checks") or {}
    published = pull.get("remotePublishedSeq")
    applied, acked, seq_source = pull.get("appliedSeq"), pull.get("ackedSeq"), "segment-pull.status.json"
    if not isinstance(applied, int):
        state = _puller_state()
        if isinstance(state.get("applied_seq"), int):
            applied, seq_source = state["applied_seq"], f"{PULLER_STATE_PATH} (pull status had no appliedSeq)"
            if not isinstance(acked, int) and isinstance(state.get("acked_seq"), int):
                acked = state["acked_seq"]
    data = {
        "published_seq": published, "applied_seq": applied, "laptop_acked_seq": acked,
        "applied_seq_source": seq_source if isinstance(applied, int) else None,
        "last_attempt_result": pull.get("lastAttemptResult"),
        "consecutive_failures": pull.get("consecutiveFailures"),
        "last_success_at": pull.get("lastSuccessAt"),
        "fly_shipped_seq": head.get("shipped_seq"), "fly_laptop_acked_seq": head.get("laptop_acked_seq"),
        "fly_unshipped_bytes": head.get("unshipped_bytes"), "fly_last_error": head.get("last_error"),
        "applied_behind_published": (published - applied) if isinstance(published, int) and isinstance(applied, int)
        else None,
        "last_pull_finished_at": pull.get("finishedAt"), "last_pull_exit": pull.get("exitCode"),
        "last_pull_error": pull.get("error"), "last_parity_at": pull.get("lastParityAt"),
        "fly_head_observed_at": head.get("observedAt"),
        "health_checks": {k: checks.get(k) for k in ("shipper.progress", "laptop.pull_ack") if k in checks},
    }
    as_of = _ts(pull.get("finishedAt"))
    comp = _fresh(data, as_of=as_of, max_age=TRANSFER_MAX_AGE_SEC, source=STATE_DIR, now=now,
                  what="segment pull status")
    if comp["status"] != OK:
        return comp
    if not isinstance(applied, int):
        return _component(UNKNOWN, as_of=as_of, max_age=TRANSFER_MAX_AGE_SEC, source=STATE_DIR, now=now,
                          reason="applied_seq unknown: segment-pull status has no appliedSeq and puller "
                                 f"state.json has none either (last pull exit={pull.get('exitCode')}, "
                                 f"error={pull.get('error')})")
    problems = []
    if seq_source != "segment-pull.status.json":
        problems.append("pull status had no appliedSeq; using puller state.json")
    failures = pull.get("consecutiveFailures")
    brief_lock_wait = (pull.get("lastAttemptResult") == "LOCK_BUSY" and isinstance(failures, int)
                       and failures < TRANSFER_LOCK_BUSY_TOLERANCE)
    if pull.get("exitCode") not in (0, None) and not brief_lock_wait:
        problems.append(f"last pull exit={pull.get('exitCode')} result={pull.get('lastAttemptResult')} "
                        f"consecutive_failures={failures} error={pull.get('error')}")
    if problems:
        comp = _component(DEGRADED, data=data, as_of=as_of, max_age=TRANSFER_MAX_AGE_SEC, source=STATE_DIR,
                          now=now, reason="; ".join(problems))
    return comp


# --------------------------------------------------------------------------- deploy queue (WALL)
_WALL_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) \| ([^(|]+?) \(([^)]*)\) (.*)$")


def _wall_key(tag: str, worker: str) -> str:
    # The task tag is the stable identity; the worker label in parentheses varies between posts.
    return tag.strip()


def parse_wall(lines, now: float) -> dict:
    """Fly slot holder and queue from WALL-STATUS-FLY.md lines (oldest first)."""
    holder, holder_since, entries, last = None, None, [], {}
    queued_at: dict = {}
    for raw in lines:
        m = _WALL_LINE.match(raw.strip())
        if not m:
            continue
        ts, tag, worker, text = m.groups()
        key = _wall_key(tag, worker)
        up = text.upper()
        # "PRIORITY CLAIM of the next Fly slot" queues at the head; it does not take a held slot.
        claims = ("CLAIMS FLY SLOT" in up or "HOLDS FLY SLOT" in up or up.startswith("CLAIMS ")) \
            and "NOT CLAIMING" not in up
        if up.startswith("PRIORITY CLAIM"):
            queued_at.setdefault(key, "0000-" + ts)
        released = "SLOT RELEASED" in up or ("DONE" in up[:12] and holder == key and "NEVER" not in up[:60])
        if claims:
            holder, holder_since = key, ts
        if released and holder == key:
            holder, holder_since = None, None
        if up.startswith("QUEUED") or " QUEUED" in up[:40]:
            queued_at.setdefault(key, ts)
        if up.startswith("DONE") or "SLOT RELEASED" in up or claims:
            queued_at.pop(key, None)
        state = re.split(r"[,:;(]", text, maxsplit=1)[0].strip()[:60]
        last[key] = {"task": key, "worker": re.sub(r"^worker\s+", "", worker.strip()), "at": ts, "state": state,
                     "text": text[:400]}
        entries.append((ts, key))
    queue = [{**last[k], "queued_since": t} for k, t in sorted(queued_at.items(), key=lambda kv: kv[1])
             if k != holder]
    holder_entry = last.get(holder) if holder else None
    recent_done = [last[k] for k in last if last[k]["state"].upper().startswith("DONE")]
    recent_done.sort(key=lambda r: r["at"], reverse=True)
    last_ts = _ts(entries[-1][0]) if entries else None
    return {
        "slot_holder": holder, "slot_held_since": holder_since,
        "slot_holder_latest": holder_entry,
        "slot_state": "FREE" if not holder else (
            "DEPLOYING" if holder_entry and "IN PROGRESS" in holder_entry["text"].upper() else "HELD"),
        "queue": queue, "recent_done": recent_done[:5],
        "latest_entry_at": entries[-1][0] if entries else None,
        "latest_entry_age_sec": round(now - last_ts, 1) if last_ts else None,
        "entries_parsed": len(entries),
    }


def deploy_queue_component(now: float) -> dict:
    try:
        with open(WALL_PATH, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()[-WALL_TAIL_LINES:]
        mtime = os.path.getmtime(WALL_PATH)
    except Exception as exc:
        return _component(UNAVAILABLE, reason=f"WALL unreadable: {type(exc).__name__}: {exc}", now=now)
    data = parse_wall(lines, now)
    if not data["entries_parsed"]:
        return _component(UNAVAILABLE, reason="WALL has no parseable entries", source=WALL_PATH, now=now)
    # The WALL is read live from disk, so it is current by construction; a quiet WALL is not stale data.
    data["file_modified_at"] = _iso(mtime)
    return _component(OK, data=data, as_of=now, max_age=0, source=WALL_PATH, now=now)


# --------------------------------------------------------------------------- analyzer export
def _load_client():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (here, os.path.dirname(here)):
        if path not in sys.path:
            sys.path.append(path)
    try:
        import analyzer_client as client  # export-root copy next to insights_client.py
    except ImportError:
        from strategy_lab import client  # repo / analyzer process
    return client


def _records(df, columns, limit=None) -> list:
    if df is None or len(df) == 0:
        return []
    cols = [c for c in columns if c in df.columns]
    out = df[cols].head(limit) if limit else df[cols]
    return json.loads(out.to_json(orient="records"))


def export_component(now: float, check_live: bool = True) -> tuple:
    try:
        client = _load_client()
        exp = client.load_latest(check_live=check_live, retries=1, retry_wait_sec=3)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        status = STALE if type(exc).__name__ == "StaleExportError" else UNAVAILABLE
        return _component(status, reason=reason, max_age=EXPORT_MAX_AGE_SEC, now=now), None
    s = exp.summary
    tiles = _records(exp.get("tile_stats"), ["research_lane", "label", "n", "win_rate", "mean_usd", "net_pnl_usd",
                                             "ci_lo_usd", "ci_hi_usd", "n_tested", "p_holm", "q_bh",
                                             "corrected_verdict", "last_close"])
    regret = exp.get("exit_regret")
    taker = exp.get("taker_counterfactual")
    marks = exp.get("fill_markouts")
    data = {
        "export_id": s.get("export_id"), "generated_at": s.get("generated_at"), "checks": exp.checks,
        "analyzer_revision": (s.get("generation") or {}).get("analyzer_revision"),
        "generation_id": (s.get("generation") or {}).get("generation_id"),
        "dataset_epoch": (s.get("generation") or {}).get("dataset_epoch"),
        "tile_stats": tiles,
        "hypotheses": _records(exp.get("hypotheses"), ["id", "title", "verdict", "n_test", "mean_bp", "p_holm",
                                                       "status"]),
        "main_rankings": (s.get("main_rankings") or {}).get("family_summaries"),
        "stream_health": _records(exp.get("stream_health"), ["stream", "status", "age_sec", "content_last_at",
                                                             "content_lag_sec", "analyzer_usage"]),
        "stream_study_health": _records(exp.get("stream_study_health"), ["stream", "status", "rows_used",
                                                                         "content_last_at", "error"]),
        "exit_regret_1h": _records(regret[regret["horizon_sec"] == 3600] if regret is not None and len(regret)
                                   and "horizon_sec" in regret.columns else None,
                                   ["research_lane", "n", "hold_longer_mean_usd", "ci_lo_usd", "ci_hi_usd",
                                    "verdict"]),
        "taker_ev_1s": _records(taker[(taker["horizon"] == "1s") & (taker["group"].isin(["ALL", "TILE"]))]
                                if taker is not None and len(taker) and "horizon" in taker.columns else None,
                                ["group", "value", "latency_sec", "n", "ev_exit_touch_bps", "ci_lo_bps",
                                 "ci_hi_bps"]),
        "fill_markouts": _records(marks[marks["liquidity"] == "ALL"] if marks is not None and len(marks)
                                  and "liquidity" in marks.columns else None,
                                  ["research_lane", "horizon", "n", "markout_mid_bps", "ci_lo_bps", "ci_hi_bps"]),
        "quarantine": _records(exp.get("quarantine"), ["trade_id", "research_lane", "reason"]),
        # Coverage over the whole epoch (not only the loaded window) with rows by source.
        "stream_coverage": {name: {k: (cov or {}).get(k) for k in (
            "epoch_start", "first_available", "last", "horizon_hours", "epoch_coverage_share",
            "in_window_present_share", "sources")}
            for name, cov in (s.get("stream_coverage") or
                              (s.get("strategy_lab") or {}).get("stream_coverage") or {}).items()},
        "event_study": {k: (s.get("event_study") or {}).get(k) for k in ("status", "generated_ts", "hypotheses")},
        "data_health": {k: (s.get("data_health") or {}).get(k) for k in ("status", "status_counts", "streams")},
        "generation_receipt": {k: (s.get("generation_receipt") or {}).get(k)
                               for k in ("level", "complete", "reasons", "failed_required_studies", "status")},
        "input_blockers": s.get("input_blockers") or {"status": "MISSING"},
        "ledger_reconciliation": {k: (s.get("ledger_reconciliation") or {}).get(k)
                                  for k in ("level", "reasons", "win_pct_definition", "analyzer_cohort", "status")},
        "tile_pool": ((s.get("main_rankings") or {}).get("tile_pool") or {}).get("rows"),
    }
    comp = _component(OK, data=data, as_of=s.get("generated_at_ts"), max_age=EXPORT_MAX_AGE_SEC,
                      source=exp.path, now=now)
    return comp, tiles


# --------------------------------------------------------------------------- analysis archive + retention
def _retention_state() -> dict:
    def optional(name: str) -> dict:
        try:
            return _read_json(os.path.join(RETENTION_DIR, name))
        except (OSError, ValueError):
            return {}

    last, mode = optional("last-run.json"), optional("mode.json")
    keys = ("mode", "finished_at", "level", "bytes_after", "cap_bytes", "usage_fraction", "deny_reasons",
            "reclaimed_bytes", "would_reclaim_bytes", "ledger_rows")
    return {"configured_mode": mode.get("mode") or "dry_run", **{k: last.get(k) for k in keys}}


def archive_component(now: float, days: int = 30) -> dict:
    """Long-horizon archive: snapshot freshness, schema compat, per-tile daily rollups, retention."""
    client = _load_client()
    since = datetime.fromtimestamp(now - days * 86400, timezone.utc).date().isoformat()
    archive = client.load_archive(since=since)
    snaps, compat, daily = archive["snapshots"], archive["compat"], archive["daily"]
    if len(snaps) == 0:
        return _component(UNAVAILABLE, reason="analysis archive has no verified snapshot", source=archive.root,
                          now=now)
    latest = snaps.iloc[-1].to_dict()
    tiles = []
    tile_rows = daily[daily["dimension"] == "tile"] if len(daily) else daily
    if len(tile_rows):
        cutoff = datetime.fromtimestamp(now - 7 * 86400, timezone.utc).date().isoformat()
        for key, group in tile_rows.groupby("key"):
            recent, prior = group[group["day"] >= cutoff], group[group["day"] < cutoff]

            def mean(frame):
                n = int(frame["n"].sum())
                return round(float(frame["net_pnl_usd"].sum()) / n, 6) if n else None

            tiles.append({"key": key, "days": int(group["day"].nunique()), "n": int(group["n"].sum()),
                          "net_pnl_usd": round(float(group["net_pnl_usd"].sum()), 6),
                          "mean_usd_last_7d": mean(recent), "mean_usd_before": mean(prior)})
    status_counts = compat.groupby(["dataset", "status"]).size().unstack(fill_value=0).to_dict("index") \
        if len(compat) else {}
    data = {
        "root": archive.root, "window_days": days,
        "latest_snapshot": {k: latest.get(k) for k in ("snapshot_id", "generation_id", "dataset_epoch",
                                                        "segment_seq_through", "written_at",
                                                        "analyzer_revision")},
        "snapshots_in_window": int(len(snaps)),
        "schema_compat": status_counts,
        "incompatible": _records(compat[compat["status"] != "COMPATIBLE"], ["dataset", "id", "schema_version",
                                                                             "status"]),
        "tiles_long_horizon": sorted(tiles, key=lambda row: -row["n"]),
        "retention": _retention_state(),
    }
    return _fresh(data, as_of=_ts(latest.get("written_at")), max_age=ARCHIVE_MAX_AGE_SEC, source=archive.root,
                  now=now, what="latest archive snapshot")


# --------------------------------------------------------------------------- snapshot
def snapshot(*, check_live: bool = True, timeout: float = 20.0) -> dict:
    """Every component in one dict; stale or unavailable components are refused (``data`` None)."""
    t0 = now = _now()
    components: dict = {}

    def guard(name: str, fn: Callable[[], dict]) -> dict:
        try:
            components[name] = fn()
        except Exception as exc:  # one broken source must not hide the others
            components[name] = _component(UNAVAILABLE, reason=f"{type(exc).__name__}: {exc}", now=now)
        return components[name]

    health = guard("system_health", lambda: health_component(now, timeout=min(timeout, 10.0)))
    exp_holder: dict = {}

    def _export():
        comp, tiles = export_component(now, check_live=check_live)
        exp_holder["tiles"] = tiles
        return comp

    guard("analyzer_export", _export)
    guard("fly_bot", lambda: fly_component(now, exp_holder.get("tiles"), timeout=timeout))
    guard("transfer", lambda: transfer_component(now, health))
    guard("deploy_queue", lambda: deploy_queue_component(now))
    alerts = guard("alerts", lambda: alerts_component(now))
    guard("analysis_archive", lambda: archive_component(now))
    refused = [{"component": k, "status": v["status"], "reason": v["reason"]}
               for k, v in components.items() if v["status"] != OK]
    hd = health.get("data") or {}
    return {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "status": "COMPLETE" if not refused else "PARTIAL",
        "refused": refused,
        "system_verdict": hd.get("verdict") if health.get("status") == OK else "UNKNOWN",
        "failing_checks": [f.get("id") for f in hd.get("failing") or []],
        "active_alerts": [{k: a.get(k) for k in ("check", "title", "level", "started_at", "duration_text", "observed")}
                          for a in (alerts.get("data") or {}).get("active") or []],
        "components": components,
        "elapsed_sec": round(_now() - t0, 2),
        "freshness_policy": {
            "system_health_max_age_sec": HEALTH_MAX_AGE_SEC, "fly_snapshot_fallback_max_age_sec":
            FLY_SNAPSHOT_MAX_AGE_SEC, "transfer_max_age_sec": TRANSFER_MAX_AGE_SEC,
            "analyzer_export_max_age_sec": EXPORT_MAX_AGE_SEC, "analysis_archive_max_age_sec": ARCHIVE_MAX_AGE_SEC,
            "rule": "a component older than its limit (or unreachable) is STALE/UNAVAILABLE with data=None",
        },
    }


if __name__ == "__main__":  # python insights_client.py [--json]
    snap = snapshot()
    if "--json" in sys.argv:
        print(json.dumps(snap, indent=1, default=str))
    else:
        print(f"{snap['status']} system={snap['system_verdict']} failing={snap['failing_checks']} "
              f"({snap['elapsed_sec']}s)")
        for name, comp in snap["components"].items():
            print(f"  {name:16s} {comp['status']:11s} age={comp['age_sec']}s {comp['reason'] or ''}")
