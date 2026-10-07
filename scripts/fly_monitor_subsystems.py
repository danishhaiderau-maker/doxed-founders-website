"""Rules over Fly subsystem blocks that are not inputs to ``/ready`` ok.

Shadow collectors, the cross-venue evaluator, the lifecycle pipeline, the
collector V3 reconcile worker, the relay outbox and the laptop health push all
report their own health, but none of them makes ``/ready`` fail, so before
these rules a dead subsystem stayed green indefinitely.

Each function maps probed payloads (``None`` when the endpoint was not
read) to findings. Duration gates use ``fly_monitor_alerts.track_since`` so a
condition must hold continuously across runs; run-count confirmation and
deploy suppression live in the alert policies.

``contract_findings`` replaces the old "a missing field is never a finding"
rule for the blocks the deployed revision is known to emit.
"""

from __future__ import annotations

from typing import Any, Mapping

import fly_monitor_alerts as alerts

STARTUP_GRACE_SEC = 20 * 60.0
XVL_TICK_STALE_SEC = 60.0
CROSS_VENUE_COLLECTOR_STALE_SEC = 120.0
CROSS_VENUE_DEGRADED_SEC = 30 * 60.0
# A pending pre-entry receipt older than this means the queue is not draining;
# used to separate a genuinely stuck backlog from transient barrier timeouts.
PREENTRY_EVIDENCE_STALE_PENDING_AGE_SEC = 60.0
CROSS_VENUE_RECONNECTS_PER_RUN = 30
BBO_SUCCESS_STALE_SEC = 300.0
BBO_INFLIGHT_STALE_SEC = 120.0
BBO_CONSECUTIVE_FAILURES = 10
LIFECYCLE_SUCCESS_STALE_SEC = 60 * 60.0
LIFECYCLE_BLOCKED_LIFECYCLES = 10
V3_PHASE_STALE_SEC = 600.0
V3_FIRST_RUN_SEC = 15 * 60.0
RELAY_STALE_OWNER_SEC = 30 * 60.0
ENTRIES_BLOCKED_SEC = 2 * 3600.0
LAPTOP_HEALTH_SILENT_SEC = 1800.0
ORDER_BOOK_STALE_SEC = 120.0
ORDER_BOOK_CONSECUTIVE_FAILURES = 10
RELAY_CACHE_STALE_SEC = 300.0
RESTART_LOOP_WINDOW_SEC = 3600.0
RESTART_LOOP_BOOTS = 3
COLLECTION_FAILURE_COUNTERS: tuple[tuple[str, str], ...] = (
    ("status", "collection.xvl_evaluator.write_failures"),
    ("status", "collection.execution_markouts.write_failures"),
    ("status", "collection.execution_markouts.dropped"),
    ("status", "collection.execution_markouts.taker_capture_failures"),
    ("status", "collection.shadow_exit_recorder.write_failures"),
    ("status", "collection.shadow_exit_recorder.errors"),
    ("status", "collection.shadow_exit_recorder.dropped_full"),
    ("status", "collection.microstructure_tape.write_failures_this_process"),
    ("status", "collection.microstructure_tape.io_write_failures_this_process"),
    ("status", "collection.cross_venue_tape.stats.write_failures"),
    ("status", "collection.cross_venue_tape.stats.live_write_failures"),
    ("ready", "cross_venue_health.stats.write_failures"),
    ("ready", "xvl_evaluator_health.write_failures"),
    ("ready", "indicator_engine_health.write_failures"),
    ("ready", "indicator_engine_health.compute_failures"),
)

# Field paths the deployed revision emits; absence means a contract regression
# or a monitor blind spot, never "nothing to check". Presence only: several of
# these are legitimately null (e.g. no lifecycle success yet after boot).
REQUIRED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "health": (
        "probe_contract", "process_alive", "force_paper_mode", "live_armed", "bitfinex_live_enabled",
        "source_git_rev", "execution_paused", "pause_owner", "volume.used_pct", "volume.transfer",
        "research_collection.alarms", "research_collection.multiverse.v3_reconcile_worker",
    ),
    "ready": (
        "strategy_progress.process_startup_age_sec",
        "strategy_progress.scheduled_ai_cycle.last_poll_ts",
        "strategy_progress.scheduled_ai_cycle.last_poll_entry_eligible",
        "active_tiles", "bbo_refresh.last_success_age_sec", "ai_input_health.status",
        "cross_venue_health.status", "xvl_evaluator_health.status", "xvl_evaluator_health.tick_age_s",
        "market_context_health.status", "market_context_health.stale_feeds",
    ),
    "status": (
        "lifecycle_pipeline.running", "lifecycle_pipeline.last_success_age_sec",
        "lifecycle_pipeline.blocker_counts", "lifecycle_pipeline.emergency_wal",
        "collection.cross_venue_tape.venues", "collection.market_context_tape.status",
        "book_refresh.book_age_sec", "uptime.boot_at", "collection.execution_markouts.write_failures",
        "collection.shadow_exit_recorder.write_failures", "collection.shadow_exit_recorder.errors",
        "collection.shadow_exit_recorder.dropped_full",
    ),
    "relay": ("state_integrity.relay_push.delivery_scheduler",),
    "system_health": ("age_sec", "stale"),
}
_MISSING = object()


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _preentry_evidence_genuinely_degraded(preentry: Mapping[str, Any]) -> bool:
    """True only for a real, non-self-healing pre-entry evidence problem.

    The bot marks ``health`` DEGRADED for a *cumulative* ``barrier_timeouts``
    counter, but a barrier timeout is transient collector-lock contention: the
    receipt stays pending and is retried on the next barrier (or the boot
    replay), so a small ``dead=0`` backlog is recoverable and must not page the
    operator.  Degradation is genuine only when a receipt was dead-lettered
    (``dead > 0``) or the pending backlog is aging past its bound.
    """
    if preentry.get("health") != "DEGRADED":
        return False
    dead = _num(preentry.get("dead"))
    if dead is not None and dead > 0:
        return True
    pending = _num(preentry.get("pending"))
    oldest_age = _num(preentry.get("oldest_pending_age_s"))
    return bool(
        pending is not None
        and pending > 0
        and oldest_age is not None
        and oldest_age > PREENTRY_EVIDENCE_STALE_PENDING_AGE_SEC
    )


def _get(payload: Any, path: str) -> Any:
    node = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _warming(ready: Mapping[str, Any] | None) -> bool:
    startup = _num(_dict(_dict(ready).get("strategy_progress")).get("process_startup_age_sec"))
    return startup is not None and startup < STARTUP_GRACE_SEC


def ready_block_findings(ready: Mapping[str, Any] | None, *, paused: bool | None) -> dict[str, str]:
    """XVL evaluator, cross-venue, market-context, AI inputs and BBO refresh from /ready."""
    if not isinstance(ready, dict) or _warming(ready):
        return {}
    findings: dict[str, str] = {}

    xvl = _dict(ready.get("xvl_evaluator_health"))
    tick_age = _num(xvl.get("tick_age_s"))
    if xvl.get("status") not in (None, "DISABLED", "STARTING") and (
        xvl.get("status") == "STALE" or (tick_age is not None and tick_age > XVL_TICK_STALE_SEC)
    ):
        findings["xvl_evaluator_stale"] = (
            f"cross-venue lead evaluator stopped ticking: status={xvl.get('status')!r} "
            f"reason={xvl.get('reason')!r} tick_age_s={tick_age} (> {XVL_TICK_STALE_SEC:.0f}s)"
        )
    slow = []
    for lane, block in sorted(_dict(xvl.get("lanes")).items()):
        latency = _dict(_dict(block).get("latency"))
        if latency.get("status") != "SLOW":
            continue
        fill = _dict(_dict(latency.get("stages")).get("signal_to_fill"))
        slow.append(
            f"{lane} median signal->fill {fill.get('p50_s')}s (p90 {fill.get('p90_s')}s, n={fill.get('n')}) "
            f"> {latency.get('target_median_signal_to_fill_s')}s"
        )
    if slow:
        findings["xvl_signal_to_fill_slow"] = (
            "cross-venue IMMEDIATE tiles miss the pre-registered signal->fill gate: " + "; ".join(slow)
        )
    preentry = _dict(xvl.get("preentry_evidence"))
    if _preentry_evidence_genuinely_degraded(preentry):
        findings["preentry_evidence_degraded"] = (
            f"submit-first pre-entry evidence queue degraded: dead={preentry.get('dead')} "
            f"barrier_timeouts={preentry.get('barrier_timeouts')} pending={preentry.get('pending')} "
            f"last_error={preentry.get('last_error')!r}"
        )

    cross = _dict(ready.get("cross_venue_health"))
    collector_age = _num(cross.get("collector_age_s"))
    if cross.get("status") in ("DOWN", "STALE") or (
        cross.get("status") not in (None, "DISABLED")
        and collector_age is not None and collector_age > CROSS_VENUE_COLLECTOR_STALE_SEC
    ):
        findings["cross_venue_stale"] = (
            f"cross-venue collector {cross.get('status')!r} reason={cross.get('reason')!r} "
            f"collector_age_s={collector_age} stale_venues={cross.get('stale_venues')}"
        )

    context = _dict(ready.get("market_context_health"))
    if context.get("status") in ("COLLECTOR_DOWN", "DEGRADED", "UNAVAILABLE"):
        findings["market_context_stale"] = (
            f"market-context collector {context.get('status')!r} age_sec={context.get('age_sec')} "
            f"stale_feeds={context.get('stale_feeds')}"
        )

    engine = _dict(ready.get("indicator_engine_health"))
    if engine.get("status") in ("ENGINE_DOWN", "STALLED", "UNAVAILABLE"):
        findings["indicator_engine_stalled"] = (
            f"indicator engine {engine.get('status')!r}: last bar closed "
            f"{engine.get('last_bar_close_age_sec')}s ago (bars must advance every 180s), "
            f"live age_sec={engine.get('age_sec')} rows_written={engine.get('rows_written')}"
        )

    if paused is False:
        inputs = _dict(ready.get("ai_input_health"))
        if inputs.get("status") == "DEAD_INPUT":
            dead = [d.get("path") for d in inputs.get("dead_fields") or [] if isinstance(d, dict)]
            findings["ai_input_dead"] = (
                f"AI prompt {inputs.get('prompt_id')!r} has dead input fields: {dead[:8]}"
            )

        bbo = _dict(ready.get("bbo_refresh"))
        problems = []
        success_age = _num(bbo.get("last_success_age_sec"))
        if success_age is not None and success_age > BBO_SUCCESS_STALE_SEC:
            problems.append(f"last success {success_age / 60:.0f} min ago")
        inflight_age = _num(bbo.get("inflight_age_sec"))
        if bbo.get("inflight") is True and inflight_age is not None and inflight_age > BBO_INFLIGHT_STALE_SEC:
            problems.append(f"refresh in flight for {inflight_age:.0f}s")
        failures = _num(bbo.get("consecutive_failures"))
        if failures is not None and failures >= BBO_CONSECUTIVE_FAILURES:
            problems.append(f"{int(failures)} consecutive failures (last_error={str(bbo.get('last_error'))[:120]!r})")
        if problems:
            findings["bbo_refresh_stale"] = "REST BBO refresh stalled: " + "; ".join(problems)
    return findings


def cross_venue_reconnect_findings(
    state: dict[str, Any], status: Mapping[str, Any] | None
) -> dict[str, str]:
    """Reconnect storm: total venue reconnects grew by >= the per-run limit since the last run."""
    venues = _dict(_dict(_dict(_dict(status).get("collection")).get("cross_venue_tape")).get("venues"))
    counts = [_num(_dict(v).get("reconnects")) for v in venues.values()]
    if not venues or any(c is None for c in counts):
        return {}
    total = sum(counts)
    counters = state.setdefault("counters", {})
    previous = counters.get("cross_venue_reconnects")
    counters["cross_venue_reconnects"] = total
    if previous is None or total < previous:
        return {}
    if total - previous < CROSS_VENUE_RECONNECTS_PER_RUN:
        return {}
    return {
        "cross_venue_reconnects": (
            f"cross-venue websockets reconnected {int(total - previous)} times since the previous run "
            f"(>= {CROSS_VENUE_RECONNECTS_PER_RUN}): "
            + ", ".join(f"{name}={int(_dict(v).get('reconnects') or 0)}" for name, v in sorted(venues.items()))
        )
    }


def cross_venue_degraded_findings(state: dict[str, Any], ready: Mapping[str, Any] | None, now: float) -> dict[str, str]:
    """Some (not all) venues stale continuously for > 30 min."""
    cross = _dict(_dict(ready).get("cross_venue_health")) if isinstance(ready, dict) else None
    degraded = None if cross is None else cross.get("status") == "DEGRADED"
    held = alerts.track_since(state, "cross_venue_degraded", degraded, now)
    if not degraded or held < CROSS_VENUE_DEGRADED_SEC:
        return {}
    return {
        "cross_venue_stale": (
            f"cross-venue venues stale for {held / 60:.0f} min: {cross.get('stale_venues')} "
            f"(reason={cross.get('reason')!r})"
        )
    }


def lifecycle_findings(status: Mapping[str, Any] | None) -> dict[str, str]:
    """Lifecycle pipeline progress, emergency WAL and blocked lifecycles from /api/status."""
    pipeline = _dict(_dict(status).get("lifecycle_pipeline"))
    if not pipeline or pipeline.get("available") is False:
        return {}
    findings: dict[str, str] = {}
    uptime = _dict(_dict(status).get("uptime"))
    success_age = _num(pipeline.get("last_success_age_sec"))
    problems = []
    if pipeline.get("running") is False:
        problems.append("worker not running")
    if success_age is not None and success_age > LIFECYCLE_SUCCESS_STALE_SEC:
        problems.append(f"last success {success_age / 60:.0f} min ago")
    if pipeline.get("emergency") is True:
        problems.append("emergency mode")
    if problems:
        findings["lifecycle_stalled"] = (
            "lifecycle pipeline stalled: " + "; ".join(problems)
            + f" (last_outcome={pipeline.get('last_outcome')!r}, last_error_code={pipeline.get('last_error_code')!r}, "
            f"failure_count={pipeline.get('failure_count')}, uptime_boot={uptime.get('boot_at')!r})"
        )

    wal = pipeline.get("emergency_wal")
    if isinstance(wal, dict) and wal.get("status") in ("ALARM", "INVALID", "STALE"):
        findings["lifecycle_wal"] = (
            f"emergency evidence WAL {wal.get('status')!r}: reserve_ready={wal.get('reserve_ready')} "
            f"observed_age_sec={wal.get('observed_age_sec')} alarms={wal.get('alarms')}"
        )

    blockers = {k: int(v) for k, v in _dict(pipeline.get("blocker_counts")).items() if _num(v) is not None}
    if blockers and max(blockers.values()) >= LIFECYCLE_BLOCKED_LIFECYCLES:
        top = sorted(blockers.items(), key=lambda kv: -kv[1])[:5]
        findings["lifecycle_blocked"] = (
            f"lifecycle pipeline has >= {LIFECYCLE_BLOCKED_LIFECYCLES} lifecycles blocked on one code: "
            + ", ".join(f"{k}={v}" for k, v in top)
        )
    return findings


def v3_reconcile_findings(health: Mapping[str, Any] | None, now: float) -> dict[str, str]:
    """COLLECTOR_V3_RECONCILE_STALLED from /health research_collection."""
    worker = _get(health, "research_collection.multiverse.v3_reconcile_worker")
    if not isinstance(worker, dict):
        return {}
    started = _num(worker.get("started_ts"))
    phase_started = _num(worker.get("phase_started_ts"))
    runs = _num(worker.get("runs"))
    problems = []
    if worker.get("alive") is False and started is not None:
        problems.append(f"worker not alive (exit_error={str(worker.get('exit_error'))[:120]!r})")
    phase = worker.get("phase")
    if phase not in (None, "IDLE") and phase_started is not None and now - phase_started > V3_PHASE_STALE_SEC:
        problems.append(f"phase {phase!r} for {(now - phase_started) / 60:.0f} min (> {V3_PHASE_STALE_SEC / 60:.0f} min)")
    if runs == 0 and started is not None and now - started > V3_FIRST_RUN_SEC:
        problems.append(f"0 runs after {(now - started) / 60:.0f} min uptime")
    if not problems:
        return {}
    return {
        "collector_v3_reconcile_stalled": (
            "COLLECTOR_V3_RECONCILE_STALLED: " + "; ".join(problems)
            + f" (last_error={str(worker.get('last_error'))[:120]!r})"
        )
    }


def relay_findings(state: dict[str, Any], relay: Mapping[str, Any] | None, now: float) -> dict[str, str]:
    """Relay outbox holds events from a previous bot owner for > 30 min."""
    counts = _get(relay, "state_integrity.relay_push.delivery_scheduler.counts") if relay is not None else _MISSING
    pending = _num(counts.get("stale_owner_pending")) if isinstance(counts, dict) else None
    if relay is None:
        active = None
    elif _get(relay, "state_integrity.relay_push.relay_stack.relay_stack_mode") == "research_only":
        # RELAY_STACK_MODE=research_only: delivery is deliberately disabled, so
        # undeliverable history is expected (INFO on /health), never an alert.
        active = False
    elif _get(relay, "state_integrity.relay_push.delivery_scheduler") is None:
        # The owner filter is off (not paper/disarmed): the scheduler block is
        # deliberately absent, so there is nothing stale to hold.
        active = False
    else:
        active = None if pending is None else pending > 0
    held = alerts.track_since(state, "relay_stale_owner_pending", active, now)
    if not active or held < RELAY_STALE_OWNER_SEC:
        return {}
    return {
        "relay_stale_owner_pending": (
            f"relay outbox has {int(pending)} events from a previous bot owner pending for "
            f"{held / 60:.0f} min (> {RELAY_STALE_OWNER_SEC / 60:.0f} min); counts={dict(counts)}"
        )
    }


def entries_blocked_findings(
    state: dict[str, Any], ready: Mapping[str, Any] | None, *, paused: bool | None, now: float
) -> dict[str, str]:
    """The AI scheduler keeps polling but entries stay ineligible for > 2h while unpaused."""
    cycle = _get(ready, "strategy_progress.scheduled_ai_cycle") if isinstance(ready, dict) else _MISSING
    eligible = cycle.get("last_poll_entry_eligible") if isinstance(cycle, dict) else None
    if paused is True:
        active: bool | None = False
    elif paused is None or not isinstance(eligible, bool):
        active = None
    else:
        active = eligible is False
    held = alerts.track_since(state, "entries_blocked", active, now)
    if not active or held < ENTRIES_BLOCKED_SEC:
        return {}
    return {
        "entries_blocked": (
            f"new entries ineligible for {held / 3600:.1f}h while paper is unpaused "
            f"(last_poll_reason={cycle.get('last_poll_reason')!r}, stage={cycle.get('stage')!r})"
        )
    }


def laptop_health_findings(system_health: Mapping[str, Any] | None) -> dict[str, str]:
    """Fly holds the laptop watcher's last push; stale means the watcher stopped pushing."""
    if not isinstance(system_health, dict):
        return {}
    age = _num(system_health.get("age_sec"))
    if system_health.get("stale") is not True and (age is None or age <= LAPTOP_HEALTH_SILENT_SEC):
        return {}
    return {
        "laptop_health_silent": (
            f"Fly /api/system-health last laptop push age_sec={age} stale={system_health.get('stale')} "
            f"(received_at={system_health.get('received_at')!r}; limit {LAPTOP_HEALTH_SILENT_SEC / 60:.0f} min): "
            "laptop system-health watcher is not pushing"
        )
    }


def order_book_findings(status: Mapping[str, Any] | None, *, paused: bool | None) -> dict[str, str]:
    """The REST order-book refresh loop exposes its age but nothing alerted on it."""
    book = _dict(_dict(status).get("book_refresh"))
    if not book or paused is not False:
        return {}
    problems = []
    age = _num(book.get("book_age_sec"))
    if age is not None and age > ORDER_BOOK_STALE_SEC:
        problems.append(f"book_age_sec={age:.0f} (> {ORDER_BOOK_STALE_SEC:.0f}s)")
    failures = _num(book.get("consecutive_failures"))
    if failures is not None and failures >= ORDER_BOOK_CONSECUTIVE_FAILURES:
        problems.append(f"{int(failures)} consecutive failures (last_error={str(book.get('last_error'))[:120]!r})")
    if not problems:
        return {}
    return {"order_book_stale": "order-book refresh stalled: " + "; ".join(problems)}


def relay_cache_findings(relay_state: Mapping[str, Any] | None, notes: list[str]) -> dict[str, str]:
    """/api/relay-state cache age and /api/relay-execution-state 503s were exposed, never alerted."""
    problems = []
    age = _num(_dict(_dict(relay_state).get("relay_cache")).get("age_sec"))
    if age is not None and age > RELAY_CACHE_STALE_SEC:
        problems.append(f"/api/relay-state relay_cache.age_sec={age:.0f} (> {RELAY_CACHE_STALE_SEC:.0f}s)")
    if any("relay-execution-state" in n and "HTTP 503" in n for n in notes):
        problems.append("/api/relay-execution-state answered HTTP 503 (snapshot stale)")
    if not problems:
        return {}
    return {"relay_cache_stale": "relay snapshot cache stale: " + "; ".join(problems)}


def collection_write_failure_findings(
    state: dict[str, Any], status: Mapping[str, Any] | None, ready: Mapping[str, Any] | None
) -> dict[str, str]:
    """Shadow/research writers count their failures; alert when any counter grew since the last run."""
    sources = {"status": status, "ready": ready}
    current: dict[str, float] = {}
    for name, path in COLLECTION_FAILURE_COUNTERS:
        value = _num(_get(sources.get(name), path))
        if value is not None:
            current[f"{name}:{path}"] = value
    counters = state.setdefault("counters", {})
    previous = counters.get("collection_write_failures")
    counters["collection_write_failures"] = current
    if not isinstance(previous, dict):
        return {}
    grew = {k: v - previous[k] for k, v in current.items() if k in previous and v > previous[k]}
    if not grew:
        return {}
    return {
        "collection_write_failures": (
            f"{len(grew)} research/shadow writer failure counter(s) grew since the previous run: "
            + ", ".join(f"{k} +{int(d)}" for k, d in sorted(grew.items())[:8])
        )
    }


def shadow_exit_recorder_findings(state: dict[str, Any], status: Mapping[str, Any] | None) -> dict[str, str]:
    """Observation-only recorder must advance: closed trades submitted since the last run must be drained."""
    rec = _dict(_dict(_dict(status).get("collection")).get("shadow_exit_recorder"))
    if not rec or rec.get("enabled") is False:
        return {}
    keys = ("submitted", "written", "skipped", "errors", "write_failures")
    nums = {k: _num(rec.get(k)) for k in keys}
    if any(v is None for v in nums.values()):
        return {}
    counters = state.setdefault("counters", {})
    previous = counters.get("shadow_exit_recorder")
    counters["shadow_exit_recorder"] = nums
    if not isinstance(previous, dict) or nums["submitted"] < (_num(previous.get("submitted")) or 0.0):
        return {}
    drained = lambda c: sum(_num(c.get(k)) or 0.0 for k in keys[1:])  # noqa: E731
    pending = nums["submitted"] - drained(nums)
    if nums["submitted"] > 0 and rec.get("worker_alive") is False:
        problem = "worker thread is dead"
    elif pending > 0 and drained(nums) <= drained(previous) and nums["submitted"] >= previous["submitted"]:
        problem = f"{int(pending)} submitted path(s) not drained since the previous run"
    else:
        return {}
    return {"shadow_exit_recorder_stalled": (
        f"shadow-exit recorder not advancing: {problem} (submitted={int(nums['submitted'])}, "
        f"written={int(nums['written'])}, queue_depth={rec.get('queue_depth')})")}


def restart_loop_findings(state: dict[str, Any], status: Mapping[str, Any] | None, now: float) -> dict[str, str]:
    """Each new uptime.boot_at is a process start; three inside an hour is a restart loop."""
    boot = _dict(_dict(status).get("uptime")).get("boot_at")
    boots = [b for b in state.setdefault("boots", []) if isinstance(b, list) and now - b[1] <= RESTART_LOOP_WINDOW_SEC]
    if isinstance(boot, str) and boot and all(b[0] != boot for b in boots):
        boots.append([boot, now])
    state["boots"] = boots
    if len(boots) < RESTART_LOOP_BOOTS:
        return {}
    return {
        "restart_loop": (
            f"Fly bot started {len(boots)} times within {RESTART_LOOP_WINDOW_SEC / 60:.0f} min "
            f"(boot_at: {', '.join(b[0] for b in boots[-5:])})"
        )
    }


def deploy_failure_findings(deploy: Mapping[str, Any] | None) -> dict[str, str]:
    """The newest finished image deploy failed and no later deploy succeeded (was GHA email only)."""
    last = _dict(_dict(deploy).get("last_finished_deploy"))
    if last.get("conclusion") not in ("failure", "timed_out"):
        return {}
    return {
        "deploy_failed": (
            f"latest fly-bot-deploy image deploy run {last.get('id')} ({last.get('event')}) on "
            f"{str(last.get('head_sha'))[:12]} concluded {last.get('conclusion')} at {last.get('updated_at')}"
        )
    }


def contract_findings(payloads: Mapping[str, Any]) -> dict[str, str]:
    """Required field paths missing from endpoints that answered this run."""
    missing = [
        f"{name}:{path}"
        for name, paths in REQUIRED_FIELDS.items()
        if isinstance(payloads.get(name), dict)
        for path in paths
        if _get(payloads[name], path) is _MISSING
    ]
    if not missing:
        return {}
    return {
        "contract_field_missing": (
            f"{len(missing)} required field(s) missing from Fly responses: " + ", ".join(missing[:12])
        )
    }
