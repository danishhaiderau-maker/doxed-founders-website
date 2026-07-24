# Provider-free elevated recovery for a Windows host whose process/WMI
# providers are stalled. This preserves all paper/research data and does not
# touch the website relay state, tile configuration, prompts, or sizing.
param(
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9001
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$recoveryLog = Join-Path $repoRoot ".home-provider-free-recovery.log"
$Host.UI.RawUI.WindowTitle = "Doxed Provider-Free Recovery"
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -BridgePort 7810

function Log([string]$Message) {
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') recovery[$PID] $Message"
  Add-Content -LiteralPath $recoveryLog -Value $line -ErrorAction SilentlyContinue
  Write-Host $Message
}

trap {
  Log "FAILED: $($_.Exception.Message)"
  throw
}

function Test-LockAvailable([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return $true }
  $handle = $null
  try {
    $handle = [System.IO.File]::Open(
      $Path,
      [System.IO.FileMode]::Open,
      [System.IO.FileAccess]::ReadWrite,
      [System.IO.FileShare]::None
    )
    return $true
  } catch {
    return $false
  } finally {
    if ($handle) { $handle.Dispose() }
  }
}

function Stop-VerifiedLockOwners([string]$LockPath, [int]$ServicePort) {
  $stopped = @()
  if ((Test-PortOpen $ServicePort) -or -not (Test-Path -LiteralPath $LockPath)) {
    return $stopped
  }
  foreach ($ownerPid in @(Get-FileLockOwnerProcessIdsFast $LockPath)) {
    if ($ownerPid -le 0 -or $ownerPid -eq $PID) { continue }
    $name = Get-ProcessExecutableNameFast $ownerPid
    if (@("powershell", "pwsh") -notcontains $name) {
      throw "Lock owner pid=$ownerPid executable=$name is not an approved startup host; refusing termination."
    }
    $stoppedDirectly = Stop-ProcessIdFast $ownerPid
    $stoppedByRestartManager = $false
    if (-not $stoppedDirectly) {
      # Restart Manager registered this exact PID plus its creation time. It
      # can close a same-user process across a UAC integrity boundary without
      # enumerating or terminating unrelated PowerShell processes.
      $stoppedByRestartManager = Stop-ExactProcessViaRestartManagerFast $ownerPid
    }
    if (-not $stoppedDirectly -and -not $stoppedByRestartManager) {
      throw "Could not terminate verified lock owner pid=$ownerPid for $LockPath."
    }
    $method = if ($stoppedDirectly) { "native-process" } else { "restart-manager" }
    Log "Stopped verified lock owner pid=$ownerPid executable=$name method=$method lock=$LockPath"
    $stopped += $ownerPid
  }
  if ($stopped.Count -gt 0) {
    # TerminateProcess/RmShutdown can acknowledge before Windows closes the
    # final file handle. Wait for both process exit and lock release so the
    # clean replacement owner never races the legacy starter.
    $releaseDeadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $releaseDeadline) {
      $ownerAlive = @($stopped | Where-Object { Test-ProcessIdAliveFast $_ }).Count -gt 0
      if (-not $ownerAlive -and (Test-LockAvailable $LockPath)) { break }
      Start-Sleep -Milliseconds 250
    }
    $ownerAlive = @($stopped | Where-Object { Test-ProcessIdAliveFast $_ }).Count -gt 0
    if ($ownerAlive -or -not (Test-LockAvailable $LockPath)) {
      throw "Verified owner termination was acknowledged, but $LockPath did not release within 20 seconds."
    }
  }
  return $stopped
}

Set-HomeStackUserStopped
Log "Starting provider-free cleanup with watchdog respawn suppressed"

# A scheduled watchdog can be alive but wedged before it acquires its own lock.
# With the user-stopped sentinel already present, stop only watchdog processes
# whose native command line names this repo's exact script.
$watchdogScript = Join-Path $scriptDir "bridge-watchdog.ps1"
$watchdogNeedle = [regex]::Escape($watchdogScript)
foreach ($executable in @("powershell.exe", "pwsh.exe")) {
  foreach ($watchdogPid in @(Get-ProcessIdsByExecutableNameFast $executable)) {
    if ($watchdogPid -le 0 -or $watchdogPid -eq $PID) { continue }
    $commandLine = Get-ProcessCommandLineFast $watchdogPid
    if (-not $commandLine -or $commandLine -notmatch "(?i)$watchdogNeedle") { continue }
    $stopped = Stop-ProcessIdFast $watchdogPid
    if (-not $stopped) {
      $stopped = Stop-ExactProcessViaRestartManagerFast $watchdogPid
    }
    $deadline = (Get-Date).AddSeconds(10)
    while ((Test-ProcessIdAliveFast $watchdogPid) -and (Get-Date) -lt $deadline) {
      Start-Sleep -Milliseconds 200
    }
    if (Test-ProcessIdAliveFast $watchdogPid) {
      # The sentinel is already present and bridge-watchdog rechecks it on
      # every relaunch path. A higher-integrity scheduled owner may refuse
      # termination, but it cannot recover components while the marker is
      # present; log it and continue restoring the updated supervisor.
      Log "Exact watchdog owner pid=$watchdogPid remained alive; user-stopped sentinel keeps it in stand-down"
      continue
    }
    Log "Stopped exact watchdog owner pid=$watchdogPid"
  }
}

# New startup owners record their PID before any potentially slow work. When a
# service is offline, stop only that recorded PowerShell owner with executable
# and creation-time verification; never enumerate or terminate unrelated shells.
$botNeedsStart = -not (Test-PortOpen $BotPort)
$analyzerNeedsStart = -not (Test-PortOpen $AnalyzerPort)
if ($botNeedsStart) {
  Stop-RecordedProcess `
    (Join-Path $repoRoot ".home-bot-starter.pid") `
    @("powershell", "pwsh") `
    10 | Out-Null
}
if ($analyzerNeedsStart) {
  Stop-RecordedProcess `
    (Join-Path $repoRoot ".home-analyzer-starter.pid") `
    @("powershell", "pwsh") `
    10 | Out-Null
}

# Legacy starters created before owner-PID tracking can still hold an exclusive
# lock. Windows Restart Manager proves the exact file owner; executable
# validation plus an offline service port keeps this cleanup tightly scoped.
Stop-VerifiedLockOwners `
  (Join-Path $repoRoot ".home-bot-start.lock") `
  $BotPort | Out-Null
Stop-VerifiedLockOwners `
  (Join-Path $repoRoot ".home-analyzer-start.lock") `
  $AnalyzerPort | Out-Null

# A full provider-free recovery owns supervisor replacement too. Stop only the
# timestamp-verified recorded owner; bridge-only recovery never does this.
Stop-RecordedProcess `
  (Join-Path $repoRoot ".home-stack-supervisor.pid") `
  @("powershell", "pwsh", "cmd") `
  10 | Out-Null

# File locks are released by the terminated owners. Removing their now-stale
# pathnames lets the replacement owners acquire a clean exclusive handle.
foreach ($lockName in @(
  ".home-start-everything.lock",
  ".home-bot-start.lock",
  ".home-analyzer-start.lock",
  ".home-stack-supervisor.lock",
  ".home-bridge-watchdog.lock"
)) {
  Remove-Item -LiteralPath (Join-Path $repoRoot $lockName) -Force -ErrorAction SilentlyContinue
}

$requiredLocks = @()
if ($botNeedsStart) { $requiredLocks += ".home-bot-start.lock" }
if ($analyzerNeedsStart) { $requiredLocks += ".home-analyzer-start.lock" }
foreach ($requiredLock in $requiredLocks) {
  $requiredPath = Join-Path $repoRoot $requiredLock
  if (-not (Test-LockAvailable $requiredPath)) {
    throw "$requiredLock is still owned by an unverified legacy starter; refusing an unsafe duplicate launch."
  }
}
Log "Required offline-service startup locks are available"

function Test-BridgeHealthOnce {
  try {
    $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:7810/health")
    $request.Method = "GET"
    $request.Timeout = 7000
    $request.ReadWriteTimeout = 7000
    $response = $request.GetResponse()
    $healthy = ($response.StatusCode -eq 200)
    $response.Close()
    return $healthy
  } catch {
    return $false
  }
}

$bridgeHealthy = Test-BridgeHealthOnce
if ($bridgeHealthy) {
  Log "Existing bridge health confirmed; preserving its owner"
} else {
  $bridgeScript = Join-Path $scriptDir "ensure-home-bridge.ps1"
  Start-HiddenPs1 $bridgeScript @("-Port", "7810", "-Force", "-Quiet")
  Log "Queued bridge recovery owner"
}

$bridgeDeadline = (Get-Date).AddSeconds(60)
while (-not $bridgeHealthy -and (Get-Date) -lt $bridgeDeadline) {
  $bridgeHealthy = Test-BridgeHealthOnce
  if ($bridgeHealthy) { break }
  Start-Sleep -Seconds 2
}

if (-not $bridgeHealthy) {
  throw "Bridge recovery failed; refusing to start dependent services."
}
Log "Bridge health confirmed"

Start-HiddenPs1 (Join-Path $scriptDir "home-stack-start-everything.ps1") @(
  "-BotPort", "$BotPort",
  "-AnalyzerPort", "$AnalyzerPort",
  "-SkipBridgeRestart",
  "-NoWait"
)
Log "Provider-free bot/analyzer/tunnel recovery queued"
