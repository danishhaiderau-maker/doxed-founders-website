# 24/7 home stack supervisor - HTTP health checks + auto-recovery with cooldowns.
# Replaces tunnel-watchdog for long runs (48h-1 week). Started by Start everything.
# F4c (2026-07-07 incident) — default BotPort is 7002 (canonical showcase per
# config/bot-architecture.lock.json), not 7800 (legacy local lab).
#
# ====================================================================
#  UPTIME CONTRACT — read this before editing. See scripts/BOT_UPTIME.md.
# ====================================================================
#  This supervisor implements Scenario A of the uptime contract:
#    - Crash / python.exe dies -> detected here via /api/ping health check
#      (BotFailThreshold consecutive fails = ~2 min) AND by the
#      bot-auto-restart.ps1 monitor (sub-second, attached to the PID).
#
#  MANUAL STOP CONTRACT (Scenario C):
#    This supervisor NEVER reads the dashboard API (doxxedcrypto.digital,
#    Railway, Neon) to decide whether the bot should run. The dashboard
#    Stop button is a PER-USER PAUSE for the relay - it does not stop the
#    python.exe process for the showcase bot. The only thing that can
#    legitimately stop the bot is the bridge Stop button, which writes the
#    .home-stack-user-stopped flag (Test-HomeStackUserStopped). Every
#    recovery path below checks that flag and stands down if it is set.
#
#  CRASH-LOOP CIRCUIT BREAKER:
#    If the bot is recovered $MaxBotRestartsInWindow times in $BotRestartWindowMin
#    (default 5 in 5 min), recovery is HALTED and a Windows toast + log entry
#    is emitted. This prevents an infinite restart loop on a fatal misconfig
#    (e.g. bad vault env) from hammering Bitfinex or burning disk.
# ====================================================================
param(
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9001,
  [int]$BridgePort = 7810,
  [int]$IntervalSec = 60,
  [int]$FailThreshold = 5,
  [int]$BotFailThreshold = 2,
  [int]$BotCooldownSec = 300,
  [int]$AnalyzerCooldownSec = 600,
  [int]$TunnelCooldownSec = 900,
  [int]$BridgeCooldownSec = 300,
  # Crash-loop circuit breaker (see UPTIME CONTRACT above).
  [int]$MaxBotRestartsInWindow = 5,
  [int]$BotRestartWindowMin = 5
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -BridgePort $BridgePort
. (Join-Path $scriptDir "home-stack-health.ps1")

$logFile = Join-Path $repoRoot ".home-stack-supervisor.log"
$lockFile = Join-Path $repoRoot ".home-stack-supervisor.lock"
$namedFlag = Join-Path $repoRoot ".home-use-named-tunnel"

function Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

function Prevent-Sleep {
  try {
    Add-Type @"
using System.Runtime.InteropServices;
public class HomeStackPower {
  [DllImport("kernel32.dll", CharSet=CharSet.Auto, SetLastError=true)]
  public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
    [HomeStackPower]::SetThreadExecutionState(0x80000002) | Out-Null
  } catch { }
}

function Test-SupervisorLock {
  try {
    $script:LockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    return $true
  } catch {
    return $false
  }
}

function Stop-DuplicateSupervisors {
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and $_.CommandLine -like "*home-stack-supervisor.ps1*" -and $_.ProcessId -ne $PID
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Get-TunnelPublicUrl {
  # F4c (2026-07-07 incident) — Use-NamedTunnel already does the full check
  # (namedFlag file OR showcase lock frozen). The previous extra Test-Path
  # $namedFlag AND meant the supervisor could only recover the tunnel when
  # the legacy flag file existed - if only the lock file was frozen, the
  # supervisor logged named=False and skipped tunnel recovery entirely,
  # which is exactly why the 2026-07-07 outage lasted 4h instead of ~5min.
  if (Use-NamedTunnel) {
    return "https://bot.doxxedcrypto.digital"
  }
  return Get-TunnelUrl
}

function Restart-BotComponent {
  Log "RECOVER bot - stop + start on :$BotPort (visible window)"
  Stop-PythonMatching "btc_conservative_agent" | Out-Null
  Stop-ListenPortFast $BotPort | Out-Null
  Start-Sleep -Seconds 3
  Start-VisibleConsole (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -Title "Doxed Bot :$BotPort"
}

function Restart-AnalyzerComponent {
  Log "RECOVER analyzer - stop + start on :$AnalyzerPort (visible window)"
  Stop-PythonMatching "analyzer_research_engine" | Out-Null
  Stop-ListenPortFast $AnalyzerPort | Out-Null
  Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
  Start-VisibleConsole (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Port", "$AnalyzerPort") -Title "Doxed Analyzer :$AnalyzerPort"
}

function Restart-TunnelComponent {
  param([string]$Reason)
  Log ("RECOVER tunnel - " + $Reason)
  Stop-Cloudflared | Out-Null
  Start-Sleep -Seconds 3
  try {
    if (Use-NamedTunnel) {
      Start-HomeTunnel -Port $BotPort -Force
    } else {
      Start-HomeTunnel -Port $BotPort -Force -PreferVisible
    }
  } catch {
    Log ("RECOVER tunnel start failed: " + $_.Exception.Message)
  }
}

function Restart-BridgeComponent {
  Log "RECOVER bridge - restart launcher on :$BridgePort"
  Close-WindowsByTitlePrefix @("Doxed Home Bridge :$BridgePort", "TEST Bridge") | Out-Null
  Stop-ListenPortFast $BridgePort | Out-Null
  Start-Sleep -Seconds 2
  Start-VisibleConsole (Join-Path $scriptDir "home-stack-launcher.ps1") @() -Title "Doxed Home Bridge :$BridgePort"
}

# Respawn the bot-auto-restart.ps1 monitor if its heartbeat is stale or missing.
# This is the missing link from the 2026-07-08 audit: the auto-restart monitor
# was dying silently (transient .NET exception escaped the watch loop before
# hardening, or the user clicked Stop which exits the monitor cleanly) and
# nothing was bringing it back. Without it, sub-second crash detection is lost
# and the bot relies solely on this supervisor's slower /api/ping probe.
#
# NEVER call this when the user has stopped the stack (Test-HomeStackUserStopped)
# - the monitor would just re-launch the bot and undo the user's Stop.
#
# PID RESOLUTION (avoid racing a live bot):
# The .home-bot.pid file can be stale (the previous monitor died before
# updating it). If we handed a stale PID to a fresh monitor, the monitor would
# see "bot dead", force-kill whatever is on :7002 (a DIFFERENT, live bot) and
# launch its own - taking down a healthy bot for ~10s. So we ALWAYS look up
# the live listener on the bot port first and only fall back to the pid file.
function Restart-AutoRestartMonitor {
  param([int]$WatchPid)
  if (Test-HomeStackUserStopped) {
    Log "auto-restart monitor respawn skipped - user stopped stack"
    return
  }
  $monitorScript = Join-Path $scriptDir "bot-auto-restart.ps1"
  if (-not (Test-Path $monitorScript)) {
    Log "auto-restart monitor script missing - cannot respawn"
    return
  }
  # If $WatchPid is unknown, resolve the live bot PID. Prefer the actual TCP
  # listener on the bot port (authoritative - that is the bot serving right
  # now); fall back to .home-bot.pid only if no port listener exists.
  if ($WatchPid -le 0) {
    try {
      $listener = Get-NetTCPConnection -LocalPort $BotPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($listener -and $listener.OwningProcess -gt 0) {
        $WatchPid = [int]$listener.OwningProcess
      }
    } catch { }
    if ($WatchPid -le 0) {
      $pidFile = Join-Path $repoRoot ".home-bot.pid"
      if (Test-Path $pidFile) {
        try { $WatchPid = [int](Get-Content $pidFile -Raw).Trim() } catch { }
      }
    }
  }
  if ($WatchPid -le 0) {
    Log "auto-restart monitor respawn skipped - no live bot PID to watch"
    return
  }
  # Sanity check: confirm the PID is actually alive before handing it to the
  # monitor. If we hand it a dead PID, the monitor's first iteration will fire
  # Start-BotHidden which kills whatever holds :7002 - exactly the race we
  # are trying to avoid.
  $live = Get-Process -Id $WatchPid -ErrorAction SilentlyContinue
  if (-not $live) {
    Log "auto-restart monitor respawn skipped - pid $WatchPid not alive (would race a live bot); will retry next tick"
    return
  }
  # Clear any stale single-instance lock from a previous dead monitor so the
  # fresh instance's Test-LockHeldByLive check does not exit silently.
  $lockFile = Join-Path $repoRoot ".home-bot-auto-restart.lock"
  if (Test-Path $lockFile) {
    try {
      $lockPid = [int]((Get-Content $lockFile -Raw).Trim())
      if ($lockPid -gt 0) {
        $lockProc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
        if (-not $lockProc) {
          Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
          Log "auto-restart monitor stale lock (pid=$lockPid dead) - cleared"
        }
      }
    } catch { }
  }
  # Build arg list as a single quoted string - repo path has a space ("Final Bots").
  $vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
  $monitorArgString = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$monitorScript`" -BotPid $WatchPid -Port $BotPort"
  if (Test-Path $vaultEnv) { $monitorArgString += " -VaultEnv `"$vaultEnv`"" }
  try {
    $mon = Start-Process -FilePath "powershell" -ArgumentList $monitorArgString -WindowStyle Hidden -PassThru
    if ($mon -and $mon.Id -gt 0) {
      Set-Content -Path (Join-Path $repoRoot ".home-bot-crash-monitor.pid") -Value "$($mon.Id)" -NoNewline
      Log "auto-restart monitor respawned pid=$($mon.Id) watching bot=$WatchPid"
    } else {
      Log "auto-restart monitor respawn FAILED (Start-Process returned no pid)"
    }
  } catch {
    Log "auto-restart monitor respawn FAILED: $($_.Exception.Message)"
  }
}

function Test-AutoRestartMonitorAlive {
  # Heartbeat is written every ~2 min by the monitor; treat anything older than
  # 5 min (or missing) as dead. Same rule start-home-bot.ps1 uses.
  $heartbeatFile = Join-Path $repoRoot ".home-bot-auto-restart.heartbeat"
  if (-not (Test-Path $heartbeatFile)) { return $false }
  try {
    $hbRaw = (Get-Content $heartbeatFile -Raw -ErrorAction SilentlyContinue)
    if (-not $hbRaw) { return $false }
    $hbTime = [datetime]::Parse($hbRaw.Trim(), [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AdjustToUniversal)
    $ageMin = ((Get-Date).ToUniversalTime() - $hbTime).TotalMinutes
    return ($ageMin -ge 0 -and $ageMin -le 5)
  } catch { return $false }
}

function Invoke-Recovery {
  param(
    [string]$Name,
    [scriptblock]$Action,
    [datetime]$LastAt,
    [int]$CooldownSec
  )
  $since = ((Get-Date) - $LastAt).TotalSeconds
  if ($since -lt $CooldownSec) {
    Log ("RECOVER $Name skipped - cooldown $([int]$since)s/$CooldownSec")
    return $LastAt
  }
  & $Action
  return (Get-Date)
}

Stop-DuplicateSupervisors
if (-not (Test-SupervisorLock)) {
  Log "Another supervisor is already running - exit"
  exit 0
}

Set-Content -Path (Join-Path $repoRoot ".home-stack-supervisor.pid") -Value $PID -NoNewline
Prevent-Sleep
Log "supervisor started bot=:$BotPort analyzer=:$AnalyzerPort interval=${IntervalSec}s threshold=$FailThreshold named=$(Use-NamedTunnel)"

$hygieneTick = 0

$fail = @{
  bot = 0; analyzer = 0; tunnel = 0; bridge = 0
}
$lastRecover = @{
  bot = [datetime]::MinValue
  analyzer = [datetime]::MinValue
  tunnel = [datetime]::MinValue
  bridge = [datetime]::MinValue
}
# Rolling timestamps of bot recoveries (for the crash-loop circuit breaker).
$botRestartTimes = [System.Collections.Generic.List[datetime]]::new()
$botHalted = $false

while ($true) {
  $hygieneTick++
  if ($hygieneTick -ge 6) {
    $hygieneTick = 0
    try {
      $hygiene = Invoke-HomeTerminalHygiene -BotPort $BotPort -AnalyzerPort $AnalyzerPort
      if ($hygiene -and $hygiene.Count -gt 0) {
        Log ("hygiene " + ($hygiene -join " | "))
      }
    } catch { }
  }

  $tunnelUrl = Get-TunnelPublicUrl
  $botOk = Test-BotHealthy
  $analyzerOk = Test-AnalyzerHealthy
  $bridgeOk = Test-BridgeHealthy
  $cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $tunnelOk = if ($botOk -and $tunnelUrl) { Test-TunnelPublicHealthy $tunnelUrl } else { $false }

  if ($botOk) { $fail.bot = 0 } else { $fail.bot++ }
  if ($analyzerOk) { $fail.analyzer = 0 } else { $fail.analyzer++ }
  if ($bridgeOk) { $fail.bridge = 0 } else { $fail.bridge++ }
  if ($tunnelOk) { $fail.tunnel = 0 } else { $fail.tunnel++ }

  $botHung = Test-BotHung
  # F4c-429 — surface the rate-limit-backoff state on the tick line so we can
  # see in the log when 429s are being absorbed (instead of causing flaps).
  $backoff = Get-TunnelBackoffState
  if (Test-TunnelBackoffActive) {
    $remaining = ((Get-Date) - $backoff.until).TotalSeconds * -1
    $backoffTag = "rl-backoff(remaining=$([int]$remaining)s,count=$($backoff.count))"
  } else {
    $backoffTag = "rl-ok"
  }
  Log ("tick bot=$botOk analyzer=$analyzerOk bridge=$bridgeOk tunnel=$tunnelOk cf=$cfRunning hung=$botHung fails=b$($fail.bot)/a$($fail.analyzer)/t$($fail.tunnel)/br$($fail.bridge) $backoffTag url=$tunnelUrl")

  if ($fail.bridge -ge $FailThreshold) {
    $lastRecover.bridge = Invoke-Recovery "bridge" { Restart-BridgeComponent } $lastRecover.bridge $BridgeCooldownSec
    $fail.bridge = 0
    Start-Sleep -Seconds $IntervalSec
    continue
  }

  # Never restart on a single slow probe — require fail threshold (hung alone is logged only).
  # Bot uses a tighter threshold (BotFailThreshold=2) so a real crash restarts in ~2 min, not 15.
  if ($fail.bot -ge $BotFailThreshold) {
    if (Test-HomeStackUserStopped) {
      Log "RECOVER bot skipped - user stopped stack (.home-stack-user-stopped)"
      $fail.bot = 0
    } elseif ($botHalted) {
      # Crash-loop circuit breaker tripped earlier in this run - do not attempt
      # further bot recoveries. The user must clear .home-stack-user-stopped /
      # restart the supervisor manually after fixing the root cause.
      Log "RECOVER bot skipped - crash-loop breaker tripped ($MaxBotRestartsInWindow in $BotRestartWindowMin min). Manual intervention required."
      $fail.bot = 0
    } else {
      # Crash-loop circuit breaker: prune restarts older than the window, then
      # check the cap. If exceeded, HALT bot recovery and surface a Windows
      # toast + log entry so the user knows the bot is not restarting itself.
      $now = Get-Date
      for ($i = $botRestartTimes.Count - 1; $i -ge 0; $i--) {
        if (($now - $botRestartTimes[$i]).TotalMinutes -ge $BotRestartWindowMin) { $botRestartTimes.RemoveAt($i) }
      }
      if ($botRestartTimes.Count -ge $MaxBotRestartsInWindow) {
        $botHalted = $true
        Log ("RECOVER bot HALTED - crash-loop breaker tripped: $MaxBotRestartsInWindow restarts in $BotRestartWindowMin min. Manual intervention required. Last log: logs\last_crash.json")
        try { msg * /TIME:120 "Doxed supervisor: bot crash-loop breaker tripped ($MaxBotRestartsInWindow in $BotRestartWindowMin min). Auto-restart HALTED. See logs\.home-stack-supervisor.log" 2>$null } catch { }
        $fail.bot = 0
      } else {
        $lastRecover.bot = Invoke-Recovery "bot" { Restart-BotComponent } $lastRecover.bot $BotCooldownSec
        $botRestartTimes.Add((Get-Date))
        $fail.bot = 0
        $fail.tunnel = 0
        Start-Sleep -Seconds 30
        continue
      }
    }
  }

  if ($fail.analyzer -ge $FailThreshold) {
    if (Test-HomeStackUserStopped) {
      Log "RECOVER analyzer skipped - user stopped stack"
      $fail.analyzer = 0
    } else {
      $lastRecover.analyzer = Invoke-Recovery "analyzer" { Restart-AnalyzerComponent } $lastRecover.analyzer $AnalyzerCooldownSec
      $fail.analyzer = 0
      Start-Sleep -Seconds 20
      continue
    }
  }

  # Zombie cloudflared: process up but public URL dead for multiple checks.
  if ($botOk -and $tunnelUrl -and -not $tunnelOk -and $fail.tunnel -ge $FailThreshold) {
    if (Test-HomeStackUserStopped) {
      Log "RECOVER tunnel skipped - user stopped stack"
      $fail.tunnel = 0
    } else {
      $reason = if ($cfRunning) { "zombie cloudflared (process up, public ping dead)" } else { "cloudflared not running" }
      $lastRecover.tunnel = Invoke-Recovery "tunnel" { Restart-TunnelComponent $reason } $lastRecover.tunnel $TunnelCooldownSec
      $fail.tunnel = 0
      Start-Sleep -Seconds 45
      continue
    }
  }

  # Auto-restart monitor health: respawn it if the heartbeat has gone stale.
  # This is the gap that left the bot unwatched for hours on 2026-07-08: the
  # monitor died silently and no one was bringing it back. Skip when the user
  # has stopped the stack (no point watching a bot the user wants down) and
  # when the bot itself is down (Restart-BotComponent above re-launches the
  # monitor via start-home-bot.ps1 -NoWait, so we'd just race it).
  if ($botOk -and -not (Test-HomeStackUserStopped) -and -not (Test-AutoRestartMonitorAlive)) {
    Restart-AutoRestartMonitor -WatchPid 0
  }

  # F4c-429 — add up to 5s of jitter so the supervisor (60s default tick)
  # and the bridge-watchdog (10s tick) cannot lockstep on the same phase.
  # Lockstep was the proximate cause of the 429 cascade: both polled the
  # public tunnel URL within milliseconds of each other every ~60s.
  $jitterMs = Get-Random -Minimum 0 -Maximum 5000
  Start-Sleep -Milliseconds (($IntervalSec * 1000) + $jitterMs)
}
