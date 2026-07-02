# Watches a detached bot PID, writes logs/last_crash.json on death, AND relaunches
# the bot automatically with cooldown + exponential backoff + rate cap. Mirrors the
# Fly restart loop (fly-entrypoint.sh) but for the local showcase bot on :7002.
#
# Started by start-home-bot.ps1 -NoWait (detached, hidden). Keeps the bot alive for
# long unattended runs (e.g. 6h relay-sim) without a human having to restart it.
#
# Params:
#   -BotPid     Initial bot PID to watch (the one start-home-bot just launched).
#   -Port       Bot listen port (default 7002). Used for env + crash report + port cleanup.
#   -VaultEnv   Path to vault home-bot.env (defaults to sibling secrets repo).
#   -MaxRestartsPerHour  Rate cap (default 10). After this many restarts in a rolling
#                        60-min window, stop restarting and log a crash-loop notice.
#   -BaseCooldownSec     Initial restart cooldown (default 5).
#   -MaxCooldownSec      Backoff cap (default 60).
param(
  [int]$BotPid = 0,
  [int]$Port = 7002,
  [string]$VaultEnv = "",
  [int]$MaxRestartsPerHour = 10,
  [int]$BaseCooldownSec = 5,
  [int]$MaxCooldownSec = 60
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
$agentDir  = Join-Path $repoRoot "services\btc-conservative-agent"
$logsDir   = Join-Path $repoRoot "logs"
$pidFile   = Join-Path $repoRoot ".home-bot.pid"
$lockFile  = Join-Path $repoRoot ".home-bot-auto-restart.lock"
$heartbeatFile = Join-Path $repoRoot ".home-bot-auto-restart.heartbeat"
$restartLog = Join-Path $logsDir "bot_restarts.log"

# Shared helpers (Test-HomeStackUserStopped / Set-HomeStackUserStopped). The supervisor
# dot-sources the same file; we mirror it so this monitor stands down when the user
# clicks Stop (which sets the .home-stack-user-stopped flag) instead of relaunching
# the bot and undoing the user's stop.
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $Port -AnalyzerPort 9001 -BridgePort 7810

if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }

# --- Single-instance lock ----------------------------------------------------
# Only one auto-restart monitor should ever run. If a stale lock points at a dead
# PID, reclaim it; if a live monitor is already running, exit silently.
function Test-LockHeldByLive {
  if (-not (Test-Path $lockFile)) { return $false }
  try {
    $raw = (Get-Content $lockFile -Raw -ErrorAction SilentlyContinue)
    $lockPid = [int]"$raw".Trim()
    if ($lockPid -le 0) { return $false }
    $p = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
    return ($null -ne $p)
  } catch { return $false }
}
if (Test-LockHeldByLive) { exit 0 }
Set-Content -Path $lockFile -Value "$PID" -NoNewline -Encoding UTF8
$lockHeld = $true
try {
  # --- Load vault env (so restarted bot inherits the same secrets) -------------
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

  # Script port wins over anything in home-bot.env (vault must not override :7002).
  $env:PORT = "$Port"
  $env:DASHBOARD_PORT = "$Port"

  # --- Crash report (mirrors start-home-bot.ps1 Write-CrashReport) -------------
  function Write-CrashReport([int]$CrashedPid, [int]$Code, [string]$Message) {
    try {
      $botLog = Join-Path $agentDir "bot_runtime.log"
      $tail = @()
      if (Test-Path $botLog) {
        $tail = @(Get-Content $botLog -Tail 60 -ErrorAction SilentlyContinue)
      }
      $report = [ordered]@{
        ts          = (Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz")
        exit_code   = $Code
        message     = $Message
        port        = $Port
        pid         = $CrashedPid
        bot_version = "v11.1-virtual-chase-known-combos-v1"
        log_tail    = ($tail -join "`n")
      }
      $json = $report | ConvertTo-Json -Depth 4
      Set-Content -Path (Join-Path $logsDir "last_crash.json") -Value $json -Encoding UTF8
      Add-Content -Path (Join-Path $logsDir "crash_history.jsonl") `
        -Value ($json -replace "`n"," " -replace "`r","") -Encoding UTF8

      $wh = (Get-Item -Path "env:CRASH_NOTIFY_WEBHOOK" -ErrorAction SilentlyContinue).Value
      if ($wh) {
        try {
          $body = @{ content = "BOT CRASH exit=$Code port=$Port ts=$($report.ts)`n```````n$(($tail | Select-Object -First 30) -join "`n")`n``````" } | ConvertTo-Json
          Invoke-RestMethod -Uri $wh -Method Post -ContentType "application/json" -Body $body -TimeoutSec 5 -ErrorAction Stop | Out-Null
        } catch { }
      }
      try { msg * /TIME:30 "Doxed bot crashed (exit $Code) on port $Port. Auto-restarting. See logs\last_crash.json" 2>$null } catch { }
    } catch { }
  }

  function Write-RestartLog([string]$Line) {
    $ts = Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"
    Add-Content -Path $restartLog -Value "$ts`t$Line" -Encoding UTF8
  }

  # --- Relaunch the bot exactly like start-home-bot.ps1 -NoWait ---------------
  function Start-BotHidden {
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
      $botProc = Start-Process -FilePath "python" -ArgumentList @("btc_conservative_agent.py") `
        -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
    } finally { Pop-Location }

    if ($botProc -and $botProc.Id -gt 0) {
      Set-Content -Path $pidFile -Value "$($botProc.Id)" -NoNewline -Encoding UTF8
      return $botProc.Id
    }
    return 0
  }

  # --- Main watch/restart loop ------------------------------------------------
  $currentPid = $BotPid
  $restartTimes = [System.Collections.Generic.List[datetime]]::new()
  $consecutiveCrashes = 0
  $lastHeartbeat = [datetime]::MinValue

  while ($true) {
    try {
      # --- Heartbeat: every ~2 min, log + touch a heartbeat file so an external
      # checker can detect a silently-dead monitor. Without this, a transient
      # .NET exception could escape the loop and the monitor would vanish with
      # no observable trace.
      $hbNow = Get-Date
      if (($hbNow - $lastHeartbeat).TotalSeconds -ge 120) {
        $lastHeartbeat = $hbNow
        Write-RestartLog "watching	pid=$currentPid"
        try {
          Set-Content -Path $heartbeatFile -Value ($hbNow.ToString("o")) -Encoding UTF8
        } catch { }
      }

      if ($currentPid -le 0) {
        # No PID to watch (shouldn't happen on first pass since start-home-bot passes one).
        Write-RestartLog "no_pid_watched	exiting"
        break
      }

      $p = Get-Process -Id $currentPid -ErrorAction SilentlyContinue
      if (-not $p) {
        # Already gone before we could attach. Treat as a crash with unknown exit code.
        $code = -1
        Write-CrashReport -CrashedPid $currentPid -Code $code -Message "bot process gone before monitor attached"
      } else {
        $p.WaitForExit()
        $code = $p.ExitCode
      }

      # Exit code 0 = intentional shutdown (e.g. user stop-home-bot). Don't restart.
      if ($code -eq 0) {
        Write-RestartLog "clean_exit	pid=$currentPid	code=0	no_restart"
        break
      }

      # Crash path: report already written above (or by Write-CrashReport for the gone case).
      $consecutiveCrashes++

      # Rate cap: prune restart timestamps older than 1 hour, then check the cap.
      $now = Get-Date
      for ($i = $restartTimes.Count - 1; $i -ge 0; $i--) {
        if (($now - $restartTimes[$i]).TotalHours -ge 1.0) { $restartTimes.RemoveAt($i) }
      }
      if ($restartTimes.Count -ge $MaxRestartsPerHour) {
        Write-RestartLog "rate_capped	restarts_in_last_hour=$($restartTimes.Count)	max=$MaxRestartsPerHour	pid=$currentPid	code=$code	stopping_monitor"
        try { msg * /TIME:60 "Doxed bot crash-loop on port ${Port}: $MaxRestartsPerHour restarts in 1h. Auto-restart HALTED to protect disk. See logs\bot_restarts.log" 2>$null } catch { }
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
      $newPid = Start-BotHidden
      if ($newPid -le 0) {
        Write-RestartLog "relaunch_failed	pid=$currentPid	code=$code	stopping_monitor"
        break
      }
      $restartTimes.Add((Get-Date))
      Write-RestartLog "restarted	old_pid=$currentPid	new_pid=$newPid	code=$code	cooldown=${cooldown}s"
      $currentPid = $newPid
      # Give the new process a moment to settle before we start watching it.
      Start-Sleep -Seconds 2
    } catch {
      # Inner guard: any transient .NET exception (Get-Process on a PID that just
      # vanished, Get-NetTCPConnection under port-table contention, etc.) is logged
      # and swallowed so the watch loop continues. Previously these escaped straight
      # to the outer finally, silently killing the monitor and leaving the bot
      # unwatched. Intentional break-paths above still break out of the while loop
      # (a break inside a try inside a while breaks the while).
      Write-RestartLog "watch_loop_error	$_"
      Start-Sleep -Seconds 5
    }
  }
} finally {
  if ($lockHeld) {
    try { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue } catch { }
  }
}
