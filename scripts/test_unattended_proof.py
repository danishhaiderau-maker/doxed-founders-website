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
            "last_ai_success_at": up.iso(now - 130), "ai_consecutive_failures": 0,
            "ai_provider": {"last_model_echo": "deepseek-flash"},
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
        "head": {"observedAt": up.iso(now - 20), "ok": True, "shipped_seq": 100, "laptop_acked_seq": 100,
                 "pruning_enabled": False, "last_segment_at": now - 140},
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


def _deploys(now, *runs):
    return {"schema": "fly_deploy_runs_snapshot_v1", "observedAt": up.iso(now - 20), "ok": True,
            "workflow": "fly-bot-deploy.yml", "runs": list(runs)}


def _run(run_id, created, updated=None, status="in_progress", conclusion=""):
    return {"databaseId": run_id, "status": status, "conclusion": conclusion, "event": "push",
            "createdAt": up.iso(created), "updatedAt": up.iso(updated if updated is not None else created)}


def _deploy_pause(now, **over):
    # What the guarded workflow really sets: owner DEPLOY_MAINTENANCE and manual_admin_pause=true.
    return _runtime(now, execution_paused=True, pause_owner="DEPLOY_MAINTENANCE", manual_admin_pause=True, **over)


def test_guarded_deploy_pause_is_allowed_while_its_run_is_active_but_manual_pause_fails():
    now = T0 + 60
    deploys = _deploys(now, _run(36794875288, now - 900))
    row = up.evaluate_row(**_inputs(now, runtime=_deploy_pause(now), deploy_runs=deploys))
    assert row["status"] == up.ALLOWED_GUARDED_DEPLOY, row
    assert row["checks"]["no_manual_intervention"]["ok"] is True
    assert "ALLOWED_GUARDED_DEPLOY" in row["checks"]["no_manual_intervention"]["detail"]
    assert row["observed"]["deploy_run_id"] == 36794875288
    manual = up.evaluate_row(**_inputs(now, runtime=_runtime(now, execution_paused=True, pause_owner="ADMIN_MANUAL",
                                                               manual_admin_pause=True), deploy_runs=deploys))
    assert manual["status"] == "FAIL"
    assert {"paper_running", "no_manual_intervention"} <= set(manual["failed_checks"])


def test_deploy_pause_without_an_attributable_run_fails_closed():
    now = T0 + 60
    unattributed = up.evaluate_row(**_inputs(now, runtime=_deploy_pause(now), deploy_runs=_deploys(now)))
    assert unattributed["status"] == "FAIL"
    assert {"paper_running", "no_manual_intervention"} <= set(unattributed["failed_checks"])
    # A run that finished before the observation does not cover a later pause.
    finished = _deploys(now, _run(1, now - 3600, now - 1200, "completed", "success"))
    assert up.evaluate_row(**_inputs(now, runtime=_deploy_pause(now), deploy_runs=finished))["status"] == "FAIL"
    no_evidence = up.evaluate_row(**_inputs(now, runtime=_deploy_pause(now), deploy_runs=None))
    assert no_evidence["status"] == "FAIL" and no_evidence["checks"]["paper_running"]["ok"] is None
    failed_run = _deploys(now, _run(2, now - 900, now - 10, "completed", "failure"))
    assert up.evaluate_row(**_inputs(now, runtime=_deploy_pause(now), deploy_runs=failed_run))["status"] == "FAIL"


def test_guarded_deploy_pause_longer_than_the_bound_fails():
    now = T0 + 3600
    deploys = _deploys(now, _run(3, now - 3600))
    previous = {"status": up.ALLOWED_GUARDED_DEPLOY,
                "observed": {"deploy_pause_since": now - 30 - 46 * 60, "deploy_run_id": 3,
                             "ai_cycle_completed_ts": 1.0}}
    row = up.evaluate_row(**_inputs(now, runtime=_deploy_pause(now), deploy_runs=deploys, previous=previous))
    assert row["status"] == "FAIL" and "paper_running" in row["failed_checks"]
    assert "> 45 min" in row["checks"]["paper_running"]["detail"]


def test_after_the_boundary_the_run_must_succeed():
    now = T0 + 1800
    previous = {"status": up.ALLOWED_GUARDED_DEPLOY,
                "observed": {"deploy_run_id": 4, "pending_deploy_run_id": 4, "ai_cycle_completed_ts": 1.0}}
    ok = up.evaluate_row(**_inputs(now, previous=previous,
                                   deploy_runs=_deploys(now, _run(4, now - 2400, now - 600, "completed", "success"))))
    assert ok["status"] == "PASS", ok
    assert "Paper ACTIVE + 2 advancing AI cycles" in ok["checks"]["no_manual_intervention"]["detail"]
    assert "pending_deploy_run_id" not in ok["observed"]
    running = up.evaluate_row(**_inputs(now, previous=previous, deploy_runs=_deploys(now, _run(4, now - 2400))))
    assert running["status"] == "PASS" and running["observed"]["pending_deploy_run_id"] == 4
    later = now + 1800
    failed = up.evaluate_row(**_inputs(later, previous=running,
                                       deploy_runs=_deploys(later, _run(4, now - 2400, later - 60, "completed", "failure"))))
    assert failed["failed_checks"] == ["no_manual_intervention"]


def test_tiles_come_from_the_runtime_roster_not_a_fixed_lane_list():
    now = T0 + 60
    single = _runtime(now, active_tile_lanes=["FAMILY_XVENUE_SESSION_FOLLOW_60M"],
                      research_lane_enabled={"FAMILY_XVENUE_SESSION_FOLLOW_60M": True})
    row = up.evaluate_row(**_inputs(now, runtime=single))
    assert row["status"] == "PASS" and row["checks"]["tiles_all_on"]["detail"] == "1/1 tiles ON"
    off = _runtime(now, active_tile_lanes=["FAMILY_XVENUE_SESSION_FOLLOW_60M"],
                   research_lane_enabled={"FAMILY_XVENUE_SESSION_FOLLOW_60M": False, "FAMILY_ATR_TRAIL": True})
    assert up.evaluate_row(**_inputs(now, runtime=off))["failed_checks"] == ["tiles_all_on"]
    pair = ["TILE_A", "TILE_B"]
    both = _runtime(now, active_tile_lanes=pair, research_lane_enabled=dict.fromkeys(pair, True))
    row = up.evaluate_row(**_inputs(now, runtime=both))
    assert row["status"] == "PASS" and row["checks"]["tiles_all_on"]["detail"] == "2/2 tiles ON"
    half = _runtime(now, active_tile_lanes=pair,
                    research_lane_enabled={"TILE_A": True, "TILE_B": False})
    assert up.evaluate_row(**_inputs(now, runtime=half))["failed_checks"] == ["tiles_all_on"]
    source = Path(up.__file__).read_text(encoding="utf-8")
    assert "FAMILY_" not in source


def test_ai_cycle_not_advancing_since_previous_row_fails():
    now = T0 + 1800
    runtime = _runtime(now)
    previous = {"observed": {"ai_cycle_completed_ts": runtime["strategy_progress"]["scheduled_ai_cycle"]["completed_ts"]}}
    row = up.evaluate_row(**_inputs(now, runtime=runtime, previous=previous))
    assert row["failed_checks"] == ["ai_advancing"]


def test_ai_cycles_advancing_without_a_successful_response_fail():
    """2026-10-01 outage shape: cycles complete, every DeepSeek call times out."""
    now = T0 + 1800
    runtime = _runtime(now)
    runtime["strategy_progress"]["last_ai_success_at"] = up.iso(T0 - 60)
    runtime["strategy_progress"]["ai_consecutive_failures"] = 3
    previous = {"observed": {"ai_cycle_completed_ts": now - 1900, "ai_last_success_at": up.iso(T0 - 60)}}
    row = up.evaluate_row(**_inputs(now, runtime=runtime, previous=previous))
    assert row["failed_checks"] == ["ai_advancing"]
    assert "no SUCCESSFUL model response since the previous proof row" in row["checks"]["ai_advancing"]["detail"]
    assert row["observed"]["ai_consecutive_failures"] == 3


def test_ai_success_truth_missing_or_absent_fails_closed():
    now = T0 + 60
    runtime = _runtime(now)
    del runtime["strategy_progress"]["last_ai_success_at"]
    row = up.evaluate_row(**_inputs(now, runtime=runtime))
    assert row["failed_checks"] == ["ai_advancing"] and row["checks"]["ai_advancing"]["ok"] is None
    runtime["strategy_progress"]["last_ai_success_at"] = None
    assert up.evaluate_row(**_inputs(now, runtime=runtime))["failed_checks"] == ["ai_advancing"]
    runtime["strategy_progress"]["last_ai_success_at"] = up.iso(now - 20 * 60)
    row = up.evaluate_row(**_inputs(now, runtime=runtime))
    assert row["failed_checks"] == ["ai_advancing"] and "> 900s" in row["checks"]["ai_advancing"]["detail"]


def test_new_successful_response_since_previous_row_passes_and_records_served_model():
    now = T0 + 1800
    previous = {"observed": {"ai_cycle_completed_ts": now - 1900, "ai_last_success_at": up.iso(now - 1850)}}
    row = up.evaluate_row(**_inputs(now, previous=previous))
    assert row["status"] == "PASS", row
    assert row["observed"]["ai_served_model"] == "deepseek-flash"
    assert "last SUCCESSFUL response" in row["checks"]["ai_advancing"]["detail"]


def test_segment_lag_stuck_ack_and_pruning_fail():
    now = T0 + 60
    lagging = {"observedAt": up.iso(now), "ok": True, "shipped_seq": 200, "laptop_acked_seq": 100, "pruning_enabled": False}
    assert up.evaluate_row(**_inputs(now, head=lagging))["failed_checks"] == ["segments_acked"]
    stuck = {"observedAt": up.iso(now), "ok": True, "shipped_seq": 110, "laptop_acked_seq": 98, "pruning_enabled": False}
    previous = {"observed": {"shipped_seq": 100, "laptop_acked_seq": 98, "ai_cycle_completed_ts": 1.0}}
    assert "segments_acked" in up.evaluate_row(**_inputs(now, head=stuck, previous=previous))["failed_checks"]
    ahead = {"observedAt": up.iso(now), "ok": True, "shipped_seq": 100, "laptop_acked_seq": 100,
             "pruning_enabled": True, "pruned_through_seq": 90, "custody_through_seq": 80}
    assert up.evaluate_row(**_inputs(now, head=ahead))["failed_checks"] == ["segments_acked"]
    no_custody = {**ahead, "custody_through_seq": None}
    assert up.evaluate_row(**_inputs(now, head=no_custody))["failed_checks"] == ["segments_acked"]


def test_guarded_pruning_passes():
    now = T0 + 60
    guarded = {"observedAt": up.iso(now - 20), "ok": True, "shipped_seq": 100, "laptop_acked_seq": 100,
               "pruning_enabled": True, "prune_mode": "enforce", "pruned_through_seq": 80,
               "custody_through_seq": 90, "last_segment_at": now - 140}
    row = up.evaluate_row(**_inputs(now, head=guarded))
    assert "segments_acked" not in row["failed_checks"]
    assert row["observed"]["pruned_through_seq"] == 80
    dry = {**guarded, "prune_mode": "dry_run", "pruned_through_seq": None}
    assert "segments_acked" not in up.evaluate_row(**_inputs(now, head=dry))["failed_checks"]


def _head(now, shipped, acked, newest_age_sec):
    return {"observedAt": up.iso(now), "ok": True, "shipped_seq": shipped, "laptop_acked_seq": acked,
            "pruning_enabled": False, "last_segment_at": now - newest_age_sec}


def _pull(now, acked, age_sec=60, **over):
    status = {"schema": "laptop_segment_pull_status_v1", "finishedAt": up.iso(now - age_sec), "exitCode": 0,
              "appliedSeq": acked, "ackedSeq": acked, "remotePublishedSeq": acked, "error": None}
    status.update(over)
    return status


def _seg(now, head, previous=None, laptop_pull=None):
    prev = None if previous is None else {"observed": {"shipped_seq": previous[0], "laptop_acked_seq": previous[1],
                                                       "ai_cycle_completed_ts": 1.0}}
    return up.evaluate_row(**_inputs(now, head=head, previous=prev, laptop_pull=laptop_pull))["checks"]["segments_acked"]


def test_fly_ack_view_lag_confirmed_by_the_laptop_passes_once():
    # 2026-10-01T18:15:50Z: previous row 2008/2008; Fly published 2009/2010 by
    # 18:03:18 and the laptop acked both by 18:03:28, but Fly's ACK poll ran
    # only after its 13-27 min idle cycles, so the head still said 2008.
    now = T0 + 60
    head = _head(now, 2010, 2008, newest_age_sec=12.5 * 60)
    ok = _seg(now, head, previous=(2008, 2008), laptop_pull=_pull(now, 2010))
    assert ok["ok"] is True and "laptop acked 2010" in ok["detail"]
    assert _seg(now, head, previous=(2008, 2008))["ok"] is False
    assert _seg(now, head, previous=(2008, 2008), laptop_pull=_pull(now, 2010, age_sec=11 * 60))["ok"] is False
    assert _seg(now, head, previous=(2008, 2008), laptop_pull=_pull(now, 2009))["ok"] is False
    assert _seg(now, head, previous=(2008, 2008), laptop_pull=_pull(now, 2010, exitCode=1))["ok"] is False


def test_young_unacked_segment_is_in_flight():
    now = T0 + 60
    assert _seg(now, _head(now, 2010, 2008, newest_age_sec=3 * 60), previous=(2008, 2008))["ok"] is True
    assert _seg(now, _head(now, 2010, 2008, newest_age_sec=11 * 60), previous=(2008, 2008))["ok"] is False
    no_age = {**_head(now, 2010, 2008, 0), "last_segment_at": None}
    assert _seg(now, no_age, previous=(2008, 2008))["ok"] is False


def test_ack_lag_surviving_two_rows_without_progress_fails_even_if_the_laptop_acked():
    now = T0 + 60
    head = _head(now, 2010, 2008, newest_age_sec=60)
    stuck = _seg(now, head, previous=(2010, 2008), laptop_pull=_pull(now, 2010))
    assert stuck["ok"] is False and "across two rows" in stuck["detail"]
    advancing = _seg(now, _head(now, 2012, 2010, newest_age_sec=60), previous=(2010, 2008))
    assert advancing["ok"] is True
    caught_up = _seg(now, _head(now, 2010, 2010, newest_age_sec=3600), previous=(2010, 2008))
    assert caught_up["ok"] is True


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
    allowed = [_row(T0 + 60 + i * 1800, up.ALLOWED_GUARDED_DEPLOY if i in (10, 40) else "PASS") for i in range(97)]
    assert up.verdict(allowed, t0=T0, ends_at=ends, now=ends + 1)["result"] == "PASS"
    # Two consecutive paused rows are already 30 min apart; a third exceeds the 45-min bound.
    stuck = [_row(T0 + 60 + i * 1800, up.ALLOWED_GUARDED_DEPLOY if 10 <= i <= 12 else "PASS") for i in range(97)]
    assert up.verdict(stuck, t0=T0, ends_at=ends, now=ends + 1)["result"] == "FAIL"


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


def test_check_reads_the_deploy_runs_snapshot_for_a_guarded_pause(tmp_path):
    state = _state(tmp_path, T0)
    receipt = Path(up.start(state, tmp_path / "diag", T0)["receipt"])
    now = T0 + 60
    (state / up.RUNTIME_SNAPSHOT).write_text(json.dumps(_deploy_pause(now)), encoding="utf-8")
    assert up.check(state, now)["row"] == "FAIL"  # no deploy-runs snapshot: cannot attribute
    later = T0 + 120
    (state / up.RUNTIME_SNAPSHOT).write_text(json.dumps(_deploy_pause(later)), encoding="utf-8")
    (state / up.DEPLOY_RUNS_SNAPSHOT).write_text(json.dumps(_deploys(later, _run(9, later - 600))), encoding="utf-8")
    assert up.check(state, later, force=True)["row"] == up.ALLOWED_GUARDED_DEPLOY
    row = json.loads(receipt.read_text(encoding="utf-8").splitlines()[-1])
    assert row["observed"]["deploy_run_id"] == 9


def test_check_reads_the_laptop_pull_status_for_ack_lag(tmp_path):
    state = _state(tmp_path, T0)
    receipt = Path(up.start(state, tmp_path / "diag", T0)["receipt"])
    now = T0 + 60
    (state / up.HEAD_SNAPSHOT).write_text(json.dumps(_head(now, 2010, 2008, 12.5 * 60)), encoding="utf-8")
    assert up.check(state, now)["row"] == "FAIL"
    later = T0 + 120
    (state / up.HEAD_SNAPSHOT).write_text(json.dumps(_head(later, 2010, 2008, 12.5 * 60)), encoding="utf-8")
    (state / up.LAPTOP_PULL_STATUS).write_text(json.dumps(_pull(later, 2010)), encoding="utf-8")
    # The previous row already lagged at 2008 with no progress since: still a stall.
    assert up.check(state, later, force=True)["row"] == "FAIL"
    row = json.loads(receipt.read_text(encoding="utf-8").splitlines()[-1])
    assert row["observed"]["laptop_pull_acked_seq"] == 2010


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


def test_deploy_runs_accepts_powershell5_value_wrapper():
    now = T0 + 600
    run = _run(1, T0, T0 + 300, status="completed", conclusion="success")
    flat, err = up._deploy_runs(_deploys(now, run), now)
    wrapped, err2 = up._deploy_runs(_deploys(now, {"value": [run], "Count": 1}), now)
    assert err == err2 == ""
    assert flat == wrapped == [run]


def _stale_analyzer_row(now, generated_ago_min, *runs):
    return up.evaluate_row(**_inputs(now, analyzer={"lastCompletedGenerationAt": up.iso(now - generated_ago_min * 60)},
                                     deploy_runs=_deploys(now, *runs)))


def test_stale_analyzer_right_after_a_successful_guarded_deploy_is_allowed_and_labelled():
    # Replays the 2026-10-02 02:25:51Z row: generation 01:27:02Z, run 36949767912 01:11:55Z -> 01:52:17Z success.
    now = up.parse_utc("2026-10-02T02:25:51Z")
    run = {"databaseId": 36949767912, "status": "completed", "conclusion": "success", "event": "push",
           "headSha": "1c01619482e0cb0109938e260259244f2aa6c85e",
           "createdAt": "2026-10-02T01:11:55Z", "updatedAt": "2026-10-02T01:52:17Z"}
    row = up.evaluate_row(**_inputs(now, runtime=_runtime(now, git_rev="1c01619482e0"),
                                    analyzer={"lastCompletedGenerationAt": "2026-10-02T01:27:02Z"},
                                    deploy_runs=_deploys(now, run)))
    assert row["status"] == up.ALLOWED_GUARDED_DEPLOY and row["failed_checks"] == [], row
    check = row["checks"]["analyzer_fresh"]
    assert check["ok"] is True and check["boundary"] is True
    assert check["detail"].startswith("ALLOWED_GUARDED_DEPLOY: analyzer generation 59 min old (> 45)")
    assert "36949767912" in check["detail"] and "ended 34 min ago" in check["detail"]
    assert row["observed"]["analyzer_deploy_run_id"] == 36949767912
    active = _stale_analyzer_row(T0 + 3600, 50, _run(5, T0 + 3600 - 30 * 60))
    assert active["status"] == up.ALLOWED_GUARDED_DEPLOY and "(active," in active["checks"]["analyzer_fresh"]["detail"]


def test_stale_analyzer_allowance_is_bounded_and_attributable():
    now = T0 + 6 * 3600
    ok = lambda r: r["checks"]["analyzer_fresh"]["ok"]  # noqa: E731
    success = lambda created_ago, ended_ago: {**_run(6, now - created_ago * 60, now - ended_ago * 60,  # noqa: E731
                                                     "completed", "success"), "headSha": "aaaaaaaaaaaa" + "0" * 28}
    # Grace ends 60 min after the run ended.
    assert ok(_stale_analyzer_row(now, 70, success(80, 59))) is True
    assert ok(_stale_analyzer_row(now, 70, success(80, 61))) is False
    # Already stale before the deploy started: not the deploy's fault.
    assert ok(_stale_analyzer_row(now, 100, success(50, 10))) is False
    # Hard cap on generation age even with a long run.
    assert ok(_stale_analyzer_row(now, 121, success(110, 5))) is False
    # A failed or cancelled deploy never excuses staleness; no evidence fails closed.
    assert ok(_stale_analyzer_row(now, 50, _run(7, now - 40 * 60, now - 5 * 60, "completed", "failure"))) is False
    missing = up.evaluate_row(**_inputs(now, analyzer={"lastCompletedGenerationAt": up.iso(now - 50 * 60)}))
    assert missing["failed_checks"] == ["analyzer_fresh"]
    # A fresh generation stays a plain PASS even right after a deploy.
    assert _stale_analyzer_row(now, 10, success(40, 5))["status"] == "PASS"
    # An inspect/restart dispatch of the workflow on another revision booted nothing Fly now runs.
    inspect = {**success(10, 9), "headSha": "b" * 40, "event": "workflow_dispatch"}
    assert ok(_stale_analyzer_row(now, 50, inspect)) is False


def test_analyzer_only_allowances_do_not_extend_a_deploy_pause_run():
    ends = T0 + 48 * 3600
    analyzer_only = {"paper_running": {"ok": True, "detail": "paper running"},
                     "analyzer_fresh": {"ok": True, "detail": "ALLOWED_GUARDED_DEPLOY: ...", "boundary": True}}
    paused = {"paper_running": {"ok": True, "detail": "ALLOWED_GUARDED_DEPLOY: paused", "boundary": True}}
    rows = []
    for i in range(97):
        row = _row(T0 + 60 + i * 1800)
        if i == 10:
            row = {**row, "status": up.ALLOWED_GUARDED_DEPLOY, "checks": paused}
        elif i in (11, 12):
            row = {**row, "status": up.ALLOWED_GUARDED_DEPLOY, "checks": analyzer_only}
        rows.append(row)
    assert up.verdict(rows, t0=T0, ends_at=ends, now=ends + 1)["result"] == "PASS"
    rows[11] = {**rows[11], "checks": paused}
    rows[12] = {**rows[12], "checks": paused}
    assert up.verdict(rows, t0=T0, ends_at=ends, now=ends + 1)["result"] == "FAIL"