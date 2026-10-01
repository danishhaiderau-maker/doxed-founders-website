"""Pure alert rules for disk, stuck deploys, cadence and transfer lag.

Each function maps already-probed /health and /ready payloads to findings
(condition key -> message). Deduplication and timing live in
``fly_monitor_alerts``; these rules only decide whether a condition holds now.
Missing fields (an older deployed revision) never produce a finding.
"""

from __future__ import annotations

from typing import Any, Mapping

GIB = float(1024 ** 3)
DISK_WARN_PCT = 70.0
DISK_CRITICAL_PCT = 85.0
DEPLOY_STUCK_SEC = 60 * 60.0
EVAL_STALE_SEC = 20 * 60.0
AI_STALE_MIN_SEC = 45 * 60.0
SCHEDULER_POLL_STALE_SEC = 10 * 60.0
SEGMENT_STATUS_STALE_SEC = 30 * 60.0
SEGMENT_SEQ_LAG = 36
LAPTOP_SILENT_SEC = 2 * 3600.0
DEPLOY_OWNER = "DEPLOY_MAINTENANCE"


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def disk_findings(health: Mapping[str, Any] | None) -> dict[str, str]:
    volume = (health or {}).get("volume")
    if not isinstance(volume, dict):
        return {}
    used_pct = _num(volume.get("used_pct"))
    if used_pct is None:
        return {}
    detail = f"Fly volume {used_pct:.1f}% used"
    used, total, free = (_num(volume.get(k)) for k in ("used_bytes", "total_bytes", "free_bytes"))
    if used is not None and total is not None:
        detail += f" ({used / GIB:.1f}/{total / GIB:.1f} GiB"
        detail += f", {free / GIB:.1f} GiB free)" if free is not None else ")"
    growth = _num(volume.get("growth_bytes_per_hour"))
    if growth is not None:
        detail += f"; growing {growth / 2**20:.0f} MiB/h"
    hours = _num(volume.get("hours_to_full"))
    if hours is not None:
        detail += f"; ~{hours:.0f}h to full"
    findings: dict[str, str] = {}
    if used_pct >= DISK_WARN_PCT:
        findings["disk_warn"] = f"{detail} (>= {DISK_WARN_PCT:.0f}%)"
    if used_pct >= DISK_CRITICAL_PCT:
        findings["disk_critical"] = f"URGENT: {detail} (>= {DISK_CRITICAL_PCT:.0f}%)"
    return findings


def track_deploy_pause(state: dict[str, Any], health: Mapping[str, Any] | None, now: float) -> float:
    """Continuous DEPLOY_MAINTENANCE pause duration; unknown health keeps the clock."""
    if health is None:
        since = state.get("deploy_pause_since")
        return now - since if since is not None else 0.0
    if health.get("pause_owner") != DEPLOY_OWNER:
        state["deploy_pause_since"] = None
        return 0.0
    if state.get("deploy_pause_since") is None:
        state["deploy_pause_since"] = now
    return now - state["deploy_pause_since"]


def deploy_stuck_findings(paused_for: float, health: Mapping[str, Any] | None) -> dict[str, str]:
    if paused_for < DEPLOY_STUCK_SEC:
        return {}
    reason = (health or {}).get("execution_reason")
    return {
        "deploy_stuck": (
            f"paper paused by {DEPLOY_OWNER} for {paused_for / 60:.0f} min "
            f"(> {DEPLOY_STUCK_SEC / 60:.0f} min): a guarded deploy is stuck or did not resume paper "
            f"(reason={reason!r})"
        )
    }


def cadence_findings(ready: Mapping[str, Any] | None, *, paused: bool | None, now: float) -> dict[str, str]:
    """AI/evaluation cadence stale beyond thresholds while paper should be running."""
    if paused is not False or not isinstance(ready, dict):
        return {}
    progress = ready.get("strategy_progress")
    if not isinstance(progress, dict):
        return {}
    startup = _num(progress.get("process_startup_age_sec"))
    if startup is not None and startup < EVAL_STALE_SEC:
        return {}
    cycle = progress.get("scheduled_ai_cycle") if isinstance(progress.get("scheduled_ai_cycle"), dict) else {}
    findings: dict[str, str] = {}

    # Failing provider calls block entries themselves, so this must not hide
    # behind the entry-ineligible early return below.
    provider = progress.get("ai_provider") if isinstance(progress.get("ai_provider"), dict) else {}
    if provider.get("alert"):
        findings["ai_no_success"] = (
            f"no successful DeepSeek response since {provider.get('last_ai_success_at') or 'boot'} "
            f"({int(provider.get('consecutive_failures') or 0)} consecutive failures, "
            f"last_error_class={provider.get('last_error_class')!r})"
        )

    poll_ts = _num(cycle.get("last_poll_ts"))
    poll_age = max(0.0, now - poll_ts) if poll_ts else None
    if poll_age is not None and poll_age > SCHEDULER_POLL_STALE_SEC:
        findings["eval_stale"] = (
            f"AI scheduler has not polled for {poll_age / 60:.0f} min "
            f"(> {SCHEDULER_POLL_STALE_SEC / 60:.0f} min; stage={cycle.get('stage')!r})"
        )
        return findings
    # A recent poll that proves entries are blocked (capacity, gates) is a
    # legitimate reason for no new evaluations or AI calls.
    if cycle.get("last_poll_entry_eligible") is False:
        return findings

    completed_ts = _num(cycle.get("completed_ts"))
    ages = [a for a in (
        _num(progress.get("evaluation_age_sec")),
        max(0.0, now - completed_ts) if completed_ts else None,
    ) if a is not None]
    if ages and min(ages) > EVAL_STALE_SEC:
        findings["eval_stale"] = (
            f"no completed strategy evaluation for {min(ages) / 60:.0f} min "
            f"(> {EVAL_STALE_SEC / 60:.0f} min) while entries are eligible"
        )

    ai_age = _num(progress.get("ai_age_sec"))
    ai_limit = max(AI_STALE_MIN_SEC, 3 * (_num(progress.get("ai_stale_after_sec")) or 0.0))
    if ai_age is not None and ai_age > ai_limit:
        findings["ai_stale"] = (
            f"no successful AI response for {ai_age / 60:.0f} min (> {ai_limit / 60:.0f} min) while entries are eligible"
        )
    return findings


def laptop_heartbeat_findings(raw: str | None, now: float) -> dict[str, str]:
    """Dead-man check for the laptop supervisor via the LAPTOP_CHAIN_HEARTBEAT variable.

    Unset means the laptop watchdog has never reported, so nothing is expected yet.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        age = now - float(text)
    except ValueError:
        return {"laptop_silent": f"LAPTOP_CHAIN_HEARTBEAT is not an epoch timestamp: {text[:40]!r}"}
    if age <= LAPTOP_SILENT_SEC:
        return {}
    return {
        "laptop_silent": (
            f"laptop DoxxedLaptopChainSupervisor has not reported for {age / 3600:.1f}h "
            f"(> {LAPTOP_SILENT_SEC / 3600:.0f}h): supervisor task dead, laptop asleep/offline, or gh auth broken"
        )
    }


def transfer_findings(health: Mapping[str, Any] | None) -> dict[str, str]:
    """Research transfer lag: the segment shipper is the only Fly-to-laptop transfer."""
    volume = (health or {}).get("volume")
    transfer = volume.get("transfer") if isinstance(volume, dict) else None
    if not isinstance(transfer, dict):
        return {}
    if transfer.get("segments_enabled") is True:
        if transfer.get("segment_status_present") is not True:
            return {"transfer_lag": "segments enabled but the shipper has not written a status file"}
        problems = []
        status_age = _num(transfer.get("segment_status_age_sec"))
        if status_age is not None and status_age > SEGMENT_STATUS_STALE_SEC:
            problems.append(f"shipper status {status_age / 60:.0f} min old")
        if transfer.get("last_error"):
            problems.append(f"last_error={str(transfer['last_error'])[:120]!r}")
        shipped, acked = _num(transfer.get("shipped_seq")), _num(transfer.get("laptop_acked_seq"))
        if shipped is not None and shipped - (acked or 0) > SEGMENT_SEQ_LAG:
            problems.append(f"laptop ACK {int(acked or 0)} is {int(shipped - (acked or 0))} segments behind {int(shipped)}")
        return {"transfer_lag": "segment transfer lagging: " + "; ".join(problems)} if problems else {}
    return {"transfer_lag": "segment shipping is disabled and the whole-generation transfer is retired"}


def collection_findings(health: Mapping[str, Any] | None) -> dict[str, str]:
    """Multiverse empty-path rate, tape source, maturation worker and touch-grid coverage."""
    block = (health or {}).get("research_collection")
    if not isinstance(block, dict):
        return {}
    alarms = block.get("alarms") if isinstance(block.get("alarms"), list) else []
    multiverse = block.get("multiverse") if isinstance(block.get("multiverse"), dict) else {}
    grid = block.get("touch_grid") if isinstance(block.get("touch_grid"), dict) else {}
    findings: dict[str, str] = {}
    if "MULTIVERSE_EMPTY_PATH_RATE_HIGH" in alarms:
        rate = _num(multiverse.get("empty_path_rate_1h"))
        findings["multiverse_empty_path"] = (
            f"order multiverse: {int(multiverse.get('empty_path_1h') or 0)}/"
            f"{int(multiverse.get('written_1h') or 0)} rows written in the last hour have an empty path"
            + (f" ({rate * 100:.0f}%)" if rate is not None else "")
        )
    if "MULTIVERSE_TAPE_SOURCE_UNAVAILABLE" in alarms:
        tape = block.get("tape_source") if isinstance(block.get("tape_source"), dict) else {}
        findings["multiverse_tape_source"] = (
            f"1s tape source stale for multiverse maturation "
            f"(latest bucket age={tape.get('latest_bucket_age_sec')!r}s, pending={multiverse.get('pending')!r})"
        )
    if "COLLECTOR_MATURATION_WORKER_STALLED" in alarms:
        worker = multiverse.get("maturation_worker") if isinstance(multiverse.get("maturation_worker"), dict) else {}
        findings["multiverse_worker_stalled"] = (
            f"collector maturation worker stalled (alive={worker.get('alive')!r}, "
            f"last_error={str(worker.get('last_error') or '')[:120]!r}, pending={multiverse.get('pending')!r})"
        )
    if "TOUCH_GRID_COVERAGE_LOW" in alarms:
        coverage = _num(grid.get("coverage_1h"))
        findings["touch_grid_coverage"] = (
            f"discovery touch grid armed for {int(grid.get('armed_calls_1h') or 0)}/"
            f"{int(grid.get('eligible_calls_1h') or 0)} tile-eligible calls in the last hour"
            + (f" ({coverage * 100:.0f}%)" if coverage is not None else "")
        )
    return findings
