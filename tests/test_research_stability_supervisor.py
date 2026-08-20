import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "research-stability-supervisor.py"
SPEC = importlib.util.spec_from_file_location("research_stability_supervisor", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
import sys
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_fixture(tmp_path):
    repo = tmp_path / "repo"
    mirror = tmp_path / "mirror"
    reports = tmp_path / "reports"
    repo.mkdir(); mirror.mkdir(); reports.mkdir()
    heartbeat = {"ok": True, "syncedAt": NOW.isoformat(), "sourceRevision": "a" * 40}
    write_json(repo / ".fly-data-sync-loop.heartbeat.json", heartbeat)
    events = []
    for index in range(3):
        events.append({
            "schema": "research_event_v2.2", "collector_version": "collector_v2.2",
            "epoch_id": "epoch-new", "policy_epoch_id": "policy-epoch-a",
            "policy_signature": "policy-a", "event_episode_id": f"episode-{index // 2}",
            "observation_status": "FUNNEL_COMPLETE" if index == 2 else "COMPLETE",
        })
    (mirror / "research_events_v22.jsonl").write_text("\n".join(json.dumps(x) for x in events), encoding="utf-8")
    timestamp = NOW.timestamp()
    import os
    os.utime(mirror / "research_events_v22.jsonl", (timestamp, timestamp))
    for name, evidence in (
        ("policy_candidate_oos_report.json", {
            "current_events": 3, "eligible_events": 3, "independent_episodes": 2,
        }),
        ("best_policy_research_report.json", {
            "current_epoch_events": 3, "replay_eligible_events": 3,
            "replay_ineligible_events": 0, "independent_episode_count": 2,
        }),
    ):
        write_json(reports / name, {
            "generated_at": NOW.isoformat(), "epoch_id": "epoch-new",
            "policy_epoch_id": "policy-epoch-a", "evidence_policy_signature": "policy-a",
            "evidence": evidence,
        })
    return repo, mirror, reports


def fetcher(url, token, timeout):
    if url.endswith("manifest"):
        return {"files": [{"path": "research_events_v22.jsonl"}], "total_bytes": 100, "source_git_rev": "a" * 40}
    if url.endswith("/api/status"):
        return {
            "process_alive": True, "system_ready": True, "signal_generation_ready": True,
            "ws_ready": True, "git_rev": "a" * 40,
            "runtime_readiness": {"signal_generation_ready": True, "readiness_reasons": []},
            "virtual_count": 2, "pending_count": 1, "position_count": 0,
        }
    return {"volume_pct": 15.0, "cleanup_status": "ok"}


def processes():
    return [
        {"ProcessId": 1, "CommandLine": "powershell sync-fly-bot-data-loop.ps1"},
        {"ProcessId": 2, "CommandLine": "python analyzer_research_engine_v62.py --owner-port=9001"},
        {"ProcessId": 3, "CommandLine": "python research_dashboard.py --standalone"},
        {"ProcessId": 4, "CommandLine": "python research-stability-supervisor.py --loop"},
    ]


def test_healthy_separate_data_and_report_directories(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    assert result["healthy"] is True
    fly_manifest = next(x for x in result["checks"] if x["name"] == "fly_collector_manifest")
    assert fly_manifest["detail"]["source_revision"] == "a" * 40
    revision_parity = next(x for x in result["checks"] if x["name"] == "fly_sync_revision_parity")
    assert revision_parity["ok"] is True
    parity = next(x for x in result["checks"] if x["name"] == "report_count_parity")
    assert parity["detail"]["expected"]["policy_candidate_oos_report.json"] == {
        "current_events": 3, "eligible_events": 3, "eligible_independent_episodes": 2,
    }


def test_negative_evidence_uses_schema_aware_episode_denominators(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    events_path = mirror / "research_events_v22.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    # One terminalized negative-evidence path belongs to its own episode. It is
    # part of whole-epoch coverage but cannot enter candidate replay ranking.
    events.append({
        "schema": "research_event_v2.2", "collector_version": "collector_v2.2",
        "epoch_id": "epoch-new", "policy_epoch_id": "policy-epoch-a",
        "policy_signature": "policy-a", "event_episode_id": "episode-negative",
        "observation_status": "INSUFFICIENT_PATH", "primary_outcome": "ACCEPTED_FILLED",
    })
    events_path.write_text("\n".join(json.dumps(x) for x in events), encoding="utf-8")
    import os
    os.utime(events_path, (NOW.timestamp(), NOW.timestamp()))

    candidate = json.loads((reports / "policy_candidate_oos_report.json").read_text())
    candidate["evidence"].update(current_events=4, eligible_events=3, independent_episodes=2)
    write_json(reports / "policy_candidate_oos_report.json", candidate)
    best = json.loads((reports / "best_policy_research_report.json").read_text())
    best["evidence"].update(current_epoch_events=4, replay_eligible_events=3,
                            replay_ineligible_events=1, independent_episode_count=3)
    write_json(reports / "best_policy_research_report.json", best)

    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    parity = next(x for x in result["checks"] if x["name"] == "report_count_parity")
    assert parity["ok"] is True
    assert result["healthy"] is True
    assert parity["detail"]["reports"]["policy_candidate_oos_report.json"]["eligible_independent_episodes"] == 2
    assert parity["detail"]["reports"]["best_policy_research_report.json"]["all_independent_episodes"] == 3


def test_negative_evidence_denominator_swap_fails_closed(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    candidate = json.loads((reports / "policy_candidate_oos_report.json").read_text())
    candidate["evidence"]["independent_episodes"] = 3
    write_json(reports / "policy_candidate_oos_report.json", candidate)
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    parity = next(x for x in result["checks"] if x["name"] == "report_count_parity")
    assert parity["ok"] is False
    assert result["healthy"] is False


def make_one_event_pending(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    from datetime import timedelta
    for filename in ("policy_candidate_oos_report.json", "best_policy_research_report.json"):
        report = json.loads((reports / filename).read_text())
        report["generated_at"] = (NOW - timedelta(minutes=5)).isoformat()
        write_json(reports / filename, report)
    events_path = mirror / "research_events_v22.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    events.append({
        "schema": "research_event_v2.2", "collector_version": "collector_v2.2",
        "epoch_id": "epoch-new", "policy_epoch_id": "policy-epoch-a",
        "policy_signature": "policy-a", "event_episode_id": "episode-new",
        "observation_status": "COMPLETE",
    })
    events_path.write_text("\n".join(json.dumps(x) for x in events), encoding="utf-8")
    import os
    os.utime(events_path, (NOW.timestamp(), NOW.timestamp()))
    return repo, mirror, reports


def test_one_event_forward_lag_is_pending_not_unhealthy(tmp_path):
    repo, mirror, reports = make_one_event_pending(tmp_path)
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    parity = next(x for x in result["checks"] if x["name"] == "report_count_parity")
    assert parity["ok"] is True
    assert parity["detail"]["status"] == "PENDING_NEXT_ANALYZER_CYCLE"
    assert parity["detail"]["pending"]["deltas"]["current_events"] == 1
    assert result["healthy"] is True


def test_pending_lag_fails_when_reports_are_stale(tmp_path):
    repo, mirror, reports = make_one_event_pending(tmp_path)
    from datetime import timedelta
    for filename in ("policy_candidate_oos_report.json", "best_policy_research_report.json"):
        report = json.loads((reports / filename).read_text())
        report["generated_at"] = (NOW - timedelta(minutes=46)).isoformat()
        write_json(reports / filename, report)
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    parity = next(x for x in result["checks"] if x["name"] == "report_count_parity")
    assert parity["ok"] is False
    assert result["healthy"] is False


def test_pending_lag_fails_on_report_overshoot(tmp_path):
    repo, mirror, reports = make_one_event_pending(tmp_path)
    best = json.loads((reports / "best_policy_research_report.json").read_text())
    best["evidence"].update(current_epoch_events=5, replay_eligible_events=5,
                            replay_ineligible_events=0, independent_episode_count=3)
    write_json(reports / "best_policy_research_report.json", best)
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    parity = next(x for x in result["checks"] if x["name"] == "report_count_parity")
    assert parity["ok"] is False
    assert parity["detail"]["pending"]["nonnegative"] is False


def test_pending_lag_fails_on_identity_drift(tmp_path):
    repo, mirror, reports = make_one_event_pending(tmp_path)
    candidate = json.loads((reports / "policy_candidate_oos_report.json").read_text())
    candidate["evidence_policy_signature"] = "policy-old"
    write_json(reports / "policy_candidate_oos_report.json", candidate)
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    parity = next(x for x in result["checks"] if x["name"] == "report_count_parity")
    assert parity["ok"] is False
    assert parity["detail"]["pending"]["identity_ok"] is False
    identity = next(x for x in result["checks"] if x["name"] == "report_epoch_policy_signature_parity")
    assert identity["ok"] is False


def test_count_or_signature_mismatch_fails_closed_without_restart(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    report = json.loads((reports / "best_policy_research_report.json").read_text())
    report["evidence"]["current_epoch_events"] = 0
    report["evidence_policy_signature"] = "wrong"
    write_json(reports / "best_policy_research_report.json", report)
    calls = []
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", repair=True,
                                now=lambda: NOW, fetcher=fetcher, process_reader=processes,
                                launcher=lambda *args, **kwargs: calls.append((args, kwargs)))
    result = checker.check()
    assert result["healthy"] is False
    assert calls == []
    assert result["repairs"] == []


def test_fly_and_sync_revision_mismatch_fails_closed(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    heartbeat = json.loads((repo / ".fly-data-sync-loop.heartbeat.json").read_text())
    heartbeat["sourceRevision"] = "b" * 40
    write_json(repo / ".fly-data-sync-loop.heartbeat.json", heartbeat)
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    assert result["healthy"] is False
    parity = next(x for x in result["checks"] if x["name"] == "fly_sync_revision_parity")
    assert parity["ok"] is False


def test_only_missing_sync_and_analyzer_use_safe_launchers(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    (repo / "scripts").mkdir()
    calls = []
    rows = [{"ProcessId": 3, "CommandLine": "python research_dashboard.py --standalone"}]
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", repair=True,
                                now=lambda: NOW, fetcher=fetcher, process_reader=lambda: rows,
                                launcher=lambda *args, **kwargs: calls.append((args, kwargs)))
    result = checker.check()
    assert len(calls) == 2
    flattened = " ".join(str(part) for call in calls for part in call[0][0])
    assert "sync-fly-bot-data-loop.ps1" in flattened
    assert "start-home-analyzer.ps1" in flattened
    assert all(forbidden not in flattened.lower() for forbidden in ("bot.py", "fly deploy", "wipe", "toggle"))
    assert result["repairs"] == ["started_missing_sync_through_safe_launcher", "started_missing_analyzer_through_safe_launcher"]


def test_duplicate_process_is_reported_and_never_killed(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    rows = processes() + [{"ProcessId": 9, "CommandLine": "python analyzer_research_engine_v62.py --owner-port=9001"}]
    calls = []
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", repair=True,
                                now=lambda: NOW, fetcher=fetcher, process_reader=lambda: rows,
                                launcher=lambda *args, **kwargs: calls.append((args, kwargs)))
    result = checker.check()
    assert result["healthy"] is False
    assert calls == []
    row = next(x for x in result["checks"] if x["name"] == "unique_analyzer_process")
    assert row["detail"]["count"] == 2


def test_runtime_repo_owns_sync_heartbeat_and_launcher(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    runtime_repo = tmp_path / "runtime-owner"
    (runtime_repo / "scripts").mkdir(parents=True)
    heartbeat = repo / ".fly-data-sync-loop.heartbeat.json"
    heartbeat.replace(runtime_repo / heartbeat.name)
    calls = []
    rows = [
        {"ProcessId": 2, "CommandLine": "python analyzer_research_engine_v62.py --owner-port=9001"},
        {"ProcessId": 3, "CommandLine": "python research_dashboard.py --standalone"},
    ]
    checker = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token", repair=True,
        now=lambda: NOW, fetcher=fetcher, process_reader=lambda: rows,
        launcher=lambda *args, **kwargs: calls.append((args, kwargs)),
        runtime_repo=runtime_repo,
    )
    result = checker.check()
    assert len(calls) == 1
    assert str(runtime_repo / "scripts" / "sync-fly-bot-data-loop.ps1") in calls[0][0][0]
    heartbeat_check = next(x for x in result["checks"] if x["name"] == "atomic_sync_heartbeat")
    assert heartbeat_check["ok"] is True


def starvation_status():
    return {
        "process_alive": True, "system_ready": False, "signal_generation_ready": False,
        "ws_ready": True, "git_rev": "a" * 40,
        "runtime_readiness": {
            "prerequisites_ready": False, "signal_generation_ready": False,
            "rest_entry_quote_ready": False, "ohlcv_ready": False, "ohlcv_age_sec": 480,
            "readiness_reasons": ["REST_ENTRY_QUOTE_NOT_READY", "OHLCV_NOT_READY", "CANDLE_STALE"],
        },
        "virtual_count": 4, "pending_count": 0, "position_count": 0,
    }


def test_runtime_starvation_is_transient_before_conservative_threshold(tmp_path):
    from datetime import timedelta
    state_file = tmp_path / "runtime-state.json"
    prior = {
        "runtime_identity": "a" * 40,
        "first_starved_at": (NOW - timedelta(minutes=7)).isoformat(),
    }
    write_json(state_file, prior)
    ok, detail, persisted = module.evaluate_runtime_readiness(
        starvation_status(), prior, now=NOW, counts={"virtual_count": 4, "pending_count": 0, "position_count": 0},
    )
    assert ok is True
    assert detail["state"] == "TRANSIENT_NOT_READY"
    assert detail["starved_duration_seconds"] == 420
    assert detail["virtual_count"] == 4
    assert persisted["first_starved_at"] == prior["first_starved_at"]


def test_runtime_starvation_becomes_unhealthy_after_threshold(tmp_path):
    from datetime import timedelta
    prior = {"runtime_identity": "a" * 40, "first_starved_at": (NOW - timedelta(minutes=16)).isoformat()}
    ok, detail, _ = module.evaluate_runtime_readiness(
        starvation_status(), prior, now=NOW, counts={"virtual_count": 4, "pending_count": 0, "position_count": 0},
    )
    assert ok is False
    assert detail["state"] == "PERSISTENT_COLLECTION_STARVATION"
    assert detail["starved_duration_seconds"] == 960


def test_readiness_recovery_clears_persistent_starvation_clock(tmp_path):
    from datetime import timedelta
    prior = {"runtime_identity": "a" * 40, "first_starved_at": (NOW - timedelta(minutes=30)).isoformat()}
    ready = starvation_status()
    ready.update(system_ready=True, signal_generation_ready=True)
    ready["runtime_readiness"] = {"prerequisites_ready": True, "signal_generation_ready": True, "readiness_reasons": []}
    ok, detail, persisted = module.evaluate_runtime_readiness(
        ready, prior, now=NOW, counts={"virtual_count": 0, "pending_count": 0, "position_count": 0},
    )
    assert ok is True
    assert detail["state"] == "READY"
    assert persisted["first_starved_at"] is None


def test_stabilization_and_admin_pause_are_not_collection_starvation(tmp_path):
    stabilizing = starvation_status()
    stabilizing["runtime_readiness"] = {
        "prerequisites_ready": True, "signal_generation_ready": False,
        "readiness_reasons": ["READINESS_STABILIZING"],
    }
    ok, detail, _ = module.evaluate_runtime_readiness(
        stabilizing, {}, now=NOW, counts={"virtual_count": None, "pending_count": None, "position_count": None},
    )
    assert ok is True and detail["state"] == "STABILIZING"
    paused = starvation_status()
    paused["runtime_readiness"]["readiness_reasons"] = ["ADMIN_MANUAL_PAUSE"]
    ok, detail, _ = module.evaluate_runtime_readiness(
        paused, {}, now=NOW, counts={"virtual_count": 0, "pending_count": 0, "position_count": 1},
    )
    assert ok is True and detail["state"] == "PAUSED_NOT_STARVATION"


def test_runtime_identity_change_does_not_inherit_old_starvation_duration(tmp_path):
    from datetime import timedelta
    prior = {"runtime_identity": "b" * 40, "first_starved_at": (NOW - timedelta(hours=2)).isoformat()}
    ok, detail, _ = module.evaluate_runtime_readiness(
        starvation_status(), prior, now=NOW, counts={"virtual_count": 1, "pending_count": 0, "position_count": 0},
    )
    assert ok is True
    assert detail["starved_duration_seconds"] == 0


def test_dead_runtime_fails_immediately_without_remote_repair(tmp_path):
    dead = starvation_status()
    dead["process_alive"] = False
    ok, detail, persisted = module.evaluate_runtime_readiness(
        dead, {}, now=NOW, counts={"virtual_count": None, "pending_count": None, "position_count": None},
    )
    assert ok is False
    assert detail["state"] == "PROCESS_NOT_ALIVE"
    assert persisted["first_starved_at"] is None
