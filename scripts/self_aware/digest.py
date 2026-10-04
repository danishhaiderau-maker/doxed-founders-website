"""Hourly digest: what changed, what broke, what recovered, any edge found.

Computed once from the same tables the dashboards and APIs read, stored in
``res_digests`` and surfaced in the Alerts section (``selfaware.digest``) and
``GET /api/selfaware/digest``.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .facts import iso, parse_ts

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _SYDNEY = _ZoneInfo("Australia/Sydney")
except Exception:  # Windows without tzdata
    _SYDNEY = None
_AEST_FIXED = timezone(timedelta(hours=10), "AEST")
_AEDT = timezone(timedelta(hours=11), "AEDT")
AEST = _AEST_FIXED  # legacy name; labels use to_sydney()


def _sydney_tz_for(moment: datetime):
    """Australia/Sydney offset for an aware instant: AEDT (UTC+11) in summer, AEST (UTC+10) otherwise.

    Uses the IANA zone when available; Windows without the ``tzdata`` package
    falls back to the NSW rule (DST from the first Sunday of October 02:00
    AEST to the first Sunday of April 03:00 AEDT).
    """
    if _SYDNEY is not None:
        return _SYDNEY
    utc = moment.astimezone(timezone.utc)

    def first_sunday(year: int, month: int) -> datetime:
        day = datetime(year, month, 1, tzinfo=timezone.utc)
        return day + timedelta(days=(6 - day.weekday()) % 7)

    start = first_sunday(utc.year, 10) - timedelta(hours=8)   # 02:00 AEST = 16:00Z Saturday
    end = first_sunday(utc.year, 4) - timedelta(hours=8)      # 03:00 AEDT = 16:00Z Saturday
    return _AEDT if (utc >= start or utc < end) else _AEST_FIXED


def to_sydney(moment: datetime) -> datetime:
    """Convert an aware datetime to Danish's local Australia/Sydney time."""
    return moment.astimezone(_sydney_tz_for(moment))


def _aest(ts: float) -> str:
    return to_sydney(datetime.fromtimestamp(ts, timezone.utc)).strftime("%Y-%m-%d %H:%M %Z")


def _dur(sec: float | None) -> str:
    if sec is None:
        return "?"
    m = int(max(0, sec) // 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def build(store, facts: dict, findings: list[dict], uptime: dict, state: dict, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    since = state.get("last_digest_at") or (now - 3600)
    since_iso = iso(since)
    events = [e for e in store.history("findings_events", limit=500, since=since_iso)]
    broke = [e for e in events if e.get("to") in ("AMBER", "RED") and e.get("kind") in ("OPENED", "CHANGED")]
    recovered = [e for e in events if e.get("kind") == "CLEARED"]
    watcher = []
    try:
        watcher = store.read('SELECT "at", event, "check", observed FROM raw_alarms WHERE "at" >= ? AND "check" NOT LIKE '
                             "'selfaware.%' ORDER BY \"at\"", [since_iso])
    except Exception:  # noqa: BLE001
        pass
    w_broke = [w for w in watcher if w["event"] in ("OPEN", "AMBER", "STILL_RED")]
    w_rec = [w for w in watcher if w["event"] in ("RECOVERED", "AMBER_CLEAR")]

    rt = facts.get("runtime") or {}
    prev_rt = state.get("digest_runtime") or {}
    cur_rt = {"git_rev": rt.get("git_rev"), "paused": rt.get("execution_paused"), "pause_owner": rt.get("pause_owner"),
              "lanes": sorted(rt.get("active_tile_lanes") or []),
              "enabled": {k: bool(v) for k, v in (rt.get("research_lane_enabled") or {}).items()},
              "live_armed": rt.get("live_armed"), "v2c_head": (facts.get("analyzer_head") or "")[:12]}
    changed = []
    for k, label in (("git_rev", "Fly revision"), ("paused", "paper paused"), ("lanes", "tile roster"),
                     ("enabled", "tile toggles"), ("live_armed", "live armed"), ("v2c_head", "analyzer checkout")):
        if prev_rt and prev_rt.get(k) != cur_rt.get(k):
            changed.append(f"{label}: {prev_rt.get(k)} -> {cur_rt.get(k)}")
    runs = [r.get("value", r) if isinstance(r, dict) else r for r in (facts.get("deploys") or {}).get("runs") or []]
    for r in runs:
        if isinstance(r, dict) and (parse_ts(r.get("createdAt")) or 0) >= since:
            changed.append(f"deploy run {r.get('databaseId')} {r.get('status')}/{r.get('conclusion')} "
                           f"head {str(r.get('headSha'))[:9]}")
    for m in facts.get("manual") or []:
        if (parse_ts(m.get("at")) or 0) >= since:
            changed.append(f"manual intervention journalled: {str(m.get('reason') or m.get('action'))[:80]}")

    edges = []
    if store.table_exists("res_edges"):
        edges = store.read("SELECT spec_id, horizon, status, holdout_n, holdout_hit, holdout_net_bp, bh_q, holm_p, reasons "
                           "FROM res_edges WHERE status IN ('CANDIDATE','HINT') ORDER BY status, holdout_net_bp DESC")
    prev_edges = set(state.get("digest_edges") or [])
    cur_edges = {f"{e['spec_id']}@{e['horizon']}:{e['status']}" for e in edges}
    new_edges = sorted(cur_edges - prev_edges)
    candidates = [e for e in edges if e["status"] == "CANDIDATE"]
    hints = [e for e in edges if e["status"] == "HINT"]

    ai = {}
    if store.table_exists("res_ai_calls"):
        r = store.read("SELECT count(*) AS n FROM res_ai_calls WHERE ts >= ?", [since])
        ai["calls_in_window"] = r[0]["n"] if r else 0
    if store.table_exists("res_ai_scorecard"):
        rows = store.read("SELECT strategy, horizon, n, hit_rate, net_bp FROM res_ai_scorecard WHERE \"window\"='all' AND "
                          "slice_dim='overall' AND horizon IN ('5m','60m') AND strategy IN ('AI_SCORE_LED','ALWAYS_LONG',"
                          "'RANDOM','RULE_VOTE')")
        ai["all_time"] = {f"{x['strategy']}@{x['horizon']}": {"n": x["n"], "hit": round(x["hit_rate"], 3),
                                                              "net_bp": round(x["net_bp"], 2)} for x in rows}

    open_now = [f for f in findings if f["severity"] in ("AMBER", "RED")]
    reds = [f for f in open_now if f["severity"] == "RED"]
    attention = bool(broke or reds or candidates or [w for w in w_broke if w["event"] == "OPEN"])
    parts = [f"{_aest(now)} digest:"]
    parts.append(f"{len(broke)} new self-diagnosis problem(s)" + (f" ({', '.join(e['id'] + ' ' + e['to'] for e in broke[:3])})" if broke else ""))
    parts.append(f"{len(recovered)} recovered")
    parts.append(f"{len(w_broke)} watcher alert event(s)")
    parts.append(f"{len(candidates)} edge candidate(s), {len(hints)} hint(s)")
    parts.append(f"uptime {_dur(uptime.get('running_uninterrupted_sec'))}")
    headline = " ".join([parts[0], "; ".join(parts[1:])])
    summary = (f"open now: {', '.join(f['id'] + ' ' + f['severity'] for f in open_now[:6]) or 'none'}; "
               f"changed: {'; '.join(changed[:4]) or 'nothing'}")
    digest = {
        "schema": "self_aware_digest_v1", "id": iso(now), "kind": "DIGEST", "at": iso(now),
        "window": {"start": since_iso, "end": iso(now)}, "headline": headline, "summary_line": summary,
        "attention": attention,
        "broke": [{"id": e["id"], "to": e["to"], "at": e["at"], "observed": e["finding"]["observed"]} for e in broke],
        "recovered": [{"id": e["id"], "at": e["at"]} for e in recovered],
        "watcher_alerts": {"opened": w_broke[-20:], "cleared": w_rec[-20:]},
        "changed": changed, "open_findings": [{"id": f["id"], "severity": f["severity"], "observed": f["observed"]} for f in open_now],
        "edges": {"candidates": candidates, "hints": hints, "new": new_edges},
        "ai": ai, "uptime": {k: uptime.get(k) for k in ("running_uninterrupted_sec", "interruptions_24h",
                                                         "longest_run_7d_sec", "last_interruption", "proof")},
    }
    state["last_digest_at"] = now
    state["digest_runtime"] = cur_rt
    state["digest_edges"] = sorted(cur_edges)
    return digest
