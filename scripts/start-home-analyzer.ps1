# Run the read-only desktop analyzer (30-min loop, or --Once) against the
# canonical Fly data mirror. It binds to loopback and receives no trading,
# exchange, Fly, Railway, or AI credentials.
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
. (Join-Path $scriptDir "fly-data-paths.ps1")
$flyCanonicalLock = Join-Path $repoRoot "config\fly-canonical.lock.json"
$analyzerDataDir = if (Test-Path -LiteralPath $flyCanonicalLock) {
  Get-DoxxedFlyMirrorDir
} else {
  $agentDir
}
$vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
$machineStateBase = if ($env:LOCALAPPDATA) {
  $env:LOCALAPPDATA
} else {
  [System.IO.Path]::GetTempPath()
}
$machineLockDir = Join-Path $machineStateBase "DoxxedCrypto\locks"
New-Item -ItemType Directory -Path $machineLockDir -Force | Out-Null
$lockFile = Join-Path $machineLockDir "home-analyzer-start-$AnalyzerPort.lock"
$starterPidFile = Join-Path $repoRoot ".home-analyzer-starter.pid"

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

function Get-AnalyzerListenerPids([int]$P) {
  try {
    return @(
      Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction Stop |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { [int]$_ -gt 0 -and [int]$_ -ne 4 }
    )
  } catch {
    $matches = @(
      netstat -ano -p tcp 2>$null |
        Select-String -Pattern "^\s*TCP\s+\S+:$P\s+\S+\s+LISTENING\s+(\d+)\s*$"
    )
    return @(
      $matches |
        ForEach-Object { [int]$_.Matches[0].Groups[1].Value } |
        Sort-Object -Unique
    )
  }
}

function Wait-ForKey {
  Write-Host ""
  Write-Host "--- Console stays open so you can copy logs. Press Enter to close ---" -ForegroundColor Cyan
  try { Read-Host } catch { while ($true) { Start-Sleep -Seconds 3600 } }
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
Set-Content -LiteralPath $starterPidFile -Value "$PID" -NoNewline

Set-Location $agentDir
foreach ($secretName in @(
  "BITFINEX_API_KEY", "BITFINEX_API_SECRET", "DEEPSEEK_API_KEY",
  "DDOLLAR_GATE_TOKEN", "BOT_ADMIN_TOKEN", "BOT_CONTROL_SECRET",
  "FLY_API_TOKEN", "RAILWAY_TOKEN", "DATABASE_URL",
  "CREDENTIALS_ENCRYPTION_KEY"
)) {
  Remove-Item -LiteralPath ("Env:" + $secretName) -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $vaultEnv) {
  $allowedAnalyzerVars = @(
    "ANALYZER_INTERVAL_MINUTES",
    "ANALYZER_GRID_SWEEP_MAX_REPLAYS",
    "ANALYZER_SKIP_3D_SWEEP",
    "RESEARCH_API_CACHE_TTL_SEC",
    "RESEARCH_OPPORTUNITY_CACHE_TTL_SEC"
  )
  Get-Content -LiteralPath $vaultEnv | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
      $name = $matches[1].Trim()
      if ($name -in $allowedAnalyzerVars) {
        Set-Item -LiteralPath ("Env:" + $name) -Value $matches[2].Trim().Trim('"').Trim("'")
      }
    }
  }
}

$env:RESEARCH_DASHBOARD_BIND_HOST = "127.0.0.1"
$env:RESEARCH_DASHBOARD_PORT = "$AnalyzerPort"
$env:RESEARCH_DASHBOARD_PUBLIC_URL = "http://127.0.0.1:$AnalyzerPort/"
$env:ANALYZER_EMBEDDED_DASHBOARD = "0"
$env:BTC_AGENT_DATA_DIR = $analyzerDataDir
# Pin report discovery as well as raw-data discovery. The bridge and desktop
# launcher are long-lived and can otherwise pass an obsolete report directory
# into a freshly restarted dashboard.
$env:BTC_AGENT_REPORT_DIR = $agentDir

. (Join-Path $scriptDir "home-stack-common.ps1") -AnalyzerPort $AnalyzerPort -BridgePort 7810
. (Join-Path $scriptDir "home-stack-health.ps1")

# Avoid duplicate on THIS port only (local lab :9001 may run in parallel on another port).
if (Test-PortOpen $AnalyzerPort) {
  $listenerPids = @(Get-AnalyzerListenerPids $AnalyzerPort)
  if ((Test-AnalyzerHealthy) -and $listenerPids.Count -eq 1) {
    Write-Host "Analyzer healthy on :$AnalyzerPort (manifest + sync OK) - not starting a duplicate." -ForegroundColor Yellow
    if ($lockHandle) { $lockHandle.Dispose() }
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    if (-not $NoWait) { Wait-ForKey }
    exit 0
  }
  if ($listenerPids.Count -gt 1) {
    Write-Host "Port $AnalyzerPort has $($listenerPids.Count) listeners - replacing them with one loopback dashboard owner..." -ForegroundColor Yellow
  } else {
    Write-Host "Port $AnalyzerPort has a stale dashboard listener - clearing and starting the analyzer..." -ForegroundColor Yellow
  }
  $analyzerPidFile = Join-Path $repoRoot ".home-analyzer.pid"
  if (Test-Path -LiteralPath $analyzerPidFile) {
    try {
      $staleAnalyzerPid = [int](Get-Content -LiteralPath $analyzerPidFile -Raw)
      if ($staleAnalyzerPid -gt 0) {
        Stop-Process -Id $staleAnalyzerPid -Force -ErrorAction SilentlyContinue
      }
    } catch { }
    Remove-Item -LiteralPath $analyzerPidFile -Force -ErrorAction SilentlyContinue
  }
  Stop-ListenPortFast $AnalyzerPort | Out-Null
  Start-Sleep -Seconds 2
}

# Publish the read-only dashboard before the heavy analyzer import. On this PC
# pandas/scikit imports can take several minutes when the bot is busy; making
# Flask wait behind those imports caused blank pages and false watchdog kills.
if (-not $Once -and -not (Test-PortOpen $AnalyzerPort)) {
  $dashboardProc = Start-Process -FilePath "python" `
    -ArgumentList @("research_dashboard.py", "--standalone") `
    -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
  if ($dashboardProc -and $dashboardProc.Id -gt 0) {
    Set-Content -Path (Join-Path $repoRoot ".home-analyzer-dashboard.pid") `
      -Value "$($dashboardProc.Id)" -NoNewline -Encoding UTF8
  }
}

Write-Host "IMPORTANT: Analyzer reads CSV/JSONL from THIS folder only:"
Write-Host "  $analyzerDataDir"
Write-Host "Research dashboard (Flask, this PC only): http://127.0.0.1:$AnalyzerPort/"
Write-Host ""
Write-Host "Mode: $(if ($Once) { 'single pass (--once)' } else { 'continuous loop (every 30 min)' })"
Write-Host ""

$pyArgs = @("analyzer_research_engine_v62.py")
if ($Once) { $pyArgs += "--once" }

if ($NoWait) {
  Write-Host "Starting analyzer detached on :$AnalyzerPort ..."
  if ($lockHandle) {
    try { $lockHandle.Dispose() } catch { }
    $lockHandle = $null
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
  }
  $analyzerProc = Start-Process -FilePath "python" -ArgumentList $pyArgs -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
  # The retired analyzer-auto-restart monitor is intentionally not launched.
  # The explicit launcher/supervisor owns recovery; two independent restart
  # owners previously caused a crash-loop popup storm and divergent PID files.
  if ($analyzerProc -and $analyzerProc.Id -gt 0 -and -not $Once) {
    Set-Content -Path (Join-Path $repoRoot ".home-analyzer.pid") -Value "$($analyzerProc.Id)" -NoNewline -Encoding UTF8
    Remove-Item -LiteralPath (Join-Path $repoRoot ".home-analyzer-crash-monitor.pid") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $repoRoot ".home-analyzer-auto-restart.lock") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $repoRoot ".home-analyzer-auto-restart.heartbeat") -Force -ErrorAction SilentlyContinue
  }
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
