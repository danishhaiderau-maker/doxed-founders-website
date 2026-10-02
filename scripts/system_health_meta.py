"""Watcher-of-the-watchers checks for system_health.py.

Each function turns one laptop-side status file (parity report, puller lock,
laptop-chain monitor, incident relay, interim task, WALL) into a regular
``check()`` row so a failure there shows on :9011 instead of a toast, a log line
or nothing. ``finalize`` then dedupes checks by id, applies operator acks
(AMBER only, never RED) and flags flapping checks. Read-only: nothing here
pauses, deploys, arms or touches trading.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

GREEN, AMBER, RED, SKIP = "GREEN", "AMBER", "RED", "SKIP"
RANK = {SKIP: -1, GREEN: 0, AMBER: 1, RED: 2}
MIN = 60.0
HOUR = 3600.0

META_THRESHOLDS: dict[str, float] = {
    "parity_amber_sec": 3 * HOUR,
    "parity_red_sec": 8 * HOUR,
    "parity_lock_amber_sec": 15 * MIN,
    "puller_lock_amber_sec": 20 * MIN,
    "puller_lock_red_sec": 45 * MIN,
    "chain_monitor_stale_sec": 30 * MIN,
    "incident_heartbeat_stale_sec": 30 * MIN,
    "incident_maintenance_cap_sec": 90 * MIN,
    "interim_stale_sec": 20 * MIN,
    "banner_fail_amber": 2,
    "banner_fail_red": 12,
    "fly_copy_amber_sec": 15 * MIN,
    "fly_copy_red_sec": HOUR,
    "wall_recent_lines": 40,
    "wall_quiet_amber_sec": 12 * HOUR,
    "flap_window": 12,
    "flap_transitions": 4,
}

PARITY_LOCK_HOLDER = "research_segment_fly_parity"
WALL_LINE = re.compile(r"^\d{4}-\d\d-\d\dT[^|]+\|[^|]+\|.*\|\s*[A-Za-z][^|]*$")
ADHOC_PORTS = (7002, 9097)
ADHOC_SCRIPTS = ("watch_queue.ps1",)

CheckFn = Callable[..., dict[str, Any]]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _age(now: float, ts: float | None) -> float | None:
    return None if ts is None else max(0.0, now - ts)


# ------------------------------------------------------------------ collect

def collect_meta(*, state_dir: Path, shadow_root: Path, wall: Path, parse_ts: Callable[[Any], float | None],
                 fly_published: Any = None, probe_processes: bool = True) -> dict[str, Any]:
    puller = shadow_root / ".puller"
    holder = _read_json(puller / "run.lock.holder.json")
    return {
        "parity": _read_json(shadow_root / "parity-latest.json"),
        "parity_mtime": _mtime(shadow_root / "parity-latest.json"),
        "puller_status": _read_json(puller / "status.json"),
        "lock_holder": holder if isinstance(holder, Mapping) else None,
        "chain_monitor": _read_json(state_dir / "laptop-chain-monitor.state.json"),
        "incident": _read_json(state_dir / "laptop-chain-incident.state.json"),
        "interim": _read_json(state_dir / "health" / "interim-tick.status.json"),
        "acks": _read_json(state_dir / "health" / "acks.json"),
        "wall_tail": _tail_lines(wall),
        "fly_published": fly_published,
        "adhoc": probe_adhoc() if probe_processes else None,
        "parse_ts": parse_ts,
    }


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _tail_lines(path: Path, max_bytes: int = 256 * 1024) -> list[str] | None:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    lines = data.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines


def probe_adhoc(timeout: float = 20.0) -> dict[str, Any] | None:
    """Listeners on ad-hoc ports and ad-hoc watcher scripts (visibility only)."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=timeout,
                             check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    listeners = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING":
            port = parts[1].rsplit(":", 1)[-1]
            if port.isdigit() and int(port) in ADHOC_PORTS:
                listeners.append({"port": int(port), "pid": parts[4]})
    scripts: list[dict[str, Any]] = []
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'powershell%' or Name like 'pwsh%'\" | "
              "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }")
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=timeout, check=False).stdout
        for line in res.splitlines():
            pid, _, cmd = line.partition("|")
            for name in ADHOC_SCRIPTS:
                if name.lower() in cmd.lower():
                    scripts.append({"script": name, "pid": pid.strip()})
    except (OSError, subprocess.SubprocessError):
        pass
    return {"listeners": sorted(listeners, key=lambda x: x["port"]), "scripts": scripts}


# ------------------------------------------------------------------- checks

def meta_checks(meta: Mapping[str, Any], state: dict[str, Any], now: float, check: CheckFn,
                fmt_age: Callable[[float | None], str], local_generated_ts: float | None = None,
                thresholds: Mapping[str, float] | None = None) -> list[dict[str, Any]]:
    t = {**META_THRESHOLDS, **(thresholds or {})}
    parse_ts = meta.get("parse_ts") or (lambda v: None)
    out: list[dict[str, Any]] = []
    holder = meta.get("lock_holder") or {}
    status = meta.get("puller_status") if isinstance(meta.get("puller_status"), Mapping) else {}
    lock = _lock_contention(holder, status, now, parse_ts)
    out.append(parity_check(meta, lock, now, check, fmt_age, parse_ts, t))
    out.append(puller_lock_check(status, lock, now, check, fmt_age, t))
    out.append(chain_monitor_check(meta.get("chain_monitor"), now, check, fmt_age, parse_ts, t))
    out.append(incident_relay_check(meta.get("incident"), now, check, fmt_age, t))
    out.append(interim_check(meta.get("interim"), now, check, fmt_age, parse_ts, t))
    out.append(delivery_check(state, check, t))
    out.append(fly_copy_check(meta.get("fly_published"), local_generated_ts, now, check, fmt_age, parse_ts, t))
    out.append(wall_check(meta.get("wall_tail"), now, check, fmt_age, parse_ts, t))
    out.append(adhoc_check(meta.get("adhoc"), check))
    return out


def _lock_contention(holder: Mapping[str, Any], status: Mapping[str, Any], now: float,
                     parse_ts: Callable[[Any], float | None]) -> dict[str, Any]:
    """The holder sidecar is only authoritative while pullers are being refused by it."""
    acquired = parse_ts(holder.get("acquired_at"))
    refused_by = status.get("lock_holder") if isinstance(status.get("lock_holder"), Mapping) else {}
    busy = str(status.get("last_attempt_result") or "").upper() == "LOCK_BUSY"
    active = bool(holder) and busy and (not refused_by or refused_by.get("acquired_at") == holder.get("acquired_at"))
    return {"active": active, "holder": holder.get("holder"), "pid": holder.get("pid"),
            "held_sec": _age(now, acquired) if active else None,
            "refusals": status.get("consecutive_failures") if busy else 0}


def parity_check(meta, lock, now, check, fmt_age, parse_ts, t):
    report = meta.get("parity")
    if not isinstance(report, Mapping):
        return check("analyzer.parity_checker", "analyzer", AMBER, "parity-latest.json missing or unreadable",
                     "checkpoint parity report present and fresh")
    gen = parse_ts(report.get("generated_at")) or meta.get("parity_mtime")
    age = _age(now, gen)
    counts = report.get("counts") or {}
    verdict = str(report.get("verdict") or "UNKNOWN").upper()
    timing = report.get("timing") or {}
    st, reasons = GREEN, []
    if verdict != "GREEN":
        st = RED
        reasons.append(f"verdict {verdict}: missing={counts.get('missing')} sealed_mismatch="
                       f"{counts.get('sealed_mismatch')} sqlite_corrupt={counts.get('sqlite_corrupt')}")
    if age is None or age > t["parity_red_sec"]:
        st = RED
        reasons.append(f"no parity report for {fmt_age(age)}")
    elif age > t["parity_amber_sec"]:
        st = max(st, AMBER, key=RANK.get)
        reasons.append(f"parity report {fmt_age(age)} old")
    running = lock["active"] and lock["holder"] == PARITY_LOCK_HOLDER
    if running and (lock["held_sec"] or 0) > t["parity_lock_amber_sec"]:
        st = max(st, AMBER, key=RANK.get)
        reasons.append(f"parity scan holding the puller lock for {fmt_age(lock['held_sec'])}")
    held = timing.get("lock_held_sec")
    return check("analyzer.parity_checker", "analyzer", st,
                 f"verdict={verdict} seq={report.get('seq')} age={fmt_age(age)} "
                 + (f"lock_held={held}s hashed={timing.get('hashed')} cache_hits={timing.get('hash_cache_hits')}"
                    if timing else "no timing (pre-cache parity build)")
                 + (f"; scan running {fmt_age(lock['held_sec'])}" if running else ""),
                 f"GREEN verdict within {fmt_age(t['parity_amber_sec'])} (RED {fmt_age(t['parity_red_sec'])}), "
                 f"scan holds the puller lock < {fmt_age(t['parity_lock_amber_sec'])}",
                 "; ".join(reasons),
                 fields={"verdict": verdict, "seq": report.get("seq"), "age_sec": age, "counts": dict(counts),
                         "timing": dict(timing), "running": running})


def puller_lock_check(status, lock, now, check, fmt_age, t):
    fields = {k: status.get(k) for k in ("last_attempt_result", "consecutive_failures", "run_seconds",
                                          "max_run_seconds", "deadline_reached", "last_success_at")}
    fields.update(lock_holder=lock["holder"], lock_pid=lock["pid"], lock_held_sec=lock["held_sec"])
    if not status:
        return check("laptop.puller_lock", "laptop", AMBER, "puller status.json missing",
                     "puller runs are not starved by the shadow-root lock", fields=fields)
    held = lock["held_sec"] or 0
    st = (RED if held > t["puller_lock_red_sec"] else AMBER if held > t["puller_lock_amber_sec"] else GREEN) \
        if lock["active"] else GREEN
    run = status.get("run_seconds")
    obs = (f"{lock['holder']} pid {lock['pid']} holds the lock for {fmt_age(lock['held_sec'])}; "
           f"{lock['refusals']} refused pulls" if lock["active"] else
           f"no lock contention; last attempt {status.get('last_attempt_result')}")
    obs += f"; last run {run}s of max {status.get('max_run_seconds')}s" if run is not None else ""
    return check("laptop.puller_lock", "laptop", st, obs,
                 f"no holder starves pulls for > {fmt_age(t['puller_lock_amber_sec'])} "
                 f"(RED {fmt_age(t['puller_lock_red_sec'])})",
                 "" if st == GREEN else "a long parity scan or wedged puller holds the shadow-root lock; "
                                        "transfer stalls until it releases", fields=fields)


def chain_monitor_check(mon, now, check, fmt_age, parse_ts, t):
    if not isinstance(mon, Mapping):
        return check("laptop.chain_monitor", "laptop", AMBER, "laptop-chain-monitor.state.json missing",
                     "monitor ran recently with no active alerts")
    age = _age(now, parse_ts(mon.get("checkedAt")))
    alerts = [a for a in (mon.get("alerts") or []) if isinstance(a, Mapping)]
    crit = [a for a in alerts if str(a.get("severity")).lower() == "critical"]
    st = RED if crit else AMBER if alerts else GREEN
    if age is None or age > t["chain_monitor_stale_sec"]:
        st = max(st, AMBER, key=RANK.get)
    codes = [f"{a.get('code')}({a.get('severity')})" for a in alerts]
    return check("laptop.chain_monitor", "laptop", st,
                 f"{len(alerts)} active monitor alerts {codes[:6]}; checked {fmt_age(age)} ago",
                 f"0 active laptop-chain-monitor alerts, checked within {fmt_age(t['chain_monitor_stale_sec'])}",
                 "; ".join(f"{a.get('code')}: {str(a.get('detail'))[:120]}" for a in alerts[:4]),
                 fields={"alerts": [{k: a.get(k) for k in ("code", "severity", "openedAt")} for a in alerts],
                         "checked_age_sec": age})


def incident_relay_check(inc, now, check, fmt_age, t):
    if not isinstance(inc, Mapping):
        return check("laptop.incident_relay", "laptop", AMBER, "laptop-chain-incident.state.json missing",
                     "incident watchdog heartbeat fresh")
    hb_age = _age(now, _num(inc.get("heartbeat_at")))
    alerts = inc.get("alerts") if isinstance(inc.get("alerts"), Mapping) else {}
    maint = _num(alerts.get("maintenance_since"))
    maint_age = _age(now, maint)
    conds = alerts.get("conditions") if isinstance(alerts.get("conditions"), Mapping) else {}
    reasons, st = [], GREEN
    if hb_age is None or hb_age > t["incident_heartbeat_stale_sec"]:
        st = AMBER
        reasons.append(f"incident watchdog heartbeat {fmt_age(hb_age)} old")
    if maint_age is not None and maint_age > t["incident_maintenance_cap_sec"]:
        st = RED
        reasons.append(f"maintenance suppression active for {fmt_age(maint_age)} (cap "
                       f"{fmt_age(t['incident_maintenance_cap_sec'])})")
    open_conds = {k: (v or {}).get("severity") for k, v in conds.items() if isinstance(v, Mapping)}
    return check("laptop.incident_relay", "laptop", st,
                 f"heartbeat {fmt_age(hb_age)} ago; maintenance={'none' if maint is None else fmt_age(maint_age)}; "
                 f"open conditions {sorted(open_conds)}",
                 f"heartbeat within {fmt_age(t['incident_heartbeat_stale_sec'])}; maintenance suppression "
                 f"<= {fmt_age(t['incident_maintenance_cap_sec'])}",
                 "; ".join(reasons),
                 fields={"heartbeat_age_sec": hb_age, "maintenance_age_sec": maint_age,
                         "paused_since": alerts.get("paused_since"), "deploy_pause_since":
                         alerts.get("deploy_pause_since"), "open_conditions": open_conds})


def interim_check(interim, now, check, fmt_age, parse_ts, t):
    if not isinstance(interim, Mapping):
        return check("watcher.interim", "watcher", AMBER, "interim-tick.status.json not written yet",
                     "interim watcher task decided within the stale window")
    age = _age(now, parse_ts(interim.get("at")))
    st = GREEN if age is not None and age <= t["interim_stale_sec"] else AMBER
    return check("watcher.interim", "watcher", st,
                 f"{interim.get('decision')} {fmt_age(age)} ago (supervisor ran {interim.get('supervisor_run')} ago, "
                 f"verdict {interim.get('verdict_age')} old, carriesWatcher={interim.get('carries_watcher')})",
                 f"interim task decided within {fmt_age(t['interim_stale_sec'])}",
                 "" if st == GREEN else "DoxxedSystemHealthWatcher task is not running; the watcher-of-watchers is blind",
                 fields={k: interim.get(k) for k in ("decision", "at", "supervisor_run", "verdict_age",
                                                    "carries_watcher")})


def delivery_check(state, check, t):
    d = state.get("watcher_delivery") or {}
    result = d.get("fly_banner")
    fails = int(d.get("fly_banner_fails") or 0)
    if result is None:
        return check("watcher.delivery", "watcher", SKIP, "no banner push recorded yet",
                     "Fly banner push succeeds")
    st = RED if fails >= t["banner_fail_red"] else AMBER if fails >= t["banner_fail_amber"] else GREEN
    return check("watcher.delivery", "watcher", st,
                 f"last Fly banner push: {str(result)[:120]}; {fails} consecutive failures; "
                 f"last toast push={d.get('pushed')}",
                 f"< {int(t['banner_fail_amber'])} consecutive banner push failures (RED {int(t['banner_fail_red'])})",
                 "" if st == GREEN else "dashboard banner/alarm history on Fly is going stale",
                 fields={"fly_banner": result, "fly_banner_fails": fails, "pushed": d.get("pushed")})


def record_delivery(state: dict[str, Any], banner: str, delivery: Mapping[str, Any] | None) -> None:
    d = state.setdefault("watcher_delivery", {})
    ok = isinstance(banner, str) and (banner.startswith("ok") or banner == "disabled")
    d["fly_banner"] = banner
    d["fly_banner_fails"] = 0 if ok else int(d.get("fly_banner_fails") or 0) + 1
    d["pushed"] = (delivery or {}).get("pushed")


def fly_copy_check(published, local_ts, now, check, fmt_age, parse_ts, t):
    if not isinstance(published, Mapping):
        return check("watcher.fly_copy", "watcher", SKIP, "Fly-published report not fetched",
                     "Fly copy lags the local verdict by < 15m")
    pub_ts = parse_ts(published.get("generated_at"))
    if pub_ts is None and published.get("age_sec") is not None:
        pub_ts = now - float(published["age_sec"])
    lag = None if pub_ts is None or local_ts is None else max(0.0, local_ts - pub_ts)
    st = SKIP if lag is None else RED if lag > t["fly_copy_red_sec"] else AMBER if lag > t["fly_copy_amber_sec"] \
        else GREEN
    return check("watcher.fly_copy", "watcher", st,
                 f"Fly copy generated {fmt_age(_age(now, pub_ts))} ago; lag behind previous local verdict "
                 f"{fmt_age(lag)}",
                 f"lag < {fmt_age(t['fly_copy_amber_sec'])} (RED {fmt_age(t['fly_copy_red_sec'])})",
                 "" if st in (GREEN, SKIP) else "Fly dashboard shows an old system-health verdict",
                 fields={"lag_sec": lag, "published_verdict": published.get("verdict")})


def wall_check(lines, now, check, fmt_age, parse_ts, t):
    if lines is None:
        return check("coordination.wall", "coordination", AMBER, "WALL-STATUS-FLY.md unreadable",
                     "WALL readable and well-formed")
    entries = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    recent = entries[-int(t["wall_recent_lines"]):]
    bad = [ln for ln in recent if not WALL_LINE.match(ln)]
    last_ts = None
    for ln in reversed(entries):
        last_ts = parse_ts(ln.split("|", 1)[0].strip())
        if last_ts:
            break
    age = _age(now, last_ts)
    st = AMBER if bad or age is None or age > t["wall_quiet_amber_sec"] else GREEN
    return check("coordination.wall", "coordination", st,
                 f"last entry {fmt_age(age)} ago; {len(bad)} malformed of last {len(recent)}",
                 "every recent line is 'timestamp | owner | msg | STATE'",
                 "; ".join(b[:80] for b in bad[:3]),
                 fields={"last_entry_age_sec": age, "malformed_recent": len(bad), "recent": len(recent)})


def adhoc_check(adhoc, check):
    if not isinstance(adhoc, Mapping):
        return check("laptop.adhoc_processes", "laptop", SKIP, "process probe unavailable",
                     "ad-hoc listeners/scripts are visible")
    ls, sc = adhoc.get("listeners") or [], adhoc.get("scripts") or []
    return check("laptop.adhoc_processes", "laptop", GREEN,
                 f"listeners {[(x['port'], x['pid']) for x in ls]}; ad-hoc scripts {[(x['script'], x['pid']) for x in sc]}",
                 "visibility only (owner: Danish); never affects the verdict",
                 fields={"listeners": ls, "scripts": sc})


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- finalize

def dedupe(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per check id; the worst status wins, first position kept."""
    order: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for c in checks:
        i = order.get(c["id"])
        if i is None:
            order[c["id"]] = len(out)
            out.append(c)
        elif RANK.get(c["status"], -1) > RANK.get(out[i]["status"], -1):
            out[i] = c
    return out


def apply_acks(checks: list[dict[str, Any]], acks: Any, now: float,
               parse_ts: Callable[[Any], float | None]) -> list[dict[str, Any]]:
    """Mark AMBER checks with an unexpired ack. RED is never ackable."""
    rows = acks.get("acks") if isinstance(acks, Mapping) else None
    by_id: dict[str, Mapping[str, Any]] = {}
    for a in rows or []:
        if isinstance(a, Mapping) and a.get("check"):
            until = parse_ts(a.get("until"))
            if until and until > now:
                by_id[str(a["check"])] = {"until": a.get("until"), "by": a.get("by"), "reason": a.get("reason")}
    acked = []
    for c in checks:
        a = by_id.get(c["id"])
        if not a:
            continue
        if c["status"] == AMBER:
            c["acked"] = dict(a)
            acked.append({"id": c["id"], **a})
        elif c["status"] == RED:
            c["ack_ignored"] = "RED cannot be acked"
    return acked


def flapping(checks: list[dict[str, Any]], state: dict[str, Any], check: CheckFn,
             thresholds: Mapping[str, float] | None = None) -> dict[str, Any]:
    t = {**META_THRESHOLDS, **(thresholds or {})}
    hist = state.setdefault("status_history", {})
    window = int(t["flap_window"])
    flappers = []
    present = set()
    for c in checks:
        present.add(c["id"])
        h = (hist.get(c["id"]) or [])[-(window - 1):] + [c["status"]]
        hist[c["id"]] = h
        rated = [s for s in h if s != SKIP]
        flips = sum(1 for a, b in zip(rated, rated[1:]) if a != b)
        if flips >= t["flap_transitions"]:
            c["flapping"] = True
            flappers.append(f"{c['id']}({flips})")
    for cid in [k for k in hist if k not in present]:
        hist.pop(cid, None)
    return check("watcher.flapping", "watcher", AMBER if flappers else GREEN,
                 f"{len(flappers)} flapping checks {flappers[:8]}",
                 f"< {int(t['flap_transitions'])} status changes in the last {window} ticks",
                 "" if not flappers else "threshold too tight or an intermittent source; tune before it trains people "
                                         "to ignore alarms",
                 fields={"flapping": flappers})
