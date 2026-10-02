"""Schedule-gap heartbeat, image-deploy-only suppression, and crash/reset-safe incident closing."""

import json

import fly_monitor_alerts as alerts
import fly_monitor_heartbeat as hb
import fly_monitor_run as runner
from test_fly_monitor_run import _FakeIssues

NOW = 1_790_938_800.0
MIN = 60.0


# --- heartbeat format and gap -------------------------------------------------

def test_heartbeat_round_trips_and_accepts_legacy_forms():
    value = hb.format_heartbeat(NOW, run_id="123", attempt="1", crashed=False, restored=True)
    data = json.loads(value)
    assert data == {"at": hb.iso(NOW), "run_id": "123", "attempt": "1", "crashed": False, "restored": True}
    assert hb.parse_heartbeat(value) == NOW
    assert hb.parse_heartbeat(str(int(NOW))) == NOW
    assert hb.parse_heartbeat(hb.iso(NOW)) == NOW
    for bad in (None, "", "{not json", "garbage", '{"at": "2026-10-02T10:00:00"}'):
        assert hb.parse_heartbeat(bad) is None


def test_gap_uses_newest_evidence_and_fires_over_45_minutes():
    found, note = hb.schedule_gap_findings({"variable": NOW - 63 * MIN, "runs API": NOW - 50 * MIN}, NOW)
    assert "50 min" in found["monitor_schedule_gap"] and "runs API" in found["monitor_schedule_gap"]
    found, note = hb.schedule_gap_findings({"variable": NOW - 63 * MIN, "cached state": NOW - 15 * MIN}, NOW)
    assert found == {} and "cached state" in note
    assert hb.schedule_gap_findings({"variable": NOW - 45 * MIN}, NOW)[0] == {}
    assert hb.schedule_gap_findings({"variable": None, "cached state": None}, NOW)[0] == {}


def test_previous_run_skips_the_current_run():
    runs = [
        {"id": 9, "created_at": hb.iso(NOW - 10)},
        {"id": 8, "created_at": hb.iso(NOW - 15 * MIN)},
    ]
    assert hb.previous_run_ts(runs, "9", NOW) == NOW - 15 * MIN
    assert hb.previous_run_ts(runs, "7", NOW) == NOW - 10
    assert hb.previous_run_ts([], "9", NOW) is None


def test_schedule_gap_is_a_warning_that_resolves_on_the_next_runs():
    state = alerts.empty_state()
    gap = {"monitor_schedule_gap": "63 min"}
    decision = alerts.evaluate(state, gap, now=NOW, maintenance=True)[0][0]
    assert decision["action"] == "alert" and decision["severity"] == "warning"
    assert not alerts.is_failing(decision)
    alerts.evaluate(state, {}, now=NOW + 15 * MIN, maintenance=False)
    assert alerts.evaluate(state, {}, now=NOW + 30 * MIN, maintenance=False)[1][0]["key"] == "monitor_schedule_gap"


def test_main_schedule_gap_reads_variable_state_and_runs(monkeypatch):
    monkeypatch.setenv(hb.HEARTBEAT_VARIABLE, hb.format_heartbeat(
        NOW - 70 * MIN, run_id="1", attempt="1", crashed=False, restored=True))

    class _GH:
        def previous_monitor_run_ts(self, now):
            return NOW - 64 * MIN

    monkeypatch.setattr(runner, "GitHub", _GH)
    state = alerts.empty_state()
    state["last_run"] = {"ts": NOW - 66 * MIN}
    notes = []
    found = runner.schedule_gap(state, NOW, notes)
    assert "64 min" in found["monitor_schedule_gap"] and "Actions runs API" in notes[-1]


def test_heartbeat_write_is_skipped_without_the_variables_token(monkeypatch):
    monkeypatch.delenv("FLY_MONITOR_VARIABLES_TOKEN", raising=False)
    assert "not written" in runner.write_heartbeat(NOW, crashed=False, restored=True)


def test_heartbeat_write_patches_then_creates_the_variable(monkeypatch):
    calls = []

    class _GH:
        def __init__(self, token=None):
            assert token == "t0ken"

        def set_variable(self, name, value):
            calls.append((name, json.loads(value)))

    monkeypatch.setenv("FLY_MONITOR_VARIABLES_TOKEN", "t0ken")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    monkeypatch.setattr(runner, "GitHub", _GH)
    note = runner.write_heartbeat(NOW, crashed=True, restored=False)
    assert "t0ken" not in note
    assert calls == [("FLY_MONITOR_HEARTBEAT", {
        "at": hb.iso(NOW), "run_id": "42", "attempt": "3", "crashed": True, "restored": False,
    })]


def test_set_variable_falls_back_to_create_on_404(monkeypatch):
    import io
    import urllib.error

    gh = runner.GitHub.__new__(runner.GitHub)
    seen = []

    def call(path, method="GET", body=None):
        seen.append((method, path))
        if method == "PATCH":
            raise urllib.error.HTTPError(path, 404, "nf", {}, io.BytesIO(b""))
        return None

    gh.call = call
    gh.set_variable("FLY_MONITOR_HEARTBEAT", "{}")
    assert seen == [("PATCH", "/actions/variables/FLY_MONITOR_HEARTBEAT"), ("POST", "/actions/variables")]


# --- deploy suppression ----------------------------------------------------------

def _run(run_id, event, age_min, status="in_progress"):
    return {"id": run_id, "event": event, "status": status, "created_at": hb.iso(NOW - age_min * MIN)}


def _jobs(mapping):
    return lambda run_id: mapping.get(run_id, [])


def test_inspect_or_snapshot_dispatch_never_suppresses():
    runs = [_run(1, "workflow_dispatch", 5)]
    jobs = _jobs({1: [{"name": "test-and-deploy", "conclusion": "skipped"}, {"name": "inspect-runtime"}]})
    suppress, note = runner.deploy_suppression(runs, jobs, NOW)
    assert suppress is False and "not an image deploy" in note


def test_queued_dispatch_without_jobs_does_not_suppress():
    assert runner.deploy_suppression([_run(1, "workflow_dispatch", 1, "queued")], _jobs({}), NOW)[0] is False


def test_push_or_deploy_mode_dispatch_suppresses_for_45_minutes_only():
    jobs = _jobs({2: [{"name": "test-and-deploy", "conclusion": None}]})
    assert runner.deploy_suppression([_run(1, "push", 10)], jobs, NOW)[0] is True
    assert runner.deploy_suppression([_run(2, "workflow_dispatch", 44)], jobs, NOW)[0] is True
    suppress, note = runner.deploy_suppression([_run(2, "workflow_dispatch", 46)], jobs, NOW)
    assert suppress is False and "older than 45 min" in note
    assert runner.deploy_suppression([_run(1, "push", 10, "completed")], jobs, NOW)[0] is False


def test_maintenance_grace_is_45_minutes_and_safety_is_never_suppressed():
    assert alerts.MAINTENANCE_GRACE_SEC == 45 * MIN
    state = alerts.empty_state()
    for t in (0, 15 * MIN, 30 * MIN):
        actions = {d["key"]: d["action"] for d in alerts.evaluate(
            state, {"not_ready": "x", "safety": "live_armed=True"}, now=NOW + t, maintenance=True)[0]}
        assert actions["not_ready"] == "suppressed" and actions["safety"] in ("alert", "known")
    late = {d["key"]: d["action"] for d in alerts.evaluate(
        state, {"not_ready": "x"}, now=NOW + 45 * MIN, maintenance=True)[0]}
    assert late["not_ready"] == "alert"


# --- crash / restored / clean-streak gating -----------------------------------------

def test_crashed_run_neither_clears_nor_resolves_alerted_conditions():
    state = alerts.empty_state()
    alerts.evaluate(state, {"disk_warn": "72%"}, now=NOW, maintenance=False)
    for i in range(1, 4):
        _, resolved = alerts.evaluate(
            state, {"monitor_error": "monitor crashed"}, now=NOW + i * 15 * MIN, maintenance=False, crashed=True
        )
        assert resolved == []
        assert state["conditions"]["disk_warn"]["clear_runs"] == 0
        assert state["clean_streak"] == 0
    assert not alerts.can_close_incident(state, crashed=True, restored=True)


def test_reset_state_needs_two_restored_clean_runs_before_close():
    state = alerts.empty_state()
    alerts.evaluate(state, {}, now=NOW, maintenance=False, restored=False)
    assert state["clean_streak"] == 0
    assert not alerts.can_close_incident(state, crashed=False, restored=False)
    alerts.evaluate(state, {}, now=NOW + 15 * MIN, maintenance=False, restored=True)
    assert not alerts.can_close_incident(state, crashed=False, restored=True)
    alerts.evaluate(state, {}, now=NOW + 30 * MIN, maintenance=False, restored=True)
    assert alerts.can_close_incident(state, crashed=False, restored=True)


def test_normal_flow_still_closes_after_two_clean_runs():
    state = alerts.empty_state()
    alerts.evaluate(state, {"safety": "x"}, now=NOW, maintenance=False)
    alerts.evaluate(state, {}, now=NOW + 15 * MIN, maintenance=False)
    assert not alerts.can_close_incident(state, crashed=False, restored=True)
    _, resolved = alerts.evaluate(state, {}, now=NOW + 30 * MIN, maintenance=False)
    assert [r["key"] for r in resolved] == ["safety"]
    assert alerts.can_close_incident(state, crashed=False, restored=True)


def test_sync_issue_keeps_open_issue_open_after_cache_miss(monkeypatch):
    fake = _FakeIssues()
    monkeypatch.setattr(runner, "GitHub", fake)
    fake.issues.append({"number": 1, "state": "open", "body": "", "labels": ["fly-monitor-incident"]})
    fresh = alerts.empty_state()
    decisions, resolved = alerts.evaluate(fresh, {}, now=NOW, maintenance=False, restored=False)
    can_close = alerts.can_close_incident(fresh, crashed=False, restored=False)
    runner.sync_issue(fresh, decisions, resolved, NOW, can_close=can_close)
    assert fake.issues[0]["state"] == "open" and fake.comments == []
    assert "Not closed yet" in fake.issues[0]["body"]


def test_sync_issue_keeps_open_issue_open_after_crash(monkeypatch):
    fake = _FakeIssues()
    monkeypatch.setattr(runner, "GitHub", fake)
    fake.issues.append({"number": 1, "state": "open", "body": "", "labels": ["fly-monitor-incident"]})
    fresh = alerts.empty_state()
    decisions, resolved = alerts.evaluate(
        fresh, {"monitor_error": "crashed"}, now=NOW, maintenance=False, crashed=True
    )
    runner.sync_issue(fresh, decisions, resolved, NOW,
                      can_close=alerts.can_close_incident(fresh, crashed=True, restored=True))
    assert fake.issues[0]["state"] == "open"


def test_warning_alert_opens_issue_without_failing_run(monkeypatch):
    fake = _FakeIssues()
    monkeypatch.setattr(runner, "GitHub", fake)
    state = alerts.empty_state()
    decisions, resolved = alerts.evaluate(state, {"monitor_schedule_gap": "63 min"}, now=NOW, maintenance=False)
    runner.sync_issue(state, decisions, resolved, NOW)
    assert len(fake.issues) == 1 and "| warning |" in fake.issues[0]["body"]
    assert not any(alerts.is_failing(d) for d in decisions)


def test_load_state_reports_restored(tmp_path):
    path = tmp_path / "state.json"
    assert runner.load_state(path) == (alerts.empty_state(), False)
    path.write_text("{broken", encoding="utf-8")
    assert runner.load_state(path)[1] is False
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert runner.load_state(path)[1] is False
    saved = alerts.empty_state()
    saved["clean_streak"] = 3
    saved["since"] = {"entries_blocked": NOW}
    saved["last_run"] = {"ts": NOW, "run_id": "1", "restored": True, "crashed": False}
    path.write_text(json.dumps(saved), encoding="utf-8")
    state, restored = runner.load_state(path)
    assert restored is True and state == saved


def test_collect_wires_subsystem_rules_with_live_shapes(monkeypatch):
    import copy

    import test_fly_monitor_subsystems as fx

    health = copy.deepcopy(fx.HEALTH)
    health["source_git_rev"] = "a" * 12
    health["volume"]["transfer"] = {"segments_enabled": True, "segment_status_present": True}
    health["research_collection"]["multiverse"]["v3_reconcile_worker"].update(phase="RECONCILING",
                                                                             phase_started_ts=NOW - 900)
    ready = copy.deepcopy(fx.READY)
    ready.update(ok=True, process_ready=True, strategy_progress={**ready["strategy_progress"], "ok": True})
    relay = copy.deepcopy(fx.RELAY)
    relay["state_integrity"]["live_armed"] = True
    payloads = {
        runner.HEALTH_URL: health, runner.READY_URL: ready, runner.STATUS_URL: fx.STATUS,
        runner.RELAY_URL: relay, runner.SYSTEM_HEALTH_URL: {**fx.SYSTEM_HEALTH, "stale": True},
        runner.RELAY_STATE_URL: {"relay_cache": {"age_sec": 1.9}},
    }

    class _GH:
        def deploy_state(self, now=None):
            return {"deployed": "a" * 40, "in_flight": "", "deploy_active": False, "deploy_note": "none"}

    monkeypatch.setattr(runner, "GitHub", _GH)
    monkeypatch.setattr(runner, "probe", lambda url, accept, attempts=3: (200, payloads[url]))
    monkeypatch.setattr(runner, "git_resolve", lambda rev: "a" * 40)
    monkeypatch.setattr(runner, "on_master", lambda sha: True)
    monkeypatch.setattr(runner, "require_tile_registry", lambda *a, **k: None)
    monkeypatch.setenv("LAPTOP_CHAIN_HEARTBEAT", str(int(NOW)))
    findings, maintenance, notes, escalate = runner.collect(alerts.empty_state(), NOW)
    assert set(findings) == {"collector_v3_reconcile_stalled", "laptop_health_silent"}
    assert maintenance is False
    assert escalate == frozenset({"relay_stale_owner_pending"})


def test_main_marks_crash_and_does_not_close(monkeypatch, tmp_path):
    fake = _FakeIssues()
    fake.issues.append({"number": 1, "state": "open", "body": "", "labels": ["fly-monitor-incident"]})
    monkeypatch.setattr(runner, "GitHub", fake)
    monkeypatch.setattr(runner, "collect", lambda state, now: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(runner, "schedule_gap", lambda state, now, notes: {})
    monkeypatch.setenv("FLY_MONITOR_STATE", str(tmp_path / "state.json"))
    monkeypatch.delenv("FLY_MONITOR_VARIABLES_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert runner.main() == 0
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["last_run"]["crashed"] is True and saved["last_run"]["restored"] is False
    assert fake.issues[0]["state"] == "open"
