"""Alert history for the dashboards' Alerts section (Fly, analyzer :9001, agent insights).

The laptop watcher (``scripts/system_health.py``) appends edge-triggered events
to ``alarms.jsonl`` (``OPEN``/``STILL_RED``/``RECOVERED`` for RED checks,
``AMBER``/``AMBER_CLEAR`` for AMBER ones). This module turns those events into
one entry per alert episode, newest first, with plain-English wording, AEST and
UTC times, when it cleared and how long it lasted. The analyzer reads the log
directly; Fly receives the same events through the watcher's push to
``POST /api/system-health/report`` and keeps a bounded in-memory copy.

Read-only: nothing here touches trading, relay, or exchange state.
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = "system_health_alert_history_v1"
EVENT_SCHEMA = "system_health_alarm_v1"
RETAIN_DAYS = 30
RETAIN_EVENTS = 2000
MAX_EVENTS_PER_PUSH = 400
READ_TAIL_BYTES = 4 * 1024 * 1024
AEST = timezone(timedelta(hours=10), "AEST")
RUNBOOK_BASE = "https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/"
_EVENTS = ("OPEN", "STILL_RED", "RECOVERED", "AMBER", "AMBER_CLEAR")
_STATUSES = ("GREEN", "AMBER", "RED", "SKIP")

# check id -> (plain title, what it means, likely cause in plain words)
CHECKS: dict[str, tuple[str, str, str]] = {
    "fly.process": ("Trading bot server is reachable",
                    "The trading bot on Fly did not answer the watcher.",
                    "The Fly server is restarting or down, or the laptop lost its internet connection."),
    "fly.paused": ("Paper trading is running",
                   "Paper trading was paused.",
                   "A deploy is in progress (normal for up to 45 minutes) or a safety pause stopped trading."),
    "fly.revision": ("Bot runs the latest deployed code",
                     "The last deploy did not finish cleanly.",
                     "A guarded deploy failed, so Fly is still running the previous version."),
    "ai.success": ("AI is giving answers",
                   "The AI model has not returned a successful answer recently.",
                   "DeepSeek is timing out or failing (provider outage, network, key or balance)."),
    "ai.failures": ("AI calls are succeeding",
                    "Several AI calls in a row failed.",
                    "DeepSeek returned errors or timed out on consecutive calls."),
    "ai.decision_mix": ("AI decisions look normal",
                        "Every recent AI answer was 'no trade'.",
                        "A prompt or parsing problem, or the AI is receiving dead inputs."),
    "ai.served_model": ("AI model is the expected one",
                        "DeepSeek is answering with a different model than configured.",
                        "DeepSeek renamed or retired a model and silently switched to another."),
    "deepseek.balance": ("AI account has credit",
                         "The DeepSeek account balance is low.",
                         "The account needs a top-up; at $0 every AI call fails."),
    "trading.orders": ("Tiles are placing paper orders",
                       "No tile has placed a paper order for a long time.",
                       "The AI is failing, entry filters are rejecting everything, or execution is stuck."),
    "trading.orphans": ("Every order has an owner",
                        "An order or position exists without a matching lifecycle record.",
                        "A bookkeeping gap between orders and positions that must be reconciled."),
    "trading.lifecycle": ("Order records are consistent",
                          "Some trades are recorded as both expired and filled.",
                          "A race between the fill and the order-expiry timer (see fill-guard fixes)."),
    "ws.ticks": ("Live price feed is flowing",
                 "The live Bitfinex price feed stopped or slowed down.",
                 "The Bitfinex WebSocket disconnected or stalled."),
    "shipper.progress": ("Fly is sending data to the laptop",
                         "Fly stopped sending new data segments to the laptop.",
                         "The segment shipper is erroring or stalled on Fly."),
    "laptop.pull_ack": ("Laptop is receiving Fly data",
                        "The laptop fell behind in downloading or confirming Fly data.",
                        "The laptop pull or its confirmation back to Fly is stuck or slow."),
    "laptop.supervisor": ("Laptop scheduler is running",
                          "The laptop's supervisor task has not run recently.",
                          "The scheduled task is disabled, erroring, or the laptop was asleep."),
    "analyzer.generation": ("Analyzer results are fresh",
                            "The analyzer has not produced new results recently.",
                            "An analyzer run crashed, stalled, or was blocked by data copying."),
    "analyzer.api": ("Analyzer dashboard is up",
                     "The analyzer dashboard (:9001) is down or not current.",
                     "It is being replaced during an analyzer run, or it crashed."),
    "analyzer.cycle": ("Analyzer runs finish on time",
                       "An analyzer run is slow, failed, or uses older code than Fly.",
                       "Slow data promotion, a failed run, or the laptop has not picked up the latest deploy."),
    "exports.freshness": ("Agent export is fresh",
                          "The analyzer export for agents is old.",
                          "The export step is not running after analyzer runs."),
    "streams.coverage": ("Research data streams are healthy",
                         "A research data stream is stale or unhealthy.",
                         "A collector stalled, or an exchange feed went quiet."),
    "dashboards.parity": ("Dashboards agree with each other",
                          "The dashboards, tile registry and toggles disagree.",
                          "A deploy or registry change has not reached every layer yet."),
    "railway.relay": ("Bitfinex relay is safely off",
                      "The Bitfinex relay looks armed, or its status is stale.",
                      "Unexpected relay arming or a stale relay heartbeat; check Railway right away."),
    "railway.api": ("Platform API is healthy",
                    "The Railway platform API or its database is not healthy.",
                    "Railway is redeploying, or the Neon database connection is failing."),
    "neon.usage": ("Database usage is within budget",
                   "Neon database data transfer is growing faster than budget.",
                   "A polling loop or unbounded database query."),
    "bitfinex.exposure": ("No real Bitfinex exposure",
                          "Real exchange exposure or arming was detected.",
                          "Unexpected arming or a live position; never force-close, tell Danish."),
    "proof.latest": ("Unattended proof is passing",
                     "The latest unattended-proof row failed or is late.",
                     "One of the proof checks failed; the matching health check shows why."),
    "disk.space": ("Enough disk space",
                   "Free disk space is low on the laptop or on Fly.",
                   "Data growth; do not prune, add space instead."),
    "watcher.stale": ("Health watcher is reporting",
                      "The health watcher stopped publishing reports.",
                      "The laptop watcher task is not running, or the laptop is offline."),
    "fly.ws_ticks": ("Fly sees live prices",
                     "Fly has not received a live price tick recently.",
                     "The Bitfinex WebSocket on Fly disconnected."),
    "fly.live_armed": ("Live trading is disarmed",
                       "Fly reports live trading as armed.",
                       "Unexpected arming; it must stay disarmed."),
    "fly.ai_success": ("Fly AI calls are succeeding",
                       "Fly's own AI tracker reports no recent success.",
                       "DeepSeek timeouts or errors."),
}


def _parse_ts(value) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value).strip().replace("Z", "+00:00")
    if "." in text:
        head, _, rest = text.partition(".")
        digits = "".join(ch for ch in rest if ch.isdigit())
        tail = rest[len(digits):]
        text = f"{head}.{digits[:6]}{tail}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value, limit: int) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def times(ts: float | None) -> dict | None:
    """The same instant in Danish's local AEST (UTC+10) and in UTC."""
    if ts is None:
        return None
    moment = datetime.fromtimestamp(ts, timezone.utc)
    return {"aest": moment.astimezone(AEST).strftime("%Y-%m-%d %H:%M AEST"),
            "utc": moment.strftime("%H:%M UTC"), "iso": _iso(ts)}


def duration_text(sec: float | None) -> str | None:
    if sec is None:
        return None
    minutes = int(max(0.0, sec) // 60)
    if minutes < 1:
        return "under a minute"
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    parts = [f"{days}d"] if days else []
    if hours:
        parts.append(f"{hours}h")
    if mins and not days:
        parts.append(f"{mins}m")
    return " ".join(parts)


def runbook_url(runbook: str | None) -> str | None:
    if not runbook:
        return None
    if runbook.startswith(("http://", "https://")):
        return runbook
    return RUNBOOK_BASE + runbook.lstrip("/")


def describe(check_id: str) -> tuple[str, str, str]:
    if check_id in CHECKS:
        return CHECKS[check_id]
    words = check_id.replace(".", " ").replace("_", " ").strip()
    return (words[:1].upper() + words[1:], f"The '{check_id}' health check failed.",
            "See the runbook entry for this check.")


# ----------------------------------------------------------------- events

def sanitize_event(raw) -> dict | None:
    """Reduce one untrusted alarm event to the bounded fields the history needs."""
    if not isinstance(raw, dict) or raw.get("event") not in _EVENTS:
        return None
    check_id = _text(raw.get("check"), 64)
    at = _parse_ts(raw.get("at"))
    if not check_id or at is None:
        return None
    status = raw.get("status")
    event = {
        "at": _iso(at), "ts": at, "event": raw["event"], "check": check_id,
        "status": status if status in _STATUSES else None,
        "observed": _text(raw.get("observed"), 240),
        "threshold": _text(raw.get("threshold"), 200),
        "hint": _text(raw.get("hint"), 300),
        "runbook": _text(raw.get("runbook"), 160),
    }
    opened = _parse_ts(raw.get("opened_at"))
    if opened is not None:
        event["opened_at"] = _iso(opened)
    return event


def _key(event: dict) -> tuple:
    return (round(float(event["ts"]), 3), event["event"], event["check"])


def merge_events(existing: list, incoming, now: float | None = None) -> list:
    """Deduplicated union, oldest first, bounded to RETAIN_DAYS and RETAIN_EVENTS."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    merged = {_key(e): e for e in existing or [] if isinstance(e, dict) and e.get("ts") is not None}
    for raw in list(incoming or [])[:MAX_EVENTS_PER_PUSH]:
        event = sanitize_event(raw)
        if event is not None:
            merged.setdefault(_key(event), event)
    cutoff = now - RETAIN_DAYS * 86400
    events = sorted((e for e in merged.values() if e["ts"] >= cutoff), key=lambda e: e["ts"])
    return events[-RETAIN_EVENTS:]


def read_events_file(path, now: float | None = None) -> list:
    """Bounded tail of ``alarms.jsonl`` as sanitized events (oldest first)."""
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - READ_TAIL_BYTES)
            handle.seek(start)
            blob = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = blob.splitlines()
    if start:
        lines = lines[1:]
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    events = [e for e in (sanitize_event(r) for r in rows) if e is not None]
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    cutoff = now - RETAIN_DAYS * 86400
    return [e for e in events if e["ts"] >= cutoff][-RETAIN_EVENTS:]


def check_statuses(report) -> dict:
    """``{check_id: status}`` from a full watcher report or a compact pushed map."""
    if not isinstance(report, dict):
        return {}
    if isinstance(report.get("check_status"), dict):
        return {str(k)[:64]: v for k, v in report["check_status"].items() if v in _STATUSES}
    return {str(c.get("id"))[:64]: c.get("status") for c in report.get("checks") or []
            if isinstance(c, dict) and c.get("status") in _STATUSES}


# ---------------------------------------------------------------- history

def _episode(check_id: str, level: str, started: float, event: dict) -> dict:
    return {"check": check_id, "level": level, "started_ts": started, "cleared_ts": None,
            "observed": event.get("observed"), "latest_observed": event.get("observed"),
            "cleared_observed": None, "threshold": event.get("threshold"), "hint": event.get("hint"),
            "runbook": event.get("runbook"), "clear_note": None}


def build_history(events: list, *, now: float | None = None, statuses: dict | None = None,
                  statuses_at: float | None = None, limit: int | None = None) -> dict:
    """One entry per alert episode, active first, then newest first.

    ``statuses`` (from the latest non-stale report) closes an episode whose
    check is GREEN now even if its clear event was never logged.
    """
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    open_: dict[tuple, dict] = {}
    done: list[dict] = []
    for event in sorted(events or [], key=lambda e: e["ts"]):
        kind, cid, ts = event["event"], event["check"], float(event["ts"])
        level = "AMBER" if kind in ("AMBER", "AMBER_CLEAR") else "RED"
        key = (cid, level)
        current = open_.get(key)
        if kind in ("OPEN", "AMBER"):
            if current is not None:
                current.update(cleared_ts=ts, clear_note="superseded by a newer alert")
                done.append(open_.pop(key))
            amber = open_.get((cid, "AMBER")) if level == "RED" else None
            if amber is not None:
                # One check is in one state: escalating to RED ends its AMBER episode.
                amber.update(cleared_ts=ts, clear_note="escalated to RED")
                done.append(open_.pop((cid, "AMBER")))
            open_[key] = _episode(cid, level, ts, event)
        elif kind == "STILL_RED":
            if current is None:
                started = _parse_ts(event.get("opened_at")) or ts
                current = open_[key] = _episode(cid, level, started, event)
            current["latest_observed"] = event.get("observed") or current["latest_observed"]
        else:  # RECOVERED / AMBER_CLEAR
            if current is None:
                started = _parse_ts(event.get("opened_at"))
                if started is None:
                    continue
                current = _episode(cid, level, started, event)
                current["observed"] = None
            else:
                open_.pop(key)
            current.update(cleared_ts=ts, cleared_observed=event.get("observed"))
            done.append(current)
    statuses = statuses or {}
    for key, episode in list(open_.items()):
        status = statuses.get(episode["check"])
        still_failing = status == episode["level"] or (episode["level"] == "AMBER" and status == "RED")
        if statuses and status in ("GREEN", "SKIP", "AMBER", "RED") and not still_failing:
            episode.update(cleared_ts=statuses_at or now,
                           clear_note="check no longer failing in the latest report (no clear event logged)")
            done.append(open_.pop(key))
    entries = [_entry(e, now) for e in list(open_.values()) + done]
    active = sorted((e for e in entries if e["active"]), key=lambda e: (e["level"] != "RED", -e["started_ts"]))
    past = sorted((e for e in entries if not e["active"]), key=lambda e: -(e["cleared_ts"] or e["started_ts"]))
    ordered = active + past
    if limit:
        ordered = ordered[:limit]
    return {
        "schema": SCHEMA, "generated_at": _iso(now), "timezone": "AEST (UTC+10) and UTC",
        "retention": {"days": RETAIN_DAYS, "max_events": RETAIN_EVENTS},
        "events": len(events or []),
        "oldest_event_at": events[0]["at"] if events else None,
        "counts": {"active": len(active), "active_red": sum(1 for e in active if e["level"] == "RED"),
                   "active_amber": sum(1 for e in active if e["level"] == "AMBER"), "total": len(entries)},
        "active": active, "alerts": ordered,
    }


def _entry(e: dict, now: float) -> dict:
    title, meaning, cause = describe(e["check"])
    active = e["cleared_ts"] is None
    end = now if active else e["cleared_ts"]
    duration = max(0.0, end - e["started_ts"])
    return {
        "id": f"{e['check']}|{e['level']}|{_iso(e['started_ts'])}",
        "check": e["check"], "title": title, "meaning": meaning, "likely_cause": cause,
        "level": e["level"], "severity": e["level"] if active else "RECOVERED", "active": active,
        "started_ts": e["started_ts"], "started_at": _iso(e["started_ts"]), "started": times(e["started_ts"]),
        "cleared_ts": e["cleared_ts"], "cleared_at": _iso(e["cleared_ts"]), "cleared": times(e["cleared_ts"]),
        "clear_note": e["clear_note"],
        "duration_sec": round(duration), "duration_text": duration_text(duration),
        "observed": e["observed"] or e["latest_observed"], "latest_observed": e["latest_observed"],
        "cleared_observed": e["cleared_observed"], "threshold": e["threshold"],
        "technical_hint": e["hint"] or None, "runbook": e["runbook"], "runbook_url": runbook_url(e["runbook"]),
    }


def public_view(history: dict) -> dict:
    """Drop the watcher's technical hint for unauthenticated readers."""
    out = dict(history)
    for name in ("active", "alerts"):
        out[name] = [{k: v for k, v in entry.items() if k != "technical_hint"} for entry in history.get(name) or []]
    return out


# ------------------------------------------------------------------- HTML

_COLORS = {"RED": "#f85149", "AMBER": "#d29922", "RECOVERED": "#3fb950"}
_STYLE = (
    "body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:24px}"
    "a{color:#58a6ff}.wrap{max-width:1200px;margin:auto}.muted{color:#8b949e}"
    ".card{border:1px solid #30363d;background:#161b22;border-radius:9px;padding:14px 16px;margin:12px 0}"
    ".alert{border-left:6px solid #30363d}.alert h3{margin:0 0 4px;font-size:1.05rem}"
    ".sev{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.78rem;font-weight:700;color:#0d1117;margin-right:6px}"
    ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:4px 18px;font-size:.9rem;margin-top:8px}"
    ".k{color:#8b949e}.nav a{margin-right:12px}code{overflow-wrap:anywhere}"
    "@media(max-width:600px){body{padding:12px}}"
)


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _when(t: dict | None) -> str:
    return f"{_esc(t['aest'])} <span class='muted'>({_esc(t['utc'])})</span>" if t else "-"


def render_entry_html(entry: dict) -> str:
    sev = entry["severity"]
    label = sev if sev != "RECOVERED" else f"RECOVERED (was {entry['level']})"
    if entry["active"]:
        cleared = f"<b>Still active</b> for {_esc(entry['duration_text'])}"
    else:
        note = f" <span class='muted'>({_esc(entry['clear_note'])})</span>" if entry.get("clear_note") else ""
        cleared = f"{_when(entry['cleared'])} after {_esc(entry['duration_text'])}{note}"
    runbook = (f"<a href='{_esc(entry['runbook_url'])}' target='_blank' rel='noopener'>How to fix (runbook)</a>"
               if entry.get("runbook_url") else "")
    hint = (f"<div><span class='k'>Technical note:</span> {_esc(entry['technical_hint'])}</div>"
            if entry.get("technical_hint") else "")
    return (
        f"<div class='card alert' style='border-left-color:{_COLORS.get(sev, '#30363d')}' data-alert-id='{_esc(entry['id'])}'>"
        f"<h3><span class='sev' style='background:{_COLORS.get(sev, '#8b949e')}'>{_esc(label)}</span>"
        f"{_esc(entry['title'])}</h3>"
        f"<div>{_esc(entry['meaning'])}</div>"
        f"<div class='grid'>"
        f"<div><span class='k'>Started:</span> {_when(entry['started'])}</div>"
        f"<div><span class='k'>Cleared:</span> {cleared}</div>"
        f"<div><span class='k'>Check:</span> <code>{_esc(entry['check'])}</code></div>"
        f"</div>"
        f"<div style='margin-top:6px'><span class='k'>What the watcher saw:</span> {_esc(entry['observed'])}</div>"
        f"<div><span class='k'>Expected:</span> {_esc(entry['threshold'])}</div>"
        f"<div><span class='k'>Likely cause:</span> {_esc(entry['likely_cause'])} {runbook}</div>"
        f"{hint}</div>"
    )


def render_alerts_html(history: dict, *, title: str = "Alerts", nav_links=(), source_note: str = "") -> str:
    nav = " ".join(f"<a href='{_esc(href)}'>{_esc(label)}</a>" for label, href in nav_links)
    counts = history.get("counts") or {}
    active = history.get("active") or []
    past = [e for e in history.get("alerts") or [] if not e["active"]]
    active_html = "".join(render_entry_html(e) for e in active) or (
        "<div class='card'>No active alerts. Everything the watcher checks is currently fine.</div>")
    past_html = "".join(render_entry_html(e) for e in past) or "<div class='card muted'>No past alerts in this window.</div>"
    generated = times(_parse_ts(history.get("generated_at")))
    return (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta http-equiv='refresh' content='60'><title>{_esc(title)}</title><style>{_STYLE}</style></head>"
        f"<body><div class='wrap'><div class='nav'>{nav}</div><h1 id='alerts'>{_esc(title)}</h1>"
        f"<p class='muted'>Every alert the system-health watcher raised, newest first. Times are AEST (UTC+10) with UTC in "
        f"brackets. RED means trading, data safety or custody is affected; AMBER means degraded, look soon; RECOVERED "
        f"means it cleared by itself. Kept for {RETAIN_DAYS} days (up to {RETAIN_EVENTS} events). {_esc(source_note)}</p>"
        f"<p class='muted'>Updated {_when(generated)} &middot; {counts.get('active', 0)} active "
        f"({counts.get('active_red', 0)} RED, {counts.get('active_amber', 0)} AMBER) &middot; {counts.get('total', 0)} alerts "
        f"from {history.get('events', 0)} events since {_esc(history.get('oldest_event_at') or '-')}</p>"
        f"<h2>Active now</h2>{active_html}<h2>History</h2>{past_html}</div></body></html>"
    )


SECTION_MARKER = "data-alerts-section"


def section_script(endpoint: str = "/api/system-health/alerts", page: str = "/alerts", limit: int = 8) -> str:
    """Compact Alerts section for an existing dashboard; renders into ``#alertsSection``."""
    return (
        f'<script {SECTION_MARKER}="1">(function(){{var U={json.dumps(endpoint)},P={json.dumps(page)},N={int(limit)};'
        'var C={RED:"#f85149",AMBER:"#d29922",RECOVERED:"#3fb950"};'
        'function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[c];});}'
        'function when(t){return t?esc(t.aest)+" <span style=\\"color:#8b949e\\">("+esc(t.utc)+")</span>":"-";}'
        'function row(e){var s=e.severity,l=s==="RECOVERED"?"RECOVERED (was "+e.level+")":s;'
        'var cl=e.active?"<b>still active</b> ("+esc(e.duration_text)+")":when(e.cleared)+" after "+esc(e.duration_text);'
        'return "<tr><td style=\\"white-space:nowrap\\">"+when(e.started)+"</td><td><span style=\\"background:"+(C[s]||"#8b949e")'
        '+";color:#0d1117;border-radius:9px;padding:0 7px;font-weight:700;font-size:.75rem\\">"+esc(l)+"</span></td>'
        '<td><b>"+esc(e.title)+"</b><br><span style=\\"color:#8b949e\\">"+esc(e.observed)+"</span></td>'
        '<td>"+esc(e.likely_cause)+(e.runbook_url?" <a href=\\""+esc(e.runbook_url)+"\\" target=\\"_blank\\" rel=\\"noopener\\">runbook</a>":"")+"</td>'
        '<td>"+cl+"</td></tr>";}'
        'function draw(h){var el=document.getElementById("alertsSection");if(!el)return;'
        'if(!h){el.innerHTML="<b>Alerts</b>: history unavailable right now.";return;}'
        'var c=h.counts||{},rows=(h.alerts||[]).slice(0,N).map(row).join("");'
        'el.innerHTML="<div style=\\"display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap\\">'
        '<b style=\\"font-size:1.05rem\\">Alerts</b><span style=\\"color:#8b949e;font-size:.85rem\\">"+(c.active||0)+" active ("'
        '+(c.active_red||0)+" RED, "+(c.active_amber||0)+" AMBER) &middot; "+(c.total||0)+" in the last 30 days &middot; '
        '<a href=\\""+P+"\\">see all alerts</a></span></div>"+(rows?"<div style=\\"overflow-x:auto\\"><table style=\\"width:100%;'
        'border-collapse:collapse;font-size:.85rem;margin-top:6px\\"><thead><tr style=\\"color:#8b949e;text-align:left\\">'
        '<th>Started (AEST / UTC)</th><th>Severity</th><th>What happened</th><th>Likely cause</th><th>Cleared</th></tr></thead>'
        '<tbody>"+rows+"</tbody></table></div>":"<div style=\\"color:#8b949e\\">No alerts recorded yet.</div>");}'
        'function poll(){fetch(U,{cache:"no-store"}).then(function(x){return x.ok?x.json():null;}).then(draw)'
        '.catch(function(){draw(null);});}'
        'if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",poll);}else{poll();}'
        'setInterval(poll,60000);})();</script>'
    )


def inject_section(response, endpoint: str = "/api/system-health/alerts", page: str = "/alerts"):
    """Add the Alerts section script to an HTML page that has an ``#alertsSection`` container."""
    try:
        if response.status_code != 200 or response.mimetype != "text/html":
            return response
        if response.direct_passthrough or response.is_streamed:
            return response
        body = response.get_data(as_text=True)
        if SECTION_MARKER in body or 'id="alertsSection"' not in body or len(body) > 20 * 1024 * 1024:
            return response
        script = section_script(endpoint, page)
        idx = body.lower().rfind("</body>")
        body = body[:idx] + script + body[idx:] if idx >= 0 else body + script
        response.set_data(body)
    except Exception:
        return response
    return response


def history_from_file(alarms_path, report_path=None, *, now: float | None = None, limit: int | None = None) -> dict:
    """Analyzer / insights path: read the laptop's alarm log (and latest report) directly."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    report = None
    if report_path is not None:
        try:
            report = json.loads(Path(report_path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            report = None
    statuses, at = {}, None
    if isinstance(report, dict):
        at = _parse_ts(report.get("generated_ts")) or _parse_ts(report.get("generated_at"))
        if at is not None and now - at <= 20 * 60:
            statuses = check_statuses(report)
    history = build_history(read_events_file(alarms_path, now), now=now, statuses=statuses, statuses_at=at, limit=limit)
    history["source"] = str(alarms_path)
    return history
