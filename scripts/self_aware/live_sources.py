"""Read-only adapters to LIVE production sources (Fly + Railway).

The self-aware layer stays on the operator's laptop and normally reads the local
custody mirror. These adapters give it a *second, fresh* set of eyes on
production so a finding is not silently dependent on a stale mirror:

  * Fly    — ``/api/status``, ``/ready``, ``/api/state`` (public, no auth)
  * Railway — ``/health``, ``/admin/observatory`` (JWT/admin auth),
              ``/exchanges/bitfinex/telemetry`` (JWT/admin auth, the Task 2 fix)

Every call is a read-only GET, time-bounded, and non-fatal: a failed fetch is
returned as structured evidence (``{ok, error, elapsed_sec}``), never raised,
so the daemon keeps serving local results even when a network hop is down.

Nothing here arms, toggles, orders or copies state. It only observes.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .config import FLY_BASE_URL, LIVE_SOURCES_ENABLED, RAILWAY_BASE_URL, RAILWAY_TOKEN

SCHEMA = "self_aware_live_sources_v1"


def _http_json(url: str, *, token: str = "", timeout: float = 8.0) -> tuple[Any, str | None, float]:
    """GET ``url`` as JSON. ``token`` (optional bearer) enables authenticated Railway reads."""
    started = time.time()
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), None, time.time() - started
    except Exception as exc:  # noqa: BLE001 - failure is evidence, never fatal
        return None, f"{type(exc).__name__}: {str(exc)[:160]}", time.time() - started


def _probe(url: str, token: str = "", timeout: float = 8.0) -> dict[str, Any]:
    body, error, elapsed = _http_json(url, token=token, timeout=timeout)
    return {"url": url, "ok": body is not None, "error": error, "elapsed_sec": round(elapsed, 3),
            "body": body}


def fly_status(timeout: float = 8.0) -> dict[str, Any]:
    return _probe(f"{FLY_BASE_URL}/api/status", timeout=timeout)


def fly_ready(timeout: float = 8.0) -> dict[str, Any]:
    return _probe(f"{FLY_BASE_URL}/ready", timeout=timeout)


def fly_state(timeout: float = 8.0) -> dict[str, Any]:
    return _probe(f"{FLY_BASE_URL}/api/state", timeout=timeout)


def railway_health(timeout: float = 8.0) -> dict[str, Any]:
    return _probe(f"{RAILWAY_BASE_URL}/health", timeout=timeout)


def railway_observatory(timeout: float = 8.0) -> dict[str, Any]:
    return _probe(f"{RAILWAY_BASE_URL}/admin/observatory", token=RAILWAY_TOKEN, timeout=timeout)


def railway_telemetry(timeout: float = 8.0) -> dict[str, Any]:
    return _probe(f"{RAILWAY_BASE_URL}/exchanges/bitfinex/telemetry", token=RAILWAY_TOKEN, timeout=timeout)


def collect_live(now: float | None = None) -> dict[str, Any]:
    """Aggregate every live source into one document. Local-first and non-fatal.

    Returns ``{schema, generated_at, enabled, fly: {...}, railway: {...}}``.
    When ``LIVE_SOURCES_ENABLED`` is false, ``enabled`` is false and the source
    blocks are empty, so the daemon still reports a well-formed document.
    """
    now = time.time() if now is None else float(now)
    doc: dict[str, Any] = {"schema": SCHEMA, "generated_at": time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "enabled": bool(LIVE_SOURCES_ENABLED),
        "fly": {}, "railway": {}}
    if not LIVE_SOURCES_ENABLED:
        doc["note"] = "live sources disabled (set SELF_AWARE_LIVE_SOURCES=1 to enable)"
        return doc
    doc["fly"] = {"status": fly_status(), "ready": fly_ready(), "state": fly_state()}
    railway: dict[str, Any] = {}
    if RAILWAY_BASE_URL:
        railway = {"health": railway_health(), "observatory": railway_observatory(),
                   "telemetry": railway_telemetry()}
    else:
        railway = {"configured": False,
                   "note": "SELF_AWARE_RAILWAY_URL unset; set it (and SELF_AWARE_RAILWAY_TOKEN) to observe Railway"}
    doc["railway"] = railway
    return doc


def summarize(doc: dict[str, Any]) -> dict[str, Any]:
    """Reduce a live-sources document to one verdict + per-subsystem status."""
    fly = doc.get("fly") or {}
    railway = doc.get("railway") or {}
    parts: list[dict[str, Any]] = []

    def add(name: str, probe: dict[str, Any]) -> None:
        parts.append({"subsystem": name, "ok": bool(probe.get("ok")),
                      "error": probe.get("error"), "elapsed_sec": probe.get("elapsed_sec"),
                      "url": probe.get("url")})

    for key in ("status", "ready", "state"):
        if key in fly:
            add(f"fly.{key}", fly[key])
    for key in ("health", "observatory", "telemetry"):
        if isinstance(railway.get(key), dict):
            add(f"railway.{key}", railway[key])
    ok = [p for p in parts if p["ok"]]
    reachable = len(ok)
    # Railway observatory/telemetry require a token; absence is "unconfigured", not a failure.
    verdict = "OK" if reachable == len(parts) else ("PARTIAL" if reachable else "UNREACHABLE")
    return {"schema": SCHEMA, "enabled": doc.get("enabled"), "verdict": verdict,
            "reachable": reachable, "total": len(parts), "subsystems": parts}


_RUNBOOK = "docs/SELF_AWARE_RUNBOOK.md#live-sources"


def _status_from(ok: bool) -> str:
    return "GREEN" if ok else "RED"


def diagnose(now: float | None = None, local_verdict: str | None = None) -> dict[str, Any]:
    """One aggregate verdict over Fly + Railway + exchange telemetry (Task 3).

    Fans out to Fly ``/api/status`` + ``/ready`` and Railway ``/health`` +
    observatory + exchange telemetry, then returns ONE verdict with a typed
    finding per subsystem (``{id, status, cause, runbook}``). Local self-aware
    health is folded in as an additional subsystem so a RED here and a RED
    locally are both visible in one document.
    """
    doc = collect_live(now)
    summary = summarize(doc)
    findings: list[dict[str, Any]] = []
    for p in summary["subsystems"]:
        status = _status_from(p["ok"])
        findings.append({
            "id": f"live.{p['subsystem'].replace('.', '-')}",
            "status": status,
            "cause": None if p["ok"] else (p.get("error") or "unreachable"),
            "runbook": _RUNBOOK,
            "observed": f"{p['subsystem']} {'reachable' if p['ok'] else 'unreachable'}"
                        f" in {p.get('elapsed_sec')}s",
        })
    if local_verdict:
        findings.append({
            "id": "live.local-selfaware",
            "status": "GREEN" if local_verdict == "GREEN" else ("AMBER" if local_verdict == "AMBER" else "RED"),
            "cause": None if local_verdict == "GREEN" else f"local self-aware verdict {local_verdict}",
            "runbook": "docs/SELF_AWARE_RUNBOOK.md#health",
            "observed": f"local self-aware verdict {local_verdict}",
        })
    ranks = {"RED": 3, "AMBER": 2, "GREEN": 0}
    worst = max((ranks[f["status"]] for f in findings), default=0)
    verdict = {3: "RED", 2: "AMBER"}.get(worst, "GREEN")
    return {
        "schema": "self_aware_diagnose_v1",
        "generated_at": doc["generated_at"],
        "verdict": verdict,
        "live_sources": summary,
        "findings": findings,
        "fly": doc.get("fly") or {},
        "railway": doc.get("railway") or {},
    }
