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


def _ps_allowlist_names(common: str, var_name: str) -> list[str]:
    match = re.search(
        rf"\$script:{re.escape(var_name)}\s*=\s*@\((.*?)\)",
        common,
        re.S,
    )
    assert match, f"{var_name} declaration missing"
    return [
        n.strip().strip("'").strip('"')
        for n in match.group(1).split(",")
        if n.strip().strip("'").strip('"')
    ]


def test_vault_allowlist_is_admin_token_only_and_refuses_bitfinex():
    """RK 05:12 / HM b5700e72694d: true allowlists; never BITFINEX_* from vault."""
    common = _source("laptop-chain-common.ps1")
    assert "LaptopChainVaultAllowlist" in common
    assert "LaptopChainBotVaultAllowlist" in common
    assert "Import-LaptopChainVaultAllowlist" in common
    assert "Assert-BitfinexProcessEnvAbsent" in common
    assert "BotAllowlist" in common
    assert "ValidateSet('AdminTokenOnly', 'BotAllowlist')" in common
    assert "StartsWith('BITFINEX_'" in common or 'StartsWith("BITFINEX_"' in common
    allow_names = _ps_allowlist_names(common, "LaptopChainVaultAllowlist")
    bot_names = _ps_allowlist_names(common, "LaptopChainBotVaultAllowlist")
    assert allow_names == ["BOT_ADMIN_TOKEN"]
    assert "BOT_ADMIN_TOKEN" in bot_names and "BOT_CONTROL_SECRET" in bot_names
    assert all(not n.upper().startswith("BITFINEX_") for n in bot_names)
    assert not re.search(r'Set-Item\s+-Path\s+"env:\$', common)
    assert "Write-Host $env:BOT_ADMIN_TOKEN" not in common
    source_fixture = {
        "BOT_ADMIN_TOKEN": "fixture-admin-token",
        "BOT_CONTROL_SECRET": "fixture-control",
        "BITFINEX_API_KEY": "fixture-bitfinex-key",
        "BITFINEX_API_SECRET": "fixture-bitfinex-secret",
        "OTHER_SECRET": "fixture-other",
    }
    admin_exported = {
        name: value
        for name, value in source_fixture.items()
        if name in allow_names and not name.upper().startswith("BITFINEX_")
    }
    bot_exported = {
        name: value
        for name, value in source_fixture.items()
        if name in bot_names and not name.upper().startswith("BITFINEX_")
    }
    assert admin_exported == {"BOT_ADMIN_TOKEN": "fixture-admin-token"}
    assert "BITFINEX_API_KEY" not in bot_exported and "OTHER_SECRET" not in bot_exported
    assert "BOT_CONTROL_SECRET" in bot_exported


@pytest.mark.parametrize(
    "script_name",
    ("start-home-bot.ps1", "bot-auto-restart.ps1", "start-local-collection-bot.ps1"),
)
def test_bot_starters_route_vault_through_true_bot_allowlist(script_name):
    """RK 09:33: vault allowlist only; no inherited-env strip; restore LIVE=0."""
    source = _source(script_name)
    assert "Import-LaptopChainVaultAllowlist" in source
    assert "BotAllowlist" in source
    assert "Assert-BitfinexProcessEnvAbsent" not in source
    assert "Assert-LaptopMonitorReadTokenForBotStart" in source
    assert "laptop-chain-common.ps1" in source
    assert not re.search(
        r"Get-Content\s+\$\w*[Vv]ault\w*\s*\|[^\n]*ForEach-Object",
        source,
    )
    # RK 09:33: starters must not Clear inherited BITFINEX_* names.
    assert "Clear-BitfinexProcessEnvNames" not in source
    assert re.search(r'\$env:BITFINEX_LIVE_ENABLED\s*=\s*"0"', source)


def test_supervisor_and_cycle_fail_closed_on_bitfinex_env_names():
    """Refusal inside supervisor children / analyzer runners (not bot starters)."""
    supervisor = _source("laptop-chain-supervisor.ps1")
    cycle = _source("run-segment-analyzer-cycle.ps1")
    analyzer = _source("run-analyzer-once.ps1")
    home_analyzer = _source("start-home-analyzer.ps1")
    for source in (supervisor, cycle, analyzer):
        assert "Assert-BitfinexProcessEnvAbsent" in source
        assert "BITFINEX_ENV_NAME_COUNT count=" in _source("laptop-chain-common.ps1")
    assert "Assert-BitfinexProcessEnvAbsent" not in home_analyzer
    assert "Clear-BitfinexProcessEnvNames" not in home_analyzer
    assert re.search(r'\$env:BITFINEX_LIVE_ENABLED\s*=\s*"0"', home_analyzer)
    assert "Write-Host $env:BITFINEX" not in supervisor


def test_bot_allowlist_names_justified_or_dropped():
    """RK 09:33 / HM HOLD 716511e8582d: DEEPSEEK + MONITOR dropped; laptop GET token added."""
    common = _source("laptop-chain-common.ps1")
    bot_names = _ps_allowlist_names(common, "LaptopChainBotVaultAllowlist")
    assert "DEEPSEEK_API_KEY" not in bot_names
    assert "MONITOR_READ_TOKEN" not in bot_names
    assert "PLATFORM_API_BASE_URL" not in bot_names
    expected = {
        "BOT_ADMIN_TOKEN",
        "BOT_CONTROL_SECRET",
        "SHOWCASE_WEBHOOK_SECRET",
        "SHOWCASE_RELAY_WEBHOOK_URL",
        "CRASH_NOTIFY_WEBHOOK",
        "LIFECYCLE_LAPTOP_ATTESTATION_KEY",
        "LIFECYCLE_CLEANUP_ENABLED",
        "LAPTOP_MONITOR_READ_TOKEN",
    }
    assert set(bot_names) == expected
    # Nest POST ops that accept MONITOR via assertBotReadToken (not GET-only).
    controller = (
        ROOT / "apps" / "api" / "src" / "trading-agents" / "trading-agents.controller.ts"
    ).read_text(encoding="utf-8")
    assert "@Post(':slug/ops/pause')" in controller
    assert "@Post(':slug/ops/refresh-flat-audit')" in controller
    service = (
        ROOT / "apps" / "api" / "src" / "trading-agents" / "trading-agents.service.ts"
    ).read_text(encoding="utf-8")
    assert "assertBotReadToken" in service
    assert "DROPPED MONITOR_READ_TOKEN" in common
    assert "NOT GET-only" in common
    assert "ADDED LAPTOP_MONITOR_READ_TOKEN" in common
    # Bot process only — AdminTokenOnly chain must NOT export the laptop monitor token.
    admin_names = _ps_allowlist_names(common, "LaptopChainVaultAllowlist")
    assert "LAPTOP_MONITOR_READ_TOKEN" not in admin_names
    assert "Assert-LaptopMonitorReadTokenForBotStart" in common
    assert "LaptopMonitorTokenRefusalExitCode" in common
    assert "REFUSED_LAPTOP_MONITOR_READ_TOKEN" in common


def test_bot_auto_restart_does_not_relaunch_on_exit_79():
    """Static source contract (HM HOLD 716511e8582d). Behavioural proof is separate."""
    source = _source("bot-auto-restart.ps1")
    assert "BitfinexEnvRefusalExitCode" in source
    assert "bitfinex_env_refused" in source
    assert "no_restart" in source
    # Refusal check must precede the crash/relaunch path.
    refuse_idx = source.find("BitfinexEnvRefusalExitCode")
    crash_idx = source.find("$consecutiveCrashes++")
    assert 0 <= refuse_idx < crash_idx
    # RK 12:27: exit 78 (laptop monitor token refuse) also stands down.
    assert "LaptopMonitorTokenRefusalExitCode" in source
    assert "laptop_monitor_token_refused" in source
    assert source.find("LaptopMonitorTokenRefusalExitCode") < crash_idx


def test_fees_system_health_node_do_not_export_vault_bitfinex_into_process():
    """Evidence for HM: diagnostic readers ≠ bot process-env loaders."""
    fees = (ROOT / "scripts" / "self_aware" / "fees.py").read_text(encoding="utf-8")
    health = (ROOT / "scripts" / "system_health.py").read_text(encoding="utf-8")
    load_vault_mjs = (ROOT / "scripts" / "load-vault-env.mjs").read_text(encoding="utf-8")
    assert "load_credentials" in fees
    assert "return None" in fees
    assert "def fetch_account" not in fees
    assert "def _post_summary" not in fees
    assert "import hmac" not in fees
    assert not re.search(r"os\.environ\[[^\]]*BITFINEX", fees)
    assert not re.search(r"os\.environ\.update\(", fees)
    assert "return env[k], env[s]" not in fees
    assert "return vals[k], vals[s]" not in fees
    assert "def load_vault" in health
    assert not re.search(r"os\.environ\[[^\]]*BITFINEX", health)
    assert 'vault.get("BOT_ADMIN_TOKEN")' in health or "vault.get('BOT_ADMIN_TOKEN')" in health
    assert "loadCanonicalBotAdminToken" in load_vault_mjs
    assert "BOT_ADMIN_TOKEN=" in load_vault_mjs
    assert "BITFINEX_API_KEY" not in load_vault_mjs
    assert "BITFINEX_API_SECRET" not in load_vault_mjs


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
    # RK 20:20 / R22: Fly-only steps 1–3 removed from supervisor (segment pull gone).
    assert "research-segment-pull-loop.ps1" not in supervisor
    assert "Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentPull')" not in supervisor
    assert "laptop-status-snapshots.ps1" not in supervisor
    assert "v2c-auto-ff.ps1" not in supervisor
    assert "r22-fly-steps" in supervisor
    assert "laptop-ack-watcher" not in supervisor
    assert "run-segment-analyzer-cycle.ps1" in supervisor
    assert "Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle')" in supervisor
    cycle = _source("run-segment-analyzer-cycle.ps1")
    assert "Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle')" in cycle
    # Pull-loop script remains on disk for manual/admin use; not started by supervisor.
    assert "Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentPull')" in loop
    assert "[int]$PullIntervalSec = 120" in loop and "[int]$ParityIntervalMin = 60" in loop
    assert "'-Source', 'Http'" in loop and "-MaxSegments" in loop
    assert "laptop-ack-watcher" not in loop
    # Only the admin token is read from the vault and it is never echoed.
    assert "BOT_ADMIN_TOKEN" in pull and "Write-Host $env:BOT_ADMIN_TOKEN" not in pull
    assert "'Process')" in pull


def test_a_stopped_cycle_brings_a_dead_dashboard_back():
    cycle = _source("run-segment-analyzer-cycle.ps1")
    stop = cycle.split("function Stop-Cycle", 1)[1].split("\n}\n", 1)[0]
    assert "/api/health" in stop and "-EnsureDashboardOnly" in stop and "exit $Code" in stop
    body = cycle.split("$cycleLock = Enter-SingleInstance", 1)[1]
    assert "exit 3" not in body and "exit 4" not in body
    assert body.count("Stop-Cycle 3") == 4 and "Stop-Cycle 4" in body


def test_every_cycle_stop_carries_a_stop_reason_in_the_status_file():
    cycle = _source("run-segment-analyzer-cycle.ps1")
    stop = cycle.split("function Stop-Cycle", 1)[1].split("\n}\n", 1)[0]
    assert "$script:cycleStatus.stopReason = $StopReason" in stop
    assert "stopReason = $null; detail = $null" in cycle
    body = cycle.split("$cycleLock = Enter-SingleInstance", 1)[1]
    calls = re.findall(r"Stop-Cycle \d+[^\n]*", body)
    assert calls and all(re.match(r"Stop-Cycle \d+ ['(]", call) for call in calls), calls
    tail = body.split("$analyzerExit = $LASTEXITCODE", 1)[1]
    assert "ANALYZER_EXIT_" in tail and "$cfg.AnalyzerStatus" in tail


def test_lock_wait_outlasts_the_parity_lock_budget():
    cycle = _source("run-segment-analyzer-cycle.ps1")
    parity = _source("research_segment_fly_parity.py")
    budget = float(re.search(r"DEFAULT_MAX_LOCK_SEC = ([0-9.]+)", parity).group(1))
    wait = int(re.search(r"\[int\]\$LockWaitMaxSec = (\d+)", cycle).group(1))
    assert wait >= budget + 120


def test_promotion_waits_out_a_draining_fly_backlog_only():
    cycle = _source("run-segment-analyzer-cycle.ps1")
    loop = cycle.split("for ($attempt = 1;", 1)[1].split("\n}\n", 1)[0]
    backlog = loop.split("if ($onlyBacklog) {", 1)[1].split("\n  }\n", 1)[0]
    assert "$FlyBacklogWaitMaxSec" in backlog and "$attempt--" in backlog
    assert "'PROMOTION_WAIT_FLY_BACKLOG'" in backlog and "'FLY_BACKLOG_NOT_DRAINED'" in backlog
    only = next(line for line in loop.splitlines() if line.strip().startswith("$onlyBacklog ="))
    assert "-contains 'FLY_UNSHIPPED_BYTES'" in only and "-notin" in only


def test_disclosed_promotion_warnings_reach_cycle_status_without_blocking():
    cycle = _source("run-segment-analyzer-cycle.ps1")
    assert "promotionLevel = $null; promotionWarnings = @()" in cycle
    after = cycle.split("Stop-Cycle 3 'PROMOTION_HEAD_KEPT_MOVING'", 1)[1].split("Set-CycleStatus 'MIGRATION'", 1)[0]
    assert ".segment-promotion.heartbeat.json" in after and "promotionWarnings" in after
    assert "PROMOTION_DEGRADED" in after and "Stop-Cycle" not in after


def test_successful_generation_is_published_to_the_fly_mirror_without_gating_the_cycle():
    cycle = _source("run-segment-analyzer-cycle.ps1")
    publish = cycle.split("Set-CycleStatus 'MIRROR_PUBLISH'", 1)[1].split("Set-CycleStatus 'RETENTION'", 1)[0]
    assert "if ($analyzerExit -eq 0) {\n  Set-CycleStatus 'MIRROR_PUBLISH'" in cycle
    assert "publish_analyzer_mirror.py" in publish and "analyzer-mirror-publish.status.json" in publish
    assert "Wait-Job -Job $publishJob -Timeout $MirrorPublishTimeoutSec" in publish
    assert "$analyzerExit =" not in publish and "Stop-Cycle" not in publish


def test_promotion_waits_out_a_parity_pass_holding_the_shadow_lock():
    cycle = _source("run-segment-analyzer-cycle.ps1")
    assert "[int]$LockWaitMaxSec = 900" in cycle
    loop = cycle.split("for ($attempt = 1;", 1)[1].split("\n}\n", 1)[0]
    lock = loop.split("if ($promotion -match 'holds the shadow-root lock') {", 1)[1].split("\n  }\n", 1)[0]
    # Lock waits are bounded by their own budget and do not use up head attempts.
    assert "$LockWaitMaxSec" in lock and "Start-Sleep" in lock and "$attempt--" in lock
    assert "Stop-Cycle 3" in lock
    final = next(line for line in loop.splitlines() if "-notmatch" in line and "Stop-Cycle 3" in line)
    assert "SHADOW_BEHIND_PUBLISHED" in final and "shadow-root lock" not in final


def test_supervisor_task_uses_system_powershell():
    register = _source("register-laptop-chain-task.ps1")
    assert "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in register
    assert "-AtLogOn" in register and "New-TimeSpan -Minutes 10" in register
    assert ".cache" not in register


def test_interim_health_tick_defers_only_on_fresh_supervisor_and_verdict_timestamps():
    tick = _source("system-health-tick.ps1")
    assert "Get-ScheduledTaskInfo" in tick and "LastRunTime" in tick
    assert "generated_ts" in tick and "$FreshMinutes" in tick
    defer = next(line for line in tick.splitlines() if line.strip().startswith("if ($carriesWatcher"))
    assert "$runAge -lt $FreshMinutes" in defer and "$verdictAge -lt $FreshMinutes" in defer
    assert "TAKEOVER" in tick


@windows_only
def test_interim_health_tick_defers_while_the_supervisor_watcher_is_fresh(tmp_path):
    probe = _ps("(Get-ScheduledTask -TaskName DoxxedLaptopChainSupervisor -ErrorAction SilentlyContinue | "
                "Get-ScheduledTaskInfo).LastRunTime.ToUniversalTime().ToString('o')")
    try:
        last_run = datetime.fromisoformat(probe.stdout.strip()[:26])
    except ValueError:
        pytest.skip("DoxxedLaptopChainSupervisor task not registered on this host")
    if (datetime.utcnow() - last_run).total_seconds() > 10 * 60:
        pytest.skip("supervisor task has not run recently on this host")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "laptop-chain-supervisor.ps1").write_text("# runs system_health.py\n", encoding="utf-8")
    state = tmp_path / "state"
    (state / "health").mkdir(parents=True)
    (state / "health" / "system-health-latest.json").write_text(json.dumps({"generated_ts": time.time()}))
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPTS / "system-health-tick.ps1"),
         "-StateDir", str(state), "-SupervisorRepo", str(repo), "-Interim"],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0
    log = next((state / "logs").glob("system-health-*.log")).read_text(encoding="utf-8-sig")
    assert "DEFERRED supervisor ran" in log and "TAKEOVER" not in log


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


def _vault_fixture(tmp_path, **extra) -> Path:
    fixture = tmp_path / "home-bot.env"
    lines = [
        "BOT_ADMIN_TOKEN=fixture-admin-token-not-real",
        "BITFINEX_API_KEY=fixture-should-not-export",
        "BITFINEX_API_SECRET=fixture-should-not-export-either",
        "OTHER_SECRET=fixture-also-not",
    ]
    for key, value in extra.items():
        lines.append(f"{key}={value}")
    lines.append("")
    fixture.write_text("\n".join(lines), encoding="utf-8")
    return fixture


def _clean_chain_env(chain) -> dict:
    clean_env = {
        key: value
        for key, value in (chain["env"] or {}).items()
        if not str(key).upper().startswith("BITFINEX_")
    }
    clean_env["BOT_ADMIN_TOKEN"] = ""
    return clean_env


@windows_only
def test_import_vault_allowlist_never_exports_bitfinex_into_process_env(chain, tmp_path):
    """Process-env proof (AdminTokenOnly): BITFINEX_* names are not exported.

    Emits only name presence/counts — never secret values (R10).
    """
    fixture = _vault_fixture(tmp_path)
    script = (
        _common_prelude(chain)
        + f"Remove-Item Env:BOT_ADMIN_TOKEN -ErrorAction SilentlyContinue; "
        f"Remove-Item Env:BITFINEX_API_KEY -ErrorAction SilentlyContinue; "
        f"Remove-Item Env:BITFINEX_API_SECRET -ErrorAction SilentlyContinue; "
        f"Remove-Item Env:OTHER_SECRET -ErrorAction SilentlyContinue; "
        f"Import-LaptopChainVaultAllowlist -VaultEnv '{fixture}'; "
        f"$count = Get-BitfinexProcessEnvNameCount; "
        f"$admin = [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('BOT_ADMIN_TOKEN','Process')) -eq $false; "
        f"$other = [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('OTHER_SECRET','Process')) -eq $false; "
        f"Write-Output ('adminPresent=' + $admin + ';bitfinexNameCount=' + $count + ';otherPresent=' + $other)"
    )
    result = _ps(script, _clean_chain_env(chain))
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]
    assert line == "adminPresent=True;bitfinexNameCount=0;otherPresent=False", line
    assert "fixture-should-not-export" not in result.stdout
    assert "fixture-admin-token-not-real" not in result.stdout


@windows_only
@pytest.mark.parametrize(
    "script_name",
    ("start-home-bot.ps1", "bot-auto-restart.ps1", "start-local-collection-bot.ps1"),
)
def test_bot_starter_bot_allowlist_never_exports_vault_bitfinex_keeps_inherited(
    chain, tmp_path, script_name
):
    """RK 09:33: vault allowlist never exports BITFINEX_*; inherited KEY remains for in-process refuse."""
    source = _source(script_name)
    assert "Import-LaptopChainVaultAllowlist" in source and "BotAllowlist" in source
    assert "Clear-BitfinexProcessEnvNames" not in source
    fixture = _vault_fixture(tmp_path)
    script = (
        _common_prelude(chain)
        + f"Remove-Item Env:BOT_ADMIN_TOKEN -ErrorAction SilentlyContinue; "
        f"Remove-Item Env:BITFINEX_API_KEY -ErrorAction SilentlyContinue; "
        f"Remove-Item Env:BITFINEX_API_SECRET -ErrorAction SilentlyContinue; "
        f"Remove-Item Env:OTHER_SECRET -ErrorAction SilentlyContinue; "
        f"[Environment]::SetEnvironmentVariable('BITFINEX_API_KEY','preexisting','Process'); "
        f"Import-LaptopChainVaultAllowlist -VaultEnv '{fixture}' -Mode BotAllowlist; "
        f"$count = Get-BitfinexProcessEnvNameCount; "
        f"$admin = -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('BOT_ADMIN_TOKEN','Process')); "
        f"$other = -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('OTHER_SECRET','Process')); "
        f"$keyStill = -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('BITFINEX_API_KEY','Process')); "
        f"Write-Output ('adminPresent=' + $admin + ';bitfinexNameCount=' + $count + ';otherPresent=' + $other + ';keyStill=' + $keyStill)"
    )
    result = _ps(script, _clean_chain_env(chain))
    assert result.returncode == 0, f"{script_name}: {result.stderr}"
    line = result.stdout.strip().splitlines()[-1]
    # Inherited KEY remains (count=1); vault BITFINEX values never exported; OTHER not allowlisted.
    assert line == "adminPresent=True;bitfinexNameCount=1;otherPresent=False;keyStill=True", (
        script_name,
        line,
    )
    assert "fixture-should-not-export" not in result.stdout
    assert "preexisting" not in result.stdout


@windows_only
def test_assert_laptop_monitor_token_refuses_empty(chain, tmp_path):
    """RK 12:27 (a): empty LAPTOP_MONITOR_READ_TOKEN → exit 78."""
    fixture = _vault_fixture(tmp_path)  # no LAPTOP_MONITOR_READ_TOKEN line
    script = (
        _common_prelude(chain)
        + "Remove-Item Env:LAPTOP_MONITOR_READ_TOKEN -ErrorAction SilentlyContinue; "
        + "Remove-Item Env:MONITOR_READ_TOKEN -ErrorAction SilentlyContinue; "
        + f"Import-LaptopChainVaultAllowlist -VaultEnv '{fixture}' -Mode BotAllowlist; "
        + f"Assert-LaptopMonitorReadTokenForBotStart -VaultEnv '{fixture}'"
    )
    result = _ps(script, _clean_chain_env(chain))
    assert result.returncode == 78, (result.returncode, result.stdout, result.stderr)
    assert "REFUSED_LAPTOP_MONITOR_READ_TOKEN empty_or_short" in result.stdout
    assert "fixture-admin-token-not-real" not in result.stdout


@windows_only
def test_assert_laptop_monitor_token_refuses_equal_monitor(chain, tmp_path):
    """RK 12:27 (a): LAPTOP_MONITOR_READ_TOKEN == MONITOR_READ_TOKEN → exit 78."""
    shared = "shared-monitor-and-laptop-token-VALUE"
    fixture = _vault_fixture(
        tmp_path,
        LAPTOP_MONITOR_READ_TOKEN=shared,
        MONITOR_READ_TOKEN=shared,
    )
    script = (
        _common_prelude(chain)
        + "Remove-Item Env:LAPTOP_MONITOR_READ_TOKEN -ErrorAction SilentlyContinue; "
        + "Remove-Item Env:MONITOR_READ_TOKEN -ErrorAction SilentlyContinue; "
        + f"Import-LaptopChainVaultAllowlist -VaultEnv '{fixture}' -Mode BotAllowlist; "
        + f"Assert-LaptopMonitorReadTokenForBotStart -VaultEnv '{fixture}'"
    )
    result = _ps(script, _clean_chain_env(chain))
    assert result.returncode == 78, (result.returncode, result.stdout, result.stderr)
    assert "REFUSED_LAPTOP_MONITOR_READ_TOKEN equals_MONITOR_READ_TOKEN" in result.stdout
    assert shared not in result.stdout


@windows_only
def test_assert_laptop_monitor_token_accepts_distinct(chain, tmp_path):
    """RK 12:27 (a): distinct non-empty laptop token allows start assert to pass."""
    fixture = _vault_fixture(
        tmp_path,
        LAPTOP_MONITOR_READ_TOKEN="laptop-only-token-for-tests-ABCDEF12",
        MONITOR_READ_TOKEN="monitor-read-token-for-tests-0123456789",
    )
    script = (
        _common_prelude(chain)
        + "Remove-Item Env:LAPTOP_MONITOR_READ_TOKEN -ErrorAction SilentlyContinue; "
        + "Remove-Item Env:MONITOR_READ_TOKEN -ErrorAction SilentlyContinue; "
        + f"Import-LaptopChainVaultAllowlist -VaultEnv '{fixture}' -Mode BotAllowlist; "
        + f"Assert-LaptopMonitorReadTokenForBotStart -VaultEnv '{fixture}'; "
        + "Write-Output 'assert_ok'"
    )
    result = _ps(script, _clean_chain_env(chain))
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "assert_ok" in result.stdout
    assert "laptop-only-token" not in result.stdout
    assert "monitor-read-token" not in result.stdout


@windows_only
def test_assert_bitfinex_process_env_absent_refuses_nonzero(chain):
    """CI refusal proof: Assert exits 79 when a BITFINEX_* KEY name is present."""
    script = (
        _common_prelude(chain)
        + "[Environment]::SetEnvironmentVariable('BITFINEX_API_KEY','preexisting','Process'); "
        "Assert-BitfinexProcessEnvAbsent"
    )
    result = _ps(script, _clean_chain_env(chain))
    assert result.returncode == 79, result.stdout + result.stderr
    assert "BITFINEX_ENV_NAME_COUNT count=1" in result.stdout
    assert "REFUSED_BITFINEX_ENV_NAMES" in result.stdout
    assert "preexisting" not in result.stdout


@windows_only
def test_assert_allows_live_enabled_exact_zero_and_refuses_other(chain):
    """RK 08:26 (A): LIVE_ENABLED='0' exempt; any other value refuses."""
    ok_script = (
        _common_prelude(chain)
        + "[Environment]::SetEnvironmentVariable('BITFINEX_LIVE_ENABLED','0','Process'); "
        "Assert-BitfinexProcessEnvAbsent; "
        "Write-Output 'ok'"
    )
    ok = _ps(ok_script, _clean_chain_env(chain))
    assert ok.returncode == 0, ok.stderr
    assert "BITFINEX_ENV_NAME_COUNT count=0" in ok.stdout
    assert "ok" in ok.stdout

    bad_script = (
        _common_prelude(chain)
        + "[Environment]::SetEnvironmentVariable('BITFINEX_LIVE_ENABLED','1','Process'); "
        "Assert-BitfinexProcessEnvAbsent"
    )
    bad = _ps(bad_script, _clean_chain_env(chain))
    assert bad.returncode == 79, bad.stdout + bad.stderr
    assert "REFUSED_BITFINEX_ENV_NAMES" in bad.stdout


@windows_only
def test_real_starter_env_setup_with_inherited_key_leaves_refusal_for_process(chain, tmp_path):
    """RK 09:33: real starter setup (Import + LIVE=0, no Clear) keeps inherited KEY → Assert 79."""
    fixture = _vault_fixture(tmp_path)
    # Mirror start-home-bot env setup exactly (vault allowlist + LIVE=0, no Clear).
    script = (
        _common_prelude(chain)
        + f"[Environment]::SetEnvironmentVariable('BITFINEX_API_KEY','fake-inherited-key','Process'); "
        f"Import-LaptopChainVaultAllowlist -VaultEnv '{fixture}' -Mode BotAllowlist; "
        f"$env:FORCE_PAPER_MODE = '1'; $env:LIVE_TRADING_ENABLED = '0'; $env:LIVE_ARMED = '0'; "
        f"$env:BITFINEX_LIVE_ENABLED = '0'; "
        f"$count = Get-BitfinexProcessEnvNameCount; "
        f"Write-Output ('count=' + $count); "
        f"Assert-BitfinexProcessEnvAbsent"
    )
    result = _ps(script, _clean_chain_env(chain))
    assert result.returncode == 79, result.stdout + result.stderr
    assert "count=1" in result.stdout
    assert "REFUSED_BITFINEX_ENV_NAMES" in result.stdout
    assert "fake-inherited-key" not in result.stdout


@windows_only
def test_supervisor_bitfinex_env_name_count_helper(chain):
    """Supervisor fail-closed helper: count-only; clear then Assert passes."""
    script = (
        _common_prelude(chain)
        + "[Environment]::SetEnvironmentVariable('BITFINEX_API_KEY','x','Process'); "
        "[Environment]::SetEnvironmentVariable('BITFINEX_API_SECRET','y','Process'); "
        "$before = Get-BitfinexProcessEnvNameCount; "
        "[void](Clear-BitfinexProcessEnvNames); "
        "$after = Get-BitfinexProcessEnvNameCount; "
        "Assert-BitfinexProcessEnvAbsent; "
        "Write-Output ('before=' + $before + ';after=' + $after)"
    )
    result = _ps(script, _clean_chain_env(chain))
    assert result.returncode == 0, result.stderr
    assert "before=2;after=0" in result.stdout
    assert "Assert-BitfinexProcessEnvAbsent" in _source("laptop-chain-supervisor.ps1")


@windows_only
@pytest.mark.parametrize(
    "script_name,extra_args",
    [
        ("run-analyzer-once.ps1", []),
        ("run-segment-analyzer-cycle.ps1", None),  # filled at runtime with temp roots
        ("laptop-chain-supervisor.ps1", []),
    ],
)
def test_real_pwsh_file_launch_refuses_injected_key(chain, tmp_path, script_name, extra_args):
    """HM HOLD 716511e8582d (1): real ``pwsh -File`` entry (not only common.ps1) → exit 79."""
    env = _clean_chain_env(chain)
    env["BITFINEX_API_KEY"] = "fake-file-launch-key-not-real"
    env["BITFINEX_LIVE_ENABLED"] = "0"
    env["BOT_ADMIN_TOKEN"] = "fixture-admin-token-for-cycle-assert"
    # Unique mutex so a live host supervisor cannot steal the tick (exit 0).
    env["DOXXED_LAPTOP_CHAIN_MUTEX_PREFIX"] = f"DoxxedFile79{uuid.uuid4().hex[:10]}"

    args = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPTS / script_name),
        "-RepoRoot",
        str(ROOT),
        "-CanonicalRoot",
        str(chain["canonical"]),
        "-StateDir",
        str(chain["state"]),
    ]
    if script_name == "run-segment-analyzer-cycle.ps1":
        shadow = tmp_path / "shadow"
        archive = tmp_path / "archive"
        view = tmp_path / "view"
        for p in (shadow, archive, view):
            p.mkdir(parents=True, exist_ok=True)
        args.extend(
            [
                "-ShadowRoot",
                str(shadow),
                "-ArchiveRoot",
                str(archive),
                "-ViewRoot",
                str(view),
            ]
        )
    elif extra_args:
        args.extend(extra_args)

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=120,
        env={**dict(os.environ), **env},
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 79, combined
    assert "BITFINEX_ENV_NAME_COUNT count=1" in combined
    assert "REFUSED_BITFINEX_ENV_NAMES" in combined
    assert "fake-file-launch-key-not-real" not in combined


_FILE_LAUNCH_LOG_NAMES = {
    "run-analyzer-once.ps1": "analyzer-run",
    "run-segment-analyzer-cycle.ps1": "segment-analyzer-cycle",
    "laptop-chain-supervisor.ps1": "laptop-chain-supervisor",
}


def _pwsh_file_launch_argv(script_name: str, chain, tmp_path, extra_args=None) -> list[str]:
    """Shared ``pwsh -File`` argv for the three supervisor-child entry scripts."""
    args = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPTS / script_name),
        "-RepoRoot",
        str(ROOT),
        "-CanonicalRoot",
        str(chain["canonical"]),
        "-StateDir",
        str(chain["state"]),
    ]
    if script_name == "run-segment-analyzer-cycle.ps1":
        shadow = tmp_path / "shadow"
        archive = tmp_path / "archive"
        view = tmp_path / "view"
        for p in (shadow, archive, view):
            p.mkdir(parents=True, exist_ok=True)
        args.extend(
            [
                "-ShadowRoot",
                str(shadow),
                "-ArchiveRoot",
                str(archive),
                "-ViewRoot",
                str(view),
            ]
        )
    elif extra_args:
        args.extend(extra_args)
    return args


@windows_only
@pytest.mark.parametrize(
    "script_name,extra_args",
    [
        ("run-analyzer-once.ps1", []),
        ("run-segment-analyzer-cycle.ps1", None),
        ("laptop-chain-supervisor.ps1", []),
    ],
)
def test_real_pwsh_file_launch_clean_env_does_not_refuse(chain, tmp_path, script_name, extra_args):
    """RK follow-up: clean-env ``pwsh -File`` must pass Assert (count=0), never exit 79.

    Complements ``test_real_pwsh_file_launch_refuses_injected_key`` (fake KEY → 79).
    Kill after the gate proof so the analyzer/supervisor body cannot hang CI.
    """
    env = _clean_chain_env(chain)
    env["BITFINEX_LIVE_ENABLED"] = "0"
    env["BOT_ADMIN_TOKEN"] = "fixture-admin-token-for-cycle-assert"
    env["DOXXED_LAPTOP_CHAIN_MUTEX_PREFIX"] = f"DoxxedFileClean{uuid.uuid4().hex[:10]}"
    for key in list(env):
        if str(key).upper().startswith("BITFINEX_") and str(key).upper() != "BITFINEX_LIVE_ENABLED":
            env.pop(key, None)

    args = _pwsh_file_launch_argv(script_name, chain, tmp_path, extra_args)
    merged = {**dict(os.environ), **env}
    for key in list(merged):
        if str(key).upper().startswith("BITFINEX_") and str(key).upper() != "BITFINEX_LIVE_ENABLED":
            merged.pop(key, None)
    merged["BITFINEX_LIVE_ENABLED"] = "0"

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=merged,
    )
    log_name = _FILE_LAUNCH_LOG_NAMES[script_name]
    log_path = chain["state"] / "logs" / f"{log_name}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
    saw_count0 = False
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if log_path.exists():
                text = log_path.read_text(encoding="utf-8", errors="replace")
                if "BITFINEX_ENV_NAME_COUNT count=0" in text:
                    saw_count0 = True
                    break
                if "REFUSED_BITFINEX_ENV_NAMES" in text:
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    finally:
        if proc.poll() is None:
            proc.kill()
        stdout, stderr = proc.communicate(timeout=30)

    combined = stdout + stderr
    if log_path.exists():
        combined += log_path.read_text(encoding="utf-8", errors="replace")
    assert saw_count0 or "BITFINEX_ENV_NAME_COUNT count=0" in combined, combined
    assert proc.returncode != 79, combined
    assert "REFUSED_BITFINEX_ENV_NAMES" not in combined
    assert "BITFINEX_ENV_NAME_COUNT count=0" in combined


def _bot_auto_restart_harness(tmp_path: Path) -> Path:
    """Temp checkout copy without fly-canonical.lock so disaster-recovery opt-in can run.

    Harness-only patch: after WaitForExit, honor ``DCF_TEST_FORCE_EXIT_CODE`` so the
    real stand-down / relaunch decision runs with a known code. CI showed
    ``Get-Process`` ``ExitCode`` can be empty for a watched child (log ``code=``),
    which skips the exit-79 branch and falsely relaunches — that is a harness
    observability gap, not a production soft-exclude.
    """
    harness = tmp_path / "restart-harness"
    scripts = harness / "scripts"
    scripts.mkdir(parents=True)
    agent = harness / "services" / "btc-conservative-agent"
    agent.mkdir(parents=True)
    (harness / "logs").mkdir()
    for name in ("home-stack-common.ps1", "laptop-chain-common.ps1"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    src = (SCRIPTS / "bot-auto-restart.ps1").read_text(encoding="utf-8-sig")
    needle = "$code = $p.ExitCode"
    assert src.count(needle) == 1, "expected one ExitCode read for harness patch"
    patched = src.replace(
        needle,
        (
            "if (-not [string]::IsNullOrEmpty($env:DCF_TEST_FORCE_EXIT_CODE)) { "
            "$code = [int]$env:DCF_TEST_FORCE_EXIT_CODE } else { $code = $p.ExitCode }"
        ),
        1,
    )
    (scripts / "bot-auto-restart.ps1").write_text(patched, encoding="utf-8")
    (agent / "combo_pathway_config.py").write_text(
        'RESEARCH_STACK_VERSION = "test-harness-no-restart-79"\n',
        encoding="utf-8",
    )
    return harness


def _run_bot_auto_restart_watching_exit(harness: Path, exit_code: int, timeout: int = 90):
    """Run the harness monitor against a short-lived child; force the exit code under test."""
    # Stay alive long enough for the monitor to attach and WaitForExit (not the
    # gone-before-attach path that forces code=-1 and would relaunch). Python
    # child avoids pwsh-on-pwsh ExitCode quirks; forced code still drives the
    # decision branch after WaitForExit returns.
    fake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(4)"])
    env = {
        k: v
        for k, v in os.environ.items()
        if not str(k).upper().startswith("BITFINEX_")
    }
    env["BITFINEX_LIVE_ENABLED"] = "0"
    env["DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER"] = (
        "I_UNDERSTAND_THIS_STARTS_A_SECOND_AI_TRADING_OWNER"
    )
    env["DCF_TEST_FORCE_EXIT_CODE"] = str(int(exit_code))
    # Missing vault → skip Import/Assert-LaptopMonitorReadToken (token path is #984).
    missing_vault = harness / "missing-home-bot.env"
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness / "scripts" / "bot-auto-restart.ps1"),
            "-BotPid",
            str(fake.pid),
            "-Port",
            "17979",
            "-VaultEnv",
            str(missing_vault),
            "-BaseCooldownSec",
            "1",
            "-MaxCooldownSec",
            "1",
            "-MaxRestartsPerHour",
            "1",
        ],
        cwd=str(harness),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if fake.poll() is None:
        fake.kill()
        fake.wait(timeout=10)
    log_path = harness / "logs" / "bot_restarts.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    return result, log_text


@windows_only
def test_bot_auto_restart_behavioural_does_not_relaunch_on_exit_79(tmp_path):
    """Behavioural: monitor decision with code 79 must stand down (no relaunch)."""
    harness = _bot_auto_restart_harness(tmp_path)
    result, log_text = _run_bot_auto_restart_watching_exit(harness, exit_code=79, timeout=90)
    combined = result.stdout + result.stderr + log_text
    assert "bitfinex_env_refused" in combined, combined
    assert "no_restart" in combined, combined
    # Crash/relaunch path markers must be absent on the 79 stand-down.
    assert "\trestarting" not in log_text
    assert "restarted\t" not in log_text
    assert "relaunch_failed" not in log_text


@windows_only
def test_bot_auto_restart_behavioural_nonzero_non79_takes_relaunch_path(tmp_path):
    """Mutation contrast: forced exit 1 hits crashed/restarting (79 stand-down is special)."""
    harness = _bot_auto_restart_harness(tmp_path)
    result, log_text = _run_bot_auto_restart_watching_exit(harness, exit_code=1, timeout=120)
    combined = result.stdout + result.stderr + log_text
    assert "bitfinex_env_refused" not in combined, combined
    assert "crashed" in log_text and "restarting" in log_text, log_text


@windows_only
@pytest.mark.parametrize("pull, expected", [
    ({"ok": True, "applied_seq": 10, "remote_head": {"published_seq": 10, "unshipped_bytes": 1024}}, 120),
    ({"ok": True, "applied_seq": 10, "remote_head": {"published_seq": 10, "unshipped_bytes": 90_000_000}}, 15),
    ({"ok": True, "applied_seq": 10, "remote_head": {"published_seq": 12, "unshipped_bytes": 0}}, 15),
    ({"ok": False, "applied_seq": 10, "remote_head": {"published_seq": 12, "unshipped_bytes": 0}}, 120),
    (None, 120),
])
def test_pull_loop_catches_up_while_fly_drains_a_backlog(chain, pull, expected):
    payload = "$null" if pull is None else f"('{json.dumps(pull)}' | ConvertFrom-Json)"
    result = _ps(_common_prelude(chain) + f"Get-SegmentPullSleepSeconds -Pull {payload} -IntervalSec 120 "
                 "-CatchUpIntervalSec 15 -CatchUpUnshippedBytes 8388608", chain["env"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(expected)


def test_pull_loop_uses_the_catch_up_interval():
    loop = _source("research-segment-pull-loop.ps1")
    assert "Get-SegmentPullSleepSeconds" in loop and "CatchUpIntervalSec" in loop


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
@pytest.mark.parametrize("status,alerted", [("STALE", True), ("DOWN", True), ("DEGRADED", True),
                                            ("OK", False), ("DISABLED", False)])
def test_monitor_alerts_on_a_stale_cross_venue_tape(chain, status, alerted):
    chain["state"].mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-5)}))
    _pull_status(chain, pid=1, finishedAt=_iso(-1), exitCode=0, error=None)
    _heartbeat(chain, ok=True, inProgress=False, phase="complete")
    _puller_status(chain)
    (chain["segments"] / "parity-latest.json").write_text(json.dumps(
        {"verdict": "GREEN", "seq": 40, "generated_at": _iso(-5)}), encoding="utf-8")
    (chain["state"] / "fly_runtime_snapshot_v1.json").write_text(json.dumps({"cross_venue_health": {
        "status": status, "reason": "X", "collector_age_s": 1.0, "stale_venues": ["okx"]}}))
    result = _monitor_with_pull_loop_held(chain)
    assert ("CROSS_VENUE_TAPE_STALE" in _active_alert_codes(chain)) is alerted, result.stdout + result.stderr


@windows_only
@pytest.mark.parametrize("status,alerted", [("STALE", True), ("DEGRADED", True), ("OK", False),
                                            ("STARTING", False), ("DISABLED", False)])
def test_monitor_alerts_on_a_stalled_xvl_evaluator(chain, status, alerted):
    chain["state"].mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-5)}))
    _pull_status(chain, pid=1, finishedAt=_iso(-1), exitCode=0, error=None)
    _heartbeat(chain, ok=True, inProgress=False, phase="complete")
    _puller_status(chain)
    (chain["segments"] / "parity-latest.json").write_text(json.dumps(
        {"verdict": "GREEN", "seq": 40, "generated_at": _iso(-5)}), encoding="utf-8")
    (chain["state"] / "fly_runtime_snapshot_v1.json").write_text(json.dumps({"xvl_evaluator_health": {
        "status": status, "reason": "X", "tick_age_s": 30.0, "write_failures": 0}}))
    result = _monitor_with_pull_loop_held(chain)
    assert ("XVL_EVALUATOR_STALE" in _active_alert_codes(chain)) is alerted, result.stdout + result.stderr


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


def _healthy_chain_without_embedded_receipt(chain):
    chain["state"].mkdir(parents=True, exist_ok=True)
    (chain["state"] / "analyzer-run.status.json").write_text(json.dumps({"lastSuccessAt": _iso(-5)}))
    _pull_status(chain, pid=1, finishedAt=_iso(-1), exitCode=0, error=None)
    _puller_status(chain, ack_receipt=None, applied_now=0)
    (chain["segments"] / "parity-latest.json").write_text(json.dumps(
        {"verdict": "GREEN", "seq": 40, "generated_at": _iso(-5)}), encoding="utf-8")


@windows_only
def test_monitor_reads_the_receipt_log_when_an_idle_pull_omits_the_ack(chain):
    _healthy_chain_without_embedded_receipt(chain)
    log = chain["segments"] / ".puller" / "ack-receipts.jsonl"
    log.write_text(json.dumps({"ok": True, "result": "RECORDED", "through_seq": 40, "received_at": _iso(-40)}) + "\n",
                   encoding="utf-8")
    result = _monitor_with_pull_loop_held(chain)
    assert result.returncode == 0, result.stdout + result.stderr
    log.write_text(json.dumps({"ok": True, "result": "RECORDED", "through_seq": 38, "received_at": _iso(-40)}) + "\n",
                   encoding="utf-8")
    _monitor_with_pull_loop_held(chain)
    assert _active_alert_codes(chain) == {"SEGMENT_ACK_STALE"}


@windows_only
def test_monitor_flags_no_ack_data_when_no_receipt_exists_anywhere(chain):
    _healthy_chain_without_embedded_receipt(chain)
    _monitor_with_pull_loop_held(chain)
    assert _active_alert_codes(chain) == {"SEGMENT_ACK_NO_DATA"}


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
    # Fly /health and /ready go through Invoke-JsonGet (reads 503 bodies), still GET-only.
    assert source.count("Invoke-RestMethod -Method Get") == 3
    assert source.count("Invoke-JsonGet \"") == 2
    assert "-Method Post" not in source and "-Method Put" not in source
    persisted = source[source.index("$relay.ok = $true"):source.index("} catch {\n    $relay.error")]
    assert "userId" not in persisted and "adminToken" not in persisted and "lastError" not in persisted
    runtime = source[source.index("New-Snapshot 'fly_runtime_snapshot_v1'"):]
    assert "$runtime.research_lane_enabled = $toggles" in runtime
    assert not re.search(r"\$runtime(\.|\[)[^=\n]*=\s*\$adminToken", runtime)
    assert "$runtime.token" not in runtime and "$runtime.headers" not in runtime.lower()


def test_deploy_runs_snapshot_is_read_only_and_feeds_the_proof():
    source = _source("laptop-status-snapshots.ps1")
    deploys = source[source.index("New-Snapshot 'fly_deploy_runs_snapshot_v1'"):]
    assert "gh run list --repo $deployRepo --workflow fly-bot-deploy.yml" in deploys
    assert "fly_deploy_runs_snapshot_v1.json" in deploys and "$deploys.error = 'OFFLINE'" in deploys
    # Serving tip enrichment for fly.revision (read-only jobs GET only).
    assert "serving_deploy" in deploys
    assert 'gh api "repos/$deployRepo/actions/runs/' in deploys
    for verb in ("gh workflow run", "gh run rerun", "gh run cancel", "gh pr merge"):
        assert verb not in source


def test_analyzer_cadence_runs_from_cycle_start_and_retries_failed_cycles():
    supervisor = _source("laptop-chain-supervisor.ps1")
    assert "Read-JsonFile $cfg.CycleStatus" in supervisor
    assert "$cycleFailed" in supervisor and "ConvertTo-UtcDate $cycle.startedAt" in supervisor
    cycle = _source("run-segment-analyzer-cycle.ps1")
    phases = [p for p in ("'PROMOTION'", "'MIGRATION'", "'ANALYZER'", "'DONE' $analyzerExit") if p in cycle]
    assert len(phases) == 4
    assert cycle.index("Set-CycleStatus 'MIGRATION'") < cycle.index("migrate_canonical_research_store.py")
    stop = cycle.split("function Stop-Cycle", 1)[1].split("\n}\n", 1)[0]
    assert "Set-CycleStatus 'STOPPED' $Code" in stop
    assert "CycleStatus = Join-Path $StateDir 'segment-analyzer-cycle.status.json'" in _source("laptop-chain-common.ps1")


def test_pull_loop_defers_parity_during_an_active_cycle_with_a_hard_bound():
    loop = _source("research-segment-pull-loop.ps1")
    assert "[int]$ParityMaxDeferMin = 120" in loop
    defer = loop[loop.index("$cycle = Read-JsonFile $cfg.CycleStatus"):loop.index("$pullArgs = @(")]
    assert "@('PROMOTION', 'MIGRATION') -contains $cycle.phase" in defer
    assert "$null -eq $cycle.exitCode" in defer and "TotalMinutes -lt 75" in defer
    assert "-not $parityOverdue" in defer and "-ge $ParityMaxDeferMin" in defer
    assert "$parityDue = $false" in defer


def test_supervisor_runs_the_unattended_proof_after_the_monitor():
    supervisor = _source("laptop-chain-supervisor.ps1")
    assert supervisor.index("laptop-chain-monitor.ps1") < supervisor.index("unattended_proof.py")
    assert "--check" in supervisor and "--start" not in supervisor


def test_supervisor_collects_snapshots_before_the_monitor():
    # R22: fly_* status snapshots removed from supervisor; monitor still runs.
    supervisor = _source("laptop-chain-supervisor.ps1")
    assert "laptop-status-snapshots.ps1" not in supervisor
    assert "laptop-chain-monitor.ps1" in supervisor
    assert "snapshots=removed:r22-fly-steps" in supervisor


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


def _counting_launcher(marker: Path, once_sleep_sec: int = 0) -> str:
    return (
        "param([switch]$Once,[switch]$NoWait,[switch]$DashboardOnly,[int]$Port=0)\n"
        f"if ($DashboardOnly) {{ Add-Content -LiteralPath '{marker}' -Value 'dashboard'; exit 0 }}\n"
        f"Start-Sleep -Seconds {once_sleep_sec}\nexit 0\n"
    )


def test_once_pass_never_touches_the_dashboard_listener():
    launcher = _source("start-home-analyzer.ps1")
    assert "if (-not $Once -and (Test-PortOpen $AnalyzerPort)) {" in launcher
    reconcile = launcher.split("if (-not $Once -and (Test-PortOpen $AnalyzerPort)) {", 1)[1].split("\n}\n", 1)[0]
    assert "Stop-ListenPortFast" in reconcile
    assert launcher.count("Stop-ListenPortFast") == 1
    # The dashboard lock is separate from the start lock a -Once pass holds.
    assert "if ($DashboardOnly.IsPresent) { $lockStem = 'home-analyzer-dashboard' }" in launcher
    assert launcher.index('$lockFile = Join-Path $machineLockDir "$lockStem-$AnalyzerPort.lock"') < launcher.index("$lockHandle = $null")


@windows_only
def test_dashboard_ensure_runs_while_a_pass_holds_the_run_mutex(tmp_path, chain):
    marker = tmp_path / "dashboard-starts.txt"
    repo = _fake_repo(tmp_path, _counting_launcher(marker))
    status_path = chain["state"] / "analyzer-run.status.json"
    holder = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-Command",
         _common_prelude(chain) + "$h = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopAnalyzerRun'); 'held'; Start-Sleep -Seconds 60"],
        stdout=subprocess.PIPE, text=True, env={**os.environ, **chain["env"]},
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        result = _run_runner(repo, chain, "-EnsureDashboardOnly")
    finally:
        holder.kill()
        holder.wait()
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text().split() == ["dashboard"]
    # The running pass owns analyzer-run.status.json; an ensure never rewrites it.
    assert not status_path.exists()


@windows_only
def test_runner_restores_a_dashboard_that_dies_mid_pass(tmp_path, chain):
    marker = tmp_path / "dashboard-starts.txt"
    repo = _fake_repo(tmp_path, _counting_launcher(marker, once_sleep_sec=8))
    result = _run_runner(repo, chain, "-DashboardWatchSec 1")
    assert result.returncode == 6, result.stdout + result.stderr
    starts = marker.read_text().split()
    # Ensured before, restored at least once during, and ensured after the pass.
    assert len(starts) >= 3, starts
    log = "".join(p.read_text(encoding="utf-8-sig") for p in (chain["state"] / "logs").glob("analyzer-run-*.log"))
    assert "DASHBOARD_DOWN_MIDPASS" in log


@windows_only
def test_runner_refreshes_dashboard_code_once_per_checkout_revision(tmp_path, chain):
    marker = tmp_path / "dashboard-starts.txt"
    repo = _fake_repo(tmp_path, _counting_launcher(marker))
    for args in (("init", "-q"), ("add", "-A"),
                 ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")):
        _git(repo, *args)
    head = _git(repo, "rev-parse", "HEAD")
    server = subprocess.Popen([sys.executable, "-c", (
        "import http.server\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()\n"
        "        self.wfile.write(b'{\"analysis_run\": null}')\n"
        "    def log_message(self, *a): pass\n"
        "http.server.HTTPServer(('127.0.0.1', 59431), H).serve_forever()\n"
    )])
    try:
        time.sleep(1.5)
        first = _run_runner(repo, chain)
        second = _run_runner(repo, chain)
    finally:
        server.kill()
        server.wait()
    assert first.returncode == 6 and second.returncode == 6, first.stdout + first.stderr
    # The listener was up throughout, so the only start is the one code refresh.
    assert marker.read_text().split() == ["dashboard"]
    assert (chain["state"] / "analyzer-dashboard-revision.txt").read_text() == head
    log = "".join(p.read_text(encoding="utf-8-sig") for p in (chain["state"] / "logs").glob("analyzer-run-*.log"))
    assert f"DASHBOARD_CODE_REFRESH from=unknown to={head} exit=0" in log


_FAKE_STATUS_SERVER = (
    "import http.server\n"
    "class H(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()\n"
    "        self.wfile.write(b'{\"analysis_run\": null}')\n"
    "    def log_message(self, *a): pass\n"
    "http.server.HTTPServer(('127.0.0.1', 59431), H).serve_forever()\n"
)


@windows_only
def test_failed_code_refresh_restores_the_dashboard_on_current_code(tmp_path, chain):
    marker = tmp_path / "dashboard-calls.txt"
    pid_file = tmp_path / "server.pid"
    launcher = (
        "param([switch]$Once,[switch]$NoWait,[switch]$DashboardOnly,[int]$Port=0)\n"
        "if (-not $DashboardOnly) { exit 0 }\n"
        f"$calls = @(Get-Content -LiteralPath '{marker}' -ErrorAction SilentlyContinue).Count\n"
        "if ($calls -eq 0) {\n"
        f"  Add-Content -LiteralPath '{marker}' -Value 'refresh-failed'\n"
        f"  Stop-Process -Id ([int](Get-Content -LiteralPath '{pid_file}')) -Force\n"
        "  [Console]::Error.WriteLine('DASHBOARD_PORT_NOT_RELEASED'); exit 1\n"
        "}\n"
        f"Add-Content -LiteralPath '{marker}' -Value 'start'; exit 0\n"
    )
    repo = _fake_repo(tmp_path, launcher)
    for args in (("init", "-q"), ("add", "-A"),
                 ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")):
        _git(repo, *args)
    server = subprocess.Popen([sys.executable, "-c", _FAKE_STATUS_SERVER])
    pid_file.write_text(str(server.pid))
    try:
        time.sleep(1.5)
        result = _run_runner(repo, chain)
    finally:
        server.kill()
        server.wait()
    assert result.returncode == 6, result.stdout + result.stderr
    assert marker.read_text().split() == ["refresh-failed", "start"]
    # The restored dashboard was started from the current checkout.
    head = _git(repo, "rev-parse", "HEAD")
    assert (chain["state"] / "analyzer-dashboard-revision.txt").read_text() == head
    log = "".join(p.read_text(encoding="utf-8-sig") for p in (chain["state"] / "logs").glob("analyzer-run-*.log"))
    assert f"DASHBOARD_CODE_REFRESH from=unknown to={head} exit=1" in log and "DASHBOARD_START exit=0" in log
    runner = _source("run-analyzer-once.ps1")
    refresh = runner.split("function Update-AnalyzerDashboardCode {", 1)[1].split("\n}\n", 1)[0]
    assert refresh.index("if ($dash.ExitCode -eq 0) {") < refresh.index("$readyBy = ")
    launcher_src = _source("start-home-analyzer.ps1")
    owned = launcher_src.split("function Restart-OwnedAnalyzerDashboard {", 1)[1].split("\n}\n", 1)[0]
    assert owned.index("$releaseBy = ") < owned.index("throw 'DASHBOARD_PORT_NOT_RELEASED'")


def test_supervisor_restores_the_dashboard_during_a_running_cycle():
    supervisor = _source("laptop-chain-supervisor.ps1")
    probe = supervisor.index('"http://127.0.0.1:$Port/api/health"')
    busy = supervisor.index("$analyzerBusy = ")
    midcycle = supervisor.index("if ($analyzerBusy -and -not $dashboardUp) {")
    assert probe < busy < midcycle < supervisor.index("if (-not $analyzerBusy) {")
    block = supervisor[midcycle:supervisor.index("if (-not $analyzerBusy) {")]
    assert "run-analyzer-once.ps1" in block and "'-EnsureDashboardOnly'" in block
    assert "run-segment-analyzer-cycle.ps1" not in block
    runner = _source("run-analyzer-once.ps1")
    ensure = runner.split("if ($EnsureDashboardOnly) {", 1)[1].split("\n}\n", 1)[0]
    assert "LaptopAnalyzerRun" not in ensure and "AnalyzerStatus" not in ensure
    assert runner.index("if ($EnsureDashboardOnly) {") < runner.index("Get-ChainMutexName 'LaptopAnalyzerRun'")
    assert "Get-ChainMutexName 'LaptopAnalyzerDashboard'" in runner


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def _ff_fixture(tmp_path, chain):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "master")
    _git(origin, "config", "user.email", "t@example.invalid")
    _git(origin, "config", "user.name", "t")
    (origin / "a.txt").write_text("one\n")
    _git(origin, "add", "a.txt")
    _git(origin, "commit", "-q", "-m", "one")
    first = _git(origin, "rev-parse", "HEAD")
    (origin / "a.txt").write_text("two\n")
    _git(origin, "commit", "-q", "-am", "two")
    second = _git(origin, "rev-parse", "HEAD")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _git(clone, "reset", "-q", "--hard", first)
    chain["state"].mkdir(parents=True, exist_ok=True)
    return clone, first, second


def _ff_snapshots(chain, fly_rev, run_sha, status="completed", conclusion="success"):
    now = datetime.now(timezone.utc).isoformat()
    (chain["state"] / "fly_runtime_snapshot_v1.json").write_text(json.dumps(
        {"schema": "fly_runtime_snapshot_v1", "observedAt": now, "ok": True, "git_rev": fly_rev}), encoding="utf-8")
    (chain["state"] / "fly_deploy_runs_snapshot_v1.json").write_text(json.dumps(
        {"schema": "fly_deploy_runs_snapshot_v1", "observedAt": now, "ok": True,
         "runs": [{"databaseId": 7, "status": status, "conclusion": conclusion, "headSha": run_sha,
                   "createdAt": now, "updatedAt": now}]}), encoding="utf-8")


def _run_auto_ff(clone, chain):
    return _ps(f"& '{SCRIPTS / 'v2c-auto-ff.ps1'}' -RepoRoot '{clone}' -CanonicalRoot '{chain['canonical']}' "
               f"-StateDir '{chain['state']}'; exit $LASTEXITCODE", chain["env"])


@windows_only
def test_auto_ff_follows_only_a_successful_deploy_and_logs_a_receipt(tmp_path, chain):
    clone, first, second = _ff_fixture(tmp_path, chain)
    _ff_snapshots(chain, second[:12], second, status="in_progress", conclusion="")
    assert _run_auto_ff(clone, chain).returncode == 2
    assert _git(clone, "rev-parse", "HEAD") == first
    _ff_snapshots(chain, second[:12], second, conclusion="failure")
    assert _run_auto_ff(clone, chain).returncode == 2
    _ff_snapshots(chain, first[:12], second)
    assert _run_auto_ff(clone, chain).returncode == 2, "deploy sha must be the revision Fly reports"
    _ff_snapshots(chain, second[:12], second)
    result = _run_auto_ff(clone, chain)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(clone, "rev-parse", "HEAD") == second
    receipt = json.loads((chain["state"] / "v2c-auto-ff.receipts.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert receipt["outcome"] == "FAST_FORWARDED" and receipt["to"] == second and receipt["from"] == first
    assert _run_auto_ff(clone, chain).returncode == 0


@windows_only
def test_auto_ff_follows_the_running_revision_when_newer_runs_were_cancelled(tmp_path, chain):
    # 11 Oct 2026: deploy concurrency cancelled superseded runs and a newer run
    # was in progress, so runs[0] was never `success` and the analyzer stayed
    # on the previous revision for hours.
    clone, first, second = _ff_fixture(tmp_path, chain)
    _ff_snapshots(chain, second[:12], second)
    snap = chain["state"] / "fly_deploy_runs_snapshot_v1.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    data["runs"] = [{"databaseId": 9, "status": "in_progress", "conclusion": "", "headSha": "f" * 40,
                     "createdAt": later, "updatedAt": later},
                    {"databaseId": 8, "status": "completed", "conclusion": "cancelled", "headSha": "e" * 40,
                     "createdAt": later, "updatedAt": later}] + data["runs"]
    snap.write_text(json.dumps(data), encoding="utf-8")
    result = _run_auto_ff(clone, chain)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(clone, "rev-parse", "HEAD") == second


def _origin_commit(tmp_path, name, content, message):
    origin = tmp_path / "origin"
    (origin / name).parent.mkdir(parents=True, exist_ok=True)
    (origin / name).write_text(content)
    _git(origin, "add", name)
    _git(origin, "commit", "-q", "-m", message)
    return _git(origin, "rev-parse", "HEAD")


@windows_only
def test_auto_ff_follows_laptop_only_skip_ci_commits_on_top_of_the_deploy(tmp_path, chain):
    clone, first, second = _ff_fixture(tmp_path, chain)
    _ff_snapshots(chain, second[:12], second)
    env = {**chain["env"], "DOXXED_V2C_LAPTOP_FOLLOW_FETCH_SEC": "0"}
    run = lambda: _ps(f"& '{SCRIPTS / 'v2c-auto-ff.ps1'}' -RepoRoot '{clone}' -CanonicalRoot "
                      f"'{chain['canonical']}' -StateDir '{chain['state']}'; exit $LASTEXITCODE", env)
    assert run().returncode == 0
    assert _git(clone, "rev-parse", "HEAD") == second
    laptop = _origin_commit(tmp_path, "scripts/x.py", "x\n", "[skip ci] fix(analyzer): laptop only")
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(clone, "rev-parse", "HEAD") == laptop
    receipt = json.loads((chain["state"] / "v2c-auto-ff.receipts.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert receipt["outcome"] == "FAST_FORWARDED_LAPTOP_ONLY" and receipt["to"] == laptop


@windows_only
def test_auto_ff_never_follows_a_deployable_or_fly_runtime_commit_without_a_deploy(tmp_path, chain):
    clone, first, second = _ff_fixture(tmp_path, chain)
    _ff_snapshots(chain, second[:12], second)
    env = {**chain["env"], "DOXXED_V2C_LAPTOP_FOLLOW_FETCH_SEC": "0"}
    run = lambda: _ps(f"& '{SCRIPTS / 'v2c-auto-ff.ps1'}' -RepoRoot '{clone}' -CanonicalRoot "
                      f"'{chain['canonical']}' -StateDir '{chain['state']}'; exit $LASTEXITCODE", env)
    assert run().returncode == 0
    _origin_commit(tmp_path, "services/btc-conservative-agent/bot.py", "x\n", "[skip ci] sneaky runtime change")
    assert run().returncode == 0
    assert _git(clone, "rev-parse", "HEAD") == second
    receipt = json.loads((chain["state"] / "v2c-auto-ff.receipts.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert receipt["outcome"] == "SKIPPED_LAPTOP_FOLLOW" and "bot.py" in receipt["denied"]
    lines = len((chain["state"] / "v2c-auto-ff.receipts.jsonl").read_text(encoding="utf-8").splitlines())
    assert run().returncode == 0
    assert len((chain["state"] / "v2c-auto-ff.receipts.jsonl").read_text(encoding="utf-8").splitlines()) == lines


@windows_only
def test_auto_ff_follows_monitor_workflow_changes_but_not_the_deploy_workflow(tmp_path, chain):
    clone, first, second = _ff_fixture(tmp_path, chain)
    _ff_snapshots(chain, second[:12], second)
    env = {**chain["env"], "DOXXED_V2C_LAPTOP_FOLLOW_FETCH_SEC": "0"}
    run = lambda: _ps(f"& '{SCRIPTS / 'v2c-auto-ff.ps1'}' -RepoRoot '{clone}' -CanonicalRoot "
                      f"'{chain['canonical']}' -StateDir '{chain['state']}'; exit $LASTEXITCODE", env)
    assert run().returncode == 0
    ci = _origin_commit(tmp_path, ".github/workflows/laptop-tests.yml", "x\n", "[skip ci] ci: laptop tests")
    assert run().returncode == 0
    assert _git(clone, "rev-parse", "HEAD") == ci
    _origin_commit(tmp_path, ".github/workflows/fly-bot-deploy.yml", "x\n", "[skip ci] ci: deploy tweak")
    assert run().returncode == 0
    assert _git(clone, "rev-parse", "HEAD") == ci
    receipt = json.loads((chain["state"] / "v2c-auto-ff.receipts.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert receipt["outcome"] == "SKIPPED_LAPTOP_FOLLOW" and "fly-bot-deploy.yml" in receipt["denied"]


@windows_only
def test_auto_ff_does_not_follow_commits_without_skip_ci(tmp_path, chain):
    clone, first, second = _ff_fixture(tmp_path, chain)
    _ff_snapshots(chain, second[:12], second)
    env = {**chain["env"], "DOXXED_V2C_LAPTOP_FOLLOW_FETCH_SEC": "0"}
    run = lambda: _ps(f"& '{SCRIPTS / 'v2c-auto-ff.ps1'}' -RepoRoot '{clone}' -CanonicalRoot "
                      f"'{chain['canonical']}' -StateDir '{chain['state']}'; exit $LASTEXITCODE", env)
    assert run().returncode == 0
    _origin_commit(tmp_path, "scripts/y.py", "y\n", "feat: needs a deploy")
    assert run().returncode == 0
    assert _git(clone, "rev-parse", "HEAD") == second


@windows_only
def test_auto_ff_refuses_dirty_checkout_and_waits_for_a_busy_cycle(tmp_path, chain):
    clone, first, second = _ff_fixture(tmp_path, chain)
    _ff_snapshots(chain, second[:12], second)
    (clone / "a.txt").write_text("local edit\n")
    assert _run_auto_ff(clone, chain).returncode == 3
    assert _git(clone, "rev-parse", "HEAD") == first
    _git(clone, "checkout", "-q", "--", "a.txt")
    holder = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-Command",
         f". '{SCRIPTS / 'laptop-chain-common.ps1'}'; $h = Enter-SingleInstance -Name (Get-ChainMutexName "
         f"'LaptopSegmentAnalyzerCycle'); 'held'; Start-Sleep 30"],
        stdout=subprocess.PIPE, text=True, env={**os.environ, **chain["env"]})
    try:
        assert holder.stdout.readline().strip() == "held"
        assert _run_auto_ff(clone, chain).returncode == 2
        assert _git(clone, "rev-parse", "HEAD") == first
    finally:
        holder.kill()


def test_supervisor_fast_forwards_before_starting_a_cycle_and_cycle_refuses_old_checkout():
    supervisor = _source("laptop-chain-supervisor.ps1")
    # RK 20:20 / R22 (#972 REV7): Fly steps 1–3 removed from supervisor (not flagged).
    assert "research-segment-pull-loop.ps1" not in supervisor
    assert "laptop-status-snapshots.ps1" not in supervisor
    assert "v2c-auto-ff.ps1" not in supervisor
    assert "segment-pull.disabled" not in supervisor
    assert "fly-status-snapshots.disabled" not in supervisor
    assert "v2c-auto-ff.disabled" not in supervisor
    assert "r22-fly-steps" in supervisor
    assert "run-segment-analyzer-cycle.ps1" in supervisor or "run-analyzer-once.ps1" in supervisor
    assert "laptop-chain-monitor.ps1" in supervisor
    assert "system_health.py" in supervisor
    assert "laptop_chain_incident.py" in supervisor
    assert not (SCRIPTS / "local-fresh-disable-fly-chain-steps.ps1").exists()
    cycle = _source("run-segment-analyzer-cycle.ps1")
    check = cycle[cycle.index("ANALYZER_REVISION_MISMATCH") - 200:cycle.index("Set-CycleStatus 'ANALYZER'")]
    assert "merge-base --is-ancestor $deployedFull $checkoutHead" in cycle
    assert "Stop-Cycle 5" in check
    assert "v2c-auto-ff.ps1" not in check
    assert "laptop-status-snapshots.ps1" not in check
    assert "'.segment-promotion.index.json'" in cycle
    once = _source("run-analyzer-once.ps1")
    assert "PYTHONFAULTHANDLER" in once
    assert "ANALYZER_NATIVE_CRASH_LOG" in once
    assert r"C:\DoxxedCrypto" in once or "DOXXED_CRYPTO_ROOT" in once
    assert "ANALYZER_NATIVE_CRASH_RETRY" in once
    assert "Test-NativeCrashExit" in once
    assert "once-retry" in once

@windows_only
def test_cycle_lock_held_auto_ff_fast_forwards_while_the_cycle_mutex_is_owned(tmp_path, chain):
    clone, first, second = _ff_fixture(tmp_path, chain)
    _ff_snapshots(chain, second[:12], second)
    holder = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-Command",
         f". '{SCRIPTS / 'laptop-chain-common.ps1'}'; $h = Enter-SingleInstance -Name (Get-ChainMutexName "
         f"'LaptopSegmentAnalyzerCycle'); 'held'; Start-Sleep 30"],
        stdout=subprocess.PIPE, text=True, env={**os.environ, **chain["env"]})
    try:
        assert holder.stdout.readline().strip() == "held"
        result = _ps(f"& '{SCRIPTS / 'v2c-auto-ff.ps1'}' -RepoRoot '{clone}' -CanonicalRoot '{chain['canonical']}' "
                     f"-StateDir '{chain['state']}' -CycleLockHeld; exit $LASTEXITCODE", chain["env"])
        assert result.returncode == 0, result.stdout + result.stderr
        assert _git(clone, "rev-parse", "HEAD") == second
    finally:
        holder.kill()
    receipt = json.loads((chain["state"] / "v2c-auto-ff.receipts.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert receipt["outcome"] == "FAST_FORWARDED" and receipt["caller"] == "cycle"


def test_cycle_refuses_revision_mismatch_without_fly_auto_ff():
    # RK 20:20 / R22: inline Fly auto-ff removed; mismatch fails closed.
    cycle = _source("run-segment-analyzer-cycle.ps1")
    gate = cycle[cycle.index("ANALYZER_REVISION_MISMATCH"):cycle.index("Set-CycleStatus 'ANALYZER'")]
    assert "Stop-Cycle 5" in gate
    assert "v2c-auto-ff.ps1" not in gate
    assert "laptop-status-snapshots.ps1" not in gate
    assert "AUTO_FF_INLINE" not in gate
    assert "Test-CheckoutContainsDeployed" in cycle

def test_dashboard_revision_refreshes_after_every_generation():
    runner = _source("run-analyzer-once.ps1")
    main_finally = runner.rsplit("} finally {", 1)[1]
    assert "Update-AnalyzerDashboardCode" in main_finally.split("$status.exitCode = $exitCode", 1)[0]
    body = runner.split("function Update-AnalyzerDashboardCode {", 1)[1].split("\n}\n", 1)[0]
    assert "Set-DashboardCodeRevision $head" in body


def test_cycle_runs_mirror_publish_stale_check_every_cycle():
    cycle = _source("run-segment-analyzer-cycle.ps1")
    check = cycle.split("$mirrorReceiptPath = ", 1)[1].split("# Custody-gated retention", 1)[0]
    assert "'--check-stale'" in check and "$staleExit -eq 5" in check
    assert "ALERT ANALYZER_MIRROR_PUBLISH_STALE" in check and "mirrorPublishFreshness" in check
    # not nested under `if ($analyzerExit -eq 0)`: a failing analyzer still raises the alert
    assert cycle.split("$mirrorReceiptPath = ", 1)[0].rstrip().endswith("}") or "if ($analyzerExit" not in check
    assert "$env:ANALYZER_MIRROR_PUBLISH_RECEIPT" in cycle
