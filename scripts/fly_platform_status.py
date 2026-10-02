"""Fly.io platform status awareness for the health watcher (:9011) and self-aware (:9021).

Reads the public Fly status page (incident.io, https://status.flyio.net): the
native ``/api/v1/summary`` (affected components + maintenance windows) with the
Statuspage-compatible ``/api/v2/summary.json`` as fallback. Results are cached
5 min, every request has a short timeout, and an unreachable feed never fails
the caller: it yields SKIP ("status feed unreachable") and only turns AMBER
after a sustained outage.

Classification of each active event against our app (doxed-btc-bot, region
from services/btc-conservative-agent/fly.toml):
  INFO   notice/maintenance that does not touch our region or components
         (e.g. "Change in Status Page Provider", maintenance in ORD)
  AMBER  incident or in-progress maintenance on our region or a component our
         app depends on (Machines, Volumes, networking/proxy, deploys/builders)
The check is RED only when such an event is active AND our own Fly runtime
checks are failing too. Failing app checks are annotated "likely Fly platform
incident: <title> <url>" so they are told apart from our own bugs; a platform
event with a healthy app reads "platform notice - app unaffected".
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SCHEMA = "fly_platform_status_v1"
STATUS_PAGE = "https://status.flyio.net"
FEED_URLS = (
    ("incident_io_v1", f"{STATUS_PAGE}/api/v1/summary"),
    ("statuspage_v2", f"{STATUS_PAGE}/api/v2/summary.json"),
)
APP = "doxed-btc-bot"
DEFAULT_REGION = "sin"
CACHE_SEC = 300.0
ERROR_RETRY_SEC = 120.0
TIMEOUT_SEC = 5.0
UNREACHABLE_AMBER_SEC = 3600.0
STALE_SNAPSHOT_MAX_SEC = 3600.0

GREEN, AMBER, RED, SKIP = "GREEN", "AMBER", "RED", "SKIP"
INFO = "INFO"

# Our own runtime checks a platform incident can explain. fly.revision is a
# deploy-queue fact, not runtime health, so it never escalates.
APP_CHECK_PREFIXES = ("fly.", "ws.", "shipper.", "deploy.")
APP_CHECK_EXCLUDE = frozenset({"fly.platform_status", "fly.revision"})

# Components (status page names, lower case substrings) our app depends on.
RELEVANT_COMPONENTS = ("customer applications", "machines", "volumes", "persistent storage", "deployments",
                       "remote builds", "builder", "proxy", "network", "anycast", "edge", "dns", "certificates",
                       "github", "registry", "wireguard", "flyctl")
RELEVANT_TEXT = re.compile(
    r"\b(machines?|volumes?|proxy|network(ing)?|anycast|edge|deploy(s|ments?)?|builders?|remote builds?|"
    r"github actions?|registry|all regions|global(ly)?|api)\b", re.I)
INFORMATIONAL_TEXT = re.compile(r"status page|statuspage|status provider|page provider", re.I)
REGION_COMPONENT = re.compile(r"^([A-Z]{3})\s+-\s+")
REGION_CODES = re.compile(r"\b([A-Z]{3})\b")
REGION_NAMES = {"sin": "singapore", "syd": "sydney", "nrt": "tokyo", "bom": "mumbai", "fra": "frankfurt",
                "ams": "amsterdam", "lhr": "london", "cdg": "paris", "arn": "stockholm", "iad": "ashburn",
                "ewr": "secaucus", "ord": "chicago", "dfw": "dallas", "lax": "los angeles", "sjc": "san jose",
                "yyz": "toronto", "gru": "sao paulo", "jnb": "johannesburg"}

_PROCESS_CACHE: dict[str, Any] = {}


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _parse_ts(value: Any) -> float | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        from datetime import datetime  # noqa: PLC0415

        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", text)
        if not m:
            return None
        try:
            from datetime import datetime  # noqa: PLC0415

            return datetime.fromisoformat(m.group(1) + (m.group(3) or "+00:00")).timestamp()
        except ValueError:
            return None


def app_region(repo_root: Path | None = None) -> str:
    """Primary region of doxed-btc-bot (env FLY_PLATFORM_REGION overrides fly.toml)."""
    env = os.environ.get("FLY_PLATFORM_REGION")
    if env:
        return env.strip().lower()
    root = repo_root or Path(__file__).resolve().parents[1]
    try:
        text = (root / "services" / "btc-conservative-agent" / "fly.toml").read_text(encoding="utf-8")
        m = re.search(r'^\s*primary_region\s*=\s*"([a-z]{3})"', text, re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    return DEFAULT_REGION


# ------------------------------------------------------------------ fetch

def http_get_json(url: str, timeout: float = TIMEOUT_SEC) -> tuple[Any, str | None]:
    """(payload, bounded error code). Never echoes upstream text."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "doxxed-fly-platform/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2_000_000)
        return json.loads(raw.decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP_{exc.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
        return None, "UNREACHABLE"
    except ValueError:
        return None, "BAD_JSON"


def _component_names(items: Any) -> list[str]:
    out = []
    for c in items or []:
        if isinstance(c, Mapping) and c.get("name"):
            out.append(str(c["name"]))
        elif isinstance(c, str):
            out.append(c)
    return out


def parse_v1(payload: Any) -> dict[str, Any]:
    """incident.io ``/api/v1/summary``."""
    if not isinstance(payload, Mapping) or not any(
            k in payload for k in ("ongoing_incidents", "in_progress_maintenances", "scheduled_maintenances")):
        raise ValueError("not an incident.io summary")
    events = []
    for key, kind in (("ongoing_incidents", "incident"), ("in_progress_maintenances", "maintenance"),
                      ("scheduled_maintenances", "scheduled")):
        for e in payload.get(key) or []:
            if not isinstance(e, Mapping):
                continue
            events.append({
                "id": e.get("id"), "title": e.get("name"), "kind": kind, "status": e.get("status"),
                "impact": e.get("current_worst_impact") or e.get("impact"),
                "url": e.get("url"), "components": _component_names(e.get("affected_components")),
                "starts_at": e.get("starts_at") or e.get("started_at"), "ends_at": e.get("ends_at"),
                "updated_at": e.get("last_update_at"), "message": (e.get("last_update_message") or "")[:500],
            })
    return {"page_url": payload.get("page_url") or f"{STATUS_PAGE}/", "indicator": None, "description": None,
            "degraded_components": [], "events": events}


def parse_v2(payload: Any) -> dict[str, Any]:
    """Statuspage-compatible ``/api/v2/summary.json``."""
    if not isinstance(payload, Mapping) or "status" not in payload:
        raise ValueError("not a statuspage summary")
    page = payload.get("page") or {}
    events = []
    for e in payload.get("incidents") or []:
        if isinstance(e, Mapping) and str(e.get("status") or "").lower() not in ("resolved", "postmortem"):
            events.append(_v2_event(e, "incident", page))
    for e in payload.get("scheduled_maintenances") or []:
        if not isinstance(e, Mapping):
            continue
        st = str(e.get("status") or "").lower()
        if st in ("completed", "resolved"):
            continue
        events.append(_v2_event(e, "maintenance" if st in ("in_progress", "verifying") else "scheduled", page))
    status = payload.get("status") or {}
    degraded = [c.get("name") for c in payload.get("components") or []
                if isinstance(c, Mapping) and str(c.get("status") or "operational") != "operational"]
    return {"page_url": page.get("url") or f"{STATUS_PAGE}/", "indicator": status.get("indicator"),
            "description": status.get("description"), "degraded_components": degraded, "events": events}


def _v2_event(e: Mapping[str, Any], kind: str, page: Mapping[str, Any]) -> dict[str, Any]:
    updates = e.get("incident_updates") or []
    last = updates[0] if updates and isinstance(updates[0], Mapping) else {}
    base = str(page.get("url") or f"{STATUS_PAGE}/").rstrip("/")
    return {"id": e.get("id"), "title": e.get("name"), "kind": kind, "status": e.get("status"),
            "impact": e.get("impact"), "url": e.get("shortlink") or (f"{base}/incidents/{e.get('id')}" if e.get("id") else base),
            "components": _component_names(e.get("components")),
            "starts_at": e.get("scheduled_for") or e.get("started_at") or e.get("created_at"),
            "ends_at": e.get("scheduled_until") or e.get("resolved_at"), "updated_at": e.get("updated_at"),
            "message": str(last.get("body") or "")[:500]}


PARSERS: dict[str, Callable[[Any], dict[str, Any]]] = {"incident_io_v1": parse_v1, "statuspage_v2": parse_v2}


def fetch_snapshot(now: float, fetch: Callable[[str, float], tuple[Any, str | None]] = http_get_json,
                   urls: Iterable[tuple[str, str]] = FEED_URLS, timeout: float = TIMEOUT_SEC) -> dict[str, Any]:
    """Try each feed in order; the first that parses wins."""
    errors = {}
    for name, url in urls:
        payload, err = fetch(url, timeout)
        if err:
            errors[name] = err
            continue
        try:
            parsed = PARSERS[name](payload)
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            errors[name] = f"PARSE_{type(exc).__name__}"
            continue
        return {"schema": SCHEMA, "ok": True, "source": name, "url": url, "fetched_at": _iso(now),
                "fetched_ts": now, "errors": errors, **parsed}
    return {"schema": SCHEMA, "ok": False, "source": None, "fetched_at": _iso(now), "fetched_ts": now,
            "errors": errors, "events": []}


def cached_snapshot(cache: dict[str, Any] | None, now: float, *, fetch=http_get_json,
                    cache_sec: float = CACHE_SEC, retry_sec: float = ERROR_RETRY_SEC) -> dict[str, Any]:
    """5-min cached snapshot. On failure keeps the last good snapshot (marked stale) and tracks down_since."""
    cache = _PROCESS_CACHE if cache is None else cache
    last = cache.get("snapshot") if isinstance(cache.get("snapshot"), Mapping) else None
    attempted = float(cache.get("attempt_ts") or 0)
    good = cache.get("good") if isinstance(cache.get("good"), Mapping) else None
    fresh_for = cache_sec if (last or {}).get("ok") else retry_sec
    if last is not None and now - attempted < fresh_for:
        return dict(last)
    snap = fetch_snapshot(now, fetch=fetch)
    cache["attempt_ts"] = now
    if snap["ok"]:
        cache.pop("down_since", None)
        cache["good"] = snap
    else:
        cache.setdefault("down_since", now)
        snap["down_since"] = _iso(float(cache["down_since"]))
        snap["down_for_sec"] = round(now - float(cache["down_since"]), 1)
        if good and now - float(good.get("fetched_ts") or 0) <= STALE_SNAPSHOT_MAX_SEC:
            snap = {**good, "stale": True, "errors": snap["errors"], "down_since": snap["down_since"],
                    "down_for_sec": snap["down_for_sec"]}
    cache["snapshot"] = snap
    return dict(snap)


# --------------------------------------------------------------- classify

def _region_of(component: str) -> str | None:
    m = REGION_COMPONENT.match(component)
    return m.group(1).lower() if m else None


def classify_event(event: Mapping[str, Any], region: str, now: float) -> dict[str, Any]:
    """Adds ``active``, ``affects_app``, ``matched`` and ``level`` (INFO/AMBER) to one event."""
    region = region.lower()
    comps = [str(c) for c in event.get("components") or []]
    title = str(event.get("title") or "")
    text = f"{title} {event.get('message') or ''}"
    kind = event.get("kind")
    starts, ends = _parse_ts(event.get("starts_at")), _parse_ts(event.get("ends_at"))
    in_window = bool(starts and starts <= now and (ends is None or now <= ends))
    if kind == "scheduled" and in_window:
        kind = "maintenance"
    active = kind in ("incident", "maintenance") and not (kind == "maintenance" and ends and now > ends)
    matched, other_regions = [], []
    for c in comps:
        r = _region_of(c)
        if r is None:
            if any(k in c.lower() for k in RELEVANT_COMPONENTS):
                matched.append(c)
        elif r == region:
            matched.append(c)
        else:
            other_regions.append(r)
    if not comps:
        codes = {m.lower() for m in REGION_CODES.findall(text) if m.lower() in REGION_NAMES}
        names = {code for code, name in REGION_NAMES.items() if name in text.lower()}
        regions = codes | names
        if region in regions:
            matched.append(f"region {region.upper()} (from text)")
        elif not regions and not INFORMATIONAL_TEXT.search(text):
            hit = RELEVANT_TEXT.search(title) or (kind == "incident" and RELEVANT_TEXT.search(text))
            if hit:
                matched.append(f"'{hit.group(0)}' (from text, no components listed)")
            elif kind == "incident":
                matched.append("unscoped incident (no components listed)")
        other_regions = sorted(regions - {region})
    informational = bool(INFORMATIONAL_TEXT.search(title))
    affects = bool(matched) and not informational
    level = AMBER if active and affects else INFO
    return {**event, "kind": kind, "active": active, "affects_app": affects, "matched": matched,
            "other_regions": sorted(set(other_regions)), "level": level}


def app_failures(checks: Iterable[Mapping[str, Any]], extra_ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    extra = frozenset(extra_ids)
    return [{"id": c.get("id"), "status": c.get("status")} for c in checks
            if (str(c.get("id") or "").startswith(APP_CHECK_PREFIXES) or c.get("id") in extra)
            and c.get("id") not in APP_CHECK_EXCLUDE and c.get("status") in (AMBER, RED)]


def assess(snapshot: Mapping[str, Any] | None, app_checks: Iterable[Mapping[str, Any]], now: float,
           region: str = DEFAULT_REGION, *, unreachable_amber_sec: float = UNREACHABLE_AMBER_SEC,
           extra_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Pure: platform verdict + correlation with our app checks."""
    failures = app_failures(app_checks, extra_ids)
    snap = snapshot or {}
    base = {"schema": SCHEMA, "app": APP, "region": region, "status_page": f"{STATUS_PAGE}/",
            "source": snap.get("source"), "fetched_at": snap.get("fetched_at"), "stale": bool(snap.get("stale")),
            "feed_errors": snap.get("errors") or {}, "app_failing_checks": failures}
    if not snap.get("ok"):
        down = float(snap.get("down_for_sec") or 0)
        st = AMBER if down > unreachable_amber_sec else SKIP
        return {**base, "status": st, "classification": "UNREACHABLE", "events": [], "relevant": [], "notices": [],
                "summary": f"status feed unreachable ({', '.join(f'{k}={v}' for k, v in base['feed_errors'].items()) or 'no feed'})"
                           f" for {down / 60:.0f} min; platform state unknown",
                "correlation": {"likely_platform": None, "note": "Fly status feed unreachable; cannot attribute"}}
    events = [classify_event(e, region, now) for e in snap.get("events") or []]
    relevant = [e for e in events if e["level"] == AMBER]
    notices = [e for e in events if e["level"] == INFO]
    head = relevant[0] if relevant else None
    if relevant and failures:
        st, cls = RED, RED
        summary = (f"likely Fly platform incident: {head['title']} ({head['url']}); our checks failing: "
                   + ", ".join(f"{f['id']}={f['status']}" for f in failures[:6]))
    elif relevant:
        st, cls = AMBER, AMBER
        summary = (f"Fly {head['kind']} on {', '.join(head['matched'][:3])}: {head['title']} ({head['url']}); "
                   f"platform notice - app unaffected so far")
    elif notices:
        st, cls = GREEN, INFO
        upcoming = [e for e in notices if e["kind"] == "scheduled" and e["affects_app"]]
        summary = ("platform notice - app unaffected: "
                   + "; ".join(f"{e['title']} ({e['kind']})" for e in notices[:4])
                   + (f"; upcoming in our scope: {upcoming[0]['title']} at {upcoming[0].get('starts_at')}" if upcoming else ""))
    else:
        st, cls = GREEN, "NONE"
        summary = f"no active Fly incident or maintenance ({snap.get('description') or 'feed OK'})"
    if snap.get("stale"):
        summary += f" [cached {snap.get('fetched_at')}, feed currently unreachable]"
    if head:
        corr = {"likely_platform": True, "title": head["title"], "url": head["url"], "kind": head["kind"],
                "note": f"likely Fly platform incident: {head['title']} ({head['url']})" if failures else
                        "platform notice - app unaffected"}
    else:
        corr = {"likely_platform": False,
                "note": "no matching Fly platform incident; failing app checks are ours" if failures else
                        ("platform notice - app unaffected" if notices else "platform quiet")}
    return {**base, "status": st, "classification": cls, "summary": summary, "events": events,
            "relevant": [e["id"] for e in relevant], "notices": [e["id"] for e in notices],
            "degraded_components": snap.get("degraded_components") or [], "correlation": corr}


def annotate_checks(checks: Iterable[dict[str, Any]], result: Mapping[str, Any]) -> int:
    """Tag our failing app checks with the platform correlation; returns how many were annotated."""
    corr = result.get("correlation") or {}
    failing = {f["id"] for f in result.get("app_failing_checks") or []}
    n = 0
    for c in checks:
        if c.get("id") not in failing:
            continue
        c["platform_correlation"] = {"likely_platform": corr.get("likely_platform"), "title": corr.get("title"),
                                     "url": corr.get("url"), "classification": result.get("classification")}
        if corr.get("likely_platform"):
            note = f"likely Fly platform incident: {corr.get('title')} ({corr.get('url')})"
            if note not in str(c.get("hint") or ""):
                c["hint"] = f"{note}; {c.get('hint') or ''}".rstrip("; ")
        n += 1
    return n


def compact(result: Mapping[str, Any]) -> dict[str, Any]:
    """Bounded block for report top-level / banner / self-aware documents."""
    keep = ("id", "title", "kind", "status", "level", "affects_app", "matched", "url", "starts_at", "ends_at")
    return {k: result.get(k) for k in ("schema", "status", "classification", "summary", "app", "region",
                                       "status_page", "source", "fetched_at", "stale", "feed_errors",
                                       "app_failing_checks", "correlation", "degraded_components")} | {
        "events": [{k: e.get(k) for k in keep} for e in (result.get("events") or [])[:12]]}
