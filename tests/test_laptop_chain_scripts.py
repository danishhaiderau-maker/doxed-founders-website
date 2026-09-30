"""Contract and behaviour tests for the laptop research-chain scripts."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CHAIN_SCRIPTS = [
    "laptop-chain-common.ps1",
    "run-segment-analyzer-cycle.ps1",
    "run-analyzer-once.ps1",
    "laptop-chain-monitor.ps1",
    "laptop-chain-supervisor.ps1",
    "laptop-status-snapshots.ps1",
    "register-laptop-chain-task.ps1",
    "research-segment-pull.ps1",
    "research-segment-pull-loop.ps1",
]
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
windows_only = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell required")


def _source(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("name", CHAIN_SCRIPTS)
def test_no_hard_coded_revisions_or_generations(name):
    assert re.findall(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40,64}(?![0-9a-fA-F])", _source(name)) == []


@pytest.mark.parametrize("name", CHAIN_SCRIPTS)
def test_never_kills_by_command_line_pattern(name):
    source = _source(name)
    assert "Stop-Process" not in source
    assert not re.search(r"CommandLine\s+-match", source)
    assert "Win32_Process" not in source


@pytest.mark.parametrize("name", CHAIN_SCRIPTS)
def test_never_touches_relay_pause_or_deploy(name):
    lowered = _source(name).lower()
    for forbidden in ("/api/relay", "rearm", "live-copy", "fly deploy", "flyctl", "/pause", "/resume", "-method post -uri $($cfg"):
        assert forbidden not in lowered


def test_single_instance_helpers_and_utc_timestamps():
    common = _source("laptop-chain-common.ps1")
    assert "System.Threading.Mutex" in common
    assert "AbandonedMutexException" in common
    assert "Get-Date -Format" not in common
    assert "ToUniversalTime" in common and "UtcNow" in common


def test_legacy_ack_watcher_is_retired():
    assert not (SCRIPTS / "laptop-ack-watcher.ps1").exists()


def test_analyzer_runner_uses_real_exit_codes():
    runner = _source("run-analyzer-once.ps1")
    assert "WaitForExit" in runner
    assert "RedirectStandardError = $true" in runner
    assert "exit $exitCode" in runner
    launcher = _source("start-home-analyzer.ps1")
    assert launcher.rstrip().endswith("exit $exitCode")


def test_supervisor_keeps_one_segment_pull_loop_not_a_second_watcher():
    supervisor = _source("laptop-chain-supervisor.ps1")
    loop = _source("research-segment-pull-loop.ps1")
    pull = _source("research-segment-pull.ps1")
    assert "Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentPull')" in supervisor
    assert "research-segment-pull-loop.ps1" in supervisor
    assert "laptop-ack-watcher" not in supervisor
    assert "run-segment-analyzer-cycle.ps1" in supervisor
    assert "Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle')" in supervisor
    cycle = _source("run-segment-analyzer-cycle.ps1")
    assert "Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle')" in cycle
    assert "Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentPull')" in loop
    assert "[int]$PullIntervalSec = 120" in loop and "[int]$ParityIntervalMin = 30" in loop
    assert "'-Source', 'Http'" in loop and "-MaxSegments" in loop
    assert "laptop-ack-watcher" not in loop
    # Only the admin token is read from the vault and it is never echoed.
    assert "BOT_ADMIN_TOKEN" in pull and "Write-Host $env:BOT_ADMIN_TOKEN" not in pull
    assert "'Process')" in pull


def test_supervisor_task_uses_system_powershell():
    register = _source("register-laptop-chain-task.ps1")
    assert "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in register
    assert "-AtLogOn" in register and "New-TimeSpan -Minutes 5" in register
    assert ".cache" not in register


# --- behaviour ---------------------------------------------------------------


def _ps(script: str, env=None, timeout=120):
    merged = dict(os.environ)
    merged.update(env or {})
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout, env=merged,
    )


@pytest.fixture()
def short_root():
    # Receipt names are ~130 characters; keep the fixture under MAX_PATH.
    import tempfile

    try:
        root = Path(tempfile.mkdtemp(prefix="lc", dir=Path(tempfile.gettempdir()).anchor))
    except OSError:
        pytest.skip("no short temporary root available")
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def chain(tmp_path):
    canonical = tmp_path / "canonical"
    data = canonical / "services" / "btc-conservative-agent" / "canonical-research-data"
    data.mkdir(parents=True)
    state = tmp_path / "state"
    segments = tmp_path / "segments"
    (segments / ".puller").mkdir(parents=True)
    prefix = f"DoxxedTest{uuid.uuid4().hex[:10]}"
    return {"canonical": canonical, "data": data, "state": state, "segments": segments,
            "env": {"DOXXED_LAPTOP_CHAIN_MUTEX_PREFIX": prefix, "RESEARCH_SEGMENT_SHADOW_ROOT": str(segments)}}


def _common_prelude(chain) -> str:
    return (
        f". '{SCRIPTS / 'laptop-chain-common.ps1'}'; "
        f"$cfg = Get-LaptopChainConfig -RepoRoot '{ROOT}' -CanonicalRoot '{chain['canonical']}' -StateDir '{chain['state']}'; "
    )


def _heartbeat(chain, **payload):
    (chain["data"] / ".fly-data-sync-loop.heartbeat.json").write_text(json.dumps(payload), encoding="utf-8")


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _iso(delta_minutes: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)).isoformat()


@windows_only
def test_terminal_failure_preserves_and_closes_in_progress_heartbeat(chain):
    _heartbeat(chain, ok=True, inProgress=True, phase="chunk_complete", updatedAt=_iso(-120), syncedAt=_iso(-120))
    result = _ps(_common_prelude(chain) + "Set-SyncHeartbeatTerminalFailure -Config $cfg -Reason 'TEST' -ConsecutiveFailures 2 -BackoffSec 120", chain["env"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
    payload = json.loads((chain["data"] / ".fly-data-sync-loop.heartbeat.json").read_text(encoding="utf-8-sig"))
    assert payload["ok"] is False and payload["inProgress"] is False
    assert payload["abandonedPhase"] == "chunk_complete"
    span = datetime.fromisoformat(payload["nextRetryAt"][:26]) - datetime.fromisoformat(payload["pollFailedAt"][:26])
    assert abs(span.total_seconds() - 120) < 2
    assert list((chain["state"] / "quarantine").glob("heartbeat-in-progress-*.json"))


@windows_only
def test_terminal_failure_never_rewrites_completed_receipt(chain):
    _heartbeat(chain, ok=True, inProgress=False, phase="complete")
    result = _ps(_common_prelude(chain) + "Set-SyncHeartbeatTerminalFailure -Config $cfg -Reason 'TEST'", chain["env"])
    assert result.stdout.strip() == "False"
    assert json.loads((chain["data"] / ".fly-data-sync-loop.heartbeat.json").read_text())["ok"] is True


@windows_only
@pytest.mark.parametrize("payload,expected", [
    ({"inProgress": True, "ownerPid": "DEAD", "updatedAt": "NOW"}, "True"),
    ({"inProgress": True, "updatedAt": "OLD"}, "True"),
    ({"inProgress": True, "updatedAt": "NOW"}, "False"),
    ({"inProgress": False, "updatedAt": "OLD"}, "False"),
])
def test_abandoned_in_progress_detection(chain, payload, expected):
    payload = dict(payload)
    if payload.get("ownerPid") == "DEAD":
        payload["ownerPid"] = _dead_pid()
    payload["updatedAt"] = _iso(-60) if payload["updatedAt"] == "OLD" else _iso()
    _heartbeat(chain, **payload)
    result = _ps(_common_prelude(chain) + "Test-InProgressHeartbeatAbandoned -Config $cfg -StaleMinutes 15", chain["env"])
    assert result.stdout.strip() == expected, result.stderr


@windows_only
def test_named_mutex_is_exclusive_across_processes_and_released_on_death(chain):
    holder = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-Command",
         _common_prelude(chain) + "$h = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentPull'); 'held'; Start-Sleep -Seconds 60"],
        stdout=subprocess.PIPE, text=True, env={**os.environ, **chain["env"]},
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        probe = _ps(_common_prelude(chain) + "Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentPull')", chain["env"])
        assert probe.stdout.strip() == "True"
    finally:
        holder.kill()
        holder.wait()
    probe = _ps(_common_prelude(chain) + "Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentPull')", chain["env"])
    assert probe.stdout.strip() == "False"


@windows_only
def test_last_ack_comes_from_terminal_receipts(short_root):
    chain = {"canonical": short_root, "data": short_root / "d", "state": short_root / "s", "env": {}}
    receipts = chain["data"] / "receipts" / "terminal-transfer-membership"
    receipts.mkdir(parents=True)
    older, newer = "a" * 64, "b" * 64
    (receipts / f"terminal-transfer-membership-{older}-{'1' * 32}.json").write_text("{}")
    time.sleep(0.05)
    (receipts / f"terminal-transfer-membership-{newer}-{'2' * 32}.json").write_text("{}")
    (receipts / "unrelated.json").write_text("{}")
    result = _ps(_common_prelude(chain) + f"$a = Get-AckedGenerations '{chain['data']}'; " + "\"$($a.Set.Count) $($a.Last.generation)\"", chain["env"])
    assert result.stdout.strip() == f"2 {newer}", result.stderr


def _run_monitor(chain):
    return _ps(
        f"& '{SCRIPTS / 'laptop-chain-monitor.ps1'}' -RepoRoot '{ROOT}' -CanonicalRoot '{chain['canonical']}' "
        f"-StateDir '{chain['state']}' -NoNotify; exit $LASTEXITCODE",
        chain["env"],
    )


def _pull_status(chain, **payload):
    chain["state"].mkdir(parents=True, exist_ok=True)
    (chain["state"] / "segment-pull.status.json").write_text(json.dumps(payload))


def _puller_status(chain, **overrides):
    status = {"schema": "research_segment_puller_status_v1", "prefix": "v2", "applied_seq": 40, "acked_seq": 40,
              "last_error": None, "updated_at": _iso(),
              "ack_receipt": {"ok": True, "result": "RECORDED", "through_seq": 40, "received_at": _iso()}}
    status.update(overrides)
    (chain["segments"] / ".puller" / "status.json").write_text(json.dumps(status), encoding="utf-8")


def _active_alert_codes(chain):
    active = json.loads((chain["state"] / "alerts" / "active-alerts.json").read_text(encoding="utf-8-sig"))
    return {a["code"] for a in active["alerts"]}


@windows_only
def test_monitor_raises_every_alert(chain):
    chain["state"].mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-200), "state": "FAILED"}))
    _pull_status(chain, pid=1, finishedAt=_iso(-60), exitCode=0, error=None)
    _heartbeat(chain, ok=True, inProgress=True, phase="chunk_complete", updatedAt=_iso(-30))
    result = _run_monitor(chain)
    assert result.returncode == 10, result.stdout + result.stderr
    active = json.loads((chain["state"] / "alerts" / "active-alerts.json").read_text(encoding="utf-8-sig"))
    assert {a["code"] for a in active["alerts"]} == {
        "ANALYZER_NO_COMPLETION", "SYNC_HEARTBEAT_IN_PROGRESS_TOO_LONG", "SEGMENT_PULL_DEAD", "SEGMENT_PULL_STALE",
    }
    assert list((chain["state"] / "alerts").glob("alerts-*.jsonl"))


def _monitor_with_pull_loop_held(chain):
    holder = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-Command",
         _common_prelude(chain) + "$h = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentPull'); 'held'; Start-Sleep -Seconds 60"],
        stdout=subprocess.PIPE, text=True, env={**os.environ, **chain["env"]},
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        return _run_monitor(chain)
    finally:
        holder.kill()
        holder.wait()


@windows_only
def test_monitor_flags_a_failing_pull_loop(chain):
    (chain["state"]).mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-5)}))
    _pull_status(chain, pid=1, finishedAt=_iso(-1), exitCode=2, error="HEAD_UNREACHABLE")
    result = _monitor_with_pull_loop_held(chain)
    active = json.loads((chain["state"] / "alerts" / "active-alerts.json").read_text(encoding="utf-8-sig"))
    assert {a["code"] for a in active["alerts"]} == {"SEGMENT_PULL_FAILING"}, result.stdout + result.stderr


@windows_only
def test_monitor_treats_shadow_lock_contention_as_busy(chain):
    (chain["state"]).mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-5)}))
    _pull_status(chain, pid=1, finishedAt=_iso(-1), exitCode=2,
                 error="PullerError: another puller run holds the shadow-root lock")
    result = _monitor_with_pull_loop_held(chain)
    active = json.loads((chain["state"] / "alerts" / "active-alerts.json").read_text(encoding="utf-8-sig"))
    assert active["alerts"] == [], result.stdout + result.stderr


@windows_only
def test_monitor_is_quiet_when_chain_is_healthy(chain):
    chain["state"].mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-5)}))
    _pull_status(chain, pid=1, finishedAt=_iso(-1), exitCode=0, error=None)
    _heartbeat(chain, ok=True, inProgress=False, phase="complete")
    _puller_status(chain)
    (chain["segments"] / "parity-latest.json").write_text(json.dumps(
        {"verdict": "GREEN", "seq": 40, "generated_at": _iso(-5)}), encoding="utf-8")
    result = _monitor_with_pull_loop_held(chain)
    assert result.returncode == 0, result.stdout + result.stderr


@windows_only
def test_monitor_raises_v2_ack_parity_and_shipper_alerts_not_retired_mirror_alerts(chain):
    chain["state"].mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-5)}))
    _pull_status(chain, pid=1, finishedAt=_iso(-1), exitCode=0, error=None)
    (chain["state"] / "laptop-ack-watcher.status.json").write_text(json.dumps({"consecutiveSyncFailures": 9, "detail": "x"}))
    _puller_status(chain, ack_receipt={"ok": True, "result": "RECORDED", "through_seq": 12, "received_at": _iso(-60)})
    (chain["segments"] / "parity-latest.json").write_text(json.dumps(
        {"verdict": "RED", "seq": 12, "generated_at": _iso(-5)}), encoding="utf-8")
    (chain["state"] / "fly_segment_head_snapshot_v1.json").write_text(json.dumps(
        {"schema": "fly_segment_head_snapshot_v1", "ok": True, "observedAt": _iso(), "last_error": "PLAN_RACE"}))
    result = _monitor_with_pull_loop_held(chain)
    assert result.returncode == 10, result.stdout + result.stderr
    assert _active_alert_codes(chain) == {"SEGMENT_ACK_STALE", "SEGMENT_PARITY_NOT_GREEN", "FLY_SEGMENT_SHIPPER_ERROR"}


@windows_only
def test_monitor_treats_plan_race_as_benign_until_shipping_stalls(chain):
    import time
    chain["state"].mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-5)}))
    _pull_status(chain, pid=1, finishedAt=_iso(-1), exitCode=0, error=None)
    _puller_status(chain)
    (chain["segments"] / "parity-latest.json").write_text(json.dumps(
        {"verdict": "GREEN", "seq": 40, "generated_at": _iso(-5)}), encoding="utf-8")
    head_path = chain["state"] / "fly_segment_head_snapshot_v1.json"

    def head(stall_sec):
        head_path.write_text(json.dumps(
            {"schema": "fly_segment_head_snapshot_v1", "ok": True, "observedAt": _iso(),
             "last_segment_at": time.time() - stall_sec,
             "last_error": "PLAN_RACE: v3/receipts/x/complete.json changed identity"}))

    head(120)
    result = _monitor_with_pull_loop_held(chain)
    assert result.returncode == 0, result.stdout + result.stderr
    head(45 * 60)
    result = _monitor_with_pull_loop_held(chain)
    assert result.returncode == 10, result.stdout + result.stderr
    assert _active_alert_codes(chain) == {"FLY_SEGMENT_SHIPPER_ERROR"}


@windows_only
def test_monitor_flags_a_rejected_v2_ack(chain):
    chain["state"].mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-5)}))
    _pull_status(chain, pid=1, finishedAt=_iso(-1), exitCode=0, error=None)
    _puller_status(chain, ack_receipt={"ok": False, "result": "SEQ_REGRESSION", "through_seq": 3, "received_at": _iso()})
    _monitor_with_pull_loop_held(chain)
    assert _active_alert_codes(chain) == {"SEGMENT_ACK_REJECTED"}


@windows_only
def test_status_snapshots_offline_are_explicit_failures_never_flat(chain):
    result = _ps(
        f"& '{SCRIPTS / 'laptop-status-snapshots.ps1'}' -RepoRoot '{ROOT}' -CanonicalRoot '{chain['canonical']}' "
        f"-StateDir '{chain['state']}'; exit $LASTEXITCODE",
        {**chain["env"], "DOXXED_LAPTOP_CHAIN_OFFLINE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    head = json.loads((chain["state"] / "fly_segment_head_snapshot_v1.json").read_text(encoding="utf-8-sig"))
    relay = json.loads((chain["state"] / "relay_status_snapshot_v1.json").read_text(encoding="utf-8-sig"))
    assert head["schema"] == "fly_segment_head_snapshot_v1" and head["ok"] is False and head["error"] == "OFFLINE"
    assert relay["schema"] == "relay_status_snapshot_v1" and relay["ok"] is False
    assert "relayArmedAt" not in relay and "reconciliation" not in relay


def test_status_snapshots_are_read_only_and_never_persist_secrets():
    source = _source("laptop-status-snapshots.ps1")
    assert source.count("Invoke-RestMethod -Method Get") == 4
    assert "-Method Post" not in source and "-Method Put" not in source
    persisted = source[source.index("$relay.ok = $true"):source.index("} catch {\n    $relay.error")]
    assert "userId" not in persisted and "adminToken" not in persisted and "lastError" not in persisted
    runtime = source[source.index("New-Snapshot 'fly_runtime_snapshot_v1'"):]
    assert "$runtime.research_lane_enabled = $toggles" in runtime
    assert not re.search(r"\$runtime(\.|\[)[^=\n]*=\s*\$adminToken", runtime)
    assert "$runtime.token" not in runtime and "$runtime.headers" not in runtime.lower()


def test_supervisor_runs_the_unattended_proof_after_the_monitor():
    supervisor = _source("laptop-chain-supervisor.ps1")
    assert supervisor.index("laptop-chain-monitor.ps1") < supervisor.index("unattended_proof.py")
    assert "--check" in supervisor and "--start" not in supervisor


def test_supervisor_collects_snapshots_before_the_monitor():
    supervisor = _source("laptop-chain-supervisor.ps1")
    assert supervisor.index("laptop-status-snapshots.ps1") < supervisor.index("laptop-chain-monitor.ps1")


def _fake_repo(tmp_path, launcher_body: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "services" / "btc-conservative-agent").mkdir(parents=True)
    for name in ("laptop-chain-common.ps1", "run-analyzer-once.ps1"):
        shutil.copy(SCRIPTS / name, repo / "scripts" / name)
    (repo / "scripts" / "start-home-analyzer.ps1").write_text(launcher_body, encoding="utf-8")
    return repo


def _run_runner(repo, chain, *extra):
    return _ps(
        f"& '{repo / 'scripts' / 'run-analyzer-once.ps1'}' -RepoRoot '{repo}' -CanonicalRoot '{chain['canonical']}' "
        f"-StateDir '{chain['state']}' -Port 59431 -DashboardReadySec 0 -Reason test {' '.join(extra)}; exit $LASTEXITCODE",
        {**chain["env"], "DOXXED_LAPTOP_CHAIN_OFFLINE": "1"},
        timeout=180,
    )


@windows_only
def test_runner_propagates_launcher_exit_code_and_stderr(tmp_path, chain):
    repo = _fake_repo(tmp_path, "param([switch]$Once,[switch]$NoWait,[switch]$DashboardOnly,[int]$Port=0)\n"
                               "if ($DashboardOnly) { exit 0 }\n[Console]::Error.WriteLine('boom from analyzer'); exit 7\n")
    result = _run_runner(repo, chain)
    assert result.returncode == 7, result.stdout + result.stderr
    status = json.loads((chain["state"] / "analyzer-run.status.json").read_text(encoding="utf-8-sig"))
    assert status["exitCode"] == 7 and status["state"] == "FAILED"
    assert "boom from analyzer" in status["detail"]
    assert "boom from analyzer" in Path(status["stderrLog"]).read_text()
    link = repo / "services" / "btc-conservative-agent" / "canonical-research-data"
    assert link.exists()


@windows_only
def test_runner_zero_exit_without_new_generation_is_not_success(tmp_path, chain):
    repo = _fake_repo(tmp_path, "param([switch]$Once,[switch]$NoWait,[switch]$DashboardOnly,[int]$Port=0)\nexit 0\n")
    result = _run_runner(repo, chain)
    assert result.returncode == 6, result.stdout + result.stderr
    status = json.loads((chain["state"] / "analyzer-run.status.json").read_text(encoding="utf-8-sig"))
    assert status["lastSuccessAt"] is None
    assert "NO_NEW_COMPLETED_GENERATION" in status["detail"]


@windows_only
def test_runner_waits_for_dashboard_before_single_pass():
    runner = _source("run-analyzer-once.ps1")
    assert "while (-not (Get-AnalyzerStatus)" in runner
    single_pass = runner.index("'-Once'")
    ensures = [m.start() for m in re.finditer(r"Confirm-AnalyzerDashboard\s*(\n|\})", runner)]
    assert any(pos < single_pass for pos in ensures)
    assert any(pos > single_pass for pos in ensures)
    assert runner.index("'-Once'") < runner.index("$after = Get-AnalyzerStatus")


@windows_only
def test_research_mode_probe_matches_score_led_upstream(chain):
    repo_root = SCRIPTS.parent
    agent = repo_root / "services" / "btc-conservative-agent"
    probe = "import combo_pathway_config as c; print(c.ANALYZER_SYNC_ID); print(c.active_tile_registry_signature())"
    env = {k: v for k, v in os.environ.items() if k != "SCORE_LED_PAPER_RESEARCH_ENABLED"}
    env["SCORE_LED_PAPER_RESEARCH_ENABLED"] = "1"
    sync_id, signature = subprocess.run(
        [sys.executable, "-c", probe], cwd=agent, env=env, capture_output=True, text=True, check=True,
    ).stdout.split()[-2:]
    result = _ps(
        _common_prelude(chain)
        + f"$h = [pscustomobject]@{{ analyzer_sync_id = '{sync_id}'; tile_registry_signature = '{signature}' }}; "
        + f"$m = Resolve-AnalyzerResearchMode -RepoRoot '{repo_root}' -Health $h; "
        + "if ($m.Matched -and $m.Flag -eq '1') { exit 0 } else { exit 9 }",
        chain["env"],
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@windows_only
def test_runner_refuses_overlap(tmp_path, chain):
    repo = _fake_repo(tmp_path, "exit 0\n")
    holder = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-Command",
         _common_prelude(chain) + "$h = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopAnalyzerRun'); 'held'; Start-Sleep -Seconds 60"],
        stdout=subprocess.PIPE, text=True, env={**os.environ, **chain["env"]},
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        result = _run_runner(repo, chain)
    finally:
        holder.kill()
        holder.wait()
    assert result.returncode == 3, result.stdout + result.stderr
