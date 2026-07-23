"""Static regression checks for Windows one-owner Start/Stop safety."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMON = (ROOT / "scripts" / "home-stack-common.ps1").read_text(encoding="utf-8")
START = (ROOT / "scripts" / "start-home-bot.ps1").read_text(encoding="utf-8")
HEALTH = (ROOT / "scripts" / "home-stack-health.ps1").read_text(encoding="utf-8")
MONITOR = (ROOT / "scripts" / "bot-auto-restart.ps1").read_text(encoding="utf-8")
AUTOSTART = (ROOT / "scripts" / "register-bot-autostart.ps1").read_text(
    encoding="utf-8"
)
SUPERVISOR = (ROOT / "scripts" / "home-stack-supervisor.ps1").read_text(
    encoding="utf-8"
)
SUPERVISOR_WATCHDOG = (
    ROOT / "scripts" / "home-stack-supervisor-watchdog.ps1"
).read_text(encoding="utf-8")
BOT = (
    ROOT / "services" / "btc-conservative-agent" / "bot.py"
).read_text(encoding="utf-8")
ENTRYPOINT = (
    ROOT / "services" / "btc-conservative-agent" / "btc_conservative_agent.py"
).read_text(encoding="utf-8")
EARLY_BOOT = (
    ROOT / "services" / "btc-conservative-agent" / "early_boot.py"
).read_text(encoding="utf-8")
BOT_HUNG = HEALTH.split("function Test-BotHung", 1)[1].split(
    "function Test-AnalyzerHung", 1
)[0]
BOT_MAIN = BOT.split("def main():", 1)[1]


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"  [PASS] {name}")


def main() -> None:
    check(
        "listener ownership uses non-blocking native netstat",
        "function Get-ListenPortOwners" in COMMON
        and "netstat.exe -ano -p TCP" in COMMON
        and "Get-NetTCPConnection -LocalPort $ListenPort" not in COMMON,
    )
    check(
        "listener cleanup has a taskkill fallback",
        "taskkill.exe /PID $procId /T /F" in COMMON
        and "Unable to stop listener PID $procId" in COMMON
        and "$killed += $procId" in COMMON,
    )
    check(
        "start refuses an occupied port after cleanup",
        "refusing duplicate bot start" in START
        and START.count("Test-PortBound $BotListenPort") >= 3,
    )
    check(
        "all bot startup paths share one exclusive starter lock",
        '".home-bot-start.lock"' in START
        and "[System.IO.FileShare]::None" in START
        and "Another bot startup is already in progress" in START,
    )
    check(
        "hung detection includes bound-but-unconnectable listeners",
        "Test-PortBound $BotPort" in BOT_HUNG
        and 'Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 20' in BOT_HUNG,
    )
    check(
        "auto-restart supervises liveness instead of process existence only",
        "function Test-BotPingQuick" in MONITOR
        and "consecutiveLivenessFailures -ge 3" in MONITOR
        and "bot process alive but /api/ping unavailable" in MONITOR,
    )
    check(
        "auto-restart never calls the blocking TCP provider",
        "Stop-ListenPortFast $Port" in MONITOR
        and "Get-NetTCPConnection -LocalPort $Port" not in MONITOR,
    )
    check(
        "autostart has a current-user fallback when admin rights are unavailable",
        "$runLevel = if ($isAdministrator) { 'Highest' } else { 'Limited' }"
        in AUTOSTART
        and "-RunLevel $runLevel" in AUTOSTART,
    )
    check(
        "autostart preserves an existing administrator-owned task",
        "Existing administrator-owned task" in AUTOSTART
        and "if ($_.Exception.Message -match 'Access is denied')" in AUTOSTART,
    )
    check(
        "Bitfinex market metadata cannot block normal bot import",
        "EAGER_EXCHANGE_MARKET_LOAD = (" in BOT
        and "if EAGER_EXCHANGE_MARKET_LOAD" in BOT
        and "else {}" in BOT,
    )
    check(
        "dashboard ping remains live throughout persistent-state restoration",
        BOT_MAIN.index("threading.Thread(target=run_flask")
        < BOT_MAIN.index("prune_aux_logs_on_startup()")
        and BOT_MAIN.count("threading.Thread(target=run_flask") == 1
        and "_DASHBOARD_BOOTSTRAP_COMPLETE = True" in BOT_MAIN,
    )
    check(
        "temporary boot health proves current owner and revision",
        "def _read_boot_revision()" in ENTRYPOINT
        and "source_git_rev=_boot_revision" in ENTRYPOINT
        and '"dashboard_owner": True' in EARLY_BOOT
        and '"dashboard_pid": os.getpid()' in EARLY_BOOT
        and '"source_git_rev": _source_git_rev' in EARLY_BOOT,
    )
    check(
        "temporary health stays bound through showcase route registration",
        ENTRYPOINT.index("register_showcase_ui(signal_engine.app")
        < ENTRYPOINT.index("def main() -> None:")
        < ENTRYPOINT.index(
            "stop_early_ping_server()", ENTRYPOINT.index("def main() -> None:")
        )
        < ENTRYPOINT.index("signal_engine.main()"),
    )
    check(
        "partially restored dashboard and relay snapshots fail closed",
        "not _DASHBOARD_BOOTSTRAP_COMPLETE" in BOT
        and '"error": "dashboard state is restoring"' in BOT
        and '"boot": "ready" if _DASHBOARD_BOOTSTRAP_COMPLETE else "starting"' in BOT,
    )
    check(
        "boot health bypasses state-locking handlers",
        'if path in ("/", "/health", "/status", "/api/status"):' in BOT
        and '"status": "starting"' in BOT
        and 'if path in ("/api/ping", "/api/build"):' in BOT,
    )
    check(
        "crash notification cannot block supervised restart",
        'Start-Process -FilePath "msg.exe"' in MONITOR
        and 'msg * /TIME:30' not in MONITOR
        and 'Start-Process -FilePath "msg.exe"' in START
        and 'msg * /TIME:30' not in START,
    )
    check(
        "crash reporting never scans the large runtime log before restart",
        "Get-Content $botLog -Tail" not in MONITOR
        and "Get-Content $botLog -Tail" not in START
        and "Get-Content $startupStderrLog -Tail 40" in MONITOR
        and "Get-Content $startupStderrLog -Tail 40" in START
        and "runtime_log = $botLog" in MONITOR
        and "runtime_log   = $botLog" in START,
    )
    check(
        "detached startup errors remain inspectable",
        "bot-startup.stdout.log" in START
        and "bot-startup.stderr.log" in START
        and "BOT_STARTUP_STDOUT_LOG" in START
        and "BOT_STARTUP_STDERR_LOG" in START
        and "BOT_STARTUP_STDOUT_LOG" in MONITOR
        and "BOT_STARTUP_STDERR_LOG" in MONITOR
        and "def _attach_startup_logs()" in ENTRYPOINT
        and "setattr(sys, stream_name, handle)" in ENTRYPOINT
        and "-RedirectStandardOutput" not in START
        and "-RedirectStandardError" not in START
        and "-RedirectStandardOutput" not in MONITOR
        and "-RedirectStandardError" not in MONITOR,
    )
    check(
        "supervisor singleton never depends on blocking CIM enumeration",
        "Get-CimInstance Win32_Process" not in SUPERVISOR
        and "if (-not (Test-SupervisorLock))" in SUPERVISOR
        and SUPERVISOR.index("if (-not (Test-SupervisorLock))")
        < SUPERVISOR.index('".home-stack-supervisor.pid"'),
    )
    check(
        "scheduled watchdog uses a progress heartbeat instead of CIM",
        '".home-stack-supervisor.heartbeat"' in SUPERVISOR
        and "Set-Content -Path $heartbeatFile" in SUPERVISOR
        and '".home-stack-supervisor.heartbeat"' in SUPERVISOR_WATCHDOG
        and "Get-CimInstance" not in SUPERVISOR_WATCHDOG
        and "$ageSeconds -ge 0 -and $ageSeconds -le 300" in SUPERVISOR_WATCHDOG,
    )
    check(
        "authoritative listener repairs stale bot pid before monitor attach",
        'Set-Content -Path (Join-Path $repoRoot ".home-bot.pid")' in SUPERVISOR
        and SUPERVISOR.index(
            'Set-Content -Path (Join-Path $repoRoot ".home-bot.pid")'
        )
        < SUPERVISOR.index("# Clear any stale single-instance lock"),
    )
    print("PASS: home stack one-owner startup guards")


if __name__ == "__main__":
    main()
