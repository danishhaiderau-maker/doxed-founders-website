# Load home-bot.env and run the research analyzer (30-min loop, or --Once).
# Embedded Flask research dashboard (:9500 global / :9001 local lab — see research/research_dashboard.py).
param([switch]$Once, [switch]$NoWait, [int]$Port = 0)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "home-stack-mode.ps1") -ErrorAction SilentlyContinue 2>$null
if ($Port -le 0) {
  $mode = Get-HomeStackMode
  $Port = $mode.AnalyzerPort
}
$AnalyzerPort = $Port

$Host.UI.RawUI.WindowTitle = if ($Once) { "Doxed Analyzer (once)" } else { "Doxed Analyzer :$AnalyzerPort" }
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
$lockFile = Join-Path $repoRoot ".home-analyzer-start.lock"

function Test-PortOpen([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $async = $c.ConnectAsync("127.0.0.1", $P)
    if (-not $async.Wait(1200)) { return $false }
    $c.Close()
    return $true
  } catch {
    return $false
  }
}

function Wait-ForKey {
  Write-Host ""
  Write-Host "--- Console stays open so you can copy logs. Press Enter to close ---" -ForegroundColor Cyan
  try { Read-Host } catch { while ($true) { Start-Sleep -Seconds 3600 } }
}

if (-not (Test-Path $vaultEnv)) {
  Write-Host "Missing $vaultEnv - run: npm run print:home-bot-env" -ForegroundColor Red
  Wait-ForKey
  exit 1
}

if (-not (Test-Path $agentDir)) {
  Write-Host "Agent dir not found: $agentDir" -ForegroundColor Red
  Wait-ForKey
  exit 1
}

# Stale lock from a crashed start blocks Agent Hub - clear when nothing is listening.
if (-not (Test-PortOpen $AnalyzerPort)) {
  Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}

$lockHandle = $null
try {
  $lockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
  Write-Host "Stale analyzer lock - clearing and retrying..." -ForegroundColor Yellow
  Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
  try {
    $lockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
  } catch {
    Write-Host "Another analyzer start is in progress - not starting a duplicate." -ForegroundColor Yellow
    if (-not $NoWait) { Wait-ForKey }
    exit 0
  }
}

Set-Location $agentDir
Get-Content $vaultEnv | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    Set-Item -Path ("env:" + $matches[1].Trim()) -Value $matches[2].Trim()
  }
}

$env:RESEARCH_DASHBOARD_BIND_HOST = "0.0.0.0"
$env:RESEARCH_DASHBOARD_PORT = "$AnalyzerPort"
$env:RESEARCH_DASHBOARD_PUBLIC_URL = "http://10.0.0.102:$AnalyzerPort/"
$env:BTC_AGENT_DATA_DIR = $agentDir

. (Join-Path $scriptDir "home-stack-common.ps1") -AnalyzerPort $AnalyzerPort
. (Join-Path $scriptDir "home-stack-health.ps1")

# Avoid duplicate on THIS port only (local lab :9001 may run in parallel on another port).
if (Test-PortOpen $AnalyzerPort) {
  if (Test-AnalyzerHealthy) {
    Write-Host "Analyzer healthy on :$AnalyzerPort (manifest + sync OK) - not starting a duplicate." -ForegroundColor Yellow
    if ($lockHandle) { $lockHandle.Dispose() }
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    if (-not $NoWait) { Wait-ForKey }
    exit 0
  }
  Write-Host "Port $AnalyzerPort has stale dashboard-only listener - clearing and starting full analyzer..." -ForegroundColor Yellow
  Stop-PythonMatching "research_dashboard" | Out-Null
  Stop-PythonMatching "analyzer_research_engine" | Out-Null
  Stop-ListenPortFast $AnalyzerPort | Out-Null
  Start-Sleep -Seconds 2
}

Write-Host "IMPORTANT: Analyzer reads CSV/JSONL from THIS folder only:"
Write-Host "  $agentDir"
Write-Host "Research dashboard (Flask): http://127.0.0.1:$AnalyzerPort/  LAN: http://10.0.0.102:$AnalyzerPort/"
Write-Host ""
Write-Host "Mode: $(if ($Once) { 'single pass (--once)' } else { 'continuous loop (every 30 min)' })"
Write-Host ""

$pyArgs = @("research\analyzer_research_engine_v62.py")
if ($Once) { $pyArgs += "--once" }

if ($NoWait) {
  Write-Host "Starting analyzer detached on :$AnalyzerPort ..."
  if ($lockHandle) {
    try { $lockHandle.Dispose() } catch { }
    $lockHandle = $null
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
  }
  Start-Process -FilePath "python" -ArgumentList $pyArgs -WorkingDirectory $agentDir -WindowStyle Normal
  exit 0
}

$exitCode = 0
try {
  Write-Host "Starting analyzer in $agentDir ..."
  Write-Host ""
  python @pyArgs
  if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE }
} catch {
  Write-Host "Analyzer error: $($_.Exception.Message)" -ForegroundColor Red
  $exitCode = 1
} finally {
  if ($lockHandle) {
    try { $lockHandle.Dispose() } catch { }
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
  }
  if ($exitCode -ne 0) {
    Write-Host "Analyzer exited with code $exitCode" -ForegroundColor Yellow
  } elseif ($Once) {
    Write-Host "Analyzer single pass complete." -ForegroundColor Green
  } else {
    Write-Host "Analyzer loop ended." -ForegroundColor Yellow
  }
  Wait-ForKey
}
