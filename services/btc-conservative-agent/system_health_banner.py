"""Shared system-health banner for the Fly dashboard and the analyzer dashboard.

The laptop watcher (``scripts/system_health.py``) publishes a ``system_health_v1``
report.  Both dashboards expose a bounded copy at ``/api/system-health`` and
inject a small polling script into HTML pages that renders a red/amber banner
whenever the report is not GREEN or has gone stale, plus an always-visible
uninterrupted-runtime line when the payload carries ``uptime``
(``runtime_uptime``).  Read-only: nothing here
touches trading, relay, or exchange state.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "system_health_v1"
STALE_AFTER_SEC = 20 * 60
MAX_REPORT_BYTES = 256 * 1024
MAX_FAILING = 25
_STATUSES = ("GREEN", "AMBER", "RED", "SKIP")


def _parse_ts(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text(value, limit: int = 240) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def sanitize_report(report) -> dict | None:
    """Reduce an untrusted report to the fields the banner and API need."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        return None
    verdict = report.get("verdict")
    if verdict not in _STATUSES:
        return None
    failing = []
    for item in (report.get("failing") or [])[:MAX_FAILING]:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        failing.append({
            "id": _text(item.get("id"), 64),
            "status": status if status in _STATUSES else "AMBER",
            "observed": _text(item.get("observed")),
            "threshold": _text(item.get("threshold")),
            "hint": _text(item.get("hint"), 400),
            "runbook": _text(item.get("runbook"), 160),
            "last_good_at": _text(item.get("last_good_at"), 40),
        })
    alarms = []
    for item in (report.get("open_alarms") or [])[:MAX_FAILING]:
        if isinstance(item, dict):
            alarms.append({"id": _text(item.get("id"), 64), "since": _text(item.get("since"), 40)})
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    return {
        "schema": SCHEMA,
        "generated_at": _text(report.get("generated_at"), 40),
        "verdict": verdict,
        "counts": {k: int(v) for k, v in counts.items() if k in _STATUSES and isinstance(v, int)},
        "failing": failing,
        "open_alarms": alarms,
    }


def with_staleness(report: dict | None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if not report:
        return {"schema": SCHEMA, "verdict": "AMBER", "stale": True, "age_sec": None,
                "failing": [{"id": "watcher.stale", "status": "AMBER",
                             "observed": "no system-health report published",
                             "hint": "laptop watcher not reporting; run scripts/system_health.py --tick",
                             "runbook": "docs/SYSTEM_HEALTH_RUNBOOK.md#watcher-stale"}],
                "open_alarms": [], "counts": {}}
    out = dict(report)
    generated = _parse_ts(report.get("generated_at"))
    age = None if generated is None else max(0.0, now - generated)
    out["age_sec"] = None if age is None else round(age)
    out["stale"] = age is None or age > STALE_AFTER_SEC
    if out["stale"]:
        out["failing"] = list(out.get("failing") or []) + [{
            "id": "watcher.stale", "status": "AMBER",
            "observed": "report age unknown" if age is None else f"report {int(age // 60)}m old",
            "threshold": f"<{STALE_AFTER_SEC // 60}m",
            "hint": "laptop watcher stopped publishing; check DoxxedSystemHealthWatcher / supervisor",
            "runbook": "docs/SYSTEM_HEALTH_RUNBOOK.md#watcher-stale",
        }]
        if out.get("verdict") == "GREEN":
            out["verdict"] = "AMBER"
    return out


def read_report_file(path: Path) -> dict | None:
    try:
        if path.stat().st_size > MAX_REPORT_BYTES * 8:
            return None
        return sanitize_report(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def write_report_file(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".system-health-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


BANNER_MARKER = "data-system-health-banner"


def banner_script(endpoint: str = "/api/system-health") -> str:
    return (
        f'<script {BANNER_MARKER}="1">(function(){{'
        f'var U={json.dumps(endpoint)};'
        'function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[c];});}'
        'var C={green:"#1b7f3b",amber:"#9a6700",red:"#b00020"};'
        'function uptimeHtml(u){if(!u)return "";'
        'var s="<b>"+esc(u.uninterrupted_label||"Uptime unavailable")+"</b>";'
        'var since=u.since_melbourne||u.since_aest;if(since)s+=" &middot; since "+esc(since)+" ("+esc(u.since_utc)+")";'
        'var li=u.last_interruption;if(li&&li.text)s+=" &middot; "+(u.running?"last interruption: ":"cause: ")+esc(li.text);'
        'if(u.interruptions_24h!=null)s+=" &middot; 24h: "+esc(u.interruptions_24h)+" interruption"+(u.interruptions_24h===1?"":"s");'
        'if(u.longest_run_7d_label&&u.available!==false)s+=" &middot; 7d longest: "+esc(u.longest_run_7d_label);'
        'if(u.proof&&u.proof.label)s+=" &middot; "+esc(u.proof.label);'
        'if(u.note)s+=" &middot; <i>"+esc(u.note)+"</i>";return s;}'
        'function draw(r){var el=document.getElementById("system-health-banner");'
        'var u=r&&r.uptime;var bad=r&&!(r.verdict==="GREEN"&&!r.stale);'
        'if(!r||(!bad&&!u)){if(el)el.remove();return;}'
        'if(!el){el=document.createElement("div");el.id="system-health-banner";'
        'el.style.cssText="position:sticky;top:0;z-index:99999;font:13px/1.4 system-ui,sans-serif;color:#fff;overflow-wrap:anywhere;";'
        'document.body.insertBefore(el,document.body.firstChild);}'
        'var h="";if(u){h+="<div id=\\"runtime-uptime-strip\\" data-state=\\""+esc(u.state)+"\\" style=\\"padding:5px 12px;background:"'
        '+(C[u.colour]||C.red)+"\\">"+uptimeHtml(u)+"</div>";}'
        'if(bad){var red=r.verdict==="RED";'
        'var f=(r.failing||[]).filter(function(c){return c.status!=="GREEN";});'
        'var parts=f.slice(0,4).map(function(c){return "<b>"+esc(c.id)+"</b>: "+esc(c.observed);});'
        'h+="<div style=\\"padding:6px 12px;background:"+(red?C.red:C.amber)+"\\">SYSTEM HEALTH "+esc(r.verdict)+(r.stale?" (stale)":"")+" &mdash; "+(parts.join(" &middot; ")||"see /api/system-health")'
        '+(f.length>4?" &middot; +"+(f.length-4)+" more":"")'
        '+" &middot; <a href=\\"/alerts\\" style=\\"color:#fff;text-decoration:underline\\">all alerts</a></div>";}'
        'el.innerHTML=h;}'
        'function poll(){fetch(U,{cache:"no-store"}).then(function(x){return x.ok?x.json():null;})'
        '.then(draw).catch(function(){});}'
        'if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",poll);}else{poll();}'
        'setInterval(poll,60000);})();</script>'
    )


def inject_banner(response, endpoint: str = "/api/system-health"):
    """Append the banner script to a Flask HTML response (idempotent, bounded)."""
    try:
        if response.status_code != 200 or response.mimetype != "text/html":
            return response
        if response.direct_passthrough or response.is_streamed:
            return response
        body = response.get_data(as_text=True)
        if BANNER_MARKER in body or len(body) > 20 * 1024 * 1024:
            return response
        script = banner_script(endpoint)
        idx = body.lower().rfind("</body>")
        body = body[:idx] + script + body[idx:] if idx >= 0 else body + script
        response.set_data(body)
    except Exception:
        return response
    return response


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
