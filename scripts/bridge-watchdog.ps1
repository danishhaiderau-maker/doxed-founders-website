# Bridge :7810 + Cloudflare tunnel auto-respawn watchdog. Polls /health every
# ~10s and relaunches the bridge (via ensure-home-bridge.ps1) when it is down,
# AND guards the cloudflare tunnel: respawns cloudflared via the bridge
# /cmd/start-tunnel endpoint when the process is missing or the public URL
# answers 530 (wedged). This stops the recurring "tunnel_offline_ping=530"
# popups after a bot crash takes cloudflared down. Lock-protected so only one
# instance runs at a time - safe to invoke from the scheduled task AND a
# detached hidden loop together.
#
# Survives terminal closure because it is launched -WindowStyle Hidden via Start-Process
# (detached from any console). The register-bridge-watchdog.ps1 scheduled task
# (DoxxedBridgeWatch) re-invokes this script every 1 min so coverage survives logoff/reboot.
param(
  [int]$BridgePort = 7810,
  [int]$PollIntervalSec = 10,
  [int]$DurationMin = 5,
  [int]$RelaunchCooldownSec = 30,
  [int]$TunnelRelaunchCooldownSec = 60,
  [string]$TunnelUrl = "https://bot.doxxedcrypto.digital",
  [switch]$Quiet
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logFile = Join-Path $repoRoot ".home-bridge-watchdog.log"
$lockFile = Join-Path $repoRoot ".home-bridge-watchdog.lock"
$pidFile = Join-Path $repoRoot ".home-bridge-watchdog.pid"

# Whether the cloudflare tunnel should be up for this stack. Production showcase
# keeps the tunnel; legacy local-collection does not. Resolved once at startup
# from config/home-showcase.lock.json via Get-HomeStackMode.
$TunnelEnabled = $true
try {
  . (Join-Path $scriptDir "home-stack-mode.ps1")
  $mode = Get-HomeStackMode
  if ($mode -and $mode.PSObject.Properties.Name -contains "TunnelEnabled") {
    $TunnelEnabled = [bool]$mode.TunnelEnabled
  }
} catch { Add-Content -Path $logFile -Value ("{0} tunnel-mode resolve failed (defaulting TunnelEnabled=true): {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_.Exception.Message) -ErrorAction SilentlyContinue }

function Wd-Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
  if (-not $Quiet) { Write-Host $line }
}

# Single-instance lock. Hold the handle for the lifetime of this process so a second
# invocation (e.g. the scheduled task firing while a hidden loop is already running)
# exits immediately without duplicating recovery work.
try {
  $script:LockHandle = [System.IO.File]::Open(
    $lockFile,
    [System.IO.FileMode]::Create,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None)
} catch {
  # Lock held by another instance - confirm it is actually alive (not a stale handle
  # from a crashed process) before backing off.
  $alive = $false
  if (Test-Path $pidFile) {
    $otherPid = 0
    [int]::TryParse((Get-Content $pidFile -Raw -ErrorAction SilentlyContinue), [ref]$otherPid) | Out-Null
    if ($otherPid -gt 0) {
      $p = Get-Process -Id $otherPid -ErrorAction SilentlyContinue
      if ($p -and $p.Name -match 'powershell|pwsh') { $alive = $true }
    }
  }
  if ($alive) { exit 0 }
  # Stale lock - force clear and retry once.
  Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
  try {
    $script:LockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
  } catch { exit 0 }
}

Set-Content -Path $pidFile -Value $PID -NoNewline

function Test-BridgeUp {
  try {
    $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$BridgePort/health")
    $req.Method = "GET"
    $req.Timeout = 2000
    $req.ReadWriteTimeout = 2000
    $resp = $req.GetResponse()
    $ok = ($resp.StatusCode -eq 200)
    $resp.Close()
    return $ok
  } catch { return $false }
}

# Cloudflared process presence (cheap, runs every poll).
function Test-CloudflaredRunning {
  $p = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*cloudflared*" }
  return [bool]$p
}

# Tunnel end-to-end reachability (heavier; called less often). Returns true only
# when the public URL answers 200 - catches the case where cloudflared is alive
# but the tunnel is wedged (530).
function Test-TunnelUp {
  try {
    $req = [System.Net.HttpWebRequest]::Create("$TunnelUrl/api/ping")
    $req.Method = "GET"
    $req.Timeout = 8000
    $req.ReadWriteTimeout = 8000
    $resp = $req.GetResponse()
    $ok = ($resp.StatusCode -eq 200)
    $resp.Close()
    return $ok
  } catch { return $false }
}

# Respawn cloudflared via the bridge /cmd/start-tunnel endpoint (the same path
# the Start button uses). The bridge owns the cloudflared launch config, so we
# delegate rather than launching cloudflared directly.
function Restart-Tunnel {
  try {
    $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$BridgePort/cmd/start-tunnel")
    $req.Method = "GET"
    $req.Timeout = 20000
    $req.ReadWriteTimeout = 20000
    $resp = $req.GetResponse()
    $resp.Close()
    return $true
  } catch { return $false }
}

$deadline = (Get-Date).AddMinutes($DurationMin)
$lastRelaunch = [datetime]::MinValue
$lastTunnelRelaunch = [datetime]::MinValue
$ticks = 0

Wd-Log "bridge-watchdog start pid=$PID port=:$BridgePort poll=${PollIntervalSec}s duration=${DurationMin}m tunnelEnabled=$TunnelEnabled"

while ((Get-Date) -lt $deadline) {
  $ticks++
  if (Test-BridgeUp) {
    # Bridge healthy - nothing to log on every tick (would flood); log a heartbeat every ~2 min.
    if (($ticks % 12) -eq 0) { Wd-Log "tick #$ticks bridge UP" }
  } else {
    $since = ((Get-Date) - $lastRelaunch).TotalSeconds
    if ($since -lt $RelaunchCooldownSec) {
      Wd-Log "bridge DOWN but cooldown $([int]$since)s/${RelaunchCooldownSec}s - waiting"
    } else {
      Wd-Log "bridge DOWN on :$BridgePort - relaunching via ensure-home-bridge.ps1"
      try {
        # Repo path contains a space ("Final Bots"); build the arg list as a
        # single string with a quoted -File path, otherwise Start-Process
        # splits the path on the space and the relaunch silently fails.
        $bridgeScript = Join-Path $scriptDir "ensure-home-bridge.ps1"
        $bridgeArgString = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$bridgeScript`" -Quiet"
        $p = Start-Process -FilePath "powershell.exe" -ArgumentList $bridgeArgString -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
        if ($p) { Wd-Log "ensure-home-bridge launched pid=$($p.Id)" }
      } catch {
        Wd-Log "relaunch FAILED: $($_.Exception.Message)"
      }
      $lastRelaunch = Get-Date
    }
  }

  # Cloudflare tunnel guard - only when the stack expects a tunnel. Catches both
  # a dead cloudflared process (cheap check every poll) and a wedged tunnel that
  # answers 530 (end-to-end probe every ~60s). Respawn via the bridge so the
  # launch config stays single-sourced. This is what stops the recurring 530
  # popups after a bot crash takes cloudflared down.
  if ($TunnelEnabled) {
    $tunnelNeedsRespawn = $false
    if (-not (Test-CloudflaredRunning)) {
      $tunnelNeedsRespawn = $true
      Wd-Log "cloudflared process MISSING"
    } elseif (($ticks % 6) -eq 0) {
      # End-to-end probe every ~60s - cloudflared may be alive but tunnel wedged.
      if (-not (Test-TunnelUp)) {
        $tunnelNeedsRespawn = $true
        Wd-Log "tunnel $TunnelUrl NOT reachable (530/wedged)"
      } elseif (($ticks % 12) -eq 0) {
        Wd-Log "tick #$ticks tunnel UP"
      }
    }
    if ($tunnelNeedsRespawn) {
      $tSince = ((Get-Date) - $lastTunnelRelaunch).TotalSeconds
      if ($tSince -lt $TunnelRelaunchCooldownSec) {
        Wd-Log "tunnel respawn skipped - cooldown $([int]$tSince)s/${TunnelRelaunchCooldownSec}s"
      } else {
        Wd-Log "tunnel respawn -> bridge /cmd/start-tunnel"
        $ok = Restart-Tunnel
        Wd-Log "tunnel respawn result: $ok"
        $lastTunnelRelaunch = Get-Date
      }
    }
  }

  Start-Sleep -Seconds $PollIntervalSec
}

Wd-Log "bridge-watchdog exit pid=$PID (duration reached)"
$script:LockHandle.Close()
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
