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
    for name, key_events, key_episodes in (
        ("policy_candidate_oos_report.json", "current_events", "independent_episodes"),
        ("best_policy_research_report.json", "current_epoch_events", "independent_episode_count"),
    ):
        write_json(reports / name, {
            "generated_at": NOW.isoformat(), "epoch_id": "epoch-new",
            "policy_epoch_id": "policy-epoch-a", "evidence_policy_signature": "policy-a",
            "evidence": {key_events: 3, key_episodes: 2},
        })
    return repo, mirror, reports


def fetcher(url, token, timeout):
    if url.endswith("manifest"):
        return {"files": [{"path": "research_events_v22.jsonl"}], "total_bytes": 100, "source_git_rev": "a" * 40}
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
    assert parity["detail"]["expected"] == (3, 2)


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
