import json
from pathlib import Path

import pytest

import unattended_proof as up

T0 = 1_790_000_000.0
LANES = ["FAMILY_CHANDELIER_3", "FAMILY_ATR_TARGET_2_5", "FAMILY_ATR_TRAIL"]


def _runtime(now, **over):
    snap = {
        "schema": "fly_runtime_snapshot_v1", "observedAt": up.iso(now - 30), "ok": True,
        "execution_paused": False, "pause_owner": None, "execution_reason": "", "manual_admin_pause": False,
        "live_armed": False, "bitfinex_live_enabled": False, "git_rev": "aaaaaaaaaaaa",
        "tile_registry_signature": "sig", "active_tile_lanes": list(LANES),
        "research_lane_enabled": {lane: True for lane in LANES},
        "strategy_progress": {
            "ai_progressing": True, "ai_age_sec": 100.0, "ai_stale_after_sec": 300.0, "evaluation_age_sec": 90.0,
            "process_startup_age_sec": 9000.0, "ws_age_sec": 0.5, "ws_progressing": True,
            "scheduled_ai_cycle": {"completed_ts": now - 120, "last_poll_ts": now - 40,
                                   "last_poll_entry_eligible": True, "stage": "IDLE"},
        },
    }
    snap.update(over)
    return snap


def _inputs(now, **over):
    base = {
        "runtime": _runtime(now),
        "head": {"observedAt": up.iso(now - 20), "ok": True, "shipped_seq": 100, "laptop_acked_seq": 98,
                 "pruning_enabled": False},
        "relay": {"observedAt": up.iso(now - 20), "ok": True, "relayExecutionMode": "PAUSED", "relayArmedAt": None},
        "analyzer": {"lastCompletedGenerationAt": up.iso(now - 600)},
        "alerts": {"checkedAt": up.iso(now - 10), "alerts": []},
        "baseline": {"git_rev": "aaaaaaaaaaaa"},
        "previous": None,
        "manual_entries": [],
        "now": now,
    }
    base.update(over)
    return base


def test_healthy_row_passes_every_check():
    row = up.evaluate_row(**_inputs(T0 + 60))
    assert row["status"] == "PASS", row
    assert set(row["checks"]) == {"paper_running", "tiles_all_on", "ai_advancing", "ws_fresh", "segments_acked",
                                  "analyzer_fresh", "no_critical_alarms", "live_disarmed", "no_manual_intervention"}


def test_missing_or_stale_runtime_evidence_fails_closed():
    now = T0 + 60
    assert up.evaluate_row(**_inputs(now, runtime=None))["status"] == "FAIL"
    stale = _runtime(now, observedAt=up.iso(now - 3600))
    row = up.evaluate_row(**_inputs(now, runtime=stale))
    assert row["status"] == "FAIL" and row["checks"]["paper_running"]["ok"] is None


def test_toggles_unobserved_is_not_all_on():
    now = T0 + 60
    runtime = _runtime(now, research_lane_enabled=None, toggles_error="ADMIN_TOKEN_MISSING")
    row = up.evaluate_row(**_inputs(now, runtime=runtime))
    assert row["checks"]["tiles_all_on"]["ok"] is None and row["status"] == "FAIL"


def test_one_tile_off_fails():
    now = T0 + 60
    runtime = _runtime(now, research_lane_enabled={**{l: True for l in LANES}, "FAMILY_ATR_TRAIL": False})
    row = up.evaluate_row(**_inputs(now, runtime=runtime))
    assert row["failed_checks"] == ["tiles_all_on"]


def test_deploy_maintenance_pause_is_a_boundary_but_manual_pause_fails():
    now = T0 + 60
    boundary = up.evaluate_row(**_inputs(now, runtime=_runtime(now, execution_paused=True, pause_owner="DEPLOY_MAINTENANCE")))
    assert boundary["status"] == "BOUNDARY"
    manual = up.evaluate_row(**_inputs(now, runtime=_runtime(now, execution_paused=True, pause_owner="ADMIN_MANUAL",
                                                               manual_admin_pause=True)))
    assert manual["status"] == "FAIL"
    assert {"paper_running", "no_manual_intervention"} <= set(manual["failed_checks"])


def test_ai_cycle_not_advancing_since_previous_row_fails():
    now = T0 + 1800
    runtime = _runtime(now)
    previous = {"observed": {"ai_cycle_completed_ts": runtime["strategy_progress"]["scheduled_ai_cycle"]["completed_ts"]}}
    row = up.evaluate_row(**_inputs(now, runtime=runtime, previous=previous))
    assert row["failed_checks"] == ["ai_advancing"]


def test_segment_lag_stuck_ack_and_pruning_fail():
    now = T0 + 60
    lagging = {"observedAt": up.iso(now), "ok": True, "shipped_seq": 200, "laptop_acked_seq": 100, "pruning_enabled": False}
    assert up.evaluate_row(**_inputs(now, head=lagging))["failed_checks"] == ["segments_acked"]
    stuck = {"observedAt": up.iso(now), "ok": True, "shipped_seq": 110, "laptop_acked_seq": 98, "pruning_enabled": False}
    previous = {"observed": {"shipped_seq": 100, "laptop_acked_seq": 98, "ai_cycle_completed_ts": 1.0}}
    assert "segments_acked" in up.evaluate_row(**_inputs(now, head=stuck, previous=previous))["failed_checks"]
    pruning = {"observedAt": up.iso(now), "ok": True, "shipped_seq": 100, "laptop_acked_seq": 100, "pruning_enabled": True}
    assert up.evaluate_row(**_inputs(now, head=pruning))["failed_checks"] == ["segments_acked"]


def test_stale_analyzer_critical_alarm_armed_relay_and_journal_fail():
    now = T0 + 60
    assert up.evaluate_row(**_inputs(now, analyzer={"lastCompletedGenerationAt": up.iso(now - 46 * 60)}))["failed_checks"] == ["analyzer_fresh"]
    alarms = {"checkedAt": up.iso(now), "alerts": [{"code": "SEGMENT_PULL_DEAD", "severity": "critical"}]}
    assert up.evaluate_row(**_inputs(now, alerts=alarms))["failed_checks"] == ["no_critical_alarms"]
    armed = {"observedAt": up.iso(now), "ok": True, "relayExecutionMode": "ARMED", "relayArmedAt": up.iso(now)}
    assert up.evaluate_row(**_inputs(now, relay=armed))["failed_checks"] == ["live_disarmed"]
    journal = [{"at": up.iso(now - 5), "action": "toggled tile"}]
    assert up.evaluate_row(**_inputs(now, manual_entries=journal))["failed_checks"] == ["no_manual_intervention"]


def _row(at, status="PASS"):
    return {"kind": "ROW", "at": up.iso(at), "status": status, "failed_checks": []}


def test_verdict_pass_needs_full_coverage_and_no_fail_rows():
    ends = T0 + 48 * 3600
    rows = [_row(T0 + 60 + i * 1800) for i in range(97)]
    assert up.verdict(rows, t0=T0, ends_at=ends, now=ends + 1)["result"] == "PASS"
    assert up.verdict(rows[:10], t0=T0, ends_at=ends, now=T0 + 10 * 1800)["result"] == "IN_PROGRESS"
    gapped = rows[:20] + rows[23:]
    assert up.verdict(gapped, t0=T0, ends_at=ends, now=ends + 1)["result"] == "FAIL"
    failed = list(rows)
    failed[5] = {**failed[5], "status": "FAIL", "failed_checks": ["ws_fresh"]}
    result = up.verdict(failed, t0=T0, ends_at=ends, now=ends + 1)
    assert result["result"] == "FAIL" and "ws_fresh" in result["reasons"][0]


def test_verdict_fails_a_boundary_that_never_resumes():
    ends = T0 + 48 * 3600
    rows = [_row(T0 + 60 + i * 1800, "BOUNDARY" if 10 <= i <= 13 else "PASS") for i in range(97)]
    assert up.verdict(rows, t0=T0, ends_at=ends, now=ends + 1)["result"] == "FAIL"
    short = [_row(T0 + 60 + i * 1800, "BOUNDARY" if i == 10 else "PASS") for i in range(97)]
    assert up.verdict(short, t0=T0, ends_at=ends, now=ends + 1)["result"] == "PASS"


def _state(tmp_path, now):
    state = tmp_path / "state"
    (state / "alerts").mkdir(parents=True)
    inputs = _inputs(now)
    (state / up.RUNTIME_SNAPSHOT).write_text(json.dumps(inputs["runtime"]), encoding="utf-8")
    (state / up.HEAD_SNAPSHOT).write_text(json.dumps(inputs["head"]), encoding="utf-8")
    (state / up.RELAY_SNAPSHOT).write_text(json.dumps(inputs["relay"]), encoding="utf-8")
    (state / up.ANALYZER_STATUS).write_text(json.dumps(inputs["analyzer"]), encoding="utf-8")
    (state / up.ACTIVE_ALERTS).write_text(json.dumps(inputs["alerts"]), encoding="utf-8")
    return state


def test_start_then_check_writes_rows_on_cadence_and_refuses_double_start(tmp_path):
    state = _state(tmp_path, T0)
    record = up.start(state, tmp_path / "diag", T0)
    receipt = Path(record["receipt"])
    assert receipt.name == f"unattended-proof-{up.stamp(T0)}.jsonl"
    assert record["baseline"]["git_rev"] == "aaaaaaaaaaaa"
    with pytest.raises(SystemExit):
        up.start(state, tmp_path / "diag", T0 + 5)
    assert up.check(state, T0 + 10)["row"] == "PASS"
    assert up.check(state, T0 + 300)["row"] is None  # not due yet
    lines = [json.loads(line) for line in receipt.read_text(encoding="utf-8").splitlines()]
    assert [l["kind"] for l in lines] == ["START", "ROW"]
    active = json.loads((state / "unattended-proof" / up.ACTIVE_FILE).read_text(encoding="utf-8"))
    assert active["status"]["result"] == "IN_PROGRESS"


def test_force_restart_needs_a_reason_and_closes_the_old_window_as_superseded(tmp_path):
    state = _state(tmp_path, T0)
    old = Path(up.start(state, tmp_path / "diag", T0)["receipt"])
    up.check(state, T0 + 10)
    with pytest.raises(SystemExit, match="--reason"):
        up.start(state, tmp_path / "diag", T0 + 400, force=True)
    new = up.start(state, tmp_path / "diag", T0 + 400, force=True, reason="monitor false alarm fixed")
    assert Path(new["receipt"]) != old
    closing = json.loads(old.read_text(encoding="utf-8").splitlines()[-1])
    assert closing["kind"] == "VERDICT" and closing["result"] == "SUPERSEDED"
    assert closing["reason"] == "monitor false alarm fixed" and closing["rows"] == 1
    assert json.loads(old.with_suffix(".verdict.json").read_text(encoding="utf-8"))["result"] == "SUPERSEDED"


def test_start_refuses_without_a_fresh_runtime_baseline_or_under_onedrive(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(SystemExit):
        up.start(state, tmp_path / "diag", T0)
    with pytest.raises(SystemExit):
        up.start(tmp_path / "OneDrive" / "state", tmp_path / "diag", T0)


def test_manual_journal_written_with_a_bom_is_still_read(tmp_path):
    line = json.dumps({"at": up.iso(T0 + 60), "action": "moved checkout"})
    (tmp_path / up.MANUAL_JOURNAL).write_bytes(b"\xef\xbb\xbf" + line.encode("utf-8") + b"\n")
    assert [e["action"] for e in up._manual_entries(tmp_path, T0)] == ["moved checkout"]


def test_check_without_window_is_a_noop(tmp_path):
    assert up.check(tmp_path, T0)["result"] == "NO_ACTIVE_WINDOW"
