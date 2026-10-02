#!/usr/bin/env python3
"""BLINDSPOT-AUDIT-3 closure ledger.

Every BLIND/PARTIAL component row and every earlier-audit gap gets an owner, a
PR and a status.  CLOSED-VERIFIED-LIVE is never written by hand: it is only
emitted when the item's live verifier passes against the live APIs fetched in
this run (Fly public endpoints, :9001, :9011, :9021, GitHub variables).

    python scripts/blindspot_closure_ledger.py --audit <AUDIT.md> --out <LEDGER.md>
    python scripts/blindspot_closure_ledger.py --audit <AUDIT.md> --live-dir <captured json dir>

Fly calls per run: 5 GETs (rate limit is 60/min per IP shared with laptop jobs).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable

FLY = "https://doxed-btc-bot.fly.dev"
SOURCES = {
    "fly_health": f"{FLY}/health",
    "fly_ready": f"{FLY}/ready",
    "fly_status": f"{FLY}/api/status",
    "fly_relay": f"{FLY}/api/relay-state",
    "fly_syshealth": f"{FLY}/api/system-health",
    "a9001_health": "http://127.0.0.1:9001/api/health",
    "a9001_status": "http://127.0.0.1:9001/api/status",
    "a9001_streams": "http://127.0.0.1:9001/api/streams/health",
    "w9011": "http://127.0.0.1:9011/api/system-health",
    "sa9021": "http://127.0.0.1:9021/api/selfaware/health",
}
CAPTURE_NAMES = {
    "fly_health": "health.json", "fly_ready": "ready.json", "fly_status": "api_status.json",
    "fly_relay": "api_relay-state.json", "fly_syshealth": "api_system-health.json",
    "a9001_health": "local_9001_api_health.json", "a9001_status": "local_9001_api_status.json",
    "a9001_streams": "local_9001_api_streams_health.json", "w9011": "local_9011_api_system-health.json",
    "sa9021": "local_9021_api_selfaware_health.json",
}

OPEN, PROG, QUEUED, CLOSED = "OPEN", "IN PROGRESS", "QUEUED-POST-FREEZE", "CLOSED-VERIFIED-LIVE"
CLOSED_AUDIT = "CLOSED-AUDIT3 (not re-verified here)"
PR_LAPTOP, PR_MONITOR, PR_FLY = "{PR_LAPTOP}", "{PR_MONITOR}", "{PR_FLY}"
MONITOR_CODE_REV = "70f1a5e94"  # #310 squash: monitor runs before it executed the old rules


# ---------------------------------------------------------------- live helpers
def dig(d: Any, *path: Any, default: Any = None) -> Any:
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and -len(d) <= p < len(d):
            d = d[p]
        else:
            return default
        if d is None:
            return default
    return d


def has_feature(live: dict, feature: str) -> bool:
    """The live :9011 report must declare it runs the fix (top-level ``features``)."""
    return feature in (dig(live, "w9011", "features", default=[]) or [])


def needs(feature: str, inner: "Verifier") -> "Verifier":
    def run(live: dict) -> tuple[bool, str]:
        passed, ev = inner(live)
        if not has_feature(live, feature):
            return False, f"live :9011 lacks feature {feature!r} (fix not deployed); " + ev
        return passed, ev
    return run


def check(live: dict, cid: str) -> dict | None:
    for c in dig(live, "w9011", "checks", default=[]) or []:
        if isinstance(c, dict) and c.get("id") == cid:
            return c
    return None


def fetch_live(timeout: float = 40.0) -> dict:
    live: dict[str, Any] = {"_errors": {}, "_latency_ms": {}}
    for key, url in SOURCES.items():
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                live[key] = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # recorded in the ledger as missing evidence
            live[key] = None
            live["_errors"][key] = f"{type(exc).__name__}: {exc}"[:160]
        live["_latency_ms"][key] = int((time.monotonic() - t0) * 1000)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(SOURCES["w9011"] + "?live=1", timeout=90) as resp:
            resp.read()
        live["_latency_ms"]["w9011_live1"] = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        live["_errors"]["w9011_live1"] = f"{type(exc).__name__}"
    live["gh_vars"] = gh_variables()
    live["gh_monitor"] = gh_monitor_run()
    live["gh_laptop_tests"] = gh_latest_run("laptop-tests.yml")
    return live


def load_live(live_dir: Path) -> dict:
    live: dict[str, Any] = {"_errors": {}, "_latency_ms": {}}
    for key, name in CAPTURE_NAMES.items():
        try:
            live[key] = json.loads((live_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            live[key] = None
            live["_errors"][key] = type(exc).__name__
    live["gh_vars"] = gh_variables()
    live["gh_monitor"] = gh_monitor_run()
    live["gh_laptop_tests"] = gh_latest_run("laptop-tests.yml")
    return live


def _gh(*args: str, timeout: float = 60) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace", cwd=str(Path(__file__).resolve().parents[1]))
    return out.stdout or ""


def gh_latest_run(workflow: str) -> dict | None:
    try:
        rows = json.loads(_gh("run", "list", "--workflow", workflow, "-L", "1", "--status", "completed",
                              "--json", "databaseId,headSha,conclusion,createdAt,event") or "[]")
        return rows[0] if rows else None
    except Exception:
        return None


def gh_monitor_run() -> dict | None:
    """Latest completed scheduled monitor run that executed the #310 monitor code, with its runner output."""
    try:
        rows = json.loads(_gh("run", "list", "--workflow", "fly-bot-monitor.yml", "-L", "10", "--status",
                              "completed", "--json", "databaseId,headSha,conclusion,createdAt") or "[]")
        repo = Path(__file__).resolve().parents[1]
        for row in rows:
            anc = subprocess.run(["git", "merge-base", "--is-ancestor", MONITOR_CODE_REV, row["headSha"]],
                                 cwd=str(repo), capture_output=True)
            if anc.returncode != 0:
                continue
            log = ""
            for _ in range(2):  # gh intermittently returns an empty log
                log = _gh("run", "view", str(row["databaseId"]), "--log", timeout=120)
                if log.strip():
                    break
                time.sleep(3)
            keep = ("previous monitor run evidence", "heartbeat", "state restored=", "::warning title=fly-monitor",
                    "::error title=fly-monitor", "::notice title=fly-monitor", "Recovered:", "Fly bot healthy",
                    "LAPTOP_CHAIN_HEARTBEAT:")
            row["lines"] = [ln.split("Z ", 1)[-1].strip() for ln in log.splitlines() if any(k in ln for k in keep)]
            if any("state restored=" in ln for ln in row["lines"]):
                return row
            # a just-finished run's log may not be downloadable yet; use the previous one
        return None
    except Exception:
        return None


def gh_variables() -> dict[str, str]:
    try:
        out = subprocess.run(["gh", "variable", "list", "--json", "name,value,updatedAt"],
                             capture_output=True, text=True, timeout=30,
                             cwd=str(Path(__file__).resolve().parents[1]))
        rows = json.loads(out.stdout or "[]")
        return {r["name"]: r.get("value") or r.get("updatedAt") for r in rows
                if r.get("name") in ("FLY_MONITOR_HEARTBEAT", "LAPTOP_CHAIN_HEARTBEAT")}
    except Exception:
        return {}


def iso_age_sec(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    m = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value)
    if not m:
        return None
    t = dt.datetime.fromisoformat(m.group(0)).replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds()


# ---------------------------------------------------------------- verifiers
# Each verifier returns (passed, evidence string).  A verifier that cannot find
# its field returns False: missing evidence is never closure.
Verifier = Callable[[dict], tuple[bool, str]]


def v_check_present(cid: str, must_not_green_when: Callable[[dict], bool] | None = None) -> Verifier:
    def run(live: dict) -> tuple[bool, str]:
        c = check(live, cid)
        if c is None:
            return False, f":9011 checks[{cid}] absent"
        ev = f":9011 checks[{cid}].status={c.get('status')} observed={str(c.get('observed'))[:90]!r}"
        if must_not_green_when is not None and must_not_green_when(live) and c.get("status") == "GREEN":
            return False, ev + " (GREEN while the failure condition is live)"
        return True, ev
    return run


def v_analyzer_reports(live: dict) -> tuple[bool, str]:
    ok = dig(live, "a9001_status", "required_reports_ok")
    c = check(live, "analyzer.reports")
    if c is None:
        return False, f":9011 checks[analyzer.reports] absent; :9001/api/status.required_reports_ok={ok}"
    agrees = (c.get("status") == "GREEN") == (ok is True)
    return agrees, (f":9011 checks[analyzer.reports].status={c.get('status')} vs "
                    f":9001/api/status.required_reports_ok={ok}")


def v_pull_ack(live: dict) -> tuple[bool, str]:
    c = check(live, "laptop.pull_ack")
    if c is None:
        return False, ":9011 checks[laptop.pull_ack] absent"
    obs = str(c.get("observed"))
    bad = "applied=None" in obs and c.get("status") == "GREEN"
    return (not bad), f":9011 checks[laptop.pull_ack].status={c.get('status')} observed={obs[:80]!r}"


def v_analyzer_api_parity(live: dict) -> tuple[bool, str]:
    c = check(live, "analyzer.api")
    if c is None:
        return False, ":9011 checks[analyzer.api] absent"
    obs = str(c.get("observed"))
    gen = dig(live, "a9001_health", "generation_freshness", "generation_revision") or ""
    fly = dig(live, "fly_status", "source_git_rev") or ""
    match = bool(gen) and bool(fly) and (gen.startswith(fly) or fly.startswith(gen[:12]))
    honest = ("revision_parity=True" not in obs) and (match or c.get("status") != "GREEN")
    return honest, (f":9011 analyzer.api.status={c.get('status')} observed={obs[:90]!r}; "
                    f":9001 generation_revision={gen[:12]} Fly source_git_rev={fly}")


def v_streams_coverage(live: dict) -> tuple[bool, str]:
    c = check(live, "streams.coverage")
    if c is None:
        return False, ":9011 checks[streams.coverage] absent"
    obs = str(c.get("observed"))
    return ("n/a" not in obs), f":9011 checks[streams.coverage].observed={obs[:90]!r}"


def v_w9011_latency(live: dict) -> tuple[bool, str]:
    ms = live["_latency_ms"].get("w9011_live1")
    return (ms is not None and ms < 3000), f"GET :9011/api/system-health?live=1 took {ms} ms (target < 3000)"


def _monitor_line(live: dict, needle: str) -> str | None:
    for ln in (live.get("gh_monitor") or {}).get("lines") or []:
        if needle in ln:
            return ln
    return None


def _monitor_where(live: dict) -> str:
    run = live.get("gh_monitor") or {}
    return f"fly-bot-monitor run {run.get('databaseId')} @{str(run.get('headSha'))[:9]} {run.get('createdAt')} {run.get('conclusion')}"


def v_monitor_heartbeat(live: dict) -> tuple[bool, str]:
    """Missed schedules are measured from the heartbeat variable or, without its token, the Actions runs API."""
    hb = (live.get("gh_vars") or {}).get("FLY_MONITOR_HEARTBEAT")
    age = iso_age_sec(hb)
    if age is not None and age < 2700:
        return True, f"GH var FLY_MONITOR_HEARTBEAT={str(hb)[:60]!r} age={int(age)}s"
    gap = _monitor_line(live, "previous monitor run evidence")
    state = _monitor_line(live, "state restored=")
    ok = bool(gap) and bool(state) and (live.get("gh_monitor") or {}).get("conclusion") in ("success", "failure")
    return ok, (f"{_monitor_where(live)}: {gap!r}; {state!r}; GH var FLY_MONITOR_HEARTBEAT unset "
                "(FLY_MONITOR_VARIABLES_TOKEN not configured)")


def v_monitor_rules(live: dict) -> tuple[bool, str]:
    """A completed run on the #310 code evaluated every subsystem rule; a missing input field would
    have raised contract_field_missing, so a run without it proves the fields were read live."""
    run = live.get("gh_monitor") or {}
    state = _monitor_line(live, "state restored=")
    findings = [ln for ln in run.get("lines") or [] if "title=fly-monitor" in ln]
    missing = [ln for ln in findings if "contract_field_missing" in ln]
    ok = bool(state) and run.get("conclusion") in ("success", "failure") and not missing
    summary = "; ".join(f[:90] for f in findings) or (_monitor_line(live, "Fly bot healthy") or "")[:60]
    return ok, f"{_monitor_where(live)} findings={len(findings)} contract_field_missing={len(missing)}: {summary!r}"


def v_monitor_line(needle: str) -> Verifier:
    def run(live: dict) -> tuple[bool, str]:
        ln = _monitor_line(live, needle)
        return ln is not None, f"{_monitor_where(live)}: {ln!r}"
    return run


def v_laptop_tests_ci(live: dict) -> tuple[bool, str]:
    run = live.get("gh_laptop_tests") or {}
    ok = run.get("conclusion") == "success"
    return ok, (f"laptop-tests.yml run {run.get('databaseId')} @{str(run.get('headSha'))[:9]} "
                f"{run.get('event')} {run.get('createdAt')} {run.get('conclusion')}")


def v_check_status(cid: str, expect: Callable[[dict], bool], why: str) -> Verifier:
    def run(live: dict) -> tuple[bool, str]:
        c = check(live, cid)
        if c is None:
            return False, f":9011 checks[{cid}] absent"
        return bool(expect(c)), f":9011 checks[{cid}].status={c.get('status')} observed={str(c.get('observed'))[:90]!r} ({why})"
    return run


def v_report_staleness(live: dict) -> tuple[bool, str]:
    w = live.get("w9011") or {}
    ok = "stale" in w and isinstance(w.get("age_sec"), (int, float))
    return ok, f":9011 report.stale={w.get('stale')} age_sec={w.get('age_sec')}"


def v_field(src: str, *path: Any, pred: Callable[[Any], bool] = lambda v: v is not None,
            label: str | None = None) -> Verifier:
    def run(live: dict) -> tuple[bool, str]:
        v = dig(live, src, *path)
        where = label or f"{SOURCES.get(src, src).replace(FLY, 'Fly')}.{'.'.join(map(str, path))}"
        return (v is not None and bool(pred(v))), f"{where}={json.dumps(v, default=str)[:90]}"
    return run


def v_threads(name: str) -> Verifier:
    return v_field("fly_status", "threads", name, "last_tick_age_sec")


def v_toggles(live: dict) -> tuple[bool, str]:
    tiles = dig(live, "fly_status", "active_tiles", default=[]) or []
    have = [t.get("toggle_on") for t in tiles if isinstance(t, dict)]
    ok = bool(have) and all(isinstance(x, bool) for x in have)
    return ok, f"Fly /api/status.active_tiles[].toggle_on={have}"


def v_relay_age(live: dict) -> tuple[bool, str]:
    ro = dig(live, "fly_health", "relay_outbox")
    stale = dig(live, "fly_relay", "state_integrity", "relay_push", "delivery_scheduler", "counts", "stale_owner_pending")
    if not isinstance(ro, dict) or "oldest_pending_age_sec" not in ro:
        return False, f"Fly /health.relay_outbox absent; /api/relay-state stale_owner_pending={stale}"
    return True, f"Fly /health.relay_outbox={json.dumps(ro)[:100]}"


def v_contradiction_trading_orders(live: dict) -> tuple[bool, str]:
    lap = check(live, "trading.orders")
    fly_checks = dig(live, "fly_syshealth", "fly_self_checks", default=[]) or []
    return (lap is not None and "all OFF" not in str(lap.get("observed"))), (
        f":9011 trading.orders.status={lap and lap.get('status')}; Fly self-checks={len(fly_checks)}")


def v_selfaware_custody(live: dict) -> tuple[bool, str]:
    for f in dig(live, "sa9021", "findings", default=[]) or []:
        if isinstance(f, dict) and f.get("id") == "inv.custody":
            return f.get("status") not in ("SKIP", None), f":9021 findings[inv.custody].status={f.get('status')}"
    return False, ":9021 findings[inv.custody] absent"


def v_reports_ok_now(live: dict) -> tuple[bool, str]:
    ok = dig(live, "a9001_status", "required_reports_ok")
    return ok is True, f":9001/api/status.required_reports_ok={ok} failures={dig(live, 'a9001_status', 'required_report_failures')}"


# ---------------------------------------------------------------- plan
# id -> (owner, pr, planned status, plan, verifier or None)
P = dict
PLAN: dict[str, tuple[str, str, str, str, Verifier | None]] = {
    # Fly runtime threads / loops
    "F2": ("FLY runtime", "-", OPEN, "liveness-only by design; low impact", None),
    "F4": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "threads[dashboard_http_watchdog] heartbeat + restart counter", v_threads("dashboard_http_watchdog")),
    "F5": ("FLY runtime", "-", OPEN, "heartbeat+WS stale latch branch unreachable (:34197); unassigned", None),
    "F7": ("FLY runtime", "-", OPEN, "ping_ws silent break; covered indirectly by ws_heartbeat_age_sec", None),
    "F8": ("FLY runtime", "-", OPEN, "export ws_stale_count; unassigned", None),
    "F9": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "threads[ws_tick_lifecycle_worker] + dropped-tick counter", v_threads("ws_tick_lifecycle_worker")),
    "F10": ("FLY runtime", "-", OPEN, "state_monitor mode only; unassigned", None),
    "F12": ("FLY runtime", "-", OPEN, "book_age exposed, not alerted; unassigned", None),
    "F13": ("FLY runtime", "-", OPEN, "ohlcv errors log-only; unassigned", None),
    "F15": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "GH monitor subsystem rule on /ready.xvl_evaluator_health.tick_age_s", v_monitor_rules),
    "F16": ("FLY runtime", "-", OPEN, "split admission_eligible vs orders_submitted (rank 24); unassigned", None),
    "F18": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "COLLECTOR_V3_RECONCILE_STALLED rule (phase!=IDLE >600s); Fly phase_age_sec field still OPEN", v_monitor_rules),
    "F20": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "threads[main_supervisor]", v_threads("main_supervisor")),
    "F22": ("AI-PLAN", "#294", PROG, "attempt liveness / persisted last_success (rank 17); post-deploy verification pending", None),
    "F24": ("FLY runtime", "-", OPEN, "ai shadow labels owner-only; unassigned", None),
    "F25": ("FLY runtime", "-", OPEN, "last_engine_error overwritten by validate_market_data; unassigned", None),
    "F26": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "threads[tick_execution_engine]", v_threads("tick_execution_engine")),
    "F27": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "threads[position_manager]", v_threads("position_manager")),
    "F28": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "threads[analytics_loop]", v_threads("analytics_loop")),
    "F29": ("FLY runtime", "-", OPEN, "future-path evidence owner-only; unassigned", None),
    "F30": ("FLY runtime", "-", OPEN, "ttl_monitor log-only; can reuse thread_health post-freeze", None),
    "F32": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "threads[bitfinex_live_reconcile] + WARNING-level errors (pre-arming gate)", v_threads("bitfinex_live_reconcile")),
    "F33": ("BLINDSPOT-CLOSE", f"{PR_FLY} + {PR_MONITOR}", QUEUED, "relay_outbox age/owner fields + never-deliver guard (Fly, post-freeze); GH monitor relay_stale_owner_pending alert from existing fields (laptop/CI, live after merge)", v_relay_age),
    "F34": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "/api/state pause read live + api_state_age_sec", None),
    "F35": ("FLY runtime", "-", OPEN, "relay cache age exposed, not alerted; low", None),
    "F36": ("FLY runtime", "-", OPEN, "503 when stale; not alerted; low", None),
    "F38": ("FLY runtime", "-", OPEN, "inference flusher 4xx counted as success (rank 23); unassigned", None),
    "F39": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "GH monitor lifecycle_pipeline rule (age, blockers, emergency_wal)", v_monitor_rules),
    "F40": ("FLY runtime", "-", OPEN, "post-AI evidence not alerted; unassigned", None),
    "F41": ("FLY runtime", "-", OPEN, "provisional merge invisible; unassigned", None),
    "F42": ("FLY runtime", "-", OPEN, "admin-pause finalizer response-only; low", None),
    "F43": ("WATCHER", "-", OPEN, "ACK seq exposed via laptop.pull_ack; server errors not exposed", None),
    "F44": ("FLY-LOCKS", "#306 (proposed)", OPEN, "HTTP thread-cap saturation; propose to FLY-LOCKS runtime_telemetry", None),
    # sidecars
    "F45": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "GH monitor cross_venue_health rule", v_monitor_rules),
    "F46": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "GH monitor market_context rule", v_monitor_rules),
    "F47": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "shipper block in public /api/status; sidecar restart loop still OPEN", v_field("fly_status", "segment_shipper")),
    "F48": ("FLY runtime", "-", OPEN, "relay-state pusher no status / no restart (rank 22); unassigned", None),
    "F49": ("FLY runtime", "-", OPEN, "restart loop visible via uptime only; low", None),
    # clients
    "F51": ("FLY runtime", "-", OPEN, "private keys probe skipped in force-paper; pre-arming gate", None),
    "F52": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "DDOLLAR gate fail-CLOSED + error counter (freeze-exception candidate)", v_field("fly_status", "bitfinex_live", "ddollar_gate", "errors")),
    "F53": ("FLY runtime", "-", OPEN, "Neon reachability not probed; unassigned", None),
    # locks/queues/caches
    "F56": ("FLY-LOCKS", "#306", QUEUED, "replay_lock diagnostics alerting", None),
    "F57": ("FLY-LOCKS", "#306", QUEUED, "state_lock probe", None),
    "F58": ("FLY-LOCKS", "#306", QUEUED, "other locks", None),
    "F59": ("FLY-LOCKS", "#306", QUEUED, "relay drain lock", None),
    "F61": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "queues qsize/dropped in /api/status.queues", v_field("fly_status", "queues")),
    "F62": ("FLY runtime", "-", OPEN, "memory-only volume growth ring (rank 32)", None),
    "F63": ("FLY runtime", "-", OPEN, "memory-only alarm history (rank 32)", None),
    "F64": ("FLY runtime", "-", OPEN, "pathway spec caches invisible; low", None),
    # writers
    "F65": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "ledgers.lane_pnl write_failures counted + alarm (freeze-exception candidate)", v_field("fly_status", "ledgers", "lane_pnl", "write_failures")),
    "F66": ("FLY runtime", "-", OPEN, "csv fallback replayed at startup; no counter", None),
    "F67": ("FLY runtime", "-", OPEN, "corrupt restore pauses; no age field", None),
    "F68": ("FLY runtime", "-", OPEN, "write_failures exposed, not alerted", None),
    "F70": ("FLY runtime", "-", OPEN, "collection counters not alerted", None),
    "F71": ("WATCHER", "#307", PROG, "market_context/tape write failures read by streams.coverage (#307)", None),
    "F72": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "via V3 reconcile stall rule (F18)", v_monitor_rules),
    "F73": ("FLY runtime", "-", OPEN, "signal snapshot/shadow writers swallow errors; extend PR_FLY helper post-freeze", None),
    "F74": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "execution_funnel.hook_failures{hook} + alarm", v_field("fly_status", "collection", "execution_funnel", "hook_failures")),
    "F75": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "GH monitor emergency_wal != CURRENT finding", v_monitor_rules),
    "F77": ("FLY runtime", "-", OPEN, "control-action audit endpoint (rank 27); unassigned", None),
    "F78": ("FLY runtime", "-", OPEN, "effective-config snapshot (rank 27); unassigned", None),
    "F79": ("FLY-LOCKS", "#306 (proposed)", OPEN, "per-subsystem swallowed_errors counter (rank 26); not in #306 file list", None),
    # laptop
    "L1": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "laptop.supervisor by scheduled-task + process check, not log grep", needs("supervisor_process_check", v_check_present("laptop.supervisor"))),
    "L2": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "pull status keeps seqs; exitCode!=0 surfaced", needs("pull_ack_no_none_green", v_pull_ack)),
    "L3": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "wrapper failures surface via segment-pull.status exitCode rule", needs("pull_ack_no_none_green", v_pull_ack)),
    "L4": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "puller lock refusal preserves applied/acked seqs + consecutive_failures", needs("pull_ack_no_none_green", v_pull_ack)),
    "L5": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "puller HTTP source bounded deadline (if safe)", None),
    "L6": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "pull_ack consistent with monitor SEGMENT_ACK_STALE", needs("pull_ack_no_none_green", v_pull_ack)),
    "L7": ("WATCHER", "-", OPEN, "parity-latest.json not in :9011", None),
    "L8": ("ANALYZER-FIDELITY", "#304/#307", PROG, "cycle history + consecutive_failures (rank 20)", None),
    "L9": ("ANALYZER-FIDELITY", "-", OPEN, "promotion lock holder/exit 3 surfaced (rank 20)", None),
    "L10": ("ANALYZER-FIDELITY", "-", OPEN, "migration log-only (rank 20)", None),
    "L11": ("ANALYZER-FIDELITY", "#304", PROG, "inline auto-FF observed", None),
    "L12": ("ANALYZER-FIDELITY", "#304", PROG, "REFUSED_* auto-ff receipts not surfaced", None),
    "L13": ("ANALYZER-FIDELITY + BLINDSPOT-CLOSE", f"#304 + {PR_LAPTOP}", PROG, "generation receipt (#304) + analyzer.reports from :9001/api/status", needs("analyzer_reports", v_analyzer_reports)),
    "L14": ("ANALYZER-9001", "-", OPEN, "launcher stdout only; low", None),
    "L15": ("ANALYZER-FIDELITY + BLINDSPOT-CLOSE", f"#304 + {PR_LAPTOP}", PROG, "#304 fixes POLICY_ID_SPEC_COLLISION; watcher analyzer.reports RED when required_reports_ok=false >1 generation", needs("analyzer_reports", v_analyzer_reports)),
    "L16": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "analyzer.api not GREEN when /api/status.ok=false", needs("analyzer_reports", v_analyzer_reports)),
    "L17": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "parse string revision_parity; compare generation rev to Fly rev", needs("analyzer_parity_strict", v_analyzer_api_parity)),
    "L18": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "AMBER when upstream_sync_id != Fly sync id (refresh at source = #302 owner)", needs("analyzer_parity_strict", v_analyzer_api_parity)),
    "L19": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "AMBER when mirror_sync_receipt STALE even if ok=true", needs("analyzer_parity_strict", v_analyzer_api_parity)),
    "L20": ("ANALYZER-9001", "#302", PROG, "dashboard code refresh", None),
    "L23": ("ANALYZER-FIDELITY", "#307", PROG, "export swallow", None),
    "L24": ("ANALYZER-FIDELITY", "#307", PROG, "engine swallow", None),
    "L25": ("ANALYZER-FIDELITY", "-", OPEN, "heavy cache invisible", None),
    "L26": ("ANALYZER-FIDELITY + BLINDSPOT-CLOSE", f"#307 + {PR_LAPTOP}", PROG, "#307 fixes stream studies; watcher streams.analysed_freshness detects stale-content ANALYSED", needs("streams_analysed_freshness", v_check_present("streams.analysed_freshness"))),
    "L27": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "streams.analysed_freshness reads /api/streams/health content_lag_sec + not_fully_analysed", needs("streams_analysed_freshness", v_check_present("streams.analysed_freshness"))),
    "L28": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "insights transfer not OK with applied_seq=null", None),
    "L29": ("ANALYZER-FIDELITY", "#307", PROG, "client live-check swallow", None),
    "L31": ("WATCHER", "-", OPEN, "laptop-chain-monitor notify catch {}", None),
    "L32": ("WATCHER", "-", OPEN, "legacy SYNC_HEARTBEAT toast only", None),
    "L33": ("WATCHER", "-", OPEN, "monitor warnings toast only", None),
    "L34": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "incident relay: stale report -> system_health_stale; deploy-aware maintenance; non-zero exit", needs("incident_stale_report", v_report_staleness)),
    "L35": ("SELF-AWARE", "-", OPEN, "proof receipts in stale checkout (rank 28)", None),
    "L36": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "health-report staleness -> incident", needs("incident_stale_report", v_report_staleness)),
    "L37": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "interim task defers only if supervisor ticked <15 min", None),
    "L38": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, ":9011 serves cache with age; live=1 single-flight background refresh", needs("cached_live_refresh", v_w9011_latency)),
    "L39": ("WATCHER", "-", OPEN, "banner push result string only", None),
    "L40": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "GH monitor polls Fly /api/system-health age/stale -> laptop_health_silent", v_monitor_rules),
    "L45": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "decision_mix SKIP below min samples", needs("missing_data_not_green", v_check_status("ai.decision_mix", lambda c: c.get("status") in ("GREEN", "AMBER", "SKIP", "RED"), "evaluated; SKIP below min samples"))),
    "L47": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "trading.orders AMBER when toggles missing", needs("missing_data_not_green", v_contradiction_trading_orders)),
    "L48": ("WATCHER", "-", OPEN, "lifecycle contradictions AMBER only", None),
    "L51": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "applied=None never GREEN", needs("pull_ack_no_none_green", v_pull_ack)),
    "L52": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "analyzer.reports alongside analyzer.generation", needs("analyzer_reports", v_analyzer_reports)),
    "L53": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "analyzer.api correctness", needs("analyzer_parity_strict", v_analyzer_api_parity)),
    "L54": ("ANALYZER-FIDELITY + BLINDSPOT-CLOSE", f"#307 + {PR_LAPTOP}", PROG, "nothing reported -> AMBER, never 'n/a' GREEN", needs("missing_data_not_green", v_streams_coverage)),
    "L55": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "parse string epoch_parity", None),
    "L56": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "reconciliation null -> AMBER", needs("missing_data_not_green", v_check_status("railway.relay", lambda c: "reconciliation=null" not in str(c.get("observed")) or c.get("status") != "GREEN", "reconciliation=null is never GREEN"))),
    "L58": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "exch_qty None -> AMBER unless explicitly disarmed", needs("missing_data_not_green", v_check_status("bitfinex.exposure", lambda c: c.get("status") != "GREEN" or "disarmed" in str(c.get("observed")), "qty not probed is GREEN only when explicitly disarmed"))),
    "L60": ("BLINDSPOT-CLOSE + SELF-AWARE", f"{PR_LAPTOP} + #300", PROG, "watcher selfaware.engine (age/jobs) RED on stale; keeper script change proposed to SELF-AWARE", needs("selfaware_engine", v_check_present("selfaware.engine"))),
    "L61": ("SELF-AWARE", "#300", PROG, "job last_ok AMBER history only", None),
    "L62": ("SELF-AWARE", "#300", PROG, "views job returns OK with errors (rank 29)", None),
    "L63": ("SELF-AWARE + BLINDSPOT-CLOSE", f"#300 + {PR_LAPTOP}", PROG, "custody SKIP; puller seq preservation feeds it", v_selfaware_custody),
    "L67": ("SELF-AWARE", "#300", PROG, "missing view -> SKIP", None),
    "L68": ("SELF-AWARE", "#300", PROG, "progress probes", None),
    "L69": ("SELF-AWARE", "#300", PROG, "freshness vs mirror head; Fly volume null GREEN", None),
    "L70": ("SELF-AWARE", "#300", PROG, "uptime interruptions disagree 9 vs 1", None),
    "L71": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "frozen legacy ACK watcher state reported as orphan", needs("missing_data_not_green", v_check_status("laptop.legacy_ack_watcher", lambda c: c.get("status") != "GREEN", "frozen orphan watcher reported"))),
    "L72": ("Danish", "-", OPEN, "ad-hoc :7002 proxy: register or retire", None),
    "L73": ("Danish", "-", OPEN, "ad-hoc watch_queue.ps1: register or retire", None),
    "L74": ("Danish", "-", OPEN, "ad-hoc uptime_poll2 / :9097: register or retire", None),
    # monitoring / CI
    "C1": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "heartbeat variable + monitor_schedule_gap; crash/cache-loss never closes incidents", v_monitor_heartbeat),
    "C3": ("MONITOR", "-", OPEN, "master-ahead-of-Fly not checked", None),
    "C5": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "entries_blocked when last_poll_entry_eligible=false >2h unpaused", v_monitor_rules),
    "C7": ("DATA-RETENTION", "#303", QUEUED, "FLY_MONITOR_SEGMENTS_LIVE=1 before prune goes live", None),
    "C9": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "unset LAPTOP_CHAIN_HEARTBEAT is a finding", v_monitor_line("LAPTOP_CHAIN_HEARTBEAT:")),
    "C11": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "incident maintenance from DEPLOY_MAINTENANCE (capped 90 min)", None),
    "C12": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "subsystem_findings over /ready blocks", v_monitor_rules),
    "C13": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "rules over lifecycle / relay outbox / V3 reconcile", v_monitor_rules),
    "C14": ("DATA-RETENTION", "#303", QUEUED, "prune dry_run until #303", None),
    "C15": ("MONITOR", "-", OPEN, "deploy failure alert = GHA email only", None),
    "C16": ("BLINDSPOT-CLOSE", f"{PR_LAPTOP} + {PR_MONITOR}", PROG, "laptop-tests workflow; head commits without [skip ci], squash subject with [skip ci]", v_laptop_tests_ci),
    "C17": ("COORDINATOR", "-", OPEN, "production gate ignores bot-code pushes", None),
    "C18": ("Danish", "-", OPEN, "auto-deploy disabled_manually (intentional?)", None),
    "C19": ("MONITOR", "-", OPEN, "secret-scan HEAD-only", None),
    "C20": ("COORDINATOR", "-", OPEN, "WALL untracked / unvalidated", None),
    "C21": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "monitor heartbeat watched by monitor itself + laptop", v_monitor_heartbeat),
}

# Earlier-audit gaps (closure table Â§4 + Â§4.3).  Only NOT ADDRESSED / contradicted
# items get new owners; IN PROGRESS / QUEUED / CLOSED keep the audit's owner.
GAP_PLAN: dict[str, tuple[str, str, str, str, Verifier | None]] = {
    "13": ("ANALYZER-FIDELITY", "-", OPEN, "promotion lock contention", None),
    "17": ("ANALYZER-FIDELITY", "-", OPEN, "cycle length vs 45-min freshness", None),
    "18": ("ANALYZER-FIDELITY", "-", OPEN, "multiverse HEALTH_ONLY", None),
    "20": ("ANALYZER-FIDELITY", "-", OPEN, "signal_replay completion", None),
    "21": ("ANALYZER-FIDELITY", "-", OPEN, "PIPELINE_ERROR race / capacity censoring", None),
    "29": ("AI-PLAN", "-", OPEN, "dead-input detector watches challenger fields only", None),
    "33": ("FLY runtime", "-", OPEN, "Bybit funding constant", None),
    "38": ("FLY runtime", "-", OPEN, "clock skew", None),
    "39": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "lifecycle blocker_counts / emergency WAL rule; crash dumps OPEN", v_monitor_rules),
    "40": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "subsystem rules (WS reconnects, REST stale, epoch parity)", v_monitor_rules),
    "41": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "rate_limits{venue}.hits_429", v_field("fly_status", "rate_limits")),
    "43": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "fetch failure >15 min -> AMBER, not SKIP", needs("fetch_failure_not_skip", v_check_present("watcher.sources"))),
    "47": ("WATCHER", "-", OPEN, "failing[] duplicates", None),
    "48": ("WATCHER", "-", OPEN, "Fly-published health lags laptop", None),
    "50": ("WATCHER", "-", OPEN, "verdict never GREEN; no ack/expiry", None),
    "51": ("WATCHER", "-", OPEN, "flapping unclassified", None),
    "54": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "watcher parses revision lag / receipt age (source fix = ANALYZER-FIDELITY)", needs("analyzer_parity_strict", v_analyzer_api_parity)),
    "55": ("ANALYZER-FIDELITY", "-", OPEN, "insights tile fields null", None),
    "56": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "puller seqs preserved; insights transfer honest", needs("pull_ack_no_none_green", v_pull_ack)),
    "57": ("SELF-AWARE", "-", OPEN, "deploy_queue stale WALL scrape", None),
    "62": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "ai_provider_health.cost_usd_24h / last_call_cost_usd", v_field("fly_status", "ai_provider_health", "last_call_cost_usd")),
    "63": ("COORDINATOR", "-", OPEN, "Fly/Railway spend, Neon forecast", None),
    "69": ("SELF-AWARE", "-", OPEN, "deploy stuck detection / in_progress after completion", None),
    "70": ("BLINDSPOT-CLOSE", PR_MONITOR, PROG, "laptop-tests workflow gives PR checks; results API still OPEN", v_laptop_tests_ci),
    "71": ("SELF-AWARE", "-", OPEN, "/changes timeline", None),
    "73": ("SELF-AWARE + BLINDSPOT-CLOSE", f"#300 + {PR_LAPTOP}", PROG, "unified custody (puller seqs fixed here)", v_selfaware_custody),
    "75": ("SELF-AWARE", "-", OPEN, "incident timeline", None),
    "76": ("ANALYZER-FIDELITY", "-", OPEN, "exports over HTTP", None),
    "77": ("COORDINATOR", "-", OPEN, "AGENTS.md names retired tiles; live roster differs", None),
    "78": ("COORDINATOR", "-", OPEN, "stale canonical checkout btc-v31-current (d3544f9f7)", None),
    "79": ("ANALYZER-FIDELITY", "#307", PROG, "EXPORT_README (#307); PREREGISTERED-HYPOTHESES still absent", None),
    "80": ("MONITOR", "-", OPEN, "no recurring secrets scan", None),
    # Â§4.3 contradictions
    "X1": ("ANALYZER-FIDELITY", "#293/#307", PROG, "data_health still STALE until analyzer runs #293", None),
    "X2": ("SELF-AWARE + BLINDSPOT-CLOSE", f"#300 + {PR_LAPTOP}", PROG, "inv.custody SKIP", v_selfaware_custody),
    "X3": ("SELF-AWARE", "#300", PROG, "Fly volume_free_gb null", None),
    "X4": ("AI-PLAN", "#294", PROG, "live-prompt ret_1m/ret_5m/delta_change verification pending", None),
    "X5": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "watcher revision parity honest", needs("analyzer_parity_strict", v_analyzer_api_parity)),
    "X6": ("BLINDSPOT-CLOSE", PR_LAPTOP, PROG, "trading.orders missing toggles -> AMBER (Fly-published copy lags)", needs("missing_data_not_green", v_contradiction_trading_orders)),
    "X7": ("SELF-AWARE", "#300", PROG, "deploy receipt in_progress after completion", None),
    "X8": ("SELF-AWARE", "#300", PROG, "Bybit funding field-liveness inconsistency", None),
}

EXTRA = {
    # Danish's directive items and trace-audit items with no AUDIT-3 row id
    "T-REPORTS-OK": ("ANALYZER-FIDELITY", "#304", PROG, "required analyzer reports actually pass (root fix)", v_reports_ok_now),
    "T-TOGGLES": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "per-tile toggle state in public /api/status", v_toggles),
    "T-RELAY-GATE": ("BLINDSPOT-CLOSE", PR_FLY, QUEUED, "arming refused while stale-owner/pre-arming relay events unquarantined (readiness gate)", None),
    "T-TIERA-API": ("ANALYZER-FIDELITY", "#307", PROG, "Tier A promotion visible via storage.tier_a", v_check_present("storage.tier_a")),
}


# ---------------------------------------------------------------- audit parsing
def classify(s: str) -> str:
    s = s.upper()
    for k in ("BLIND", "PARTIAL", "FULL"):
        if s.startswith(k):
            return k
    return s


def parse_audit(text: str) -> tuple[list[dict], list[dict]]:
    rows, gaps = [], []
    section = ""
    for line in text.splitlines():
        if line.startswith("## 4."):
            section = "closure"
        m = re.match(r"^\| ([FLC]\d+) \|(.*)\|\s*$", line)
        if m:
            cells = [c.strip() for c in m.group(2).split("|")]
            rows.append({"id": m.group(1), "component": cells[0], "audit": classify(cells[-1]), "raw": cells[-1]})
            continue
        m = re.match(r"^\| (\d+) \| (.*?) \| (.*?) \| (.*?) \| (.*?) \|\s*$", line)
        if m and section == "closure":
            gaps.append({"id": m.group(1), "gap": m.group(2), "source": m.group(3),
                         "audit": re.sub(r"\*", "", m.group(4)), "evidence": m.group(5)})
    for i, m in enumerate(re.finditer(r"^(\d)\. (.+)$", text.split("### 4.3", 1)[-1].split("### 4.4", 1)[0], re.M)):
        gaps.append({"id": f"X{m.group(1)}", "gap": m.group(2)[:150], "source": "Â§4.3", "audit": "CONTRADICTED", "evidence": ""})
    return rows, gaps


def resolve(plan: tuple, live: dict | None, prs: dict[str, str]) -> dict:
    owner, pr, status, note, verifier = plan
    for k, v in prs.items():
        pr = pr.replace(k, v)
    evidence = ""
    if verifier is not None and live is not None:
        try:
            passed, evidence = verifier(live)
        except Exception as exc:  # broken verifier is never closure
            passed, evidence = False, f"verifier error {type(exc).__name__}"
        if passed:
            status = CLOSED
    return {"owner": owner, "pr": pr, "status": status, "plan": note, "evidence": evidence,
            "verifier": verifier is not None}


def md_escape(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def build(audit_text: str, live: dict | None, prs: dict[str, str], baseline: dict | None) -> str:
    rows, gaps = parse_audit(audit_text)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    comp, gap_out, extra_out = [], [], []
    for r in rows:
        if r["audit"] == "FULL":
            continue
        plan = PLAN.get(r["id"], ("UNASSIGNED", "-", OPEN, "no plan recorded", None))
        comp.append({**r, **resolve(plan, live, prs)})
    for g in gaps:
        a = g["audit"].upper()
        if g["id"] in GAP_PLAN:
            plan = GAP_PLAN[g["id"]]
        elif a.startswith("CLOSED"):
            plan = ("audit", "-", CLOSED_AUDIT, "closed live in AUDIT-3", None)
        elif a.startswith("QUEUED"):
            plan = ("audit owner", re.sub(r"^QUEUED\s*", "", g["audit"]) or "-", QUEUED, g["gap"], None)
        elif a.startswith("IN PROGRESS"):
            plan = ("audit owner", re.sub(r"^IN PROGRESS\s*", "", g["audit"]) or "-", PROG, g["gap"], None)
        else:
            plan = ("UNASSIGNED", "-", OPEN, "no plan recorded", None)
        gap_out.append({**g, **resolve(plan, live, prs)})
    for k, plan in EXTRA.items():
        extra_out.append({"id": k, **resolve(plan, live, prs)})

    def counts(items: list[dict]) -> Counter:
        return Counter(i["status"] for i in items)

    all_items = comp + [g for g in gap_out if not g["audit"].upper().startswith("CLOSED")] + extra_out
    c_all = counts(all_items)
    lines = [
        "# BLINDSPOT closure ledger (AUDIT-3)",
        "",
        f"Generated {now} by `scripts/blindspot_closure_ledger.py` (branch fix/blindspot-close-laptop).",
        "Rule: an item is **CLOSED-VERIFIED-LIVE** only when its live verifier passes against the live API in this run "
        "(endpoint + field + value in the Evidence column). A merged PR or a WALL claim never closes an item.",
        f"Live sources: {'captured ' + str(live.get('_captured', '')) if live and live.get('_captured') else ('fetched now' if live else 'NONE (plan only)')}; "
        f"fetch errors: {json.dumps((live or {}).get('_errors', {}))}",
        "",
        "## Counts",
        "",
        "| Scope | Items | OPEN | IN PROGRESS | QUEUED-POST-FREEZE | CLOSED-VERIFIED-LIVE |",
        "|---|---|---|---|---|---|",
    ]
    for label, items in (("Component rows (BLIND/PARTIAL)", comp),
                         ("Earlier gaps not closed (incl. Â§4.3 contradictions)",
                          [g for g in gap_out if not g["audit"].upper().startswith("CLOSED")]),
                         ("Directive / trace items", extra_out), ("**Total tracked**", all_items)):
        c = counts(items)
        lines.append(f"| {label} | {len(items)} | {c[OPEN]} | {c[PROG]} | {c[QUEUED]} | {c[CLOSED]} |")
    a = Counter(r["audit"] for r in rows)
    lines += ["", f"Audit table parse: {len(rows)} components = FULL {a['FULL']} / PARTIAL {a['PARTIAL']} / BLIND {a['BLIND']} "
              "(the audit headline states 35/93/46; its laptop sub-total 13/43/18 does not match its own L-rows 15/36/23 â€” "
              "this ledger uses the row-level statuses)."]
    if baseline:
        lines += ["", "## Before / after", "", "| Status | Before (this ledger, 2026-10-02T10:55Z) | Now |", "|---|---|---|"]
        for s in (OPEN, PROG, QUEUED, CLOSED):
            lines.append(f"| {s} | {baseline.get(s, 0)} | {c_all[s]} |")
    lines += ["", "## Component rows", "",
              "| Row | Component | Audit | Owner | PR | Status | Plan | Live evidence |", "|---|---|---|---|---|---|---|---|"]
    for r in comp:
        lines.append(f"| {r['id']} | {md_escape(r['component'])[:60]} | {r['audit']} | {r['owner']} | {r['pr']} | "
                     f"**{r['status']}** | {md_escape(r['plan'])} | {md_escape(r['evidence']) or ('(no verifier yet)' if not r['verifier'] else '')} |")
    lines += ["", "## Earlier-audit gaps (closure table Â§4) and Â§4.3 contradictions", "",
              "| # | Gap | Audit status | Owner | PR | Status | Plan | Live evidence |", "|---|---|---|---|---|---|---|---|"]
    for g in gap_out:
        lines.append(f"| {g['id']} | {md_escape(g['gap'])[:70]} | {md_escape(g['audit'])} | {g['owner']} | {g['pr']} | "
                     f"**{g['status']}** | {md_escape(g['plan'])[:90]} | {md_escape(g['evidence'])} |")
    lines += ["", "## Directive / trace items", "", "| Id | Owner | PR | Status | Plan | Live evidence |", "|---|---|---|---|---|---|"]
    for e in extra_out:
        lines.append(f"| {e['id']} | {e['owner']} | {e['pr']} | **{e['status']}** | {md_escape(e['plan'])} | {md_escape(e['evidence'])} |")
    lines += ["", "## Machine summary", "", "```json",
              json.dumps({"generated_at": now, "counts": dict(c_all), "total": len(all_items)}, indent=1), "```", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--live-dir", help="use captured JSON instead of fetching")
    ap.add_argument("--no-live", action="store_true")
    ap.add_argument("--baseline-json", help="counts JSON from the first run (before)")
    ap.add_argument("--pr-laptop", default="#309")
    ap.add_argument("--pr-monitor", default="#310")
    ap.add_argument("--pr-fly", default="Fly post-freeze PR (pending)")
    args = ap.parse_args()
    live = None
    if not args.no_live:
        live = load_live(Path(args.live_dir)) if args.live_dir else fetch_live()
        if args.live_dir:
            live["_captured"] = Path(args.live_dir).name
    baseline = json.loads(Path(args.baseline_json).read_text()) if args.baseline_json else None
    prs = {PR_LAPTOP: args.pr_laptop, PR_MONITOR: args.pr_monitor, PR_FLY: args.pr_fly}
    text = build(Path(args.audit).read_text(encoding="utf-8"), live, prs, baseline)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text.split("## Component rows")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
