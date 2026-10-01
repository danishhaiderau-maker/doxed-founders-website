"""48-hour unattended-operation proof for the paper runtime and laptop chain.

``--start`` records T0 and the baseline identity. Every supervisor tick then
calls ``--check``; a row is appended to the receipt at most every 30 minutes,
built only from snapshots the supervisor tick already collected (Fly runtime,
Fly segment head, relay status, guarded deploy runs, analyzer run status and
the monitor's active alerts). Missing or stale evidence fails the row: nothing is marked healthy
without an observation. At T0+48h the verdict is written once.

This script never calls the bot, the relay or Fly; it only reads local files.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fly_monitor_rules import DEPLOY_OWNER, cadence_findings  # noqa: E402

ROW_SCHEMA = "unattended_proof_row_v1"
START_SCHEMA = "unattended_proof_start_v1"
VERDICT_SCHEMA = "unattended_proof_verdict_v1"
ACTIVE_FILE = "active.json"
MANUAL_JOURNAL = "manual-interventions.jsonl"
WINDOW_HOURS = 48.0
CHECK_INTERVAL_SEC = 30 * 60.0
CHECK_DUE_SLACK_SEC = 150.0
MAX_ROW_GAP_SEC = 45 * 60.0
SNAPSHOT_MAX_AGE_SEC = 15 * 60.0
ANALYZER_MAX_AGE_SEC = 45 * 60.0
WS_MAX_AGE_SEC = 60.0
SEGMENT_ACK_TOLERANCE_SEQ = 30
MANUAL_PAUSE_OWNERS = frozenset({"ADMIN_MANUAL", "OPERATOR", "MANUAL"})
DEFAULT_RECEIPT_DIR = r"C:\DoxxedCrypto\btc-v31-current\diagnostics"

RUNTIME_SNAPSHOT = "fly_runtime_snapshot_v1.json"
HEAD_SNAPSHOT = "fly_segment_head_snapshot_v1.json"
RELAY_SNAPSHOT = "relay_status_snapshot_v1.json"
ANALYZER_STATUS = "analyzer-run.status.json"
ACTIVE_ALERTS = str(Path("alerts") / "active-alerts.json")
DEPLOY_RUNS_SNAPSHOT = "fly_deploy_runs_snapshot_v1.json"

# A DEPLOY_MAINTENANCE pause is allowed only while a guarded deploy workflow
# run (fly-bot-deploy.yml) was active at the observation, for at most this long,
# and only if that run then concludes success (the workflow itself asserts
# "Paper ACTIVE ... 2 advancing AI cycles" before succeeding).
ALLOWED_GUARDED_DEPLOY = "ALLOWED_GUARDED_DEPLOY"
BOUNDARY_STATUSES = frozenset({"BOUNDARY", ALLOWED_GUARDED_DEPLOY})
GUARDED_DEPLOY_MAX_PAUSE_SEC = 45 * 60.0
DEPLOY_RUN_SLACK_SEC = 120.0
ACTIVE_RUN_STATUSES = frozenset({"queued", "requested", "waiting", "pending", "in_progress"})


def parse_utc(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e12 else float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # PowerShell round-trip timestamps carry 7 fractional digits.
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())
        zone = tail[len(digits):]
        text = f"{head}.{digits[:6]}{zone}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def assert_not_onedrive(path: Path) -> None:
    if "onedrive" in str(path).lower():
        raise SystemExit(f"REFUSED: {path} is under OneDrive")


def _check(ok: bool | None, detail: str, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "detail": detail, **extra}


def _snapshot_age(snapshot: Mapping[str, Any] | None, now: float) -> float | None:
    observed = parse_utc((snapshot or {}).get("observedAt"))
    return None if observed is None else max(0.0, now - observed)


def _fresh_snapshot(snapshot: Mapping[str, Any] | None, now: float, name: str) -> tuple[bool, str]:
    if not isinstance(snapshot, Mapping):
        return False, f"{name} snapshot missing"
    age = _snapshot_age(snapshot, now)
    if age is None:
        return False, f"{name} snapshot has no observedAt"
    if age > SNAPSHOT_MAX_AGE_SEC:
        return False, f"{name} snapshot is {age / 60:.0f} min old (> {SNAPSHOT_MAX_AGE_SEC / 60:.0f} min)"
    if snapshot.get("ok") is not True:
        return False, f"{name} snapshot not ok (error={snapshot.get('error')!r})"
    return True, ""


def _deploy_runs(snapshot: Mapping[str, Any] | None, now: float) -> tuple[list[Mapping[str, Any]] | None, str]:
    ok, err = _fresh_snapshot(snapshot, now, "deploy runs")
    if not ok:
        return None, err
    runs: list[Mapping[str, Any]] = []
    for entry in (snapshot or {}).get("runs") or []:
        # Windows PowerShell 5 may serialize the array as {"value": [...], "Count": n}.
        nested = entry.get("value") if isinstance(entry, Mapping) and "databaseId" not in entry else None
        for run in nested if isinstance(nested, list) else [entry]:
            if isinstance(run, Mapping):
                runs.append(run)
    return runs, ""


def attribute_deploy_run(runs: list[Mapping[str, Any]], observed_at: float, now: float) -> Mapping[str, Any] | None:
    """The guarded deploy run that was active when the runtime was observed, if any."""
    for run in runs:
        created = parse_utc(run.get("createdAt"))
        if created is None:
            continue
        status = str(run.get("status") or "").lower()
        if status in ACTIVE_RUN_STATUSES:
            ended = now
        elif status == "completed":
            ended = parse_utc(run.get("updatedAt"))
            if ended is None:
                continue
        else:
            continue
        if created - DEPLOY_RUN_SLACK_SEC <= observed_at <= ended + DEPLOY_RUN_SLACK_SEC:
            return run
    return None


def _run_by_id(runs: list[Mapping[str, Any]] | None, run_id: Any) -> Mapping[str, Any] | None:
    return next((r for r in runs or [] if str(r.get("databaseId")) == str(run_id)), None)


def evaluate_row(*, runtime: Mapping[str, Any] | None, head: Mapping[str, Any] | None,
                 relay: Mapping[str, Any] | None, analyzer: Mapping[str, Any] | None,
                 alerts: Mapping[str, Any] | None, baseline: Mapping[str, Any],
                 previous: Mapping[str, Any] | None, manual_entries: list[Mapping[str, Any]],
                 now: float, deploy_runs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One proof row. Every check is True, False or None (no evidence); None fails the row."""
    checks: dict[str, dict[str, Any]] = {}
    observed: dict[str, Any] = {}
    runtime_ok, runtime_err = _fresh_snapshot(runtime, now, "Fly runtime")
    rt = runtime if runtime_ok else {}
    prev_obs = (previous or {}).get("observed") or {}
    prev_boundary = (previous or {}).get("status") in BOUNDARY_STATUSES
    runs, runs_err = _deploy_runs(deploy_runs, now)

    # Paper running. A DEPLOY_MAINTENANCE pause is ALLOWED_GUARDED_DEPLOY only
    # when a guarded deploy run was active at the observation and the pause is
    # within GUARDED_DEPLOY_MAX_PAUSE_SEC.
    paused = rt.get("execution_paused")
    owner = rt.get("pause_owner") or ""
    observed.update({"execution_paused": paused, "pause_owner": owner or None,
                     "git_rev": rt.get("git_rev"), "tile_registry_signature": rt.get("tile_registry_signature")})
    boundary = False
    deploy_pause = runtime_ok and paused is True and owner == DEPLOY_OWNER
    deploy_detail = ""
    if not runtime_ok:
        checks["paper_running"] = _check(None, runtime_err)
    elif paused is False:
        checks["paper_running"] = _check(True, "paper running (execution_paused=false)")
    elif deploy_pause:
        observed_at = parse_utc(rt.get("observedAt")) or now
        since = prev_obs.get("deploy_pause_since") if prev_boundary else None
        since = float(since) if since is not None else observed_at
        observed["deploy_pause_since"] = since
        paused_min = (observed_at - since) / 60
        run = attribute_deploy_run(runs, observed_at, now) if runs is not None else None
        if run is not None:
            observed["deploy_run_id"] = run.get("databaseId")
            deploy_detail = (f"run {run.get('databaseId')} {run.get('status')}"
                             f"{'/' + str(run.get('conclusion')) if run.get('conclusion') else ''}")
        if runs is None:
            checks["paper_running"] = _check(None, f"{DEPLOY_OWNER} pause cannot be attributed: {runs_err}")
        elif run is None:
            checks["paper_running"] = _check(False, f"{DEPLOY_OWNER} pause with no guarded deploy run active at the observation")
        elif str(run.get("status")).lower() == "completed" and str(run.get("conclusion")).lower() != "success":
            checks["paper_running"] = _check(False, f"paused by {DEPLOY_OWNER}; guarded deploy {deploy_detail} did not succeed")
        elif observed_at - since > GUARDED_DEPLOY_MAX_PAUSE_SEC:
            checks["paper_running"] = _check(False, f"paused by {DEPLOY_OWNER} for {paused_min:.0f} min "
                                                    f"(> {GUARDED_DEPLOY_MAX_PAUSE_SEC / 60:.0f} min) during {deploy_detail}")
        else:
            boundary = True
            checks["paper_running"] = _check(True, f"{ALLOWED_GUARDED_DEPLOY}: paused by {DEPLOY_OWNER} during "
                                                   f"guarded deploy {deploy_detail} ({paused_min:.0f} min)", boundary=True)
    else:
        checks["paper_running"] = _check(False, f"paper paused (owner={owner or 'none'!r}, reason={rt.get('execution_reason')!r})")

    # Every runtime-registered tile toggled ON.
    lanes = [str(x) for x in rt.get("active_tile_lanes") or []]
    toggles = rt.get("research_lane_enabled")
    observed["tiles_on"] = sorted(k for k, v in (toggles or {}).items() if v is True) if isinstance(toggles, Mapping) else None
    if not runtime_ok:
        checks["tiles_all_on"] = _check(None, runtime_err)
    elif not lanes:
        checks["tiles_all_on"] = _check(None, "runtime roster (active_tiles) not observed")
    elif not isinstance(toggles, Mapping):
        checks["tiles_all_on"] = _check(None, f"tile toggles not observed (error={rt.get('toggles_error')!r})")
    else:
        off = [lane for lane in lanes if toggles.get(lane) is not True]
        checks["tiles_all_on"] = (_check(True, f"{len(lanes)}/{len(lanes)} tiles ON") if not off
                                  else _check(False, f"tiles OFF: {', '.join(off)}"))

    # AI advancing: runtime says progressing, cadence rules clean, and a new cycle since the last row.
    progress = rt.get("strategy_progress") if isinstance(rt.get("strategy_progress"), Mapping) else {}
    cycle = progress.get("scheduled_ai_cycle") if isinstance(progress.get("scheduled_ai_cycle"), Mapping) else {}
    completed_ts = cycle.get("completed_ts")
    observed["ai_cycle_completed_ts"] = completed_ts
    observed["ai_age_sec"] = progress.get("ai_age_sec")
    if not runtime_ok:
        checks["ai_advancing"] = _check(None, runtime_err)
    elif boundary:
        checks["ai_advancing"] = _check(True, "not expected during a guarded deploy boundary", boundary=True)
    elif not progress:
        checks["ai_advancing"] = _check(None, "strategy_progress not observed")
    else:
        observed_at = parse_utc(rt.get("observedAt")) or now
        findings = cadence_findings({"strategy_progress": dict(progress)}, paused=False, now=observed_at)
        stalled = (prev_obs.get("ai_cycle_completed_ts") is not None and completed_ts is not None
                   and completed_ts == prev_obs.get("ai_cycle_completed_ts")
                   and cycle.get("last_poll_entry_eligible") is not False)
        if progress.get("ai_progressing") is not True:
            checks["ai_advancing"] = _check(False, f"ai_progressing={progress.get('ai_progressing')!r}")
        elif findings:
            checks["ai_advancing"] = _check(False, "; ".join(findings.values()))
        elif stalled:
            checks["ai_advancing"] = _check(False, "no AI cycle completed since the previous proof row")
        else:
            checks["ai_advancing"] = _check(True, f"AI progressing; last call {float(progress.get('ai_age_sec') or 0):.0f}s ago")

    # WebSocket ticks fresh.
    ws_age = progress.get("ws_age_sec", rt.get("ws_age"))
    observed["ws_age_sec"] = ws_age
    if not runtime_ok:
        checks["ws_fresh"] = _check(None, runtime_err)
    elif ws_age is None:
        checks["ws_fresh"] = _check(None, "ws age not observed")
    elif progress.get("ws_progressing") is False or float(ws_age) > WS_MAX_AGE_SEC:
        checks["ws_fresh"] = _check(False, f"ws tick {float(ws_age):.0f}s old (limit {WS_MAX_AGE_SEC:.0f}s), progressing={progress.get('ws_progressing')!r}")
    else:
        checks["ws_fresh"] = _check(True, f"ws tick {float(ws_age):.1f}s old")

    # Segments published == acked within tolerance; pruning must stay off.
    head_ok, head_err = _fresh_snapshot(head, now, "Fly segment head")
    shipped, acked = (head or {}).get("shipped_seq"), (head or {}).get("laptop_acked_seq")
    observed.update({"shipped_seq": shipped, "laptop_acked_seq": acked,
                     "pruning_enabled": (head or {}).get("pruning_enabled")})
    if not head_ok:
        checks["segments_acked"] = _check(None, head_err)
    elif shipped is None or acked is None:
        checks["segments_acked"] = _check(None, "shipped/acked seq not observed")
    elif (head or {}).get("pruning_enabled") is True:
        checks["segments_acked"] = _check(False, "segment pruning is enabled")
    elif int(shipped) - int(acked) > SEGMENT_ACK_TOLERANCE_SEQ:
        checks["segments_acked"] = _check(False, f"published {shipped} vs acked {acked} (lag > {SEGMENT_ACK_TOLERANCE_SEQ})")
    elif prev_obs.get("laptop_acked_seq") is not None and int(shipped) > int(prev_obs.get("shipped_seq") or 0) \
            and int(acked) <= int(prev_obs["laptop_acked_seq"]):
        checks["segments_acked"] = _check(False, f"acked seq stuck at {acked} while published advanced to {shipped}")
    else:
        checks["segments_acked"] = _check(True, f"published {shipped} / acked {acked} (lag {int(shipped) - int(acked)})")

    # Analyzer generation fresh.
    generated = parse_utc((analyzer or {}).get("lastCompletedGenerationAt") or (analyzer or {}).get("lastSuccessAt"))
    observed["analyzer_generation_at"] = iso(generated) if generated else None
    if generated is None:
        checks["analyzer_fresh"] = _check(None, "no completed analyzer generation recorded")
    elif now - generated > ANALYZER_MAX_AGE_SEC:
        checks["analyzer_fresh"] = _check(False, f"analyzer generation {(now - generated) / 60:.0f} min old (> {ANALYZER_MAX_AGE_SEC / 60:.0f})")
    else:
        checks["analyzer_fresh"] = _check(True, f"analyzer generation {(now - generated) / 60:.0f} min old")

    # No critical alarms in the monitor's current alert set.
    checked = parse_utc((alerts or {}).get("checkedAt"))
    critical = [a for a in (alerts or {}).get("alerts") or [] if isinstance(a, Mapping) and a.get("severity") == "critical"]
    observed["critical_alarms"] = [a.get("code") for a in critical]
    if checked is None:
        checks["no_critical_alarms"] = _check(None, "monitor alert set not found")
    elif now - checked > SNAPSHOT_MAX_AGE_SEC:
        checks["no_critical_alarms"] = _check(None, f"monitor alert set {(now - checked) / 60:.0f} min old")
    elif critical:
        checks["no_critical_alarms"] = _check(False, "critical: " + ", ".join(str(a.get("code")) for a in critical))
    else:
        checks["no_critical_alarms"] = _check(True, "no critical alarms")

    # Live copy stays disarmed.
    relay_ok, relay_err = _fresh_snapshot(relay, now, "relay status")
    mode = str((relay or {}).get("relayExecutionMode") or "").upper()
    observed.update({"live_armed": rt.get("live_armed"), "bitfinex_live_enabled": rt.get("bitfinex_live_enabled"),
                     "relay_mode": mode or None})
    if not runtime_ok or not relay_ok:
        checks["live_disarmed"] = _check(None, runtime_err or relay_err)
    elif rt.get("live_armed") is not False or rt.get("bitfinex_live_enabled") is not False:
        checks["live_disarmed"] = _check(False, f"live_armed={rt.get('live_armed')!r} bitfinex_live_enabled={rt.get('bitfinex_live_enabled')!r}")
    elif (relay or {}).get("relayArmedAt") or mode not in {"PAUSED", "DISARMED", "OFF"}:
        checks["live_disarmed"] = _check(False, f"relay mode {mode or 'unknown'}")
    else:
        checks["live_disarmed"] = _check(True, f"live disarmed; relay {mode}")

    # No manual intervention: operator pause, toggle drift from baseline, or a journalled action.
    reasons = []
    # The guarded workflow pauses with manual_admin_pause=true under its own
    # owner; that pause is judged by paper_running's deploy attribution above.
    if not deploy_pause and (rt.get("manual_admin_pause") is True
                             or (paused is True and owner.upper() in MANUAL_PAUSE_OWNERS)):
        reasons.append(f"manual pause (owner={owner!r})")
    if deploy_pause and not boundary:
        reasons.append(f"{DEPLOY_OWNER} pause not allowed as a guarded deploy")
    # After an allowed boundary the attributed run must conclude success, i.e.
    # the workflow proved Paper ACTIVE with 2 advancing AI cycles.
    pending = observed.get("deploy_run_id") or prev_obs.get("pending_deploy_run_id") \
        or (prev_obs.get("deploy_run_id") if prev_boundary else None)
    if pending is not None and not deploy_pause:
        run = _run_by_id(runs, pending)
        status = str((run or {}).get("status") or "").lower()
        conclusion = str((run or {}).get("conclusion") or "").lower()
        if status == "completed" and conclusion == "success":
            deploy_detail = f"guarded deploy run {pending} resumed paper and succeeded (Paper ACTIVE + 2 advancing AI cycles)"
        elif status == "completed":
            reasons.append(f"guarded deploy run {pending} concluded {conclusion or 'unknown'} after the boundary")
        else:
            observed["pending_deploy_run_id"] = pending
    elif deploy_pause and boundary:
        observed["pending_deploy_run_id"] = observed.get("deploy_run_id")
    base_rev, rev = baseline.get("git_rev"), rt.get("git_rev")
    if runtime_ok and base_rev and rev and rev != base_rev:
        observed["deploy_observed"] = True
    if manual_entries:
        reasons.append(f"{len(manual_entries)} manual intervention(s) journalled")
    if not runtime_ok:
        checks["no_manual_intervention"] = _check(None, runtime_err)
    elif reasons:
        checks["no_manual_intervention"] = _check(False, "; ".join(reasons))
    else:
        detail = "none observed"
        if boundary:
            detail = f"{ALLOWED_GUARDED_DEPLOY} ({deploy_detail})"
        elif deploy_detail:
            detail += f" ({deploy_detail})"
        if observed.get("deploy_observed"):
            detail += f" (Fly revision {base_rev} -> {rev}: deploy boundary, must be the guarded workflow)"
        checks["no_manual_intervention"] = _check(True, detail)

    failed = [name for name, c in checks.items() if c["ok"] is not True]
    status = "FAIL" if failed else ALLOWED_GUARDED_DEPLOY if boundary else "PASS"
    return {"schema": ROW_SCHEMA, "kind": "ROW", "at": iso(now), "status": status,
            "failed_checks": failed, "checks": checks, "observed": observed}


def verdict(rows: list[Mapping[str, Any]], *, t0: float, ends_at: float, now: float) -> dict[str, Any]:
    """Final (or running) verdict. PASS needs full coverage and no FAIL rows."""
    reasons = []
    stamps = [parse_utc(r.get("at")) for r in rows]
    stamps = [s for s in stamps if s is not None and t0 <= s <= ends_at + CHECK_INTERVAL_SEC]
    fails = [r for r in rows if r.get("status") == "FAIL"]
    if fails:
        reasons.append(f"{len(fails)} FAIL row(s); first at {fails[0].get('at')}: {', '.join(fails[0].get('failed_checks') or [])}")
    edges = [t0, *stamps, min(now, ends_at)]
    gaps = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > MAX_ROW_GAP_SEC]
    if gaps:
        a, b = gaps[0]
        reasons.append(f"{len(gaps)} coverage gap(s) > {MAX_ROW_GAP_SEC / 60:.0f} min; first {iso(a)} -> {iso(b)}")
    run_start = None
    for row in rows:
        at = parse_utc(row.get("at"))
        if row.get("status") in BOUNDARY_STATUSES:
            run_start = run_start if run_start is not None else at
            if at is not None and run_start is not None and at - run_start >= GUARDED_DEPLOY_MAX_PAUSE_SEC:
                reasons.append(f"deploy boundary not resumed within {GUARDED_DEPLOY_MAX_PAUSE_SEC / 60:.0f} min (since {iso(run_start)})")
                break
        else:
            run_start = None
    complete = now >= ends_at
    if complete:
        result = "PASS" if not reasons else "FAIL"
    else:
        result = "IN_PROGRESS" if not reasons else "FAILING"
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row.get("status"))] = counts.get(str(row.get("status")), 0) + 1
    return {"schema": VERDICT_SCHEMA, "kind": "VERDICT" if complete else "STATUS", "result": result,
            "t0": iso(t0), "ends_at": iso(ends_at), "evaluated_at": iso(now), "rows": len(rows),
            "row_status_counts": counts, "reasons": reasons}


def _manual_entries(state_dir: Path, since: float) -> list[dict[str, Any]]:
    entries = []
    for row in read_rows(state_dir / MANUAL_JOURNAL):
        at = parse_utc(row.get("at"))
        if at is not None and at >= since:
            entries.append(row)
    return entries


def start(state_dir: Path, receipt_dir: Path, now: float, *, force: bool = False, reason: str = "") -> dict[str, Any]:
    assert_not_onedrive(state_dir)
    assert_not_onedrive(receipt_dir)
    active_path = state_dir / "unattended-proof" / ACTIVE_FILE
    active = read_json(active_path)
    running = isinstance(active, Mapping) and not active.get("verdict")
    if running and not force:
        raise SystemExit(f"REFUSED: proof window already running since {active.get('t0')}")
    if running and not reason.strip():
        raise SystemExit("REFUSED: replacing a running window requires --reason")
    runtime = read_json(state_dir / RUNTIME_SNAPSHOT) or {}
    ok, err = _fresh_snapshot(runtime, now, "Fly runtime")
    if not ok:
        raise SystemExit(f"REFUSED: cannot record a baseline: {err}")
    if running:
        old_receipt = Path(str(active.get("receipt")))
        old_rows = [r for r in read_rows(old_receipt) if r.get("kind") == "ROW"]
        superseded = {
            "schema": VERDICT_SCHEMA, "kind": "VERDICT", "result": "SUPERSEDED", "at": iso(now),
            "t0": active.get("t0"), "ends_at": active.get("ends_at"), "reason": reason.strip(),
            "rows": len(old_rows), "fail_rows": sum(1 for r in old_rows if r.get("status") == "FAIL"),
        }
        with old_receipt.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(superseded, sort_keys=True) + "\n")
        write_json_atomic(old_receipt.with_suffix(".verdict.json"), superseded)
    receipt = receipt_dir / f"unattended-proof-{stamp(now)}.jsonl"
    record = {
        "schema": START_SCHEMA, "kind": "START", "t0": iso(now), "ends_at": iso(now + WINDOW_HOURS * 3600),
        "window_hours": WINDOW_HOURS, "check_interval_min": CHECK_INTERVAL_SEC / 60,
        "receipt": str(receipt),
        "baseline": {k: runtime.get(k) for k in ("git_rev", "tile_registry_signature", "active_tile_lanes",
                                                 "research_lane_enabled", "execution_paused", "live_armed",
                                                 "bitfinex_live_enabled")},
        "thresholds": {"analyzer_max_age_min": ANALYZER_MAX_AGE_SEC / 60, "ws_max_age_sec": WS_MAX_AGE_SEC,
                       "segment_ack_tolerance_seq": SEGMENT_ACK_TOLERANCE_SEQ,
                       "snapshot_max_age_min": SNAPSHOT_MAX_AGE_SEC / 60, "max_row_gap_min": MAX_ROW_GAP_SEC / 60,
                       "deploy_boundary_max_min": GUARDED_DEPLOY_MAX_PAUSE_SEC / 60},
        "manual_intervention_journal": str(state_dir / MANUAL_JOURNAL),
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    write_json_atomic(active_path, record)
    return record


def check(state_dir: Path, now: float, *, force: bool = False) -> dict[str, Any]:
    active_path = state_dir / "unattended-proof" / ACTIVE_FILE
    active = read_json(active_path)
    if not isinstance(active, Mapping):
        return {"result": "NO_ACTIVE_WINDOW"}
    if active.get("verdict"):
        return {"result": "COMPLETE", "verdict": active["verdict"]}
    t0, ends_at = parse_utc(active.get("t0")), parse_utc(active.get("ends_at"))
    receipt = Path(str(active.get("receipt")))
    rows = [r for r in read_rows(receipt) if r.get("kind") == "ROW"]
    last = parse_utc(rows[-1].get("at")) if rows else None
    due = force or last is None or now - last >= CHECK_INTERVAL_SEC - CHECK_DUE_SLACK_SEC
    written = None
    if due and now <= ends_at + CHECK_INTERVAL_SEC:
        written = evaluate_row(
            runtime=read_json(state_dir / RUNTIME_SNAPSHOT), head=read_json(state_dir / HEAD_SNAPSHOT),
            relay=read_json(state_dir / RELAY_SNAPSHOT), analyzer=read_json(state_dir / ANALYZER_STATUS),
            alerts=read_json(state_dir / ACTIVE_ALERTS), baseline=active.get("baseline") or {},
            previous=rows[-1] if rows else None, manual_entries=_manual_entries(state_dir, t0), now=now,
            deploy_runs=read_json(state_dir / DEPLOY_RUNS_SNAPSHOT))
        with receipt.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(written, sort_keys=True) + "\n")
        rows.append(written)
    summary = verdict(rows, t0=t0, ends_at=ends_at, now=now)
    updated = {**active, "status": summary}
    if summary["kind"] == "VERDICT":
        with receipt.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, sort_keys=True) + "\n")
        write_json_atomic(receipt.with_suffix(".verdict.json"), summary)
        updated["verdict"] = summary
    write_json_atomic(active_path, updated)
    return {"result": summary["result"], "row": written["status"] if written else None, "rows": len(rows)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", default=os.environ.get("DOXXED_LAPTOP_CHAIN_STATE") or r"C:\DoxxedCrypto\laptop-chain")
    parser.add_argument("--receipt-dir", default=os.environ.get("DOXXED_PROOF_RECEIPT_DIR") or DEFAULT_RECEIPT_DIR)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true", help="start: replace a running window; check: write a row now")
    parser.add_argument("--reason", default="", help="start --force: why the running window is superseded")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc).timestamp()
    state_dir = Path(args.state_dir)
    try:
        if args.start:
            record = start(state_dir, Path(args.receipt_dir), now, force=args.force, reason=args.reason)
            print(f"PROOF_STARTED t0={record['t0']} receipt={record['receipt']}")
        else:
            result = check(state_dir, now, force=args.force)
            print("PROOF " + " ".join(f"{k}={v}" for k, v in result.items() if k != "verdict"))
    except SystemExit:
        raise
    except Exception as exc:  # never fail the supervisor tick
        print(f"PROOF_ERROR {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
