# Start BTC bot on home PC using vault/home-bot.env
param(
  [int]$Port = 0,
  [string]$VaultEnv = "",
  [switch]$NoWait
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logsDir = Join-Path $repoRoot "logs"
$startupStdoutLog = Join-Path $logsDir "bot-startup.stdout.log"
$startupStderrLog = Join-Path $logsDir "bot-startup.stderr.log"
if (-not (Test-Path $logsDir)) {
  New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}
$env:BOT_STARTUP_STDOUT_LOG = $startupStdoutLog
$env:BOT_STARTUP_STDERR_LOG = $startupStderrLog

# Serialize every startup path (dashboard button, supervisor, scheduled task,
# or operator shell). Two concurrent starters both performed cleanup before
# either wrote .home-bot.pid, then blocked/raced each other and left :7002
# without a durable owner. The exclusive handle is released automatically
# when this short-lived starter process exits; the file itself may remain.
$startLockFile = Join-Path $repoRoot ".home-bot-start.lock"
try {
  $script:StartLockHandle = [System.IO.File]::Open(
    $startLockFile,
    [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None
  )
} catch {
  Write-Host "Another bot startup is already in progress - leaving it as the sole starter." -ForegroundColor Yellow
  exit 0
}

. (Join-Path $scriptDir "home-stack-mode.ps1")
$stackMode = Get-HomeStackMode
if ($Port -le 0) { $Port = [int]$stackMode.BotPort }
$BotListenPort = $Port

if ($BotListenPort -eq 7810) {
  Write-Host "ERROR: Bot cannot use port 7810 (home bridge). Use -Port 7002 for showcase." -ForegroundColor Red
  exit 1
}

$Host.UI.RawUI.WindowTitle = "Doxed Bot :$BotListenPort"
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotListenPort -BridgePort 7810 -AnalyzerPort ([int]$stackMode.AnalyzerPort)
$ResearchStackVersion = Get-ResearchStackVersion

function Wait-ForKey {
  Write-Host ""
  Write-Host "--- Console stays open so you can copy logs. Press Enter to close ---" -ForegroundColor Cyan
  try { Read-Host } catch { while ($true) { Start-Sleep -Seconds 3600 } }
}

# --- Crash feedback cycle -----------------------------------------------------
# On any non-zero bot exit we: (1) snapshot the crash to logs/last_crash.json +
# logs/crash_history.jsonl, (2) fire an optional webhook (CRASH_NOTIFY_WEBHOOK)
# so you get pinged on your phone/Discord the moment it dies, (3) pop a Windows
# toast. This is the record an AI agent (Cursor Automation) reads to investigate.
function Write-CrashReport([int]$Code, [string]$Message) {
  try {
    $botLog = Join-Path $agentDir "bot_runtime.log"
    $tail = @()
    if (Test-Path $startupStderrLog) {
      $tail = @(Get-Content $startupStderrLog -Tail 40 -ErrorAction SilentlyContinue)
    }
    $report = [ordered]@{
      ts            = (Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz")
      exit_code     = $Code
      message       = $Message
      port          = $BotListenPort
      pid           = $PID
      bot_version   = $ResearchStackVersion
      runtime_log   = $botLog
      log_tail      = ($tail -join "`n")
    }
    $json = $report | ConvertTo-Json -Depth 4
    Set-Content -Path (Join-Path $logsDir "last_crash.json") -Value $json -Encoding UTF8
    Add-Content -Path (Join-Path $logsDir "crash_history.jsonl") -Value $json.Replace("`n"," ").Replace("`r","") -Encoding UTF8

    # Optional webhook notification (set CRASH_NOTIFY_WEBHOOK to a Discord/Slack/custom URL)
    $wh = (Get-Item -Path "env:CRASH_NOTIFY_WEBHOOK" -ErrorAction SilentlyContinue).Value
    if ($wh) {
      $body = @{ content = "BOT CRASH exit=$Code port=$BotListenPort ts=$($report.ts)`n```````n$($tail -join "`n" | Select-Object -First 30)`n``````" } | ConvertTo-Json
      try { Invoke-RestMethod -Uri $wh -Method Post -ContentType "application/json" -Body $body -TimeoutSec 5 -ErrorAction Stop | Out-Null } catch { }
    }

    # Windows toast (best-effort and non-blocking).
    try {
      Start-Process -FilePath "msg.exe" `
        -ArgumentList @("*", "/TIME:30", "Doxed bot crashed (exit $Code) on port $BotListenPort. See logs\last_crash.json") `
        -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
    } catch { }
  } catch { Write-Host "Write-CrashReport failed: $($_.Exception.Message)" -ForegroundColor DarkGray }
}

if (-not $VaultEnv) {
  $VaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
}

if (-not (Test-Path $VaultEnv)) {
  Write-Host "Missing $VaultEnv - run: npm run print:home-bot-env"
  Wait-ForKey
  exit 1
}

Get-Content $VaultEnv | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    $name = $matches[1].Trim()
    $val = $matches[2].Trim()
    Set-Item -Path "env:$name" -Value $val
  }
}

# The :7002 process is the canonical paper showcase. Real funds are handled by
# the per-user website relay after explicit consent; the showcase must never
# inherit a stale live flag from a local config or parent environment.
$env:FORCE_PAPER_MODE = "1"
$env:LIVE_TRADING_ENABLED = "0"
$env:BITFINEX_LIVE_ENABLED = "0"
$env:LIVE_ARMED = "0"

$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
if (-not (Test-Path $agentDir)) {
  Write-Host "Monorepo agent dir not found: $agentDir"
  Wait-ForKey
  exit 1
}

# Always kill duplicate bot processes before a fresh start (common cause of :7002 slowness).
$recordedBotRunning = $false
$botPidFile = Join-Path $repoRoot ".home-bot.pid"
if (Test-Path -LiteralPath $botPidFile) {
  # The accepting port is the relevant ownership fact. Avoid Get-Process:
  # the Windows process provider can stall for minutes on this host, and a
  # stale PID file must never block a fresh :7002 launch.
  $recordedBotRunning = Test-PortOpen $BotListenPort
  if (-not $recordedBotRunning) {
    Remove-Item -LiteralPath $botPidFile -Force -ErrorAction SilentlyContinue
  }
}
if ($recordedBotRunning) {
  Write-Host "Stopping recorded bot owner before fresh start..." -ForegroundColor Yellow
  Stop-BotPidFile | Out-Null
  Stop-ListenPortFast $BotListenPort | Out-Null
  Start-Sleep -Seconds 2
} elseif (Test-PortOpen $BotListenPort) {
  Write-Host ("Port :" + $BotListenPort + " in use - clearing listener...") -ForegroundColor Yellow
  Stop-ListenPortFast $BotListenPort | Out-Null
  Start-Sleep -Seconds 1
}

# Never launch into an occupied port. Python's temporary early-boot server can
# coexist briefly with a stale SO_REUSEADDR listener on Windows, producing two
# apparent dashboard owners and non-deterministic responses. A failed cleanup
# is safer than a duplicate bot, so abort and let the existing owner continue.
if (Test-PortOpen $BotListenPort) {
  Stop-ListenPortFast $BotListenPort | Out-Null
  Start-Sleep -Seconds 2
}
if (Test-PortOpen $BotListenPort) {
  Write-Host "ERROR: Port $BotListenPort still has a listener after cleanup; refusing duplicate bot start." -ForegroundColor Red
  exit 1
}

Set-Location $agentDir
# Script port wins over anything in home-bot.env (vault must not override showcase :7002).
$env:PORT = "$BotListenPort"
$env:DASHBOARD_PORT = "$BotListenPort"
Write-Host "Starting bot on port $BotListenPort from $agentDir ..."
Write-Host ('Dashboard: http://127.0.0.1:' + $BotListenPort)
Write-Host '/api/ping responds in ~1s while bot loads (full dashboard ~60-90s on home PC)'
Write-Host "Exports: /api/export_csv  /api/export_session_trades.csv"
Write-Host ""

if ($NoWait) {
  Write-Host "Starting bot detached on port $BotListenPort ..."
  Set-Content -LiteralPath $startupStdoutLog -Value "" -NoNewline -Encoding UTF8
  Set-Content -LiteralPath $startupStderrLog -Value "" -NoNewline -Encoding UTF8
  # The host environment can contain both Path and PATH. PowerShell 5.1 throws
  # while constructing a redirected ProcessStartInfo for that environment, so
  # Python owns its startup log handles instead (see btc_conservative_agent.py).
  $botProc = Start-Process -FilePath "python" -ArgumentList @("btc_conservative_agent.py") `
    -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
  if ($botProc -and $botProc.Id -gt 0) {
    Set-Content -Path (Join-Path $repoRoot ".home-bot.pid") -Value "$($botProc.Id)" -NoNewline
    # Background auto-restart monitor: writes logs/last_crash.json when the detached
    # bot dies AND relaunches it automatically (cooldown + backoff + rate cap).
    # Mirrors the Fly restart loop (fly-entrypoint.sh) for the local showcase bot.
    # Pass -VaultEnv so the monitor can re-seed env vars on each relaunch.
    $monitorScript = Join-Path $scriptDir "bot-auto-restart.ps1"
    $heartbeatFile = Join-Path $repoRoot ".home-bot-auto-restart.heartbeat"
    $lockFile = Join-Path $repoRoot ".home-bot-auto-restart.lock"

    # --- Monitor health check + respawn ---------------------------------------
    # The monitor has died silently in the past (transient .NET exception escaped
    # the watch loop before it was hardened with an inner try/catch). Detect a
    # stale/missing heartbeat and respawn a fresh monitor instead of leaving the
    # bot unwatched. A healthy monitor writes the heartbeat file every ~2 min, so
    # treat anything older than 5 min (or missing) as dead.
    $monitorHealthy = $false
    if (Test-Path $heartbeatFile) {
      try {
        $hbRaw = (Get-Content $heartbeatFile -Raw -ErrorAction SilentlyContinue)
        if ($hbRaw) {
          # Heartbeat file holds an ISO 8601 timestamp written by the monitor every
          # ~2 min. Parse it (AdjustToUniversal normalizes any offset) and compare
          # against current UTC. Anything <= 5 min old counts as healthy.
          $hbTime = [datetime]::Parse($hbRaw.Trim(), [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AdjustToUniversal)
          $ageMin = ((Get-Date).ToUniversalTime() - $hbTime).TotalMinutes
          if ($ageMin -ge 0 -and $ageMin -le 5) {
            $monitorHealthy = $true
          }
        }
      } catch { $monitorHealthy = $false }
    }

    # A fresh heartbeat file is not proof that the monitor process still
    # exists: a manual stop can end the monitor seconds before a restart. Verify
    # the recorded monitor PID and command line so a stale heartbeat cannot
    # leave the canonical bot running unwatched.
    if ($monitorHealthy) {
      try {
        $monitorPidFile = Join-Path $repoRoot ".home-bot-crash-monitor.pid"
        $monitorPidRaw = if (Test-Path $monitorPidFile) {
          (Get-Content $monitorPidFile -Raw -ErrorAction Stop).Trim()
        } else { "" }
        if (-not $monitorPidRaw -or $monitorPidRaw -notmatch "^\d+$") {
          $monitorHealthy = $false
        } else {
          $monitorPid = [int]$monitorPidRaw
          $monitorName = Get-ProcessExecutableNameFast $monitorPid
          $monitorHealthy = (
            (Test-ProcessIdAliveFast $monitorPid) -and
            $monitorName -in @("powershell", "pwsh")
          )
        }
      } catch {
        $monitorHealthy = $false
      }
    }

    if ($monitorHealthy) {
      Write-Host "Auto-restart monitor already running and healthy (heartbeat fresh) - skipping respawn." -ForegroundColor DarkGray
    } else {
      # Stale or missing heartbeat: kill any orphan monitor process matching the
      # script name, then start a fresh one. Also clear the stale lock so the new
      # monitor's single-instance check does not exit silently.
      try {
        $monitorPidFile = Join-Path $repoRoot ".home-bot-crash-monitor.pid"
        if (Test-Path -LiteralPath $monitorPidFile) {
          $staleMonitorPid = [int](Get-Content -LiteralPath $monitorPidFile -Raw)
          if ($staleMonitorPid -gt 0) {
            Stop-Process -Id $staleMonitorPid -Force -ErrorAction SilentlyContinue
          }
          Remove-Item -LiteralPath $monitorPidFile -Force -ErrorAction SilentlyContinue
        }
      } catch { }
      if (Test-Path $lockFile) {
        try { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue } catch { }
      }
      Write-Host "Auto-restart monitor stale/missing - respawning fresh monitor." -ForegroundColor Yellow

      # Build the argument list as a single string with backtick-quoted paths. The
      # repo path contains a space ("Final Bots"), and Start-Process -ArgumentList
      # @("-File",$path) splits the path on the space -> "Processing -File
      # 'C:\...\Final' failed because the file does not have a '.ps1' extension"
      # and the monitor never starts (so the bot has NO auto-restart). Quoting the
      # -File value inside one argument string is the reliable fix.
      $monitorArgString = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$monitorScript`" -BotPid $($botProc.Id) -Port $BotListenPort"
      if ($VaultEnv) { $monitorArgString += " -VaultEnv `"$VaultEnv`"" }
      $mon = Start-Process -FilePath "powershell" -ArgumentList $monitorArgString -WindowStyle Hidden -PassThru
      if ($mon -and $mon.Id -gt 0) {
        Set-Content -Path (Join-Path $repoRoot ".home-bot-crash-monitor.pid") -Value "$($mon.Id)" -NoNewline
      }
    }
  }
  Start-Sleep -Seconds 2
  exit 0
}

$exitCode = 0
$crashMsg = $null
try {
  python btc_conservative_agent.py
  $exitCode = $LASTEXITCODE
} catch {
  Write-Host "Bot crashed: $($_.Exception.Message)" -ForegroundColor Red
  $exitCode = 1
  $crashMsg = $_.Exception.Message
} finally {
  if ($exitCode -ne 0) {
    Write-Host "Bot exited with code $exitCode" -ForegroundColor Yellow
    $msg = if ($crashMsg) { $crashMsg } else { "non-zero exit" }
    Write-CrashReport -Code $exitCode -Message $msg
  } else {
    Write-Host "Bot process ended." -ForegroundColor Yellow
  }
  Wait-ForKey
}
