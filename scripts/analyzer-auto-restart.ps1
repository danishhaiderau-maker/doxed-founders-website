# Watches a detached analyzer PID and relaunches the research analyzer
# automatically with cooldown + exponential backoff + rate cap. Mirrors
# scripts/bot-auto-restart.ps1 but for the analyzer on :9001.
#
# Started by start-home-analyzer.ps1 -NoWait (detached, hidden). Keeps the
# analyzer alive for long unattended runs without a human having to restart it,
# and prevents the "analyzer_offline_ping=0" stack-abnormality popups that fire
# when the analyzer crashes and stays down.
#
# Params:
#   -AnalyzerPid  Initial analyzer PID to watch (the one start-home-analyzer just launched).
#   -Port         Analyzer research dashboard port (default 9001).
#   -VaultEnv     Path to vault home-bot.env (defaults to sibling secrets repo).
#   -MaxRestartsPerHour  Rate cap (default 10). After this many restarts in a rolling
#                        60-min window, stop restarting and log a crash-loop notice.
#   -BaseCooldownSec     Initial restart cooldown (default 5).
#   -MaxCooldownSec      Backoff cap (default 60).
#   -PollIntervalSec     How often to poll process liveness + health (default 30).
#   -BootGraceSec        Window after start during which health is NOT probed, so a
#                        slow Flask startup is not mistaken for a hang (default 90).
#   -HealthProbeTimeoutSec  Per-request timeout for the /api/status probe (default 5).
param(
  [int]$AnalyzerPid = 0,
  [int]$Port = 9001,
  [string]$VaultEnv = "",
  [int]$MaxRestartsPerHour = 10,
  [int]$BaseCooldownSec = 5,
  [int]$MaxCooldownSec = 60,
  [int]$PollIntervalSec = 30,
  [int]$BootGraceSec = 90,
  [int]$HealthProbeTimeoutSec = 5
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
$agentDir  = Join-Path $repoRoot "services\btc-conservative-agent"
$logsDir   = Join-Path $repoRoot "logs"
$pidFile   = Join-Path $repoRoot ".home-analyzer.pid"
$lockFile  = Join-Path $repoRoot ".home-analyzer-auto-restart.lock"
$restartLog = Join-Path $logsDir "analyzer-auto-restart.log"

# Shared helpers (Test-HomeStackUserStopped / Set-HomeStackUserStopped). The supervisor
# dot-sources the same file; we mirror it so this monitor stands down when the user
# clicks Stop (which sets the .home-stack-user-stopped flag) instead of relaunching
# the analyzer and undoing the user's stop.
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort 7002 -AnalyzerPort $Port -BridgePort 7810

if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }

# --- Single-instance lock -----------------------------------------------------
# Only one auto-restart monitor should ever run. If a stale lock points at a dead
# PID, reclaim it; if a live monitor is already running, exit silently.
function Test-AnalyzerMonitorCommandLine([int]$ProcId) {
  if ($ProcId -le 0) { return $false }
  try {
    $p = Get-Process -Id $ProcId -ErrorAction SilentlyContinue
    if (-not $p -or $p.ProcessName -ne "powershell") { return $false }
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$ProcId" -ErrorAction SilentlyContinue).CommandLine
    return ($cmd -and $cmd -like "*analyzer-auto-restart.ps1*")
  } catch { return $false }
}

function Stop-StaleAnalyzerCrashMonitors([int]$ExceptPid = 0) {
  $killed = @()
  Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and $_.CommandLine -like "*analyzer-auto-restart.ps1*" -and
      ($ExceptPid -le 0 -or $_.ProcessId -ne $ExceptPid)
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $killed += $_.ProcessId
    }
  return $killed
}

function Test-LockHeldByLive {
  if (-not (Test-Path $lockFile)) { return $false }
  try {
    $raw = (Get-Content $lockFile -Raw -ErrorAction SilentlyContinue)
    $lockPid = [int]"$raw".Trim()
    if ($lockPid -le 0) { return $false }
    return (Test-AnalyzerMonitorCommandLine $lockPid)
  } catch { return $false }
}

Stop-StaleAnalyzerCrashMonitors -ExceptPid $PID | Out-Null
if (Test-LockHeldByLive) { exit 0 }
Set-Content -Path $lockFile -Value "$PID" -NoNewline -Encoding UTF8
$lockHeld = $true
try {
  # --- Load vault env (so restarted analyzer inherits the same secrets) --------
  if (-not $VaultEnv) {
    $VaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
  }
  if (Test-Path $VaultEnv) {
    Get-Content $VaultEnv | ForEach-Object {
      if ($_ -match '^\s*([^#=]+)=(.*)$') {
        Set-Item -Path "env:$($matches[1].Trim())" -Value $matches[2].Trim()
      }
    }
  }

  # Script port wins over anything in home-bot.env (vault must not override :9001).
  $env:RESEARCH_DASHBOARD_BIND_HOST = "0.0.0.0"
  $env:RESEARCH_DASHBOARD_PORT = "$Port"
  $env:RESEARCH_DASHBOARD_PUBLIC_URL = "http://10.0.0.102:$Port/"
  $env:BTC_AGENT_DATA_DIR = $agentDir

  # --- Crash report (mirrors start-home-analyzer.ps1 crash logging) ------------
  function Write-CrashReport([int]$CrashedPid, [int]$Code, [string]$Message) {
    try {
      $analyzerLog = Join-Path $agentDir "analyzer_run.log"
      $tail = @()
      if (Test-Path $analyzerLog) {
        $tail = @(Get-Content $analyzerLog -Tail 60 -ErrorAction SilentlyContinue)
      }
      $report = [ordered]@{
        ts        = (Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz")
        exit_code = $Code
        message   = $Message
        port      = $Port
        pid       = $CrashedPid
        component = "analyzer"
        log_tail  = ($tail -join "`n")
      }
      $json = $report | ConvertTo-Json -Depth 4
      Set-Content -Path (Join-Path $logsDir "last_analyzer_crash.json") -Value $json -Encoding UTF8
      Add-Content -Path (Join-Path $logsDir "analyzer_crash_history.jsonl") `
        -Value ($json -replace "`n"," " -replace "`r","") -Encoding UTF8

      $wh = (Get-Item -Path "env:CRASH_NOTIFY_WEBHOOK" -ErrorAction SilentlyContinue).Value
      if ($wh) {
        try {
          $body = @{ content = "ANALYZER CRASH exit=$Code port=$Port ts=$($report.ts)`n```````n$(($tail | Select-Object -First 30) -join "`n")`n``````" } | ConvertTo-Json
          Invoke-RestMethod -Uri $wh -Method Post -ContentType "application/json" -Body $body -TimeoutSec 5 -ErrorAction Stop | Out-Null
        } catch { }
      }
      try { msg * /TIME:30 "Doxed analyzer crashed (exit $Code) on port $Port. Auto-restarting. See logs\last_analyzer_crash.json" 2>$null } catch { }
    } catch { }
  }

  function Write-RestartLog([string]$Line) {
    $ts = Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"
    Add-Content -Path $restartLog -Value "$ts`t$Line" -Encoding UTF8
  }

  # --- Health probe -------------------------------------------------------------
  # The analyzer exposes /api/status (Flask research dashboard). The bridge uses
  # the same endpoint in Test-AnalyzerHealthy. We treat HTTP 200 as healthy and
  # fall back to /api/health then / (root) for forward-compat. A 200 from any of
  # these proves the Flask server actually answers, not just that the port is bound.
  function Test-AnalyzerHttpHealthy {
    $urls = @(
      "http://127.0.0.1:$Port/api/status",
      "http://127.0.0.1:$Port/api/health",
      "http://127.0.0.1:$Port/"
    )
    foreach ($u in $urls) {
      try {
        $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec $HealthProbeTimeoutSec
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) { return $true }
      } catch { }
    }
    return $false
  }

  # --- Relaunch the analyzer exactly like start-home-analyzer.ps1 -NoWait ------
  function Start-AnalyzerHidden {
    # Clear any stale listener on the port before relaunch (common cause of bind fail).
    try {
      Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
          $procId = [int]$_
          if ($procId -gt 0 -and $procId -ne 4) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
          }
        }
    } catch { }

    Push-Location $agentDir
    try {
      # Replicates start-home-analyzer.ps1 launch: python research\analyzer_research_engine_v62.py
      # (no --once; we want the continuous 30-min loop on auto-restart).
      $analyzerProc = Start-Process -FilePath "python" `
        -ArgumentList @("research\analyzer_research_engine_v62.py") `
        -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
    } finally { Pop-Location }

    if ($analyzerProc -and $analyzerProc.Id -gt 0) {
      Set-Content -Path $pidFile -Value "$($analyzerProc.Id)" -NoNewline -Encoding UTF8
      return $analyzerProc.Id
    }
    return 0
  }

  # --- Main watch/restart loop -------------------------------------------------
  # Unlike bot-auto-restart (which blocks on WaitForExit), the analyzer loop POLLS:
  # the analyzer can hang with the port still bound (Flask thread deadlock under
  # long unattended runs), so we must probe /api/status as well as PID liveness.
  # A crash = process dead OR health probe failing outside the boot grace window.
  $currentPid = $AnalyzerPid
  $restartTimes = [System.Collections.Generic.List[datetime]]::new()
  $consecutiveCrashes = 0
  $bootedAt = Get-Date

  while ($true) {
    if ($currentPid -le 0) {
      Write-RestartLog "no_pid_watched	exiting"
      break
    }

    Start-Sleep -Seconds $PollIntervalSec

    $p = Get-Process -Id $currentPid -ErrorAction SilentlyContinue
    $processAlive = ($null -ne $p)
    $code = -1

    if (-not $processAlive) {
      # Process gone. Try to recover the real exit code (best-effort; usually gone).
      Write-CrashReport -CrashedPid $currentPid -Code $code -Message "analyzer process gone (exit detected by poll)"
    } else {
      # Process alive - probe health. Skip the probe during the boot grace window
      # so a slow Flask startup (90s on a cold home PC) is not mistaken for a hang.
      $sinceBoot = ((Get-Date) - $bootedAt).TotalSeconds
      if ($sinceBoot -lt $BootGraceSec) {
        continue
      }
      if (Test-AnalyzerHttpHealthy) {
        # Healthy: reset consecutive-crash counter so backoff does not pile up
        # across one transient blip.
        $consecutiveCrashes = 0
        continue
      }
      # Process alive but health probe dead = hung. Kill it so we can relaunch
      # cleanly (a hung Flask holds the port and blocks the replacement bind).
      $code = -2
      Write-CrashReport -CrashedPid $currentPid -Code $code -Message "analyzer health probe failed (hung listener) - killing and restarting"
      try { Stop-Process -Id $currentPid -Force -ErrorAction SilentlyContinue } catch { }
      try { $p.WaitForExit(5000) | Out-Null } catch { }
    }

    # Exit code 0 = intentional shutdown (e.g. user stop-home-analyzer). Don't restart.
    if ($code -eq 0) {
      Write-RestartLog "clean_exit	pid=$currentPid	code=0	no_restart"
      break
    }

    # Crash path: report already written above.
    $consecutiveCrashes++

    # Rate cap: prune restart timestamps older than 1 hour, then check the cap.
    $now = Get-Date
    for ($i = $restartTimes.Count - 1; $i -ge 0; $i--) {
      if (($now - $restartTimes[$i]).TotalHours -ge 1.0) { $restartTimes.RemoveAt($i) }
    }
    if ($restartTimes.Count -ge $MaxRestartsPerHour) {
      Write-RestartLog "rate_capped	restarts_in_last_hour=$($restartTimes.Count)	max=$MaxRestartsPerHour	pid=$currentPid	code=$code	stopping_monitor"
      try { msg * /TIME:60 "Doxed analyzer crash-loop on port ${Port}: $MaxRestartsPerHour restarts in 1h. Auto-restart HALTED to protect disk. See logs\analyzer-auto-restart.log" 2>$null } catch { }
      break
    }

    # Exponential backoff: 5s, 10s, 20s, 40s, 60s, 60s, ...
    $cooldown = [math]::Min($BaseCooldownSec * [math]::Pow(2, $consecutiveCrashes - 1), $MaxCooldownSec)
    $cooldown = [int]$cooldown
    Write-RestartLog "crashed	pid=$currentPid	code=$code	consecutive=$consecutiveCrashes	cooldown=${cooldown}s	restarting"

    Start-Sleep -Seconds $cooldown

    # User clicked Stop while we were in cooldown -> stand down instead of relaunching.
    # Stop force-kills python (non-zero exit) so without this guard we would treat the
    # user's Stop as a crash and immediately undo it. Mirrors home-stack-supervisor.ps1.
    if (Test-HomeStackUserStopped) {
      Write-RestartLog "user_stopped	pid=$currentPid	code=$code	standing_down_no_relaunch"
      break
    }

    # Re-launch.
    $newPid = Start-AnalyzerHidden
    if ($newPid -le 0) {
      Write-RestartLog "relaunch_failed	pid=$currentPid	code=$code	stopping_monitor"
      break
    }
    $restartTimes.Add((Get-Date))
    Write-RestartLog "restarted	old_pid=$currentPid	new_pid=$newPid	code=$code	cooldown=${cooldown}s"
    $currentPid = $newPid
    $bootedAt = Get-Date
    # Give the new process a moment to settle before we start watching it.
    Start-Sleep -Seconds 2
  }
} finally {
  if ($lockHeld) {
    try { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue } catch { }
  }
}
