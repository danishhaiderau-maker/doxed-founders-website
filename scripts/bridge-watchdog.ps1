# Bridge :7810 auto-respawn watchdog. Polls /health every ~10s and relaunches the bridge
# (via ensure-home-bridge.ps1) when it is down. Lock-protected so only one instance runs
# at a time — safe to invoke from the scheduled task AND a detached hidden loop together.
#
# Survives terminal closure because it is launched -WindowStyle Hidden via Start-Process
# (detached from any console). The register-bridge-watchdog.ps1 scheduled task
# (DoxxedBridgeWatch) re-invokes this script every 1 min so coverage survives logoff/reboot.
param(
  [int]$BridgePort = 7810,
  [int]$PollIntervalSec = 10,
  [int]$DurationMin = 5,
  [int]$RelaunchCooldownSec = 30,
  [switch]$Quiet
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logFile = Join-Path $repoRoot ".home-bridge-watchdog.log"
$lockFile = Join-Path $repoRoot ".home-bridge-watchdog.lock"
$pidFile = Join-Path $repoRoot ".home-bridge-watchdog.pid"

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
  # Lock held by another instance — confirm it is actually alive (not a stale handle
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
  # Stale lock — force clear and retry once.
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

$deadline = (Get-Date).AddMinutes($DurationMin)
$lastRelaunch = [datetime]::MinValue
$ticks = 0

Wd-Log "bridge-watchdog start pid=$PID port=:$BridgePort poll=${PollIntervalSec}s duration=${DurationMin}m"

while ((Get-Date) -lt $deadline) {
  $ticks++
  if (Test-BridgeUp) {
    # Bridge healthy — nothing to log on every tick (would flood); log a heartbeat every ~2 min.
    if (($ticks % 12) -eq 0) { Wd-Log "tick #$ticks bridge UP" }
  } else {
    $since = ((Get-Date) - $lastRelaunch).TotalSeconds
    if ($since -lt $RelaunchCooldownSec) {
      Wd-Log "bridge DOWN but cooldown $([int]$since)s/${RelaunchCooldownSec}s — waiting"
    } else {
      Wd-Log "bridge DOWN on :$BridgePort — relaunching via ensure-home-bridge.ps1"
      try {
        $p = Start-Process -FilePath "powershell.exe" -ArgumentList @(
          "-NoProfile","-ExecutionPolicy","Bypass","-File",
          (Join-Path $scriptDir "ensure-home-bridge.ps1"),"-Quiet"
        ) -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
        if ($p) { Wd-Log "ensure-home-bridge launched pid=$($p.Id)" }
      } catch {
        Wd-Log "relaunch FAILED: $($_.Exception.Message)"
      }
      $lastRelaunch = Get-Date
    }
  }
  Start-Sleep -Seconds $PollIntervalSec
}

Wd-Log "bridge-watchdog exit pid=$PID (duration reached)"
$script:LockHandle.Close()
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
