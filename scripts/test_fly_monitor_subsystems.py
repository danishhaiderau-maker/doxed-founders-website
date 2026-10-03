"""Subsystem rules: shadow collectors, lifecycle, V3 reconcile, relay outbox, laptop push, contract."""

import copy

import fly_monitor_alerts as alerts
import fly_monitor_subsystems as sub

NOW = 1_790_938_800.0
MIN = 60.0

# Shapes captured from the deployed revision (/ready, /api/status,
# /api/relay-execution-state, /api/system-health, /health) on 2026-10-02.
READY = {
    "active_tiles": [],
    "strategy_progress": {
        "process_startup_age_sec": 3566.0,
        "scheduled_ai_cycle": {
            "last_poll_ts": NOW - 30, "last_poll_entry_eligible": True,
            "last_poll_reason": "READY", "stage": "IDLE",
        },
    },
    "bbo_refresh": {
        "inflight": False, "inflight_age_sec": 0.0, "last_success_age_sec": 1.3,
        "consecutive_failures": 0, "last_error": None,
    },
    "ai_input_health": {"status": "WARMING", "prompt_id": "p", "dead_fields": []},
    "cross_venue_health": {"status": "OK", "reason": None, "collector_age_s": 1.1, "stale_venues": []},
    "xvl_evaluator_health": {"status": "OK", "reason": None, "tick_age_s": 0.8},
    "market_context_health": {"status": "OK", "stale_feeds": [], "age_sec": 1.6},
}
STATUS = {
    "uptime": {"boot_at": "2026-10-02T09:56:10Z"},
    "book_refresh": {"book_age_sec": 0.49, "consecutive_failures": 0, "inflight": False, "last_error": None},
    "lifecycle_pipeline": {
        "available": True, "running": True, "last_outcome": "SUCCESS", "last_success_age_sec": 20.8,
        "emergency": False, "failure_count": 0, "last_error_code": None,
        "blocker_counts": {"POST_OBSERVATION_MISSING": 1, "TERMINAL_REASON_MISSING": 1},
        "emergency_wal": {"status": "CURRENT", "reserve_ready": True, "observed_age_sec": 21.4, "alarms": []},
    },
    "collection": {
        "cross_venue_tape": {"venues": {
            "binance": {"reconnects": 0}, "bybit": {"reconnects": 0}, "okx": {"reconnects": 0},
        }},
        "market_context_tape": {"status": "OK", "stale_feeds": []},
        "execution_markouts": {"write_failures": 0, "dropped": 0, "taker_capture_failures": 0},
        "shadow_exit_recorder": {"write_failures": 0, "errors": 0, "dropped_full": 0},
        "xvl_evaluator": {"write_failures": 0},
    },
}
RELAY = {"state_integrity": {"live_armed": False, "relay_push": {"delivery_scheduler": {
    "schema": "relay_delivery_plan_v1",
    "counts": {"pending_total": 0, "stale_owner_pending": 0, "ready_trade_heads": 0},
}}}}
SYSTEM_HEALTH = {"age_sec": 236, "stale": False, "verdict": "AMBER", "received_at": "2026-10-02T10:56:20Z"}
HEALTH = {
    "probe_contract": "PROCESS_LIVENESS_ONLY", "process_alive": True, "force_paper_mode": True,
    "live_armed": False, "bitfinex_live_enabled": False, "source_git_rev": "abc", "execution_paused": False,
    "pause_owner": None, "volume": {"used_pct": 10.0, "transfer": {}},
    "research_collection": {"alarms": [], "multiverse": {"v3_reconcile_worker": {
        "alive": True, "phase": "IDLE", "phase_started_ts": NOW - 40, "runs": 3,
        "started_ts": NOW - 3800, "last_error": None, "exit_error": None,
    }}},
}


def _with(base, path, value):
    payload = copy.deepcopy(base)
    node = payload
    *parents, leaf = path.split(".")
    for part in parents:
        node = node[part]
    node[leaf] = value
    return payload


def test_live_snapshot_shapes_are_all_healthy():
    state = alerts.empty_state()
    assert sub.ready_block_findings(READY, paused=False) == {}
    assert sub.cross_venue_degraded_findings(state, READY, NOW) == {}
    assert sub.cross_venue_reconnect_findings(state, STATUS) == {}
    assert sub.lifecycle_findings(STATUS) == {}
    assert sub.v3_reconcile_findings(HEALTH, NOW) == {}
    assert sub.relay_findings(state, RELAY, NOW) == {}
    assert sub.entries_blocked_findings(state, READY, paused=False, now=NOW) == {}
    assert sub.laptop_health_findings(SYSTEM_HEALTH) == {}
    assert sub.contract_findings({
        "health": HEALTH, "ready": READY, "status": STATUS, "relay": RELAY, "system_health": SYSTEM_HEALTH,
    }) == {}


# --- /ready blocks --------------------------------------------------------

def test_xvl_evaluator_stale_on_status_or_tick_age():
    assert "xvl_evaluator_stale" in sub.ready_block_findings(
        _with(READY, "xvl_evaluator_health", {"status": "STALE", "reason": "TICK_AGE_90S", "tick_age_s": 90.0}),
        paused=False,
    )
    assert "xvl_evaluator_stale" in sub.ready_block_findings(
        _with(READY, "xvl_evaluator_health.tick_age_s", 61.0), paused=False
    )
    for status in ("DISABLED", "STARTING"):
        found = sub.ready_block_findings(
            _with(READY, "xvl_evaluator_health", {"status": status, "tick_age_s": None}), paused=False
        )
        assert "xvl_evaluator_stale" not in found


def _xvl_latency(status, p50):
    return {"status": "OK", "tick_age_s": 0.8, "lanes": {"FAMILY_XVENUE_SESSION_FOLLOW_60M": {"latency": {
        "status": status, "target_median_signal_to_fill_s": 2.0,
        "stages": {"signal_to_fill": {"n": 40, "p50_s": p50, "p90_s": p50 + 3}},
    }}}}


def test_xvl_signal_to_fill_slow_only_when_the_runtime_gate_says_slow():
    found = sub.ready_block_findings(_with(READY, "xvl_evaluator_health", _xvl_latency("SLOW", 9.3)), paused=False)
    assert "FAMILY_XVENUE_SESSION_FOLLOW_60M median signal->fill 9.3s" in found["xvl_signal_to_fill_slow"]
    assert "xvl_signal_to_fill_slow" in alerts.POLICIES
    for status, p50 in (("OK", 1.4), ("INSUFFICIENT_FILLS", 9.3)):
        found = sub.ready_block_findings(_with(READY, "xvl_evaluator_health", _xvl_latency(status, p50)), paused=False)
        assert "xvl_signal_to_fill_slow" not in found


def test_preentry_evidence_degraded_only_on_dead_receipts_or_barrier_timeouts():
    block = {"status": "DEGRADED", "tick_age_s": 0.8, "lanes": {}, "preentry_evidence": {
        "health": "DEGRADED", "dead": 1, "barrier_timeouts": 0, "pending": 0, "last_error": "V3_LANE_DECISION not durable",
    }}
    found = sub.ready_block_findings(_with(READY, "xvl_evaluator_health", block), paused=False)
    assert "dead=1" in found["preentry_evidence_degraded"]
    assert "preentry_evidence_degraded" in alerts.POLICIES
    for health in ("OK", "SYNC_FALLBACK", "DISABLED"):
        block["preentry_evidence"]["health"] = health
        found = sub.ready_block_findings(_with(READY, "xvl_evaluator_health", block), paused=False)
        assert "preentry_evidence_degraded" not in found


def test_cross_venue_down_or_collector_age_but_not_disabled():
    down = _with(READY, "cross_venue_health", {"status": "DOWN", "reason": "COLLECTOR_HEARTBEAT_STALE",
                                               "collector_age_s": 400.0, "stale_venues": []})
    assert "cross_venue_stale" in sub.ready_block_findings(down, paused=True)
    aged = _with(READY, "cross_venue_health.collector_age_s", 121.0)
    assert "cross_venue_stale" in sub.ready_block_findings(aged, paused=False)
    off = _with(READY, "cross_venue_health", {"status": "DISABLED", "collector_age_s": None})
    assert "cross_venue_stale" not in sub.ready_block_findings(off, paused=False)


def test_cross_venue_degraded_needs_thirty_continuous_minutes():
    state = alerts.empty_state()
    degraded = _with(READY, "cross_venue_health", {"status": "DEGRADED", "reason": "VENUE_STALE",
                                                   "collector_age_s": 1.0, "stale_venues": ["okx"]})
    assert sub.cross_venue_degraded_findings(state, degraded, NOW) == {}
    assert sub.cross_venue_degraded_findings(state, degraded, NOW + 29 * MIN) == {}
    assert "okx" in sub.cross_venue_degraded_findings(state, degraded, NOW + 30 * MIN)["cross_venue_stale"]
    assert sub.cross_venue_degraded_findings(state, READY, NOW + 31 * MIN) == {}
    assert sub.cross_venue_degraded_findings(state, degraded, NOW + 32 * MIN) == {}


def test_cross_venue_reconnect_storm_uses_per_run_delta():
    state = alerts.empty_state()
    assert sub.cross_venue_reconnect_findings(state, STATUS) == {}
    storm = _with(STATUS, "collection.cross_venue_tape.venues.okx.reconnects", 30)
    assert "okx=30" in sub.cross_venue_reconnect_findings(state, storm)["cross_venue_reconnects"]
    assert sub.cross_venue_reconnect_findings(state, storm) == {}
    # A restart resets the counters; that is not a storm.
    assert sub.cross_venue_reconnect_findings(state, STATUS) == {}


def test_market_context_degraded_or_down_but_not_disabled():
    for status in ("COLLECTOR_DOWN", "DEGRADED"):
        payload = _with(READY, "market_context_health", {"status": status, "stale_feeds": ["deriv_okx"]})
        assert "deriv_okx" in sub.ready_block_findings(payload, paused=True)["market_context_stale"]
    off = _with(READY, "market_context_health", {"status": "DISABLED", "stale_feeds": []})
    assert "market_context_stale" not in sub.ready_block_findings(off, paused=False)


def test_indicator_engine_stalled_or_down_but_not_degraded_or_absent():
    for status in ("ENGINE_DOWN", "STALLED"):
        payload = _with(READY, "indicator_engine_health",
                        {"status": status, "last_bar_close_age_sec": 900.0, "age_sec": 2.0, "rows_written": 7})
        msg = sub.ready_block_findings(payload, paused=True)["indicator_engine_stalled"]
        assert "900.0" in msg and "180s" in msg
    for status in ("OK", "DEGRADED", "DISABLED"):
        payload = _with(READY, "indicator_engine_health", {"status": status})
        assert "indicator_engine_stalled" not in sub.ready_block_findings(payload, paused=False)
    # A revision without the engine reports nothing (no false alarm before the deploy).
    assert "indicator_engine_stalled" not in sub.ready_block_findings(READY, paused=False)
    assert "indicator_engine_stalled" in alerts.POLICIES


def test_indicator_engine_write_and_compute_failures_are_counted():
    state = {}
    base = _with(READY, "indicator_engine_health", {"status": "OK", "write_failures": 0, "compute_failures": 0})
    assert sub.collection_write_failure_findings(state, STATUS, base) == {}
    grew = _with(base, "indicator_engine_health.compute_failures", 2)
    msg = sub.collection_write_failure_findings(state, STATUS, grew)["collection_write_failures"]
    assert "indicator_engine_health.compute_failures +2" in msg


def test_ai_input_dead_only_while_unpaused():
    dead = _with(READY, "ai_input_health", {"status": "DEAD_INPUT", "prompt_id": "p",
                                            "dead_fields": [{"path": "funding.rate", "kind": "constant"}]})
    assert "funding.rate" in sub.ready_block_findings(dead, paused=False)["ai_input_dead"]
    assert "ai_input_dead" not in sub.ready_block_findings(dead, paused=True)
    assert "ai_input_dead" not in sub.ready_block_findings(dead, paused=None)


def test_bbo_refresh_stale_thresholds():
    assert "bbo_refresh_stale" in sub.ready_block_findings(
        _with(READY, "bbo_refresh.last_success_age_sec", 301.0), paused=False)
    assert "bbo_refresh_stale" in sub.ready_block_findings(
        _with(_with(READY, "bbo_refresh.inflight", True), "bbo_refresh.inflight_age_sec", 121.0), paused=False)
    assert "bbo_refresh_stale" in sub.ready_block_findings(
        _with(READY, "bbo_refresh.consecutive_failures", 10), paused=False)
    assert "bbo_refresh_stale" not in sub.ready_block_findings(
        _with(READY, "bbo_refresh.consecutive_failures", 9), paused=False)
    assert "bbo_refresh_stale" not in sub.ready_block_findings(
        _with(READY, "bbo_refresh.last_success_age_sec", 900.0), paused=True)


def test_ready_rules_skip_the_startup_window():
    warming = _with(READY, "strategy_progress.process_startup_age_sec", 600.0)
    warming = _with(warming, "xvl_evaluator_health.tick_age_s", 500.0)
    assert sub.ready_block_findings(warming, paused=False) == {}
    assert sub.ready_block_findings(None, paused=False) == {}


# --- /api/status lifecycle -------------------------------------------------

def test_lifecycle_stalled_on_success_age_not_running_or_emergency():
    stale = _with(STATUS, "lifecycle_pipeline.last_success_age_sec", 3660.0)
    assert "61 min" in sub.lifecycle_findings(stale)["lifecycle_stalled"]
    assert sub.lifecycle_findings(_with(STATUS, "lifecycle_pipeline.last_success_age_sec", 3600.0)) == {}
    assert "not running" in sub.lifecycle_findings(
        _with(STATUS, "lifecycle_pipeline.running", False))["lifecycle_stalled"]
    assert "emergency" in sub.lifecycle_findings(
        _with(STATUS, "lifecycle_pipeline.emergency", True))["lifecycle_stalled"]
    assert sub.lifecycle_findings(_with(STATUS, "lifecycle_pipeline.available", False)) == {}
    assert sub.lifecycle_findings(None) == {}


def test_lifecycle_wal_alarm_invalid_or_stale():
    for status in ("ALARM", "INVALID", "STALE"):
        payload = _with(STATUS, "lifecycle_pipeline.emergency_wal.status", status)
        assert status in sub.lifecycle_findings(payload)["lifecycle_wal"]
    assert "lifecycle_wal" not in sub.lifecycle_findings(_with(STATUS, "lifecycle_pipeline.emergency_wal", None))


def test_lifecycle_blocked_needs_ten_lifecycles_on_one_code():
    assert "lifecycle_blocked" not in sub.lifecycle_findings(STATUS)
    blocked = _with(STATUS, "lifecycle_pipeline.blocker_counts", {"POST_OBSERVATION_MISSING": 10, "X": 2})
    assert "POST_OBSERVATION_MISSING=10" in sub.lifecycle_findings(blocked)["lifecycle_blocked"]


# --- collector V3 reconcile ---------------------------------------------------

def test_v3_reconcile_stalled_phase_runs_or_dead():
    worker = "research_collection.multiverse.v3_reconcile_worker"
    stuck = _with(HEALTH, worker + ".phase", "RECONCILING")
    stuck = _with(stuck, worker + ".phase_started_ts", NOW - 601)
    assert "RECONCILING" in sub.v3_reconcile_findings(stuck, NOW)["collector_v3_reconcile_stalled"]
    fresh = _with(stuck, worker + ".phase_started_ts", NOW - 300)
    assert sub.v3_reconcile_findings(fresh, NOW) == {}

    never = _with(HEALTH, worker + ".runs", 0)
    assert sub.v3_reconcile_findings(_with(never, worker + ".started_ts", NOW - 14 * MIN), NOW) == {}
    found = sub.v3_reconcile_findings(_with(never, worker + ".started_ts", NOW - 16 * MIN), NOW)
    assert found["collector_v3_reconcile_stalled"].startswith("COLLECTOR_V3_RECONCILE_STALLED")

    dead = _with(HEALTH, worker + ".alive", False)
    assert "not alive" in sub.v3_reconcile_findings(dead, NOW)["collector_v3_reconcile_stalled"]
    assert sub.v3_reconcile_findings(None, NOW) == {}


# --- relay outbox ------------------------------------------------------------

def test_relay_stale_owner_pending_after_thirty_minutes():
    state = alerts.empty_state()
    stuck = _with(RELAY, "state_integrity.relay_push.delivery_scheduler.counts.stale_owner_pending", 22)
    assert sub.relay_findings(state, stuck, NOW) == {}
    # An unreadable endpoint keeps the clock instead of resetting it.
    assert sub.relay_findings(state, None, NOW + 15 * MIN) == {}
    found = sub.relay_findings(state, stuck, NOW + 30 * MIN)
    assert "22 events" in found["relay_stale_owner_pending"]
    assert sub.relay_findings(state, RELAY, NOW + 45 * MIN) == {}
    assert sub.relay_findings(state, stuck, NOW + 60 * MIN) == {}


def test_relay_finding_is_warning_unless_live_armed():
    state = alerts.empty_state()
    finding = {"relay_stale_owner_pending": "22 events"}
    decision = alerts.evaluate(state, finding, now=NOW, maintenance=True)[0][0]
    assert (decision["action"], decision["severity"]) == ("alert", "warning")
    assert not alerts.is_failing(decision)
    armed = alerts.evaluate(
        alerts.empty_state(), finding, now=NOW, maintenance=True, escalate=frozenset(finding)
    )[0][0]
    assert armed["severity"] == "critical" and alerts.is_failing(armed)


def test_relay_scheduler_absent_when_owner_filter_off_is_not_stale():
    state = alerts.empty_state()
    off = _with(RELAY, "state_integrity.relay_push.delivery_scheduler", None)
    assert sub.relay_findings(state, off, NOW) == {}
    assert "relay_stale_owner_pending" not in state["since"]


# --- entries blocked --------------------------------------------------------

def test_entries_blocked_after_two_hours_unpaused_only():
    state = alerts.empty_state()
    blocked = _with(READY, "strategy_progress.scheduled_ai_cycle.last_poll_entry_eligible", False)
    blocked = _with(blocked, "strategy_progress.scheduled_ai_cycle.last_poll_reason", "CAPACITY_FULL")
    assert sub.entries_blocked_findings(state, blocked, paused=False, now=NOW) == {}
    assert sub.entries_blocked_findings(state, blocked, paused=False, now=NOW + 119 * MIN) == {}
    found = sub.entries_blocked_findings(state, blocked, paused=False, now=NOW + 120 * MIN)
    assert "CAPACITY_FULL" in found["entries_blocked"]
    # A pause resets the clock: a paused bot legitimately takes no entries.
    assert sub.entries_blocked_findings(state, blocked, paused=True, now=NOW + 121 * MIN) == {}
    assert sub.entries_blocked_findings(state, blocked, paused=False, now=NOW + 122 * MIN) == {}


# --- laptop health push -------------------------------------------------------

def test_laptop_health_silent_on_age_or_stale_flag():
    assert sub.laptop_health_findings({**SYSTEM_HEALTH, "age_sec": 1801}) != {}
    assert sub.laptop_health_findings({**SYSTEM_HEALTH, "stale": True}) != {}
    assert sub.laptop_health_findings({**SYSTEM_HEALTH, "age_sec": 1800}) == {}
    assert sub.laptop_health_findings(None) == {}


# --- contract ------------------------------------------------------------------

def test_missing_required_field_is_a_finding_but_null_value_is_not():
    status = copy.deepcopy(STATUS)
    del status["lifecycle_pipeline"]["emergency_wal"]
    found = sub.contract_findings({"status": status, "ready": _with(READY, "bbo_refresh.last_success_age_sec", None)})
    assert found["contract_field_missing"] == (
        "1 required field(s) missing from Fly responses: status:lifecycle_pipeline.emergency_wal"
    )


def test_contract_skips_endpoints_that_did_not_answer():
    assert sub.contract_findings({"health": None, "ready": None, "status": None}) == {}


def test_contract_field_missing_needs_two_runs_and_is_a_warning():
    state = alerts.empty_state()
    finding = {"contract_field_missing": "ready:xvl_evaluator_health.status"}
    assert alerts.evaluate(state, finding, now=NOW, maintenance=False)[0][0]["action"] == "pending"
    decision = alerts.evaluate(state, finding, now=NOW + 15 * MIN, maintenance=False)[0][0]
    assert decision["action"] == "alert" and not alerts.is_failing(decision)


def test_order_book_stale_only_while_unpaused():
    status = {"book_refresh": {"book_age_sec": 0.5, "consecutive_failures": 0, "last_error": None}}
    assert sub.order_book_findings(status, paused=False) == {}
    stale = {"book_refresh": {"book_age_sec": 400.0, "consecutive_failures": 12, "last_error": "timeout"}}
    found = sub.order_book_findings(stale, paused=False)["order_book_stale"]
    assert "book_age_sec=400" in found and "12 consecutive failures" in found
    assert sub.order_book_findings(stale, paused=True) == {}
    assert sub.order_book_findings(None, paused=False) == {}


def test_relay_cache_stale_from_cache_age_or_execution_state_503():
    assert sub.relay_cache_findings({"relay_cache": {"age_sec": 1.9}}, []) == {}
    assert "age_sec=900" in sub.relay_cache_findings({"relay_cache": {"age_sec": 900.0}}, [])["relay_cache_stale"]
    note = "skipped rules for https://doxed-btc-bot.fly.dev/api/relay-execution-state: HTTP 503"
    assert "HTTP 503" in sub.relay_cache_findings(None, [note])["relay_cache_stale"]


def test_collection_write_failures_alert_only_when_a_counter_grows():
    state = alerts.empty_state()
    status = {"collection": {"xvl_evaluator": {"write_failures": 0},
                             "execution_markouts": {"write_failures": 0, "dropped": 0}}}
    assert sub.collection_write_failure_findings(state, status, None) == {}
    assert sub.collection_write_failure_findings(state, status, None) == {}
    grown = copy.deepcopy(status)
    grown["collection"]["execution_markouts"]["dropped"] = 3
    found = sub.collection_write_failure_findings(state, grown, None)["collection_write_failures"]
    assert "execution_markouts.dropped +3" in found
    # a restart resets counters to 0: new baseline, no alert
    assert sub.collection_write_failure_findings(state, status, None) == {}


def test_shadow_exit_recorder_must_advance_and_feeds_failure_counters():
    state = alerts.empty_state()
    rec = {"enabled": True, "worker_alive": True, "queue_depth": 0, "submitted": 4, "written": 4,
           "skipped": 0, "errors": 0, "write_failures": 0, "dropped_full": 0}
    status = lambda **kw: {"collection": {"shadow_exit_recorder": {**rec, **kw}}}  # noqa: E731
    assert sub.shadow_exit_recorder_findings(state, status()) == {}
    assert sub.shadow_exit_recorder_findings(state, status(submitted=6, written=6)) == {}
    found = sub.shadow_exit_recorder_findings(state, status(submitted=9, written=6, queue_depth=3))
    assert "3 submitted path(s) not drained" in found["shadow_exit_recorder_stalled"]
    dead = sub.shadow_exit_recorder_findings(state, status(submitted=9, written=9, worker_alive=False))
    assert "worker thread is dead" in dead["shadow_exit_recorder_stalled"]
    assert sub.shadow_exit_recorder_findings(state, status(submitted=0, written=0, worker_alive=False)) == {}
    assert sub.shadow_exit_recorder_findings(state, status(enabled=False, worker_alive=False)) == {}
    assert "shadow_exit_recorder_stalled" in alerts.POLICIES
    paths = {path for _name, path in sub.COLLECTION_FAILURE_COUNTERS}
    assert {"collection.shadow_exit_recorder.write_failures", "collection.shadow_exit_recorder.errors",
            "collection.shadow_exit_recorder.dropped_full"} <= paths
    # Not required until the recorder revision is deployed: the monitor probes the live app.
    assert "collection.shadow_exit_recorder.written" not in sub.REQUIRED_FIELDS["status"]


def test_restart_loop_needs_three_distinct_boots_within_an_hour():
    state = alerts.empty_state()
    for i, boot in enumerate(["2026-10-02T09:00:00Z", "2026-10-02T09:00:00Z", "2026-10-02T09:20:00Z"]):
        assert sub.restart_loop_findings(state, {"uptime": {"boot_at": boot}}, NOW + i * 10 * MIN) == {}
    found = sub.restart_loop_findings(state, {"uptime": {"boot_at": "2026-10-02T09:40:00Z"}}, NOW + 30 * MIN)
    assert "3 times" in found["restart_loop"]
    assert sub.restart_loop_findings(state, {"uptime": {"boot_at": "2026-10-02T09:40:00Z"}}, NOW + 150 * MIN) == {}


def test_deploy_failed_until_a_later_deploy_succeeds():
    failed = {"last_finished_deploy": {"id": 7, "event": "push", "head_sha": "a" * 40,
                                       "conclusion": "failure", "updated_at": "2026-10-02T10:00:00Z"}}
    assert "concluded failure" in sub.deploy_failure_findings(failed)["deploy_failed"]
    ok = {"last_finished_deploy": {**failed["last_finished_deploy"], "conclusion": "success"}}
    assert sub.deploy_failure_findings(ok) == {}
    assert sub.deploy_failure_findings(None) == {}
