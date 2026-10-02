"""Fly.io platform status in self-aware: the same classifier as the watcher's fly.platform_status.

Runs inside every diagnose pass (feed cached 5 min in engine state, short
timeout, never raises). The platform verdict is correlated with self-aware's
own Fly-facing findings and the watcher's Fly checks: failing ones get a
"likely Fly platform incident" cause, and a platform event with a healthy app
reads "platform notice - app unaffected".
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fly_platform_status as fps  # noqa: E402

# Self-aware findings that a Fly platform incident can explain (Fly runtime, feeds, custody, deploy pause).
APP_FINDINGS = ("fly.reachability", "prog.feeds", "prog.ai_cadence", "prog.counts_advancing", "inv.custody",
                "inv.mirror_ai_lag")
TITLE = "Fly.io platform status (incidents/maintenance vs our region and components)"
EXPECTED = ("INFO: notices outside our region/components; AMBER: incident on our region or Machines/Volumes/"
            "proxy/deploys; RED only if our Fly checks also fail")


def assess(facts: dict[str, Any], findings: list[Any], state: dict[str, Any], now: float,
           fetch: Callable[..., Any] = fps.http_get_json) -> dict[str, Any]:
    cache = state.setdefault("fly_platform_cache", {})
    if "svc_9011" in facts:
        snap = fps.cached_snapshot(cache, now, fetch=fetch)
    else:
        # Facts collected without local probes (tests, --once offline): never touch the network.
        snap = cache.get("snapshot") or {"ok": False, "errors": {"feed": "NOT_PROBED"}}
    watcher_checks = [c for c in (facts.get("watcher") or {}).get("checks") or []
                      if str(c.get("id") or "").startswith(fps.APP_CHECK_PREFIXES)]
    own = [{"id": f.id, "status": f.severity} for f in findings]
    res = fps.assess(snap, watcher_checks + own, now, fps.app_region(), extra_ids=APP_FINDINGS)
    w = next((c for c in watcher_checks if c.get("id") == "fly.platform_status"), None)
    res["watcher"] = None if w is None else {"status": w.get("status"),
                                             "classification": (w.get("observed_fields") or {}).get("classification")}
    return res


def annotate(findings: list[Any], res: dict[str, Any]) -> None:
    """Attach the platform correlation to failing diagnose.Finding objects (evidence + leading cause)."""
    corr = res.get("correlation") or {}
    failing = {f["id"] for f in res.get("app_failing_checks") or []}
    for f in findings:
        if f.id not in failing:
            continue
        f.evidence["platform_correlation"] = {
            "likely_platform": corr.get("likely_platform"), "title": corr.get("title"), "url": corr.get("url"),
            "classification": res.get("classification")}
        if corr.get("likely_platform"):
            f.causes = [{"cause": "fly_platform_incident", "confidence": "likely", "evidence": corr.get("url"),
                         "text": f"likely Fly platform incident: {corr.get('title')} ({corr.get('url')})"}] + [
                c for c in f.causes if c.get("cause") != "unknown"]


def finding(res: dict[str, Any]) -> dict[str, Any]:
    w = res.get("watcher") or {}
    agree = "" if not w or w.get("status") == res["status"] else f" (watcher says {w.get('status')})"
    return {"id": "fly.platform_status", "title": TITLE, "category": "attribution", "severity": res["status"],
            "observed": res["summary"] + agree, "expected": EXPECTED,
            "emit_alarm": res["status"] in (fps.AMBER, fps.RED),
            "evidence": {"classification": res["classification"], "correlation": res["correlation"],
                         "api": "/api/selfaware/fly-platform", "status_page": res["status_page"]}}
