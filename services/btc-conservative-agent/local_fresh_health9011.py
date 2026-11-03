"""Local-fresh :9011 system-health checks (RK 19:23 — fold into #972 REV5).

Drop Fly ownership probes. Local checks only. **Alert** only when the paper
engine is down **or** live-safety bits change (arm/live/keys). Everything else
is informational status on the report (not alert spam).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from local_fresh_runtime import (
    LOCAL_FRESH_CONFIRMATION,
    disarmed_from_env,
    ensure_disk_headroom,
    forbidden_trade_keys_present,
    paper_forced_from_env,
    process_rss_bytes,
    read_series_break,
)

SCHEMA = "local_fresh_system_health_v1"
FEATURES = (
    "local_fresh_9011",
    "no_fly_probes",
    "alert_engine_or_live_safety_only",
    "analyzer_age_2h",
    "fly_pull_tasks_disabled_check",
)

GREEN, AMBER, RED, SKIP = "GREEN", "AMBER", "RED", "SKIP"
RANK = {SKIP: -1, GREEN: 0, AMBER: 1, RED: 2}

DEFAULT_PORT = 9011
DEFAULT_ENGINE_HEALTH = "http://127.0.0.1:7002/health"
DEFAULT_RAILWAY_HEALTH = (
    "https://doxed-founders-website-production.up.railway.app/api/health"
)
DEFAULT_VERCEL_PROD = "https://doxxedcrypto.digital/"
ANALYZER_MAX_AGE_SEC = 2 * 3600
DATA_FRESH_MAX_AGE_SEC = 120.0
CLOCK_DRIFT_AMBER_SEC = 2.0
CLOCK_DRIFT_RED_SEC = 5.0
AUTOSTART_TASK = "DoxxedLocalPaperBot"
# Candidate Fly-pull names (N1); status only until RK names the disable set from S0.
FLY_PULL_CANDIDATES = (
    "DoxxedFlyMirrorOneshot",
    "DoxxedFlyMirrorSync",
    "DoxxedFlySyncDurable",
    "DoxxedResearchStabilitySupervisor",
)


def _check(
    cid: str,
    status: str,
    *,
    observed: Any = None,
    threshold: str | None = None,
    hint: str | None = None,
) -> dict[str, Any]:
    return {
        "id": cid,
        "status": status,
        "observed": observed,
        "threshold": threshold,
        "hint": hint,
    }


def _http_json(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "local-fresh-9011/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ms = round((time.perf_counter() - t0) * 1000, 1)
            body: Any = None
            try:
                body = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                body = None
            return {
                "ok": 200 <= int(resp.status) < 400,
                "status": int(resp.status),
                "latency_ms": ms,
                "json": body,
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": None,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
            "json": None,
        }


def _http_status(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "local-fresh-9011/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {
                "ok": 200 <= int(resp.status) < 400,
                "status": int(resp.status),
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": None,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
        }


def _worst(statuses: Sequence[str]) -> str:
    return max(statuses, key=lambda s: RANK.get(s, -1), default=SKIP)


def check_engine_heartbeat(engine_url: str = DEFAULT_ENGINE_HEALTH) -> dict[str, Any]:
    r = _http_json(engine_url)
    if not r.get("ok"):
        return _check(
            "engine.heartbeat",
            RED,
            observed=r.get("error") or r.get("status"),
            threshold="HTTP 2xx on :7002/health",
            hint="local paper engine down or not listening",
        )
    body = r.get("json") if isinstance(r.get("json"), dict) else {}
    # Prefer local_fresh health JSON fields when present on /health.
    okish = body.get("ok", True)
    status = GREEN if okish else RED
    return _check(
        "engine.heartbeat",
        status,
        observed={"http": r.get("status"), "latency_ms": r.get("latency_ms"), "ok": okish},
        threshold="ok=true on engine /health",
    )


def check_autostart_task(
    *,
    task_probe: Optional[Callable[[], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Status of DoxxedLocalPaperBot. Probe injectable (Windows Get-ScheduledTask)."""
    if task_probe is None:
        return _check(
            "autostart.task",
            SKIP,
            observed="task probe not provided (non-Windows or inject probe)",
            threshold=f"task {AUTOSTART_TASK} Ready|Running|Disabled",
        )
    try:
        info = task_probe()
    except Exception as exc:  # noqa: BLE001
        return _check("autostart.task", AMBER, observed=str(exc)[:120])
    state = str(info.get("state") or info.get("State") or "ABSENT")
    if state in ("Ready", "Running", "Disabled"):
        return _check("autostart.task", GREEN, observed={"name": AUTOSTART_TASK, "state": state})
    if state == "ABSENT":
        return _check(
            "autostart.task",
            AMBER,
            observed={"name": AUTOSTART_TASK, "state": state},
            hint="optional until -InstallAutostart",
        )
    return _check("autostart.task", AMBER, observed={"name": AUTOSTART_TASK, "state": state})


def check_clock_drift(*, offset_sec: Optional[float] = None) -> dict[str, Any]:
    """offset_sec = local - reference; None → SKIP (no NTP sample)."""
    if offset_sec is None:
        return _check(
            "clock.drift",
            SKIP,
            observed="no NTP offset sample",
            threshold=f"|offset| <= {CLOCK_DRIFT_AMBER_SEC}s amber / {CLOCK_DRIFT_RED_SEC}s red",
        )
    abs_off = abs(float(offset_sec))
    if abs_off > CLOCK_DRIFT_RED_SEC:
        status = RED
    elif abs_off > CLOCK_DRIFT_AMBER_SEC:
        status = AMBER
    else:
        status = GREEN
    return _check("clock.drift", status, observed={"offset_sec": offset_sec})


def check_rss() -> dict[str, Any]:
    rss = process_rss_bytes()
    if rss is None:
        return _check("process.rss", AMBER, observed=None, hint="RSS unmeasurable")
    return _check("process.rss", GREEN, observed={"rss_bytes": rss})


def check_disk(data_dir: str | Path) -> dict[str, Any]:
    ok, free, minimum = ensure_disk_headroom(data_dir)
    status = GREEN if ok else RED
    return _check(
        "disk.headroom",
        status,
        observed={"free": free, "min": minimum, "ok": ok},
        threshold="free measurable and >= min",
    )


def check_logs(logs_dir: str | Path) -> dict[str, Any]:
    root = Path(logs_dir)
    health = root / "local_fresh_health.json"
    if not health.is_file():
        return _check("logs.local_fresh_health", AMBER, observed="absent", hint="engine not writing health yet")
    age = time.time() - health.stat().st_mtime
    status = GREEN if age <= 300 else AMBER
    return _check("logs.local_fresh_health", status, observed={"age_sec": round(age, 1), "path": str(health)})


def check_data_freshness(data_dir: str | Path) -> dict[str, Any]:
    root = Path(data_dir)
    series = read_series_break(root)
    sqlite = root / "local_market.sqlite3"
    if not sqlite.is_file():
        return _check(
            "data.freshness",
            AMBER,
            observed={"sqlite": "absent", "series_break": bool(series)},
            hint="collector not started",
        )
    age = time.time() - sqlite.stat().st_mtime
    status = GREEN if age <= DATA_FRESH_MAX_AGE_SEC else AMBER
    return _check(
        "data.freshness",
        status,
        observed={
            "sqlite_mtime_age_sec": round(age, 1),
            "local_epoch_id": (series or {}).get("local_epoch_id"),
            "series_start_ts": (series or {}).get("series_start_ts"),
        },
        threshold=f"sqlite mtime age <= {DATA_FRESH_MAX_AGE_SEC}s",
    )


def check_analyzer_age(
    *,
    analyzer_mtime_ts: Optional[float] = None,
    reports_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    ts = analyzer_mtime_ts
    if ts is None and reports_dir is not None:
        root = Path(reports_dir)
        if root.is_dir():
            newest = None
            for path in root.rglob("*"):
                if path.is_file():
                    m = path.stat().st_mtime
                    newest = m if newest is None else max(newest, m)
            ts = newest
    if ts is None:
        return _check(
            "analyzer.age",
            SKIP,
            observed="no analyzer reports dir/mtime",
            threshold=f"age < {ANALYZER_MAX_AGE_SEC}s (2h)",
        )
    age = time.time() - float(ts)
    status = GREEN if age < ANALYZER_MAX_AGE_SEC else RED
    return _check(
        "analyzer.age",
        status,
        observed={"age_sec": round(age, 1)},
        threshold=f"< {ANALYZER_MAX_AGE_SEC}s",
    )


def check_fly_pull_tasks_disabled(
    *,
    task_states: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Informational until N1 S0+RK names the set. RED only if a candidate is Ready/Running."""
    if task_states is None:
        return _check(
            "fly_pull_tasks.disabled",
            SKIP,
            observed="no Windows task state sample",
            threshold="proven Fly-pull tasks Disabled (after N1 RK)",
        )
    bad = {
        name: state
        for name, state in task_states.items()
        if name in FLY_PULL_CANDIDATES and state in ("Ready", "Running")
    }
    if bad:
        return _check(
            "fly_pull_tasks.disabled",
            RED,
            observed=bad,
            hint="candidate Fly-pull task still enabled — N1 disable after RK",
        )
    return _check(
        "fly_pull_tasks.disabled",
        GREEN,
        observed={k: task_states.get(k, "ABSENT") for k in FLY_PULL_CANDIDATES},
    )


def check_railway_health(url: str = DEFAULT_RAILWAY_HEALTH) -> dict[str, Any]:
    r = _http_json(url)
    status = GREEN if r.get("ok") else AMBER
    return _check(
        "railway.api_health",
        status,
        observed={"ok": r.get("ok"), "status": r.get("status"), "error": r.get("error")},
        threshold="GET Railway /api/health 2xx",
    )


def check_vercel_prod(url: str = DEFAULT_VERCEL_PROD) -> dict[str, Any]:
    r = _http_status(url)
    status = GREEN if r.get("ok") else AMBER
    return _check(
        "vercel.prod",
        status,
        observed={"ok": r.get("ok"), "status": r.get("status"), "error": r.get("error")},
        threshold="GET prod site 2xx",
    )


def check_live_safety(environ: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    keys = forbidden_trade_keys_present(env)
    live_on = str(env.get("BITFINEX_LIVE_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")
    paper = paper_forced_from_env(env)
    disarmed = disarmed_from_env(env)
    ok = (not keys) and (not live_on) and paper and disarmed
    return _check(
        "live.safety",
        GREEN if ok else RED,
        observed={
            "trade_keys_absent": not bool(keys),
            "forbidden_key_names": keys,
            "bitfinex_live_enabled": live_on,
            "paper_forced": paper,
            "disarmed": disarmed,
            "confirm_phrase_ok": str(env.get("LOCAL_FRESH_START_CONFIRM") or "")
            == LOCAL_FRESH_CONFIRMATION
            or not str(env.get("LOCAL_FRESH_START") or ""),
        },
        threshold="keys absent; live OFF; paper ON; disarmed",
    )


def build_report(
    *,
    data_dir: str | Path,
    logs_dir: str | Path,
    environ: Optional[Mapping[str, str]] = None,
    engine_url: str = DEFAULT_ENGINE_HEALTH,
    railway_url: str = DEFAULT_RAILWAY_HEALTH,
    vercel_url: str = DEFAULT_VERCEL_PROD,
    clock_offset_sec: Optional[float] = None,
    analyzer_mtime_ts: Optional[float] = None,
    analyzer_reports_dir: Optional[str | Path] = None,
    autostart_probe: Optional[Callable[[], dict[str, Any]]] = None,
    fly_task_states: Optional[Mapping[str, str]] = None,
    now: Optional[float] = None,
    prior_live_safety_fingerprint: Optional[str] = None,
) -> dict[str, Any]:
    ts = float(now if now is not None else time.time())
    checks = [
        check_engine_heartbeat(engine_url),
        check_autostart_task(task_probe=autostart_probe),
        check_clock_drift(offset_sec=clock_offset_sec),
        check_rss(),
        check_disk(data_dir),
        check_logs(logs_dir),
        check_data_freshness(data_dir),
        check_analyzer_age(
            analyzer_mtime_ts=analyzer_mtime_ts, reports_dir=analyzer_reports_dir
        ),
        check_fly_pull_tasks_disabled(task_states=fly_task_states),
        check_railway_health(railway_url),
        check_vercel_prod(vercel_url),
        check_live_safety(environ),
    ]
    # Explicitly no Fly public health / Fly owner probes.
    checks.append(
        _check(
            "fly.probes",
            SKIP,
            observed="dropped — Fly deleted; local-fresh 9011 does not probe fly.dev",
        )
    )
    verdict = _worst(c["status"] for c in checks)
    engine = next(c for c in checks if c["id"] == "engine.heartbeat")
    live = next(c for c in checks if c["id"] == "live.safety")
    live_fp = json.dumps(live.get("observed") or {}, sort_keys=True)
    alerts: list[dict[str, Any]] = []
    if engine["status"] == RED:
        alerts.append(
            {
                "kind": "ENGINE_DOWN",
                "check": "engine.heartbeat",
                "observed": engine.get("observed"),
            }
        )
    if live["status"] == RED:
        alerts.append(
            {
                "kind": "LIVE_SAFETY",
                "check": "live.safety",
                "observed": live.get("observed"),
            }
        )
    elif (
        prior_live_safety_fingerprint is not None
        and prior_live_safety_fingerprint != live_fp
        and live["status"] == GREEN
    ):
        # Change in live-safety surface while still green (e.g. confirm phrase) — alert.
        alerts.append(
            {
                "kind": "LIVE_SAFETY_CHANGED",
                "check": "live.safety",
                "observed": live.get("observed"),
                "prior_fingerprint": prior_live_safety_fingerprint,
            }
        )
    failing = [c for c in checks if c["status"] in (RED, AMBER)]
    return {
        "schema": SCHEMA,
        "features": list(FEATURES),
        "generated_ts": ts,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
        "verdict": verdict,
        "checks": checks,
        "failing": failing,
        "alerts": alerts,
        "alert_policy": "engine_down_or_live_safety_change_only",
        "live_safety_fingerprint": live_fp,
        "fly_owner": False,
        "port": DEFAULT_PORT,
        "acked": [],
    }
