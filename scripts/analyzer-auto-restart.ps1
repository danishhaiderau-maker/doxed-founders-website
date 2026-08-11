# =============================================================================
# PERMANENTLY DISABLED 2026-08-04 by Danish
# Reason: auto-restart loop caused the "Doxxed analyzer crashed exit 2 port 9001"
# popup storm. The watchdog was respawning research_dashboard.py every ~5s
# whenever it crashed, instead of letting it stay down.
# Manual start only via RESTART-LAUNCHER.cmd or `python research_dashboard.py`.
# =============================================================================
Write-Host "analyzer-auto-restart.ps1 is permanently disabled. Start the analyzer manually if needed."
exit 0

# BELOW IS THE ORIGINAL (now-unreachable) SCRIPT BODY, PRESERVED FOR REFERENCE
# -----------------------------------------------------------------------------
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
#   -VaultEnv     Optional legacy path; only non-secret analyzer tuning keys are read.
#   -MaxRestartsPerHour  Rate cap (default 10). After this many restarts in a rolling
#                        60-min window, stop restarting and log a crash-loop notice.
#   -BaseCooldownSec     Initial restart cooldown (default 5).
#   -MaxCooldownSec      Backoff cap (default 60).
#   -PollIntervalSec     How often to poll process liveness + health (default 30).
#   -BootGraceSec        Window after start during which health is NOT probed, so a
#                        cold research index is not mistaken for a hang (default 180).
#   -HealthProbeTimeoutSec  Per-request timeout for the health probe (default 10).
param(
  [int]$AnalyzerPid = 0,
  [int]$Port = 9001,
  [string]$VaultEnv = "",
  [int]$MaxRestartsPerHour = 10,
  [int]$BaseCooldownSec = 5,
  [int]$MaxCooldownSec = 60,
  [int]$PollIntervalSec = 30,
  [int]$BootGraceSec = 180,
  [int]$HealthProbeTimeoutSec = 10
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
$agentDir  = Join-Path $repoRoot "services\btc-conservative-agent"
. (Join-Path $scriptDir "fly-data-paths.ps1")
$flyCanonicalLock = Join-Path $repoRoot "config\fly-canonical.lock.json"
$analyzerDataDir = if (Test-Path -LiteralPath $flyCanonicalLock) {
  Get-DoxxedFlyMirrorDir
} else {
  $agentDir
}
$logsDir   = Join-Path $repoRoot "logs"
$pidFile   = Join-Path $repoRoot ".home-analyzer.pid"
$machineStateBase = if ($env:LOCALAPPDATA) {
  $env:LOCALAPPDATA
} else {
  [System.IO.Path]::GetTempPath()
}
$machineLockDir = Join-Path $machineStateBase "DoxxedCrypto\locks"
New-Item -ItemType Directory -Path $machineLockDir -Force | Out-Null
$lockFile  = Join-Path $machineLockDir "home-analyzer-auto-restart-$Port.lock"
$restartLog = Join-Path $logsDir "analyzer-auto-restart.log"
$flySyncPidFile = Join-Path $repoRoot ".fly-data-sync-loop.lock"
$flySyncHeartbeatFile = Join-Path $repoRoot ".fly-data-sync-loop.heartbeat.json"
$flySyncScript = Join-Path $scriptDir "sync-fly-bot-data-loop.ps1"
$flySyncHeartbeatMaxAgeSec = 600

# Shared helpers (Test-HomeStackUserStopped / Set-HomeStackUserStopped). The supervisor
# dot-sources the same file; we mirror it so this monitor stands down when the user
# clicks Stop (which sets the .home-stack-user-stopped flag) instead of relaunching
# the analyzer and undoing the user's stop.
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort 7002 -AnalyzerPort $Port -BridgePort 7810

if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }

# --- Machine-wide single-instance lock ---------------------------------------
# Worktrees share the same desktop services. A repo-local PID marker allowed an
# old integration worktree's monitor to revive a second analyzer after its
# engine was intentionally stopped. Hold one exclusive handle for this port so
# exactly one monitor can own recovery across every checkout.
$lockHandle = $null
try {
  $lockHandle = [System.IO.File]::Open(
    $lockFile,
    [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None
  )
  $lockBytes = [System.Text.Encoding]::UTF8.GetBytes("$PID")
  $lockHandle.SetLength(0)
  $lockHandle.Write($lockBytes, 0, $lockBytes.Length)
  $lockHandle.Flush($true)
} catch {
  if ($lockHandle) { try { $lockHandle.Dispose() } catch { } }
  exit 0
}
$lockHeld = $true
try {
  # --- Least-privilege analyzer environment -----------------------------------
  if (-not $VaultEnv) {
    $VaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
  }
  foreach ($secretName in @(
    "BITFINEX_API_KEY", "BITFINEX_API_SECRET", "DEEPSEEK_API_KEY",
    "DDOLLAR_GATE_TOKEN", "BOT_ADMIN_TOKEN", "BOT_CONTROL_SECRET",
    "FLY_API_TOKEN", "RAILWAY_TOKEN", "DATABASE_URL",
    "CREDENTIALS_ENCRYPTION_KEY"
  )) {
    Remove-Item -LiteralPath ("Env:" + $secretName) -ErrorAction SilentlyContinue
  }
  if (Test-Path -LiteralPath $VaultEnv) {
    $allowedAnalyzerVars = @(
      "ANALYZER_INTERVAL_MINUTES",
      "ANALYZER_GRID_SWEEP_MAX_REPLAYS",
      "ANALYZER_SKIP_3D_SWEEP",
      "RESEARCH_API_CACHE_TTL_SEC",
      "RESEARCH_OPPORTUNITY_CACHE_TTL_SEC"
    )
    Get-Content -LiteralPath $VaultEnv | ForEach-Object {
      if ($_ -match '^\s*([^#=]+)=(.*)$') {
        $name = $matches[1].Trim()
        if ($name -in $allowedAnalyzerVars) {
          Set-Item -LiteralPath ("Env:" + $name) -Value $matches[2].Trim().Trim('"').Trim("'")
        }
      }
    }
  }

  # Script port wins over any legacy environment.
  $env:RESEARCH_DASHBOARD_BIND_HOST = "127.0.0.1"
  $env:RESEARCH_DASHBOARD_PORT = "$Port"
  $env:RESEARCH_DASHBOARD_PUBLIC_URL = "http://127.0.0.1:$Port/"
  $env:ANALYZER_EMBEDDED_DASHBOARD = "0"
  $env:BTC_AGENT_DATA_DIR = $analyzerDataDir
  # A long-lived bridge/console can carry the legacy research/ path. Keep the
  # dashboard and analyzer on the same canonical report root after recovery.
  $env:BTC_AGENT_REPORT_DIR = $agentDir

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
      # Desktop toast popup permanently silenced per platform owner request.
      # The crash is still recorded in logs\last_analyzer_crash.json,
      # logs\analyzer_crash_history.jsonl, and the CRASH_NOTIFY_WEBHOOK above
      # (when configured). Only the interactive `msg *` toast is suppressed
      # so analysts stop receiving "Doxed analyzer crashed" popups on every
      # transient blip. The auto-restart logic below is unchanged.
    } catch { }
  }

  function Write-RestartLog([string]$Line) {
    $ts = Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"
    Add-Content -Path $restartLog -Value "$ts`t$Line" -Encoding UTF8
  }

  # The read-only analyzer is useful only while its Fly mirror advances. The
  # sync loop has its own machine-wide exclusive guard, but a killed PowerShell
  # process cannot revive itself. Let the already-singleton analyzer monitor
  # supervise that one data-only worker. This never starts bot.py, an AI call,
  # an exchange client, or a second strategy owner.
  function Ensure-FlyDataSyncLoop {
    if (-not (Test-Path -LiteralPath $flyCanonicalLock)) { return }

    $syncAlive = $false
    $syncPid = 0
    if (Test-Path -LiteralPath $flySyncPidFile) {
      try {
        $syncPid = [int](Get-Content -LiteralPath $flySyncPidFile -Raw)
        $syncProcess = Get-Process -Id $syncPid -ErrorAction SilentlyContinue
        if ($syncProcess -and $syncProcess.ProcessName -match "^(powershell|pwsh)$") {
          $syncAgeSec = ((Get-Date) - $syncProcess.StartTime).TotalSeconds
          $heartbeatAgeSec = if (Test-Path -LiteralPath $flySyncHeartbeatFile) {
            ((Get-Date).ToUniversalTime() -
              (Get-Item -LiteralPath $flySyncHeartbeatFile).LastWriteTimeUtc).TotalSeconds
          } else {
            $syncAgeSec
          }
          $syncAlive = ($heartbeatAgeSec -le $flySyncHeartbeatMaxAgeSec)
          if (-not $syncAlive) {
            Stop-Process -Id $syncPid -Force -ErrorAction SilentlyContinue
            Write-RestartLog "fly_sync_stale`tpid=$syncPid`theartbeat_age_sec=$([Math]::Round($heartbeatAgeSec, 1))"
          }
        }
      } catch { }
    }
    if ($syncAlive) { return }

    Remove-Item -LiteralPath $flySyncPidFile,$flySyncHeartbeatFile -Force -ErrorAction SilentlyContinue
    try {
      # Start-Process joins an ArgumentList array without preserving quoting;
      # this repository path contains spaces. Pass one explicitly quoted
      # command line so -File receives the complete script path.
      $syncArgString = (
        "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"" +
        $flySyncScript + "`""
      )
      $syncProc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList $syncArgString `
        -WorkingDirectory $repoRoot `
        -WindowStyle Hidden `
        -PassThru
      Write-RestartLog "fly_sync_started`tlauncher_pid=$($syncProc.Id)"
    } catch {
      Write-RestartLog "fly_sync_start_failed`terror=$($_.Exception.Message)"
    }
  }

  # --- Health probe -------------------------------------------------------------
  # The analyzer exposes /api/status (Flask research dashboard). The bridge uses
  # the same endpoint in Test-AnalyzerHealthy. We treat HTTP 200 as healthy and
  # fall back to /api/health then / (root) for forward-compat. A 200 from any of
  # these proves the Flask server actually answers, not just that the port is bound.
  function Test-AnalyzerHttpHealthy {
    $urls = @(
      "http://127.0.0.1:$Port/api/health",
      "http://127.0.0.1:$Port/api/status",
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

    $dashboardProc = Start-Process -FilePath "python" `
      -ArgumentList @("research_dashboard.py", "--standalone") `
      -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
    if ($dashboardProc -and $dashboardProc.Id -gt 0) {
      Set-Content -Path (Join-Path $repoRoot ".home-analyzer-dashboard.pid") `
        -Value "$($dashboardProc.Id)" -NoNewline -Encoding UTF8
    }

    Push-Location $agentDir
    try {
      # Replicates start-home-analyzer.ps1 launch: python analyzer_research_engine_v62.py
      # (no --once; we want the continuous 30-min loop on auto-restart).
      $analyzerProc = Start-Process -FilePath "python" `
        -ArgumentList @("analyzer_research_engine_v62.py") `
        -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
    } finally { Pop-Location }

    if ($analyzerProc -and $analyzerProc.Id -gt 0) {
      Set-Content -Path $pidFile -Value "$($analyzerProc.Id)" -NoNewline -Encoding UTF8
      return $analyzerProc.Id
    }
    return 0
  }

  function Restart-AnalyzerDashboardHidden {
    $dashboardPidFile = Join-Path $repoRoot ".home-analyzer-dashboard.pid"
    if (Test-Path $dashboardPidFile) {
      try {
        $dashboardPid = [int](Get-Content $dashboardPidFile -Raw).Trim()
        if ($dashboardPid -gt 0) {
          Stop-Process -Id $dashboardPid -Force -ErrorAction SilentlyContinue
        }
      } catch { }
    }
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
    Start-Sleep -Seconds 1
    $dashboardProc = Start-Process -FilePath "python" `
      -ArgumentList @("research_dashboard.py", "--standalone") `
      -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
    if ($dashboardProc -and $dashboardProc.Id -gt 0) {
      Set-Content -Path $dashboardPidFile -Value "$($dashboardProc.Id)" -NoNewline -Encoding UTF8
      return $dashboardProc.Id
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
  $consecutiveHealthFailures = 0
  $bootedAt = Get-Date

  while ($true) {
    if ($currentPid -le 0) {
      Write-RestartLog "no_pid_watched	exiting"
      break
    }

    Start-Sleep -Seconds $PollIntervalSec

    Ensure-FlyDataSyncLoop

    $processAlive = Test-ProcessIdAliveFast $currentPid
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
        $consecutiveHealthFailures = 0
        continue
      }
      # The research engine and read-only dashboard are separate processes.
      # A long dashboard refresh can transiently miss one probe; killing the
      # engine here discarded an otherwise healthy collection cycle. Require
      # repeated failures, then replace only the dashboard listener.
      $consecutiveHealthFailures++
      Write-RestartLog "dashboard_health_failed`tengine_pid=$currentPid`tcount=$consecutiveHealthFailures"
      if ($consecutiveHealthFailures -lt 3) {
        continue
      }
      $dashboardPid = Restart-AnalyzerDashboardHidden
      Start-Sleep -Seconds 5
      if ($dashboardPid -gt 0 -and (Test-AnalyzerHttpHealthy)) {
        Write-RestartLog "dashboard_restarted`tengine_pid=$currentPid`tdashboard_pid=$dashboardPid"
        $consecutiveHealthFailures = 0
        continue
      }
      $code = -2
      Write-CrashReport -CrashedPid $currentPid -Code $code -Message "analyzer dashboard recovery failed while research engine remained alive"
      $consecutiveHealthFailures = 0
      continue
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
      # Desktop toast popup permanently silenced per platform owner request.
      # Crash-loop is still logged to logs\analyzer-auto-restart.log so it
      # remains observable; only the interactive `msg *` toast is suppressed.
      Write-RestartLog "crash_loop_halting_no_toast	restarts_in_last_hour=$($restartTimes.Count)	max=$MaxRestartsPerHour	pid=$currentPid	code=$code"
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
  if ($lockHandle) {
    try { $lockHandle.Dispose() } catch { }
  }
  if ($lockHeld) {
    try { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue } catch { }
  }
}
