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
$Host.UI.RawUI.WindowTitle = "Doxed Provider-Free Recovery"
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -BridgePort 7810

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

Remove-Item -LiteralPath (Join-Path $repoRoot ".home-stack-user-stopped") -Force -ErrorAction SilentlyContinue

$bridgeScript = Join-Path $scriptDir "ensure-home-bridge.ps1"
$bridgeArgString = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$bridgeScript`" -Port 7810 -Force -Quiet"
$bridgeOwner = Start-Process `
  -FilePath "powershell.exe" `
  -ArgumentList $bridgeArgString `
  -WorkingDirectory $repoRoot `
  -WindowStyle Hidden `
  -PassThru

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

& (Join-Path $scriptDir "home-stack-start-everything.ps1") `
  -BotPort $BotPort `
  -AnalyzerPort $AnalyzerPort `
  -SkipBridgeRestart `
  -NoWait
