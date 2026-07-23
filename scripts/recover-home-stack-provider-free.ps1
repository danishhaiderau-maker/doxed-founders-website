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
    if (-not (Stop-ProcessIdFast $ownerPid)) {
      throw "Could not terminate verified lock owner pid=$ownerPid for $LockPath."
    }
    Log "Stopped verified lock owner pid=$ownerPid executable=$name lock=$LockPath"
    $stopped += $ownerPid
  }
  if ($stopped.Count -gt 0) { Start-Sleep -Seconds 2 }
  return $stopped
}

function Stop-ExactWindowTree([string]$Title) {
  & taskkill.exe /F /T /FI "WINDOWTITLE eq $Title" 2>$null | Out-Null
}

# Close only the supported home-stack launch windows. Exact title filters
# avoid touching unrelated PowerShell/cmd processes or any manual application.
foreach ($title in @(
  "Doxed Start Everything",
  "Doxed Bot :$BotPort",
  "Doxed Analyzer :$AnalyzerPort",
  "Doxed Home Bridge :7810"
)) {
  Stop-ExactWindowTree $title
}
Log "Requested exact-title cleanup for supported home-stack windows"

Start-Sleep -Seconds 2

# New startup owners record their PID before any potentially slow work. When a
# service is offline, stop only that recorded PowerShell owner with executable
# and creation-time verification; never enumerate or terminate unrelated shells.
if (-not (Test-PortOpen $BotPort)) {
  Stop-RecordedProcess `
    (Join-Path $repoRoot ".home-bot-starter.pid") `
    @("powershell", "pwsh") `
    10 | Out-Null
}
if (-not (Test-PortOpen $AnalyzerPort)) {
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

foreach ($requiredLock in @(
  ".home-bot-start.lock",
  ".home-analyzer-start.lock"
)) {
  $requiredPath = Join-Path $repoRoot $requiredLock
  if (-not (Test-LockAvailable $requiredPath)) {
    throw "$requiredLock is still owned by an unverified legacy starter; refusing an unsafe duplicate launch."
  }
}
Log "Bot and analyzer startup locks are available"

Remove-Item -LiteralPath (Join-Path $repoRoot ".home-stack-user-stopped") -Force -ErrorAction SilentlyContinue

$bridgeScript = Join-Path $scriptDir "ensure-home-bridge.ps1"
$bridgeArgString = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$bridgeScript`" -Port 7810 -Force -Quiet"
$bridgeOwner = Start-Process `
  -FilePath "powershell.exe" `
  -ArgumentList $bridgeArgString `
  -WorkingDirectory $repoRoot `
  -WindowStyle Hidden `
  -PassThru
Log "Launched bridge recovery owner pid=$($bridgeOwner.Id)"

$bridgeDeadline = (Get-Date).AddSeconds(60)
$bridgeHealthy = $false
while ((Get-Date) -lt $bridgeDeadline) {
  try {
    $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:7810/health")
    $request.Method = "GET"
    $request.Timeout = 7000
    $request.ReadWriteTimeout = 7000
    $response = $request.GetResponse()
    $bridgeHealthy = ($response.StatusCode -eq 200)
    $response.Close()
  } catch {
    $bridgeHealthy = $false
  }
  if ($bridgeHealthy) { break }
  if ($bridgeOwner.HasExited) { break }
  Start-Sleep -Seconds 2
}

if (-not $bridgeHealthy) {
  throw "Bridge recovery failed; refusing to start dependent services."
}
Log "Bridge health confirmed"

Log "Starting bot/analyzer/tunnel recovery"
& (Join-Path $scriptDir "home-stack-start-everything.ps1") `
  -BotPort $BotPort `
  -AnalyzerPort $AnalyzerPort `
  -SkipBridgeRestart `
  -NoWait
Log "Provider-free recovery finished"
