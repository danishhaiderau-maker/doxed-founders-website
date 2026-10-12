"""Platform health monitor (Cloudflare, Fly.io, Neon, Vercel, GitHub Actions, Railway).

Danish 12 Oct: any change in platform behaviour must be noticed within minutes, not days.
A background thread in bot.py calls ``PlatformMonitor.run_check`` every ``INTERVAL_SEC``;
each check records a snapshot and raises alerts on state change, revision change, error,
outage or rising latency.  Read via GET /api/monitor/platforms (admin or monitor token);
alerts also appear in /api/monitor/alerts and /api/monitor/summary.

Read-only probes, short timeouts, never touches trading state.  A platform field that needs a
credential this process does not have reports ``no credential`` instead of failing.  The box
collector may POST credentialed observations to /api/monitor/platforms/receipt (admin); they
are merged into the snapshot with their own age.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

SCHEMA = "monitor_platforms_v1"
INTERVAL_SEC = 180
LATENCY_WINDOW = 20
LATENCY_RISE_FACTOR = 2.5
LATENCY_RISE_MIN_MS = 800.0
RECEIPT_STALE_SEC = 900
NO_CRED = "no credential"

SITE_HOST = "doxxedcrypto.digital"
SITE_URL = f"https://{SITE_HOST}/"
RAILWAY_WEB_HEALTH = "https://doxed-founders-website-production.up.railway.app/api/health"
FLY_PUBLIC_HEALTH = "https://doxed-btc-bot.fly.dev/health"
GH_REPO = "danishhaiderau-maker/doxed-founders-website"
R2_BUCKET = "doxed-btc-archive"
ARCHIVE_RECEIPT_MAX_AGE_SEC = 26 * 3600
STUCK_RUN_SEC = 45 * 60
QUEUED_STUCK_SEC = 20 * 60


def _now() -> float:
    return time.time()


def _http(session, method: str, url: str, timeout: float = 6.0, **kw) -> dict:
    t0 = time.perf_counter()
    try:
        r = session.request(method, url, timeout=timeout, **kw)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        body = None
        try:
            body = r.json() if r.content and "json" in (r.headers.get("content-type") or "") else None
        except Exception:
            body = None
        return {"ok": 200 <= r.status_code < 400, "status": r.status_code, "latency_ms": ms,
                "headers": {k.lower(): v for k, v in r.headers.items()}, "json": body}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": None, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": f"{type(exc).__name__}: {str(exc)[:120]}", "headers": {}, "json": None}


def _iso_to_ts(s) -> float | None:
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


# --------------------------------------------------------------------------- probes
def probe_cloudflare(session, ctx: dict) -> dict:
    out: dict[str, Any] = {"platform": "cloudflare"}
    try:
        t0 = time.perf_counter()
        addrs = sorted({a[4][0] for a in socket.getaddrinfo(SITE_HOST, 443, proto=socket.IPPROTO_TCP)})
        out["dns"] = {"ok": bool(addrs), "addresses": addrs[:6], "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        out["dns"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:80]}"}
    site = _http(session, "GET", SITE_URL, timeout=8.0)
    h = site.get("headers") or {}
    out["website"] = {"ok": site["ok"], "status": site["status"], "latency_ms": site["latency_ms"],
                      "error": site.get("error"), "served_by": h.get("server"),
                      "cf_ray": bool(h.get("cf-ray")), "vercel": bool(h.get("x-vercel-id"))}
    # The site is Cloudflare-proxied (cf-ray). Tunnel/zone state needs a Cloudflare API token.
    out["tunnel"] = ({"status": "proxied", "cf_ray_present": True} if h.get("cf-ray")
                     else {"status": "not_seen"}) if site["ok"] else {"status": "unreachable"}
    out["zone_api"] = "ok" if os.getenv("CLOUDFLARE_API_TOKEN") else NO_CRED
    rec = ctx.get("archive_receipt")
    r2: dict[str, Any] = {"bucket": R2_BUCKET}
    if isinstance(rec, dict):
        ts = rec.get("received_at") or rec.get("finished_at_ts") or _iso_to_ts(rec.get("finished_at"))
        try:
            ts = float(ts)
        except Exception:
            ts = None
        age = round(_now() - ts) if ts else None
        r2["last_backup"] = {"age_sec": age, "ok": rec.get("ok", rec.get("verdict") in (None, "OK", "GREEN")),
                             "stale": age is None or age > ARCHIVE_RECEIPT_MAX_AGE_SEC,
                             "keys": sorted(rec.keys())[:12]}
    else:
        r2["last_backup"] = {"age_sec": None, "stale": True, "missing": True}
    box = (ctx.get("receipt") or {}).get("r2")
    r2["bucket_reachable"] = box if isinstance(box, dict) else NO_CRED
    out["r2"] = r2
    ok = out["dns"].get("ok") and site["ok"]
    out["state"] = "up" if ok else "down"
    out["latency_ms"] = site["latency_ms"]
    out["revision"] = None
    return out


def probe_vercel(session, ctx: dict) -> dict:
    out: dict[str, Any] = {"platform": "vercel", "used": True}
    r = _http(session, "GET", "https://doxed-founders-website.vercel.app/", timeout=8.0)
    out["production_url"] = {"ok": r["ok"], "status": r["status"], "latency_ms": r["latency_ms"], "error": r.get("error")}
    tok = os.getenv("VERCEL_TOKEN")
    if tok:
        d = _http(session, "GET", "https://api.vercel.com/v6/deployments?limit=1&target=production",
                  headers={"Authorization": f"Bearer {tok}"})
        dep = ((d.get("json") or {}).get("deployments") or [{}])[0] if d["ok"] else {}
        out["latest_production_deployment"] = {"state": dep.get("state") or dep.get("readyState"),
                                               "uid": dep.get("uid"), "created": dep.get("created"),
                                               "sha": ((dep.get("meta") or {}).get("githubCommitSha") or "")[:8] or None,
                                               "error": d.get("error") if not d["ok"] else None}
        out["revision"] = out["latest_production_deployment"]["sha"] or dep.get("uid")
    else:
        out["latest_production_deployment"] = NO_CRED
        out["revision"] = None
    out["state"] = "up" if r["ok"] else "down"
    out["latency_ms"] = r["latency_ms"]
    return out


def probe_railway(session, ctx: dict) -> dict:
    out: dict[str, Any] = {"platform": "railway"}
    r = _http(session, "GET", RAILWAY_WEB_HEALTH, timeout=8.0)
    j = r.get("json") or {}
    out["website"] = {"ok": r["ok"] and j.get("status") == "ok", "status": r["status"],
                      "latency_ms": r["latency_ms"], "error": r.get("error"), "services": j.get("services")}
    web = ctx.get("website_view") or {}
    hb = web.get("executor_heartbeat_age_sec")
    try:
        hb = float(hb) if hb is not None else None
    except Exception:
        hb = None
    view_age = round(_now() - float(web["fetched_at_ts"])) if web.get("fetched_at_ts") else None
    out["executor"] = {"heartbeat_age_sec": hb, "view_reachable": web.get("reachable"),
                       "view_age_sec": view_age, "ok": hb is not None and hb < 300}
    box = (ctx.get("receipt") or {}).get("railway")
    if os.getenv("RAILWAY_API_TOKEN") or os.getenv("RAILWAY_TOKEN"):
        out["services_api"] = "token present; API probe via box receipt"
    out["services"] = box if isinstance(box, dict) else NO_CRED
    out["revision"] = (box or {}).get("revision") if isinstance(box, dict) else None
    out["state"] = "up" if out["website"]["ok"] and out["executor"]["ok"] else (
        "degraded" if out["website"]["ok"] or out["executor"]["ok"] else "down")
    out["latency_ms"] = r["latency_ms"]
    return out


def probe_neon(session, ctx: dict) -> dict:
    out: dict[str, Any] = {"platform": "neon"}
    r = _http(session, "GET", RAILWAY_WEB_HEALTH, timeout=8.0)
    db = ((r.get("json") or {}).get("services") or {}).get("database")
    out["db_via_website"] = {"database": db, "ok": db == "ok", "latency_ms": r["latency_ms"],
                             "error": r.get("error"), "note": "website /api/health runs a DB query on Neon"}
    out["direct_connection"] = NO_CRED if not os.getenv("DATABASE_URL") else "configured (not probed from Fly)"
    tok = os.getenv("NEON_API_KEY")
    if tok:
        p = _http(session, "GET", "https://console.neon.tech/api/v2/projects?limit=5",
                  headers={"Authorization": f"Bearer {tok}"})
        projs = (p.get("json") or {}).get("projects") or []
        out["storage"] = [{"id": x.get("id"), "synthetic_storage_size": x.get("synthetic_storage_size")}
                          for x in projs] if p["ok"] else {"error": p.get("error") or p["status"]}
    else:
        out["storage"] = NO_CRED
    out["state"] = "up" if db == "ok" else ("down" if r["ok"] else "unknown")
    out["latency_ms"] = r["latency_ms"]
    out["revision"] = None
    return out


def probe_github(session, ctx: dict) -> dict:
    out: dict[str, Any] = {"platform": "github_actions", "repo": GH_REPO}
    tok = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    out["auth"] = "token" if tok else "anonymous (public repo, 60 req/h)"
    r = _http(session, "GET", f"https://api.github.com/repos/{GH_REPO}/actions/runs?per_page=30",
              headers=headers, timeout=8.0)
    if not r["ok"]:
        out.update(state="unknown" if r["status"] in (403, 429) else "down",
                   error=r.get("error") or f"HTTP {r['status']}", latency_ms=r["latency_ms"], revision=None)
        return out
    runs = (r.get("json") or {}).get("workflow_runs") or []
    now = _now()
    def brief(x):
        return {"id": x.get("id"), "name": x.get("name"), "status": x.get("status"),
                "conclusion": x.get("conclusion"), "sha": (x.get("head_sha") or "")[:8],
                "started": x.get("run_started_at") or x.get("created_at")}
    deploy = [x for x in runs if "deploy" in str(x.get("name") or "").lower()]
    latest = brief(deploy[0]) if deploy else None
    latest_ok = next((brief(x) for x in deploy if x.get("conclusion") == "success"), None)
    queued = [brief(x) for x in runs if x.get("status") in ("queued", "waiting", "pending", "requested")]
    stuck = []
    for x in runs:
        st = _iso_to_ts(x.get("run_started_at") or x.get("created_at")) or now
        if x.get("status") == "in_progress" and now - st > STUCK_RUN_SEC:
            stuck.append(brief(x))
        elif x.get("status") in ("queued", "waiting", "pending") and now - st > QUEUED_STUCK_SEC:
            stuck.append(brief(x))
    failed_recent = [brief(x) for x in runs[:15] if x.get("conclusion") in ("failure", "timed_out", "startup_failure")]
    out.update(latest_deploy_run=latest, latest_successful_deploy=latest_ok, queued=queued[:10],
               stuck=stuck[:10], failed_recent=failed_recent[:10], latency_ms=r["latency_ms"],
               revision=(latest_ok or {}).get("sha"))
    if latest and latest.get("conclusion") in ("failure", "timed_out", "startup_failure"):
        out["state"] = "failing"
    elif stuck:
        out["state"] = "stuck"
    else:
        out["state"] = "up"
    return out


def probe_fly(session, ctx: dict) -> dict:
    out: dict[str, Any] = {"platform": "fly"}
    out["machine"] = {"app": os.getenv("FLY_APP_NAME"), "machine_id": os.getenv("FLY_MACHINE_ID"),
                      "region": os.getenv("FLY_REGION"), "image": os.getenv("FLY_IMAGE_REF"),
                      "machine_version": os.getenv("FLY_MACHINE_VERSION"), "state": "started (self)"}
    out["revision"] = ctx.get("git_rev")
    out["boot_id"] = ctx.get("boot_id")
    out["process_uptime_sec"] = ctx.get("uptime_sec")
    out["restarts"] = ctx.get("restarts")
    vols = {}
    for p in ctx.get("volume_paths") or ():
        try:
            u = shutil.disk_usage(p)
            vols[str(p)] = {"used_pct": round(u.used / u.total * 100, 1), "free_gb": round(u.free / 1e9, 2),
                            "total_gb": round(u.total / 1e9, 2)}
        except Exception as exc:  # noqa: BLE001
            vols[str(p)] = {"error": type(exc).__name__}
    out["volumes"] = vols
    pub = _http(session, "GET", FLY_PUBLIC_HEALTH, timeout=8.0)
    out["public_edge"] = {"ok": pub["ok"], "status": pub["status"], "latency_ms": pub["latency_ms"], "error": pub.get("error")}
    box = (ctx.get("receipt") or {}).get("fly")
    out["machines_api"] = box if isinstance(box, dict) else (
        "token present" if os.getenv("FLY_API_TOKEN") else NO_CRED)
    worst_vol = max([v.get("used_pct") or 0 for v in vols.values()] or [0])
    out["state"] = "up" if pub["ok"] else "degraded"
    if worst_vol >= 90:
        out["state"] = "degraded"
    out["latency_ms"] = pub["latency_ms"]
    return out


PROBES: dict[str, Callable] = {
    "cloudflare": probe_cloudflare, "fly": probe_fly, "neon": probe_neon, "vercel": probe_vercel,
    "github_actions": probe_github, "railway": probe_railway,
}


# --------------------------------------------------------------------------- diff / alerts
def diff_alerts(prev: dict | None, cur: dict, lat_hist: deque, now: float) -> list[dict]:
    name = cur.get("platform")
    alerts = []

    def add(kind, sev, msg, **extra):
        alerts.append({"id": f"platform.{name}.{kind}", "platform": name, "kind": kind, "severity": sev,
                       "message": msg[:240], "ts": now, **extra})

    state = cur.get("state")
    if cur.get("probe_error"):
        add("error", "red", f"{name} probe raised {cur['probe_error']}")
    if prev is not None:
        if prev.get("state") != state:
            sev = "red" if state in ("down", "failing", "stuck") else ("amber" if state != "up" else "info")
            add("state_change", sev, f"{name} state {prev.get('state')} -> {state}", previous=prev.get("state"), current=state)
        if prev.get("revision") and cur.get("revision") and prev.get("revision") != cur.get("revision"):
            add("revision_change", "info", f"{name} revision {prev.get('revision')} -> {cur.get('revision')}",
                previous=prev.get("revision"), current=cur.get("revision"))
    elif state in ("down", "failing", "stuck"):
        add("outage", "red", f"{name} is {state} at first check")
    lat = cur.get("latency_ms")
    if isinstance(lat, (int, float)) and len(lat_hist) >= 5:
        base = sorted(lat_hist)[len(lat_hist) // 2]
        if lat > max(base * LATENCY_RISE_FACTOR, base + LATENCY_RISE_MIN_MS):
            add("latency_rise", "amber", f"{name} latency {lat:.0f} ms vs median {base:.0f} ms", latency_ms=lat, median_ms=base)
    if name == "github_actions" and prev is not None:
        new_failed = {r["id"] for r in cur.get("failed_recent") or []} - {r["id"] for r in prev.get("failed_recent") or []}
        for r in cur.get("failed_recent") or []:
            if r["id"] in new_failed:
                add(f"run_failed.{r['id']}", "red", f"Actions run {r['name']} {r['id']} {r['conclusion']} ({r['sha']})")
        if cur.get("stuck") and not prev.get("stuck"):
            add("run_stuck", "red", f"{len(cur['stuck'])} Actions run(s) queued/in progress too long")
    if name == "cloudflare":
        lb = (cur.get("r2") or {}).get("last_backup") or {}
        plb = ((prev or {}).get("r2") or {}).get("last_backup") or {}
        if lb.get("stale") and not plb.get("stale", False if prev else True):
            add("r2_backup_stale", "amber", f"R2 backup receipt stale (age {lb.get('age_sec')} s)")
    if name == "railway" and prev is not None:
        if (cur.get("executor") or {}).get("ok") is False and (prev.get("executor") or {}).get("ok"):
            add("executor_heartbeat", "red", f"Railway executor heartbeat stale ({cur['executor'].get('heartbeat_age_sec')} s)")
    if name == "fly" and prev is not None:
        if cur.get("boot_id") != prev.get("boot_id"):
            add("restart", "amber", "Fly process restarted (boot id changed)")
        for p, v in (cur.get("volumes") or {}).items():
            if (v.get("used_pct") or 0) >= 85 and ((prev.get("volumes") or {}).get(p) or {}).get("used_pct", 0) < 85:
                add("volume_full", "amber", f"Fly volume {p} at {v['used_pct']}%")
    return alerts


class PlatformMonitor:
    def __init__(self, state_path: Path | None = None, max_alerts: int = 300, max_snapshots: int = 120):
        self.lock = threading.Lock()
        self.state_path = state_path
        self.last: dict[str, dict] = {}
        self.lat: dict[str, deque] = {k: deque(maxlen=LATENCY_WINDOW) for k in PROBES}
        self.alerts: deque = deque(maxlen=max_alerts)
        self.snapshots: deque = deque(maxlen=max_snapshots)
        self.receipt: dict = {}
        self.receipt_at: float | None = None
        self.checks = 0
        self.last_check_at: float | None = None
        self.last_check_ms: float | None = None
        self._load()

    def _load(self):
        if not self.state_path:
            return
        try:
            d = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
            self.last = d.get("last") or {}
            for a in d.get("alerts") or []:
                self.alerts.append(a)
            for k, v in (d.get("lat") or {}).items():
                if k in self.lat:
                    self.lat[k].extend(v[-LATENCY_WINDOW:])
        except Exception:
            pass

    def _save(self):
        if not self.state_path:
            return
        try:
            tmp = Path(str(self.state_path) + ".tmp")
            tmp.write_text(json.dumps({"last": self.last, "alerts": list(self.alerts)[-200:],
                                       "lat": {k: list(v) for k, v in self.lat.items()}}, default=str), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception:
            pass

    def accept_receipt(self, body: dict, now: float | None = None) -> None:
        now = now or _now()
        clean = {k: v for k, v in (body or {}).items() if k in ("fly", "railway", "r2", "neon", "vercel", "cloudflare", "source")}
        with self.lock:
            self.receipt, self.receipt_at = clean, now

    def run_check(self, session, ctx: dict) -> dict:
        now = _now()
        t0 = time.perf_counter()
        with self.lock:
            receipt = dict(self.receipt) if self.receipt_at and now - self.receipt_at < RECEIPT_STALE_SEC else {}
        ctx = {**ctx, "receipt": receipt}
        snap = {}
        for name, fn in PROBES.items():
            try:
                cur = fn(session, ctx)
            except Exception as exc:  # noqa: BLE001
                cur = {"platform": name, "state": "unknown", "probe_error": f"{type(exc).__name__}: {str(exc)[:100]}"}
            cur["checked_at"] = now
            snap[name] = cur
        with self.lock:
            new = []
            for name, cur in snap.items():
                new.extend(diff_alerts(self.last.get(name), cur, self.lat[name], now))
                if isinstance(cur.get("latency_ms"), (int, float)) and cur.get("state") == "up":
                    self.lat[name].append(cur["latency_ms"])
                self.last[name] = cur
            self.alerts.extend(new)
            self.snapshots.append({"ts": now, "states": {k: v.get("state") for k, v in snap.items()},
                                   "revisions": {k: v.get("revision") for k, v in snap.items()},
                                   "latency_ms": {k: v.get("latency_ms") for k, v in snap.items()},
                                   "new_alerts": len(new)})
            self.checks += 1
            self.last_check_at = now
            self.last_check_ms = round((time.perf_counter() - t0) * 1000)
            self._save()
        return snap

    def worst(self) -> str:
        order = {"up": 0, "unknown": 1, "degraded": 2, "stuck": 3, "failing": 3, "down": 4}
        states = [v.get("state") for v in self.last.values()]
        return max(states, key=lambda s: order.get(s, 1)) if states else "not_checked"

    def open_alerts(self, now: float, window_sec: float = 3600) -> list[dict]:
        return [a for a in self.alerts if a.get("severity") in ("red", "amber") and now - float(a.get("ts") or 0) < window_sec]

    def view(self, now: float | None = None, snapshots: int = 20) -> dict:
        now = now or _now()
        with self.lock:
            return {"schema": SCHEMA, "interval_sec": INTERVAL_SEC, "checks_since_boot": self.checks,
                    "last_check_at": self.last_check_at,
                    "last_check_age_sec": round(now - self.last_check_at) if self.last_check_at else None,
                    "last_check_ms": self.last_check_ms, "worst_state": self.worst(),
                    "platforms": self.last, "open_alerts_1h": self.open_alerts(now),
                    "alerts": list(self.alerts)[-100:], "snapshots": list(self.snapshots)[-snapshots:],
                    "box_receipt_age_sec": round(now - self.receipt_at) if self.receipt_at else None,
                    "no_credential_meaning": "field needs a platform API token not available to the Fly bot"}

    def compact(self, now: float | None = None) -> dict:
        now = now or _now()
        with self.lock:
            return {"worst_state": self.worst(), "states": {k: v.get("state") for k, v in self.last.items()},
                    "last_check_age_sec": round(now - self.last_check_at) if self.last_check_at else None,
                    "open_alerts_1h": [a["id"] for a in self.open_alerts(now)][:10],
                    "detail": "/api/monitor/platforms"}
