"""Deduplication policy for the scheduled Fly monitor."""

import fly_monitor_alerts as alerts

HOUR = 3600.0


def _run(state, findings, now, maintenance=False):
    decisions, resolved = alerts.evaluate(state, findings, now=now, maintenance=maintenance)
    return {d["key"]: d["action"] for d in decisions}, [r["key"] for r in resolved]


def test_safety_alerts_immediately_and_realerts_hourly_not_every_run():
    state = alerts.empty_state()
    finding = {"safety": "live_armed=True"}
    assert _run(state, finding, 0)[0] == {"safety": "alert"}
    assert _run(state, finding, 900)[0] == {"safety": "known"}
    assert _run(state, finding, 1800)[0] == {"safety": "known"}
    assert _run(state, finding, HOUR)[0] == {"safety": "alert"}


def test_safety_is_never_suppressed_by_deploy_maintenance():
    state = alerts.empty_state()
    assert _run(state, {"safety": "x"}, 0, maintenance=True)[0] == {"safety": "alert"}


def test_single_timeout_or_503_blip_never_alerts():
    state = alerts.empty_state()
    assert _run(state, {"not_ready": "503"}, 0)[0] == {"not_ready": "pending"}
    assert _run(state, {}, 900) == ({}, [])
    assert state["conditions"] == {}
    assert _run(state, {"unreachable": "timeout"}, 1800)[0] == {"unreachable": "pending"}
    assert _run(state, {}, 2700) == ({}, [])


def test_persistent_bot_down_alerts_once_then_every_six_hours():
    state = alerts.empty_state()
    finding = {"unreachable": "timeout"}
    assert _run(state, finding, 0)[0] == {"unreachable": "pending"}
    assert _run(state, finding, 900)[0] == {"unreachable": "alert"}
    actions = [_run(state, finding, 900 + 900 * i)[0]["unreachable"] for i in range(1, 24)]
    assert set(actions) == {"known"}
    assert _run(state, finding, 900 + 6 * HOUR)[0] == {"unreachable": "alert"}


def test_not_ready_needs_three_runs_and_thirty_minutes():
    state = alerts.empty_state()
    finding = {"not_ready": "stalled"}
    assert _run(state, finding, 0)[0] == {"not_ready": "pending"}
    assert _run(state, finding, 600)[0] == {"not_ready": "pending"}
    assert _run(state, finding, 1200)[0] == {"not_ready": "pending"}
    assert _run(state, finding, 1800)[0] == {"not_ready": "alert"}


def test_deploy_maintenance_suppresses_transitional_states_within_grace_only():
    state = alerts.empty_state()
    finding = {"not_ready": "restarting", "revision_drift": "new image"}
    for t in range(0, int(alerts.MAINTENANCE_GRACE_SEC), 900):
        assert set(_run(state, finding, t, maintenance=True)[0].values()) == {"suppressed"}
    stuck = _run(state, finding, alerts.MAINTENANCE_GRACE_SEC, maintenance=True)[0]
    assert stuck == {"not_ready": "alert", "revision_drift": "alert"}


def test_deploy_completion_clears_suppressed_conditions_without_alerting():
    state = alerts.empty_state()
    _run(state, {"not_ready": "restarting"}, 0, maintenance=True)
    _run(state, {"not_ready": "restarting"}, 900, maintenance=True)
    assert _run(state, {}, 1800) == ({}, [])
    assert state["maintenance_since"] is None


def test_alerted_condition_resolves_after_two_clean_runs():
    state = alerts.empty_state()
    _run(state, {"safety": "x"}, 0)
    assert alerts.active_alerted(state)
    assert _run(state, {}, 900) == ({}, [])
    assert alerts.active_alerted(state)
    assert _run(state, {}, 1800) == ({}, ["safety"])
    assert not alerts.active_alerted(state)


def test_flapping_alerted_condition_does_not_realert_between_clean_runs():
    state = alerts.empty_state()
    _run(state, {"safety": "x"}, 0)
    _run(state, {}, 900)
    assert _run(state, {"safety": "x"}, 1800)[0] == {"safety": "known"}


def test_long_pause_is_tracked_across_runs_and_unknown_keeps_clock():
    state = alerts.empty_state()
    assert alerts.track_pause(state, True, 0) == 0
    assert alerts.track_pause(state, None, HOUR) == HOUR
    assert alerts.track_pause(state, True, alerts.PAUSED_ALERT_SEC) == alerts.PAUSED_ALERT_SEC
    assert alerts.track_pause(state, False, alerts.PAUSED_ALERT_SEC + 1) == 0
    assert state["paused_since"] is None


def test_corrupt_or_foreign_state_starts_clean():
    assert alerts.normalize_state(None) == alerts.empty_state()
    assert alerts.normalize_state({"version": 99}) == alerts.empty_state()
    state = alerts.normalize_state(
        {"version": 1, "conditions": {"safety": {"runs": 1}, "bogus": {}}, "paused_since": "x"}
    )
    assert list(state["conditions"]) == ["safety"]
    assert state["paused_since"] is None


def test_pre_existing_cached_state_gains_deploy_pause_clock():
    state = alerts.normalize_state(
        {"version": 1, "conditions": {}, "maintenance_since": None, "paused_since": 5.0}
    )
    assert state["deploy_pause_since"] is None and state["paused_since"] == 5.0


def test_test_alert_fires_once_and_resolves_after_two_clean_runs():
    state = alerts.empty_state()
    assert _run(state, {"test_alert": "synthetic"}, 0)[0] == {"test_alert": "alert"}
    assert _run(state, {}, 900) == ({}, [])
    assert _run(state, {}, 1800) == ({}, ["test_alert"])
