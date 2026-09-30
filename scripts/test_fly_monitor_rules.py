"""Alert rules for disk, stuck deploys, AI/eval cadence and transfer lag."""

import fly_monitor_alerts as alerts
import fly_monitor_rules as rules

GIB = 1024 ** 3
NOW = 1_800_000_000.0


def _health(used_pct, **extra):
    total = 20 * GIB
    used = int(total * used_pct / 100)
    volume = {"used_pct": used_pct, "total_bytes": total, "used_bytes": used, "free_bytes": total - used}
    volume.update(extra)
    return {"volume": volume}


def _ready(**progress):
    base = {
        "process_startup_age_sec": 9000.0,
        "evaluation_age_sec": 30.0,
        "ai_age_sec": 40.0,
        "ai_stale_after_sec": 300.0,
        "scheduled_ai_cycle": {"last_poll_ts": NOW - 20, "completed_ts": NOW - 40, "last_poll_entry_eligible": True},
    }
    base.update(progress)
    return {"strategy_progress": base}


# --- disk -----------------------------------------------------------------

def test_disk_below_warn_is_silent():
    assert rules.disk_findings(_health(15.0)) == {}
    assert rules.disk_findings(_health(69.99)) == {}


def test_disk_warn_at_70_percent():
    found = rules.disk_findings(_health(70.0, growth_bytes_per_hour=215 * 2**20, hours_to_full=28.0))
    assert set(found) == {"disk_warn"}
    assert "70.0% used" in found["disk_warn"] and "215 MiB/h" in found["disk_warn"]
    assert "~28h to full" in found["disk_warn"]


def test_disk_critical_at_85_percent_keeps_warn_active():
    found = rules.disk_findings(_health(85.0))
    assert set(found) == {"disk_warn", "disk_critical"}
    assert found["disk_critical"].startswith("URGENT")


def test_disk_rule_ignores_revisions_without_volume_block():
    assert rules.disk_findings(None) == {}
    assert rules.disk_findings({"process_alive": True}) == {}
    assert rules.disk_findings({"volume": {"used_pct": None, "error": "DISK_USAGE_UNAVAILABLE"}}) == {}


def test_disk_critical_realerts_every_two_hours_not_every_run():
    state = alerts.empty_state()
    finding = rules.disk_findings(_health(90.0))
    first = {d["key"]: d["action"] for d in alerts.evaluate(state, finding, now=0, maintenance=True)[0]}
    assert first == {"disk_warn": "alert", "disk_critical": "alert"}
    later = {d["key"]: d["action"] for d in alerts.evaluate(state, finding, now=900, maintenance=False)[0]}
    assert later == {"disk_warn": "known", "disk_critical": "known"}
    again = {d["key"]: d["action"] for d in alerts.evaluate(state, finding, now=2 * 3600, maintenance=False)[0]}
    assert again == {"disk_warn": "known", "disk_critical": "alert"}


# --- stuck deploy ---------------------------------------------------------

def test_deploy_maintenance_pause_alerts_only_after_sixty_minutes():
    state = alerts.empty_state()
    health = {"pause_owner": "DEPLOY_MAINTENANCE", "execution_reason": "guarded deploy"}
    assert rules.deploy_stuck_findings(rules.track_deploy_pause(state, health, 0), health) == {}
    assert rules.deploy_stuck_findings(rules.track_deploy_pause(state, health, 3599), health) == {}
    found = rules.deploy_stuck_findings(rules.track_deploy_pause(state, health, 3600), health)
    assert set(found) == {"deploy_stuck"} and "60 min" in found["deploy_stuck"]


def test_deploy_pause_clock_resets_on_other_owner_and_survives_unknown():
    state = alerts.empty_state()
    rules.track_deploy_pause(state, {"pause_owner": "DEPLOY_MAINTENANCE"}, 0)
    assert rules.track_deploy_pause(state, None, 1800) == 1800
    assert rules.track_deploy_pause(state, {"pause_owner": "OPERATOR"}, 2000) == 0
    assert state["deploy_pause_since"] is None


def test_deploy_stuck_is_not_suppressed_by_maintenance():
    state = alerts.empty_state()
    decisions, _ = alerts.evaluate(state, {"deploy_stuck": "x"}, now=0, maintenance=True)
    assert decisions == [{"key": "deploy_stuck", "message": "x", "action": "alert"}]


# --- cadence --------------------------------------------------------------

def test_healthy_cadence_is_silent():
    assert rules.cadence_findings(_ready(), paused=False, now=NOW) == {}


def test_eval_stale_when_no_completed_evaluation_for_twenty_minutes():
    ready = _ready(
        evaluation_age_sec=1300.0,
        scheduled_ai_cycle={"last_poll_ts": NOW - 20, "completed_ts": NOW - 1300, "last_poll_entry_eligible": True},
    )
    assert set(rules.cadence_findings(ready, paused=False, now=NOW)) == {"eval_stale"}


def test_recent_scheduler_completion_counts_as_evaluation_progress():
    ready = _ready(evaluation_age_sec=5000.0)
    assert rules.cadence_findings(ready, paused=False, now=NOW) == {}


def test_ai_stale_uses_larger_of_45_minutes_and_three_bot_thresholds():
    assert rules.cadence_findings(_ready(ai_age_sec=44 * 60.0), paused=False, now=NOW) == {}
    found = rules.cadence_findings(_ready(ai_age_sec=46 * 60.0), paused=False, now=NOW)
    assert set(found) == {"ai_stale"}
    slow = _ready(ai_age_sec=46 * 60.0, ai_stale_after_sec=1200.0)
    assert rules.cadence_findings(slow, paused=False, now=NOW) == {}


def test_dead_scheduler_poll_is_eval_stale_even_when_entries_blocked():
    ready = _ready(scheduled_ai_cycle={"last_poll_ts": NOW - 900, "last_poll_entry_eligible": False, "stage": "X"})
    found = rules.cadence_findings(ready, paused=False, now=NOW)
    assert set(found) == {"eval_stale"} and "has not polled" in found["eval_stale"]


def test_blocked_entries_paused_startup_or_unknown_never_flag_cadence():
    blocked = _ready(
        evaluation_age_sec=9000.0, ai_age_sec=9000.0,
        scheduled_ai_cycle={"last_poll_ts": NOW - 20, "completed_ts": NOW - 9000, "last_poll_entry_eligible": False},
    )
    assert rules.cadence_findings(blocked, paused=False, now=NOW) == {}
    stale = _ready(ai_age_sec=9000.0)
    assert rules.cadence_findings(stale, paused=True, now=NOW) == {}
    assert rules.cadence_findings(stale, paused=None, now=NOW) == {}
    booting = _ready(ai_age_sec=9000.0, process_startup_age_sec=60.0)
    assert rules.cadence_findings(booting, paused=False, now=NOW) == {}
    assert rules.cadence_findings(None, paused=False, now=NOW) == {}


def test_cadence_needs_two_runs_and_is_suppressed_during_deploy():
    state = alerts.empty_state()
    run = lambda t, m=False: {d["key"]: d["action"] for d in alerts.evaluate(state, {"ai_stale": "x"}, now=t, maintenance=m)[0]}
    assert run(0, True) == {"ai_stale": "suppressed"}
    state = alerts.empty_state()
    assert run(0) == {"ai_stale": "pending"}
    assert run(900) == {"ai_stale": "alert"}


# --- transfer lag ---------------------------------------------------------

def _transfer(**transfer):
    return {"volume": {"used_pct": 10.0, "transfer": transfer}}


def test_disabled_segment_shipping_is_a_finding_because_nothing_else_transfers():
    found = rules.transfer_findings(_transfer(segments_enabled=False))
    assert "segment shipping is disabled" in found["transfer_lag"]
    assert not hasattr(rules, "LEGACY_ACK_STALE_SEC")


def test_segment_shipper_lag_error_and_stale_status():
    healthy = _transfer(segments_enabled=True, segment_status_present=True, segment_status_age_sec=60.0,
                        shipped_seq=50, laptop_acked_seq=40, last_error=None)
    assert rules.transfer_findings(healthy) == {}
    lagging = _transfer(segments_enabled=True, segment_status_present=True, segment_status_age_sec=3600.0,
                        shipped_seq=100, laptop_acked_seq=10, last_error="LOW_DISK_SKIPPED")
    message = rules.transfer_findings(lagging)["transfer_lag"]
    assert "60 min old" in message and "LOW_DISK_SKIPPED" in message and "90 segments behind" in message
    missing = _transfer(segments_enabled=True, segment_status_present=False)
    assert "has not written a status file" in rules.transfer_findings(missing)["transfer_lag"]


def test_retired_legacy_ack_age_never_raises_a_finding():
    healthy = _transfer(segments_enabled=True, segment_status_present=True, segment_status_age_sec=60.0,
                        shipped_seq=5, laptop_acked_seq=5, last_error=None, legacy_ack_age_sec=4 * 86400.0)
    assert rules.transfer_findings(healthy) == {}


def test_transfer_lag_is_informational_and_never_opens_incident():
    state = alerts.empty_state()
    for t in (0, 3600, 7200):
        decisions, _ = alerts.evaluate(
            state, {"transfer_lag": "behind"}, now=t, maintenance=False, informational=frozenset({"transfer_lag"})
        )
        assert decisions == [{"key": "transfer_lag", "message": "behind", "action": "info"}]
    assert state["conditions"] == {}
    assert not alerts.active_alerted(state)


def test_transfer_lag_pages_once_segments_are_declared_live():
    state = alerts.empty_state()
    actions = [
        {d["key"]: d["action"] for d in alerts.evaluate(state, {"transfer_lag": "x"}, now=t, maintenance=False)[0]}
        for t in (0, 3600)
    ]
    assert actions == [{"transfer_lag": "pending"}, {"transfer_lag": "alert"}]
