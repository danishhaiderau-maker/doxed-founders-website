import importlib.util
import json
import os
import pytest
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "research-stability-supervisor.py"
SPEC = importlib.util.spec_from_file_location("research_stability_supervisor", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
import sys
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)
REAL_LOCAL_STORAGE_SNAPSHOT = module.local_storage_snapshot


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
TEST_TILE_LANES = [
    "OFFSET_029_ATR_TP_25",
    "CONTINUOUS",
    "OFFSET_029_ATR_PROTECTED",
    "OFFSET_029_ATR_REGIME",
]
TEST_TILE_SIGNATURE = "r" * 64


@pytest.fixture(autouse=True)
def deterministic_supervisor_storage(monkeypatch):
    """Unit tests must not inherit the workstation's current free-space state."""
    monkeypatch.setattr(
        module,
        "local_storage_snapshot",
        lambda _mirror, **_kwargs: (True, {"rag": "GREEN", "disk_free_percent": 30.0, "test_fixture": True}),
    )
    monkeypatch.setattr(
        module,
        "local_tile_registry_contract",
        lambda _repo: (list(TEST_TILE_LANES), TEST_TILE_SIGNATURE),
    )


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_local_storage_snapshot_tracks_active_and_quarantined_bytes(tmp_path):
    mirror = tmp_path / "fly-data-mirror"
    quarantine = tmp_path / "fly-data-quarantine" / "epoch-old"
    mirror.mkdir()
    quarantine.mkdir(parents=True)
    (mirror / "active.jsonl").write_bytes(b"active")
    (quarantine / "old.jsonl").write_bytes(b"quarantine")

    ok, detail = REAL_LOCAL_STORAGE_SNAPSHOT(
        mirror,
        disk_usage=lambda _path: (1024**4, 700 * 1024**3, 324 * 1024**3),
    )

    assert ok is True
    assert detail["mirror_files"] == 1
    assert detail["mirror_bytes"] == 6
    assert detail["quarantine_files"] == 1
    assert detail["quarantine_bytes"] == 10
    assert detail["disk_free_percent"] == pytest.approx(31.64, abs=0.01)
    assert detail["rag"] == "GREEN"
    assert detail["green_minimum_free_bytes"] == 150 * 1024**3
    assert detail["amber_minimum_free_bytes"] == 100 * 1024**3
    assert detail["maximum_mirror_bytes"] == 25 * 1024**3
    assert detail["maximum_quarantine_bytes"] == 25 * 1024**3
    assert detail["retention_action"] == "QUARANTINE_AND_REVIEW; NEVER_SILENTLY_DELETE"
    assert detail["automatic_delete"] is False


@pytest.mark.parametrize(
    ("free_gib", "expected_rag", "expected_ok"),
    [(150, "GREEN", True), (149, "AMBER", False), (100, "AMBER", False), (99, "RED", False)],
)
def test_local_storage_snapshot_uses_explicit_gib_rag_boundaries(tmp_path, free_gib, expected_rag, expected_ok):
    mirror = tmp_path / "fly-data-mirror"
    mirror.mkdir()

    ok, detail = REAL_LOCAL_STORAGE_SNAPSHOT(
        mirror,
        disk_usage=lambda _path: (1024**4, (1024 - free_gib) * 1024**3, free_gib * 1024**3),
    )

    assert ok is expected_ok
    assert detail["rag"] == expected_rag


def test_local_storage_snapshot_requires_absolute_reserve_on_large_disk(tmp_path):
    mirror = tmp_path / "fly-data-mirror"
    mirror.mkdir()

    ok, detail = REAL_LOCAL_STORAGE_SNAPSHOT(
        mirror,
        disk_usage=lambda _path: (500 * 1024**3, 455 * 1024**3, 45 * 1024**3),
    )

    assert detail["disk_free_percent"] == 9.0
    assert detail["disk_free_bytes"] == 45 * 1024**3
    assert ok is False
    assert detail["rag"] == "RED"


def test_local_storage_snapshot_records_reports_temp_growth_and_ranked_consumers(tmp_path):
    mirror = tmp_path / "fly-data-mirror"
    reports = tmp_path / "reports"
    temp = tmp_path / "processing-temp"
    quarantine = tmp_path / "fly-data-quarantine"
    for path in (mirror, reports, temp, quarantine):
        path.mkdir()
    (mirror / "events.jsonl").write_bytes(b"m" * 30)
    (reports / "report.json").write_bytes(b"r" * 20)
    (temp / "working.tmp").write_bytes(b"t" * 12)
    (quarantine / "old.jsonl").write_bytes(b"q" * 40)

    ok, detail = REAL_LOCAL_STORAGE_SNAPSHOT(
        mirror,
        report_dir=reports,
        temp_dir=temp,
        previous_snapshot={"temporary_bytes": 5},
        disk_usage=lambda _path: (1024**4, 824 * 1024**3, 200 * 1024**3),
    )

    assert ok is True
    assert detail["analyzer_report_bytes"] == 20
    assert detail["temporary_bytes"] == 12
    assert detail["temporary_growth_bytes"] == 7
    assert detail["temporary_growth_alert"] is False
    consumers = detail["five_largest_known_generated_data_consumers"]
    assert len(consumers) == 5
    assert [row["name"] for row in consumers[:4]] == [
        "fly_data_quarantine", "active_fly_mirror", "analyzer_reports", "temporary_processing"
    ]
    assert all({"name", "path", "files", "bytes"} <= set(row) for row in consumers)


def test_local_storage_snapshot_alerts_on_abnormal_temp_growth_before_capacity_threshold(tmp_path):
    mirror = tmp_path / "fly-data-mirror"
    temp = tmp_path / "processing-temp"
    mirror.mkdir()
    temp.mkdir()
    original = module.directory_size
    module.directory_size = lambda path: (
        (1, 2 * 1024**3) if path == temp else (0, 0)
    )
    try:
        ok, detail = REAL_LOCAL_STORAGE_SNAPSHOT(
            mirror,
            temp_dir=temp,
            previous_snapshot={"temporary_bytes": 0, "observed": True},
            disk_usage=lambda _path: (1024**4, 824 * 1024**3, 200 * 1024**3),
        )
    finally:
        module.directory_size = original

    assert detail["rag"] == "GREEN"
    assert detail["temporary_growth_rag"] == "AMBER"
    assert detail["temporary_growth_alert"] is True
    assert ok is False


def test_local_storage_snapshot_fails_when_stale_quarantine_exceeds_absolute_cap(tmp_path):
    mirror = tmp_path / "fly-data-mirror"
    quarantine = tmp_path / "fly-data-quarantine"
    mirror.mkdir()
    quarantine.mkdir()

    original = module.directory_size
    module.directory_size = lambda path: (
        (1, module.LOCAL_QUARANTINE_MAX_BYTES + 1)
        if path == quarantine
        else (1, 1024)
    )
    try:
        ok, detail = REAL_LOCAL_STORAGE_SNAPSHOT(
            mirror,
            disk_usage=lambda _path: (1024**4, 700 * 1024**3, 324 * 1024**3),
        )
    finally:
        module.directory_size = original

    assert ok is False
    assert detail["quarantine_bytes"] > detail["maximum_quarantine_bytes"]


def test_mirror_partial_artifacts_detects_atomic_sync_leftovers(tmp_path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "valid.jsonl").write_text("{}\n", encoding="utf-8")
    orphan = mirror / "valid.jsonl.123.0123456789abcdef0123456789abcdef.download"
    orphan.write_bytes(b"partial")
    os.utime(orphan, (NOW.timestamp() - 601, NOW.timestamp() - 601))

    assert module.mirror_partial_artifacts(mirror, now_ts=NOW.timestamp()) == [orphan.name]


def test_mirror_partial_artifacts_ignores_active_atomic_transfer(tmp_path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    active = mirror / "active.jsonl.123.0123456789abcdef0123456789abcdef.download"
    active.write_bytes(b"still downloading")
    os.utime(active, (NOW.timestamp() - 30, NOW.timestamp() - 30))

    assert module.mirror_partial_artifacts(mirror, now_ts=NOW.timestamp()) == []


def test_mirror_partial_artifacts_allows_large_transfer_within_sync_stale_window(tmp_path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    active = mirror / "large.jsonl.123.0123456789abcdef0123456789abcdef.download"
    active.write_bytes(b"large transfer still in progress")
    os.utime(active, (NOW.timestamp() - 300, NOW.timestamp() - 300))

    assert module.mirror_partial_artifacts(mirror, now_ts=NOW.timestamp()) == []


def test_supervisor_fails_closed_when_partial_download_is_present(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    orphan = mirror / "evidence.jsonl.123.0123456789abcdef0123456789abcdef.download"
    orphan.write_bytes(b"partial")
    os.utime(orphan, (NOW.timestamp() - 601, NOW.timestamp() - 601))
    result = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
        fetcher=fetcher, process_reader=processes,
    ).check()

    check = next(x for x in result["checks"] if x["name"] == "mirror_partial_artifacts")
    assert check["ok"] is False
    assert check["detail"]["count"] == 1
    assert result["healthy"] is False


def test_runtime_counts_supports_current_state_and_nested_health_contracts():
    state_counts = module.runtime_counts({
        "active_signals": [{"id": "a"}, {"id": "b"}],
        "orders": [{"id": "o1"}],
        "positions": [{"id": "p1"}, {"id": "p2"}],
    })
    assert state_counts == {
        "virtual_count": 2,
        "pending_count": 1,
        "position_count": 2,
    }

    health_counts = module.runtime_counts({
        "strategy_progress": {
            "active_signal_count": 3,
            "pending_orders": 4,
            "open_positions": 5,
        },
    })
    assert health_counts == {
        "virtual_count": 3,
        "pending_count": 4,
        "position_count": 5,
    }


def test_opportunity_progress_establishes_baseline_then_advances():
    ok, detail, baseline = module.evaluate_opportunity_progress(
        count=4, epoch_ids=["epoch-a"], source_revision="a" * 40,
        prior={}, now=NOW, progress_expected=True,
    )
    assert ok is True
    assert detail["state"] == "BASELINE_ESTABLISHED"

    from datetime import timedelta
    ok, detail, _ = module.evaluate_opportunity_progress(
        count=5, epoch_ids=["epoch-a"], source_revision="a" * 40,
        prior=baseline, now=NOW + timedelta(minutes=5), progress_expected=True,
    )
    assert ok is True
    assert detail["state"] == "ADVANCING"
    assert detail["advanced"] is True


def test_opportunity_progress_fails_after_cadence_and_sync_grace():
    from datetime import timedelta
    prior = {
        "epoch_key": "epoch-a", "source_revision": "a" * 40,
        "independent_opportunities": 4,
        "observed_at": (NOW - timedelta(minutes=13)).isoformat(),
        "first_stalled_at": (NOW - timedelta(minutes=13)).isoformat(),
    }
    ok, detail, _ = module.evaluate_opportunity_progress(
        count=4, epoch_ids=["epoch-a"], source_revision="a" * 40,
        prior=prior, now=NOW, progress_expected=True,
    )
    assert ok is False
    assert detail["state"] == "OPPORTUNITY_PROGRESS_STALLED"
    assert detail["stalled_duration_seconds"] == 13 * 60


def test_opportunity_progress_does_not_fail_while_paused_and_resets_on_new_epoch():
    from datetime import timedelta
    prior = {
        "epoch_key": "epoch-a", "source_revision": "a" * 40,
        "independent_opportunities": 4,
        "observed_at": (NOW - timedelta(hours=1)).isoformat(),
        "first_stalled_at": (NOW - timedelta(hours=1)).isoformat(),
    }
    ok, detail, _ = module.evaluate_opportunity_progress(
        count=4, epoch_ids=["epoch-a"], source_revision="a" * 40,
        prior=prior, now=NOW, progress_expected=False,
    )
    assert ok is True
    assert detail["state"] == "PROGRESS_NOT_EXPECTED"

    ok, detail, _ = module.evaluate_opportunity_progress(
        count=0, epoch_ids=["epoch-b"], source_revision="b" * 40,
        prior=prior, now=NOW, progress_expected=True,
    )
    assert ok is True
    assert detail["state"] == "BASELINE_ESTABLISHED"


def test_supervisor_reader_does_not_block_atomic_mirror_replace(tmp_path):
    destination = tmp_path / "research_events_v22.jsonl"
    candidate = tmp_path / "candidate.jsonl"
    backup = tmp_path / "backup.jsonl"
    destination.write_bytes(b"old\n")
    candidate.write_bytes(b"new\n")

    with module.open_replace_safe(destination) as held_reader:
        if os.name == "nt":
            command = (
                f'[System.IO.File]::Replace("{candidate}", '
                f'"{destination}", "{backup}")'
            )
            replaced = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                check=False,
                capture_output=True,
                text=True,
            )
            assert replaced.returncode == 0, replaced.stderr
        else:
            os.replace(candidate, destination)
        assert held_reader.read() == b"old\n"

    assert destination.read_bytes() == b"new\n"


def make_fixture(tmp_path):
    repo = tmp_path / "repo"
    mirror = tmp_path / "mirror"
    reports = tmp_path / "reports"
    repo.mkdir(); mirror.mkdir(); reports.mkdir()
    heartbeat = {
        "ok": True,
        "syncedAt": NOW.isoformat(),
        "sourceRevision": "a" * 40,
        "tileRegistrySignature": TEST_TILE_SIGNATURE,
    }
    write_json(repo / ".fly-data-sync-loop.heartbeat.json", heartbeat)
    events = []
    for index in range(3):
        events.append({
            "event_id": f"event-{index}",
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
        return {
            "files": [{"path": "research_events_v22.jsonl"}],
            "total_bytes": 100,
            "source_git_rev": "a" * 40,
            "tile_registry_signature": TEST_TILE_SIGNATURE,
            "active_tiles": [{"lane": lane} for lane in TEST_TILE_LANES],
        }
    if url.endswith("/api/status"):
        return {
            "process_alive": True, "system_ready": True, "signal_generation_ready": True,
            "ws_ready": True, "git_rev": "a" * 40,
            "runtime_readiness": {"signal_generation_ready": True, "readiness_reasons": []},
            "virtual_count": 2, "pending_count": 1, "position_count": 0,
            "tile_registry_signature": TEST_TILE_SIGNATURE,
            "active_tiles": [{"lane": lane} for lane in TEST_TILE_LANES],
        }
    return {"volume_pct": 15.0, "cleanup_status": "ok"}


def processes():
    return [
        {"ProcessId": 1, "Name": "powershell.exe", "CommandLine": "powershell sync-fly-bot-data-loop.ps1"},
        {"ProcessId": 2, "Name": "python.exe", "CommandLine": "python analyzer_research_engine_v62.py --owner-port=9001"},
        {"ProcessId": 3, "Name": "python.exe", "CommandLine": "python research_dashboard.py --standalone"},
        {"ProcessId": 4, "Name": "python.exe", "CommandLine": "python research-stability-supervisor.py --loop"},
    ]


def test_process_classification_ignores_shell_commands_that_only_mention_worker_names():
    rows = processes() + [
        {
            "ProcessId": 5,
            "Name": "pwsh.exe",
            "CommandLine": "pwsh -Command rg research-stability-supervisor.py analyzer_research_engine_v62.py",
        }
    ]

    assert module.classify_processes(rows) == {
        "sync": [1],
        "analyzer": [2],
        "dashboard": [3],
        "supervisor": [4],
    }


def test_process_classification_does_not_count_one_shot_audit_as_supervisor():
    rows = processes() + [
        {
            "ProcessId": 8,
            "ParentProcessId": 1,
            "Name": "python.exe",
            "CommandLine": (
                "python research-stability-supervisor.py "
                "--status-file one-shot.json"
            ),
        }
    ]

    assert module.classify_processes(rows)["supervisor"] == [4]


def test_process_classification_counts_launcher_and_child_as_one_sync_worker():
    rows = processes() + [
        {
            "ProcessId": 10,
            "ParentProcessId": 0,
            "Name": "pwsh.exe",
            "CommandLine": "pwsh -Command powershell -File sync-fly-bot-data-loop.ps1",
        },
        {
            "ProcessId": 11,
            "ParentProcessId": 10,
            "Name": "powershell.exe",
            "CommandLine": "powershell -File sync-fly-bot-data-loop.ps1",
        },
    ]
    rows[0]["ParentProcessId"] = 10

    assert module.classify_processes(rows)["sync"] == [10]


def test_healthy_separate_data_and_report_directories(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    assert result["healthy"] is True
    fly_manifest = next(x for x in result["checks"] if x["name"] == "fly_collector_manifest")
    assert fly_manifest["detail"]["source_revision"] == "a" * 40


def test_v3_supervision_checks_normalized_counts_and_real_money_gate(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    def row(ledger, record_id, **extra):
        return {"schema": "research_evidence_v3", "ledger": ledger, "epoch_id": "epoch-v3", "record_id": record_id, **extra}
    (ledgers / "opportunity.jsonl").write_text(json.dumps(row("opportunity", "o-1", episode_id="e-1")) + "\n", encoding="utf-8")
    (ledgers / "decision.jsonl").write_text(json.dumps(row("decision", "d-1", episode_id="e-1")) + "\n", encoding="utf-8")
    (ledgers / "lifecycle.jsonl").write_text(json.dumps(row("lifecycle", "l-1", episode_id="e-1", terminal=True)) + "\n", encoding="utf-8")
    (ledgers / "market_segment.jsonl").write_text(
        json.dumps(row("market_segment", "m-1", episode_id="e-1")) + "\n",
        encoding="utf-8",
    )
    segment_dir = mirror / "v3" / "market_segments" / "aa"
    segment_dir.mkdir(parents=True)
    write_json(segment_dir / "aa-segment.json", {"schema": "market_segment_v3"})
    write_json(reports / "safe_policy_genome_v3_report.json", {
        "generated_at": NOW.isoformat(), "status": "V3_COLLECTING", "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "real_bitfinex_trading_allowed": False, "number_one_strategy": None,
        "collection": {
            "independent_opportunities": 1,
            "decision_branches": 1,
            "terminal_lifecycles": 1,
            "provisional_lifecycles": 0,
            "market_segment_ledger_rows": 1,
            "pre_signal_context_segments": 1,
            "terminal_path_market_segments": 0,
            "market_segments": 0,
        },
    })
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW, fetcher=fetcher, process_reader=processes)
    result = checker.check()
    parity = next(item for item in result["checks"] if item["name"] == "v3_report_fresh_and_count_parity")
    money = next(item for item in result["checks"] if item["name"] == "v3_real_money_fail_closed")
    assert parity["ok"] is True and parity["detail"]["status"] == "EXACT"
    assert money["ok"] is True
    revision_parity = next(x for x in result["checks"] if x["name"] == "fly_sync_revision_parity")
    assert revision_parity["ok"] is True
    schema = next(x for x in result["checks"] if x["name"] == "mirror_schema_and_freshness")
    assert schema["detail"]["schema_source"] == "research_evidence_v3"
    # A compatibility v2.2 file may coexist with V3.1, but its frozen counts
    # and single-policy identity are no longer current health gates.
    assert not any(x["name"] == "report_count_parity" for x in result["checks"])
    assert not any(x["name"] == "report_epoch_policy_signature_parity" for x in result["checks"])


def test_v3_report_pending_window_bounds_each_ledger_without_double_counting(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)

    def write_rows(name, rows):
        (ledgers / f"{name}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    def base(ledger, index):
        return {
            "schema": "research_evidence_v3", "ledger": ledger,
            "epoch_id": "epoch-v3", "record_id": f"{ledger}-{index}",
            "episode_id": f"episode-{index}",
            "shared_ai_call_id": f"scan-{index}",
            "signal_ts": 1000 + index,
            "symbol": "TBTCF0:USTF0",
            "raw_direction": "LONG",
        }

    write_rows("opportunity", [base("opportunity", i) for i in range(40)])
    write_rows("decision", [base("decision", i) for i in range(40)])
    write_rows("lifecycle", [dict(base("lifecycle", i), terminal=True) for i in range(40)])
    write_json(reports / "safe_policy_genome_v3_report.json", {
        "generated_at": NOW.isoformat(), "status": "V3_COLLECTING",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "real_bitfinex_trading_allowed": False, "number_one_strategy": None,
        "collection": {"independent_opportunities": 0, "decision_branches": 0,
                       "terminal_lifecycles": 0, "provisional_lifecycles": 0,
                       "market_segments": 0},
    })
    result = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
        fetcher=fetcher, process_reader=processes,
    ).check()
    parity = next(x for x in result["checks"] if x["name"] == "v3_report_fresh_and_count_parity")
    assert parity["ok"] is True
    assert parity["detail"]["status"] == "PENDING_NEXT_ANALYZER_CYCLE"
    assert sum(parity["detail"]["deltas"].values()) > module.MAX_PENDING_EVENT_DELTA


def test_v3_progress_wins_over_frozen_compatibility_writer(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    rows = [
        {
            "schema": "research_evidence_v3", "ledger": "opportunity",
            "epoch_id": "epoch-v3", "record_id": f"o-{index}",
            "episode_id": f"e-{index}", "shared_ai_call_id": f"scan-{index}",
            "grouping_basis": "SHARED_AI_CALL", "signal_ts": 1000 + index,
            "symbol": "TBTCF0:USTF0", "raw_direction": "LONG",
        }
        for index in range(4)
    ]
    (ledgers / "opportunity.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    write_json(reports / "safe_policy_genome_v3_report.json", {
        "generated_at": NOW.isoformat(), "status": "V3_COLLECTING",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "real_bitfinex_trading_allowed": False, "number_one_strategy": None,
        "collection": {"independent_opportunities": 4, "decision_branches": 0,
                       "terminal_lifecycles": 0, "provisional_lifecycles": 0,
                       "market_segments": 0},
    })
    progress = repo / ".research-opportunity-progress-state.json"
    write_json(progress, {
        "epoch_key": "epoch-v3", "source_revision": "a" * 40,
        "independent_opportunities": 3, "observed_at": NOW.isoformat(),
    })
    result = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
        fetcher=fetcher, process_reader=processes, progress_state_file=progress,
    ).check()
    schema = next(x for x in result["checks"] if x["name"] == "mirror_schema_and_freshness")
    progress_check = next(x for x in result["checks"] if x["name"] == "independent_opportunity_progress")
    assert schema["detail"]["schema_source"] == "research_evidence_v3"
    assert progress_check["ok"] is True
    assert progress_check["detail"]["independent_opportunities"] == 4
    assert progress_check["detail"]["state"] == "ADVANCING"


def test_v3_supervisor_fails_overdue_expected_order_and_accepts_terminal_no_order(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    def row(ledger, record_id, **extra):
        return {"schema": "research_evidence_v3", "ledger": ledger,
                "epoch_id": "epoch-v3", "record_id": record_id, **extra}
    (ledgers / "opportunity.jsonl").write_text(json.dumps(row(
        "opportunity", "o-1", episode_id="e-1",
    )) + "\n", encoding="utf-8")
    (ledgers / "decision.jsonl").write_text(json.dumps(row(
        "decision", "d-1", episode_id="e-1", decision_stage="LANE_POLICY_VERDICT",
        research_lane="CONTINUOUS", policy_signature="sig-c",
        order_intent_expected=True, resolution_deadline_ts=NOW.timestamp() - 1,
    )) + "\n", encoding="utf-8")
    write_json(reports / "safe_policy_genome_v3_report.json", {
        "generated_at": NOW.isoformat(), "status": "V3_ORDER_RESOLUTION_INTEGRITY_FAILED",
        "qualification": "NO_SAFE_QUALIFIED_POLICY", "real_bitfinex_trading_allowed": False,
        "number_one_strategy": None,
        "collection": {"independent_opportunities": 1, "decision_branches": 1,
                       "terminal_lifecycles": 0, "provisional_lifecycles": 0,
                       "market_segments": 0},
    })
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token",
                                now=lambda: NOW, fetcher=fetcher, process_reader=processes)
    first = checker.check()
    integrity = next(x for x in first["checks"] if x["name"] == "v3_normalized_evidence_integrity")
    assert integrity["ok"] is False
    assert integrity["detail"]["entry_resolution_integrity"]["overdue_orphan"] == 1

    (ledgers / "lifecycle.jsonl").write_text(json.dumps(row(
        "lifecycle", "l-1", episode_id="e-1", research_lane="CONTINUOUS",
        policy_signature="sig-c", resolution_scope="LANE_ENTRY",
        entry_resolution="NO_ORDER", entry_resolution_terminal=True,
        terminal=True, outcome_state="NO_TRADE",
    )) + "\n", encoding="utf-8")
    report = json.loads((reports / "safe_policy_genome_v3_report.json").read_text())
    report["collection"].update(terminal_lifecycles=1)
    write_json(reports / "safe_policy_genome_v3_report.json", report)
    second = checker.check()
    integrity = next(x for x in second["checks"] if x["name"] == "v3_normalized_evidence_integrity")
    assert integrity["ok"] is True
    assert integrity["detail"]["entry_resolution_integrity"]["terminal_no_order"] == 1


def test_v3_supervisor_accepts_awaiting_order_within_declared_deadline(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    def write_ledger(name, rows):
        (ledgers / f"{name}.jsonl").write_text(
            "".join(json.dumps({"schema": "research_evidence_v3", "ledger": name,
                                "epoch_id": "epoch-v3", **row}) + "\n" for row in rows),
            encoding="utf-8",
        )
    write_ledger("opportunity", [{"record_id": "o-1", "episode_id": "e-1"}])
    write_ledger("decision", [{
        "record_id": "d-1", "episode_id": "e-1", "decision_stage": "LANE_POLICY_VERDICT",
        "research_lane": "CONTINUOUS", "policy_signature": "sig-c",
        "order_intent_expected": True, "resolution_deadline_ts": NOW.timestamp() + 60,
    }])
    write_ledger("lifecycle", [{
        "record_id": "l-await", "episode_id": "e-1", "research_lane": "CONTINUOUS",
        "policy_signature": "sig-c", "resolution_scope": "LANE_ENTRY",
        "entry_resolution": "AWAITING", "resolution_deadline_ts": NOW.timestamp() + 60,
        "entry_resolution_terminal": False, "terminal": False,
    }])
    write_json(reports / "safe_policy_genome_v3_report.json", {
        "generated_at": NOW.isoformat(), "status": "V3_COLLECTING",
        "qualification": "NO_SAFE_QUALIFIED_POLICY", "real_bitfinex_trading_allowed": False,
        "number_one_strategy": None,
        "collection": {"independent_opportunities": 1, "decision_branches": 1,
                       "terminal_lifecycles": 0, "provisional_lifecycles": 1,
                       "market_segments": 0},
    })
    result = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
        fetcher=fetcher, process_reader=processes,
    ).check()
    integrity = next(x for x in result["checks"] if x["name"] == "v3_normalized_evidence_integrity")
    assert integrity["ok"] is True
    assert integrity["detail"]["entry_resolution_integrity"]["awaiting_within_deadline"] == 1


def test_v3_supervisor_fails_execution_and_paper_lifecycle_without_policy_provenance(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    def write_ledger(name, rows):
        (ledgers / f"{name}.jsonl").write_text(
            "".join(json.dumps({"schema": "research_evidence_v3", "ledger": name,
                                "epoch_id": "epoch-v3", **row}) + "\n" for row in rows),
            encoding="utf-8",
        )
    write_ledger("opportunity", [{"record_id": "o-1", "episode_id": "e-1"}])
    incomplete = {
        "episode_id": "e-1", "event_id": "event-1",
        "policy_id": "PATIENT", "policy_signature": "sig-p",
        "policy_epoch_id": "pe-p",
    }
    write_ledger("execution", [{"record_id": "e-1", **incomplete}])
    write_ledger("lifecycle", [{
        "record_id": "l-1", **incomplete,
        "observation_status": "PAPER_POSITION_CLOSED", "terminal": True,
    }])
    write_json(reports / "safe_policy_genome_v3_report.json", {
        "generated_at": NOW.isoformat(), "status": "V3_EPOCH_CONTAMINATION_BLOCKED",
        "qualification": "NO_SAFE_QUALIFIED_POLICY", "real_bitfinex_trading_allowed": False,
        "number_one_strategy": None,
        "collection": {"independent_opportunities": 1, "decision_branches": 0,
                       "terminal_lifecycles": 1, "provisional_lifecycles": 0,
                       "market_segments": 0},
    })
    result = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
        fetcher=fetcher, process_reader=processes,
    ).check()
    integrity = next(x for x in result["checks"] if x["name"] == "v3_normalized_evidence_integrity")
    assert integrity["ok"] is False
    provenance = integrity["detail"]["policy_provenance_integrity"]
    assert provenance["checked_rows"] == 2
    assert provenance["defect_count"] == 2
    assert {tuple(row["missing_fields"]) for row in provenance["defects"]} == {
        ("research_lane", "shared_ai_call_id"),
    }


def test_v3_supervisor_fails_paper_scope_with_false_paper_identity(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    (ledgers / "opportunity.jsonl").write_text(json.dumps({
        "schema": "research_evidence_v3", "ledger": "opportunity",
        "epoch_id": "epoch-v3", "record_id": "o-1", "episode_id": "e-1",
    }) + "\n", encoding="utf-8")
    (ledgers / "decision.jsonl").write_text(json.dumps({
        "schema": "research_evidence_v3", "ledger": "decision",
        "epoch_id": "epoch-v3", "record_id": "d-1", "episode_id": "e-1",
        "decision_stage": "LANE_POLICY_VERDICT",
        "policy_execution_scope": "PAPER_RESEARCH_ONLY",
        "paper_only": False,
        "paper_policy_spec": {"paper_only": False, "relay_eligible": True},
    }) + "\n", encoding="utf-8")
    write_json(reports / "safe_policy_genome_v3_report.json", {
        "generated_at": NOW.isoformat(), "status": "V3_EPOCH_CONTAMINATION_BLOCKED",
        "qualification": "NO_SAFE_QUALIFIED_POLICY", "real_bitfinex_trading_allowed": False,
        "number_one_strategy": None,
        "collection": {"independent_opportunities": 1, "decision_branches": 1,
                       "terminal_lifecycles": 0, "provisional_lifecycles": 0,
                       "market_segments": 0},
    })
    result = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
        fetcher=fetcher, process_reader=processes,
    ).check()
    integrity = next(x for x in result["checks"] if x["name"] == "v3_normalized_evidence_integrity")
    assert integrity["ok"] is False
    provenance = integrity["detail"]["policy_provenance_integrity"]
    assert provenance["defect_count"] == 1
    assert provenance["defects"][0]["contradiction"] == "PAPER_SCOPE_WITH_FALSE_PAPER_ONLY"


def test_clean_v3_only_epoch_satisfies_mirror_schema_check(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    (mirror / "research_events_v22.jsonl").unlink()
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    for ledger in ("opportunity", "lifecycle"):
        row = {
            "schema": "research_evidence_v3", "ledger": ledger,
            "epoch_id": "epoch-v3-fresh", "record_id": f"{ledger}-1",
            "episode_id": "episode-1", "terminal": ledger == "lifecycle",
        }
        path = ledgers / f"{ledger}.jsonl"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))

    checker = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
        fetcher=fetcher, process_reader=processes,
    )
    result = checker.check()
    schema = next(x for x in result["checks"] if x["name"] == "mirror_schema_and_freshness")
    assert schema["ok"] is True
    assert schema["detail"]["schema_source"] == "research_evidence_v3"
    assert schema["detail"]["epoch_ids"] == ["epoch-v3-fresh"]


def test_v3_identity_alias_fails_integrity_and_is_not_counted_twice(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    common = {
        "schema": "research_evidence_v3", "ledger": "opportunity",
        "epoch_id": "epoch-v3", "signal_ts": 1000.0,
        "symbol": "BTCUSD", "raw_direction": "LONG",
    }
    rows = [
        {**common, "record_id": "o-fallback", "episode_id": "episode-fallback", "grouping_basis": "TIME_DIRECTION_SYMBOL_FALLBACK"},
        {**common, "record_id": "o-shared", "episode_id": "episode-shared", "grouping_basis": "SHARED_AI_CALL", "shared_ai_call_id": "scan-1"},
    ]
    (ledgers / "opportunity.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    write_json(reports / "safe_policy_genome_v3_report.json", {
        "generated_at": NOW.isoformat(), "status": "V3_EPOCH_CONTAMINATION_BLOCKED",
        "qualification": "NO_SAFE_QUALIFIED_POLICY", "real_bitfinex_trading_allowed": False,
        "number_one_strategy": None,
        "collection": {"independent_opportunities": 1, "decision_branches": 0, "terminal_lifecycles": 0, "provisional_lifecycles": 0, "market_segments": 0},
    })
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW, fetcher=fetcher, process_reader=processes)
    result = checker.check()
    integrity = next(item for item in result["checks"] if item["name"] == "v3_normalized_evidence_integrity")
    assert integrity["ok"] is False
    assert integrity["detail"]["raw_opportunity_rows"] == 2
    assert integrity["detail"]["independent_opportunities"] == 1
    assert integrity["detail"]["identity_alias_episode_ids"] == ["episode-fallback"]


def test_v3_shared_call_alias_across_symbol_spellings_fails_integrity(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    ledgers = mirror / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    rows = []
    for suffix, symbol in (("venue", "TBTCF0:USTF0"), ("generic", "BTCUSD")):
        rows.append({
            "schema": "research_evidence_v3", "ledger": "opportunity",
            "epoch_id": "epoch-v3", "record_id": f"o-{suffix}",
            "episode_id": f"episode-{suffix}", "signal_ts": 1000.0,
            "symbol": symbol, "raw_direction": "LONG",
            "grouping_basis": "SHARED_AI_CALL", "shared_ai_call_id": "scan-same",
        })
    (ledgers / "opportunity.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    write_json(reports / "safe_policy_genome_v3_report.json", {
        "generated_at": NOW.isoformat(), "status": "V3_EPOCH_CONTAMINATION_BLOCKED",
        "qualification": "NO_SAFE_QUALIFIED_POLICY", "real_bitfinex_trading_allowed": False,
        "number_one_strategy": None,
        "collection": {"independent_opportunities": 1, "decision_branches": 0,
                       "terminal_lifecycles": 0, "provisional_lifecycles": 0,
                       "market_segments": 0},
    })
    checker = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token",
        now=lambda: NOW, fetcher=fetcher, process_reader=processes,
    )
    result = checker.check()
    integrity = next(item for item in result["checks"] if item["name"] == "v3_normalized_evidence_integrity")
    assert integrity["ok"] is False
    assert integrity["detail"]["identity_alias_count"] == 1
    assert integrity["detail"]["independent_opportunities"] == 1


def test_missing_v2_and_v3_evidence_fails_mirror_schema_check(tmp_path):
    repo, mirror, reports = make_fixture(tmp_path)
    (mirror / "research_events_v22.jsonl").unlink()
    checker = module.Supervisor(
        repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
        fetcher=fetcher, process_reader=processes,
    )
    result = checker.check()
    schema = next(x for x in result["checks"] if x["name"] == "mirror_schema_and_freshness")
    assert schema["ok"] is False
    assert "neither research_events_v22.jsonl nor V3 normalized ledgers exist" in schema["detail"]


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


def test_mid_cycle_append_uses_shared_snapshot_prefix_not_write_time(tmp_path):
    repo, mirror, reports = make_one_event_pending(tmp_path)
    from datetime import timedelta
    receipt = {
        "schema": "policy_cycle_snapshot_v1",
        "snapshot_id": "policy-snapshot-fixture",
        "captured_at": (NOW - timedelta(minutes=6)).isoformat(),
        "source_file": "research_events_v22.jsonl",
        "row_count": 3,
        "last_event_id": "event-2",
        "epoch_id": "epoch-new",
        "policy_epoch_id": "policy-epoch-a",
        "policy_signature": "policy-a",
    }
    for filename in ("policy_candidate_oos_report.json", "best_policy_research_report.json"):
        report = json.loads((reports / filename).read_text())
        report["cycle_snapshot"] = receipt
        write_json(reports / filename, report)
    # The append happened during the analyzer cycle, before its final report
    # write. Timestamp ordering alone therefore cannot identify the pinned
    # boundary, but the shared prefix receipt can.
    import os
    events_path = mirror / "research_events_v22.jsonl"
    old = (NOW - timedelta(minutes=10)).timestamp()
    os.utime(events_path, (old, old))

    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    parity = next(x for x in result["checks"] if x["name"] == "report_count_parity")
    assert parity["ok"] is True
    assert parity["detail"]["status"] == "PENDING_NEXT_ANALYZER_CYCLE"
    assert parity["detail"]["pending"]["mirror_after_reports"] is False
    assert parity["detail"]["pending"]["snapshot_prefix_ok"] is True
    assert parity["detail"]["cycle_snapshot_prefix"]["prefix_terminal_event_id"] == "event-2"


def test_mid_cycle_append_with_false_snapshot_terminal_fails_closed(tmp_path):
    repo, mirror, reports = make_one_event_pending(tmp_path)
    receipt = {
        "schema": "policy_cycle_snapshot_v1", "snapshot_id": "bad-snapshot",
        "row_count": 3, "last_event_id": "not-event-2", "epoch_id": "epoch-new",
        "policy_epoch_id": "policy-epoch-a", "policy_signature": "policy-a",
    }
    for filename in ("policy_candidate_oos_report.json", "best_policy_research_report.json"):
        report = json.loads((reports / filename).read_text())
        report["cycle_snapshot"] = receipt
        write_json(reports / filename, report)
    from datetime import timedelta
    import os
    old = (NOW - timedelta(minutes=10)).timestamp()
    os.utime(mirror / "research_events_v22.jsonl", (old, old))
    checker = module.Supervisor(repo, mirror, reports, "https://fly.invalid", "token", now=lambda: NOW,
                                fetcher=fetcher, process_reader=processes)
    result = checker.check()
    parity = next(x for x in result["checks"] if x["name"] == "report_count_parity")
    assert parity["ok"] is False
    assert parity["detail"]["pending"]["snapshot_prefix_ok"] is False


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


def test_strategy_progress_failure_is_immediately_unhealthy_even_if_ready():
    status = {
        "process_alive": True,
        "system_ready": True,
        "signal_generation_ready": True,
        "git_rev": "a" * 40,
        "runtime_readiness": {
            "prerequisites_ready": True,
            "signal_generation_ready": True,
            "readiness_reasons": [],
        },
        "strategy_progress": {
            "ok": False,
            "reasons": ["TRADE_LOCK_UNAVAILABLE", "AI_CADENCE_STALLED"],
            "trade_lock_available": False,
            "ws_age_sec": 2.0,
            "ai_age_sec": 420.0,
        },
    }
    ok, detail, _ = module.evaluate_runtime_readiness(
        status,
        {},
        now=NOW,
        counts={"virtual_count": 0, "pending_count": 2, "position_count": 2},
    )
    assert ok is False
    assert detail["state"] == "STRATEGY_PROGRESS_FAILED"
    assert detail["trade_lock_available"] is False
    assert detail["strategy_progress_reasons"] == [
        "TRADE_LOCK_UNAVAILABLE", "AI_CADENCE_STALLED",
    ]


def test_supervisor_releases_live_mirror_before_schema_counting(tmp_path, monkeypatch):
    path = tmp_path / "research_events_v22.jsonl"
    row = {
        "event_id": "event-1", "schema": "research_event_v2.2",
        "collector_version": "collector_v2.2", "epoch_id": "epoch-a",
        "policy_epoch_id": "policy-epoch-a", "policy_signature": "policy-a",
        "event_episode_id": "episode-a", "observation_status": "COMPLETE",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    parsing_started = threading.Event()
    allow_parsing = threading.Event()
    real_loads = json.loads

    def slow_loads(*args, **kwargs):
        parsing_started.set()
        assert allow_parsing.wait(5)
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(module.json, "loads", slow_loads)
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("summary", module.read_current_events(path))
    )
    worker.start()
    assert parsing_started.wait(5)
    replacement = tmp_path / "supervisor-replacement.download"
    replacement.write_text(json.dumps({**row, "event_id": "event-2"}) + "\n", encoding="utf-8")
    try:
        os.replace(replacement, path)
    finally:
        allow_parsing.set()
        worker.join(5)
    assert not worker.is_alive()
    assert result["summary"]["current_events"] == 1
