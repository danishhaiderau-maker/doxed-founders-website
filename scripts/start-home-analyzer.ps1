# Run the read-only desktop analyzer (30-min loop, or --Once) against the
# canonical Fly data mirror. It binds to loopback and receives no trading,
# exchange, Fly, Railway, or AI credentials.
param([switch]$Once, [switch]$NoWait, [switch]$Restart, [int]$Port = 0,
      [string]$ShadowScenarioConfig = '')

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

function Test-AnalyzerEngineAlive {
  $pidFile = Join-Path $repoRoot ".home-analyzer.pid"
  if (-not (Test-Path -LiteralPath $pidFile)) { return $false }
  try {
    $enginePid = [int](Get-Content -LiteralPath $pidFile -Raw)
    if ($enginePid -le 0) { return $false }
    if (-not (Test-ProcessAliveFast -ProcessId $enginePid)) { return $false }
    $commandLine = [string](Get-ProcessCommandLineFast -ProcessId $enginePid)
    return [bool]($commandLine -match '(^|[\\/\s])analyzer_research_engine_v62\.py(["''\s]|$)')
  } catch {
    return $false
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

# Explicit durable nonsecret opt-in; never load model economics from the vault.
# An absent config preserves the disabled default or a validated inherited pin.
. (Join-Path $scriptDir 'analyzer-scenario-launch-config.ps1')
if (-not $ShadowScenarioConfig) {
  $defaultScenarioConfig = Join-Path $repoRoot 'config\analyzer-shadow-scenario.launch.json'
  if (Test-Path -LiteralPath $defaultScenarioConfig) { $ShadowScenarioConfig = $defaultScenarioConfig }
}
$scenarioLaunch = Get-AnalyzerScenarioLaunchConfig -ConfigPath $ShadowScenarioConfig `
  -ModelFile ([string]$env:BTC_ANALYZER_SHADOW_MODEL_FILE) -ModelSha256 ([string]$env:BTC_ANALYZER_SHADOW_MODEL_SHA256)
if ($scenarioLaunch.enabled) {
  $env:BTC_ANALYZER_SHADOW_MODEL_FILE = [string]$scenarioLaunch.model_file
  $env:BTC_ANALYZER_SHADOW_MODEL_SHA256 = [string]$scenarioLaunch.model_sha256
}

$env:RESEARCH_DASHBOARD_BIND_HOST = "127.0.0.1"
$env:RESEARCH_DASHBOARD_PORT = "$AnalyzerPort"
$env:RESEARCH_DASHBOARD_PUBLIC_URL = "http://127.0.0.1:$AnalyzerPort/"
$env:ANALYZER_EMBEDDED_DASHBOARD = "0"
$env:BTC_AGENT_DATA_DIR = $analyzerDataDir
$env:PLATFORM_RELAY_EVIDENCE_FILE = Join-Path $analyzerDataDir "relay_lifecycle_evidence_v1.json"
$sourceRevision = (& git -C $repoRoot rev-parse HEAD 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceRevision -notmatch '^[0-9a-fA-F]{40}$') {
  throw "Analyzer source revision could not be resolved to a full Git SHA."
}
# A Git SHA is truthful executable provenance only when the analyzer and its
# import surface match that commit.  Refuse tracked edits or untracked Python
# modules in the analyzer dependency roots instead of publishing mixed code
# under the clean HEAD marker.
$analyzerProvenancePaths = @(
  "scripts/start-home-analyzer.ps1",
  "scripts/analyzer-scenario-launch-config.ps1",
  "scripts/analyzer-scenario-launch-config.py",
  "services/btc-conservative-agent/analyzer_research_engine_v62.py",
  "services/btc-conservative-agent/research_dashboard.py",
  "services/btc-conservative-agent/research_v3_store.py",
  "services/btc-conservative-agent/dynamic_policy_analyzer.py",
  "services/btc-conservative-agent/combo_pathway_config.py",
  "services/btc-conservative-agent/research"
)
$dirtyAnalyzerSources = @(
  & git -C $repoRoot status --porcelain=v1 --untracked-files=all -- @analyzerProvenancePaths 2>$null |
    Where-Object {
      $path = ([string]$_).Substring([Math]::Min(3, ([string]$_).Length)).Trim('"')
      $path -match '\.py$' -or $path -eq 'scripts/start-home-analyzer.ps1' -or $path -eq 'scripts/analyzer-scenario-launch-config.ps1'
    }
)
if ($LASTEXITCODE -ne 0) {
  throw "Analyzer executable provenance could not be verified against Git HEAD."
}
if ($dirtyAnalyzerSources.Count -gt 0) {
  throw (
    "REFUSED: analyzer executable provenance is dirty relative to $sourceRevision; " +
    "commit/revert the imported analyzer source or launch from a clean exact-revision worktree."
  )
}
# This revision identifies the analyzer code that is actually executing.  The
# deployed Fly/data revision is recorded independently by the canonical sync
# receipt and report source-data provenance.  Stamping local HEAD as the old
# Fly revision makes a new analyzer binary falsely appear to be old code.
$env:SOURCE_GIT_REV = $sourceRevision.ToLowerInvariant()
# Pin report discovery as well as raw-data discovery. The bridge and desktop
# launcher are long-lived and can otherwise pass an obsolete report directory
# into a freshly restarted dashboard.
$analyzerReportDir = Join-Path $analyzerDataDir "analyzer"
New-Item -ItemType Directory -Path $analyzerReportDir -Force | Out-Null
$env:BTC_AGENT_REPORT_DIR = $analyzerReportDir

. (Join-Path $scriptDir "home-stack-common.ps1") -AnalyzerPort $AnalyzerPort -BridgePort 7810
. (Join-Path $scriptDir "home-stack-health.ps1")

function Get-CanonicalAnalyzerEnginePids([int]$P) {
  $ownedMarker = "--owner-port=$P"
  $owned = @()
  $legacy = @()
  foreach ($proc in @(Get-Process -Name "python*" -ErrorAction SilentlyContinue)) {
    $commandLine = [string](Get-ProcessCommandLineFast -ProcessId $proc.Id)
    if ($commandLine -notmatch '(^|[\\/\s])analyzer_research_engine_v62\.py(["''\s]|$)') { continue }
    if ($commandLine.Contains($ownedMarker)) { $owned += [int]$proc.Id }
    elseif ($P -eq 9001) { $legacy += [int]$proc.Id }
  }
  # Legacy launchers did not put the port in argv. Treat every legacy engine as
  # a possible :9001 owner and fail closed instead of spawning over it.
  return @(($owned + $legacy) | Sort-Object -Unique)
}

$discoveredEnginePids = @(Get-CanonicalAnalyzerEnginePids $AnalyzerPort)
if ($discoveredEnginePids.Count -gt 1) {
  Write-Host (
    "REFUSED: multiple analyzer engines already exist for :$AnalyzerPort " +
    "(PIDs $($discoveredEnginePids -join ', ')). No new engine was started."
  ) -ForegroundColor Red
  Write-Host "Stop/reconcile the incumbents explicitly, then start again." -ForegroundColor Yellow
  if ($lockHandle) { $lockHandle.Dispose() }
  Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
  if (-not $NoWait) { Wait-ForKey }
  exit 2
}
$expectedRevisionMarker = "--source-revision=$($sourceRevision.ToLowerInvariant())"
if ($Restart -and $discoveredEnginePids.Count -eq 1 -and -not $Once) {
  $incumbentPid = [int]$discoveredEnginePids[0]
  $incumbentCommandLine = [string](Get-ProcessCommandLineFast -ProcessId $incumbentPid)
  if (-not $incumbentCommandLine.Contains("--owner-port=$AnalyzerPort") -or
      -not $incumbentCommandLine.Contains($expectedRevisionMarker)) {
    throw "REFUSED: analyzer restart target does not match the owned port and source revision."
  }
  Write-Host "Restarting verified analyzer engine PID $incumbentPid; dashboard remains available." -ForegroundColor Yellow
  Assert-AnalyzerScenarioLaunchConfig -Receipt $scenarioLaunch
  Stop-Process -Id $incumbentPid -Force -ErrorAction Stop
  Remove-Item -LiteralPath (Join-Path $repoRoot ".home-analyzer.pid") -Force -ErrorAction SilentlyContinue
  $discoveredEnginePids = @()
}
if ($discoveredEnginePids.Count -eq 1 -and -not $Once) {
  $incumbentPid = [int]$discoveredEnginePids[0]
  $incumbentCommandLine = [string](Get-ProcessCommandLineFast -ProcessId $incumbentPid)
  if (-not $incumbentCommandLine.Contains($expectedRevisionMarker)) {
    Write-Host (
      "Analyzer engine PID $incumbentPid belongs to another or unproven source revision; " +
      "replacing the engine while preserving the independent dashboard."
    ) -ForegroundColor Yellow
    Assert-AnalyzerScenarioLaunchConfig -Receipt $scenarioLaunch
    Stop-Process -Id $incumbentPid -Force -ErrorAction SilentlyContinue
    $pidFile = Join-Path $repoRoot ".home-analyzer.pid"
    if (Test-Path -LiteralPath $pidFile) {
      Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
    $discoveredEnginePids = @()
  }
}
if ($discoveredEnginePids.Count -eq 1 -and -not $Once) {
  Set-Content -Path (Join-Path $repoRoot ".home-analyzer.pid") `
    -Value "$($discoveredEnginePids[0])" -NoNewline -Encoding UTF8
}

# Avoid duplicate on THIS port only (local lab :9001 may run in parallel on another port).
if (Test-PortOpen $AnalyzerPort) {
  $listenerPids = @(Get-AnalyzerListenerPids $AnalyzerPort)
  $dashboardAlive = (Test-AnalyzerAlive)
  $dashboardReady = (Test-AnalyzerHealthy)
  $engineAlive = (Test-AnalyzerEngineAlive)
  if ($dashboardAlive -and $listenerPids.Count -eq 1 -and $engineAlive) {
    $state = if ($dashboardReady) { "ready" } else { "alive; generation readiness pending" }
    Write-Host "Analyzer dashboard is $state and the research engine is alive on :$AnalyzerPort - not starting a duplicate." -ForegroundColor Yellow
    if ($lockHandle) { $lockHandle.Dispose() }
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    if (-not $NoWait) { Wait-ForKey }
    exit 0
  }
  if ($dashboardAlive -and $listenerPids.Count -eq 1 -and -not $engineAlive) {
    Write-Host "Analyzer dashboard is alive but the research engine is absent - preserving the dashboard and restarting collection." -ForegroundColor Yellow
  } elseif ($listenerPids.Count -gt 1) {
    Write-Host "Port $AnalyzerPort has $($listenerPids.Count) listeners - replacing them with one loopback dashboard owner..." -ForegroundColor Yellow
  } else {
    Write-Host "Port $AnalyzerPort has a stale dashboard listener - clearing and starting the analyzer..." -ForegroundColor Yellow
  }
  if (-not ($dashboardAlive -and $listenerPids.Count -eq 1)) {
    # The engine and dashboard have separate owners. A stale HTTP listener must
    # never terminate the healthy analyzer engine recorded in
    # .home-analyzer.pid; doing so leaves reports permanently stale until a
    # later supervisor pass. The bounded listener cleanup below owns the actual
    # process stop, so this block only clears the dashboard's stale PID receipt.
    $dashboardPidFile = Join-Path $repoRoot ".home-analyzer-dashboard.pid"
    if (Test-Path -LiteralPath $dashboardPidFile) {
      Remove-Item -LiteralPath $dashboardPidFile -Force -ErrorAction SilentlyContinue
    }
    Assert-AnalyzerScenarioLaunchConfig -Receipt $scenarioLaunch
    Stop-ListenPortFast $AnalyzerPort | Out-Null
    Start-Sleep -Seconds 2
  }
}

# Publish the read-only dashboard before the heavy analyzer import. On this PC
# pandas/scikit imports can take several minutes when the bot is busy; making
# Flask wait behind those imports caused blank pages and false watchdog kills.
if (-not $Once -and -not (Test-PortOpen $AnalyzerPort)) {
  Assert-AnalyzerScenarioLaunchConfig -Receipt $scenarioLaunch
  $dashboardProc = Start-Process -FilePath "python" `
    -ArgumentList @("research_dashboard.py", "--standalone") `
    -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
  if ($dashboardProc -and $dashboardProc.Id -gt 0) {
    Set-Content -Path (Join-Path $repoRoot ".home-analyzer-dashboard.pid") `
      -Value "$($dashboardProc.Id)" -NoNewline -Encoding UTF8
  }
}

if ($discoveredEnginePids.Count -eq 1 -and -not $Once) {
  Write-Host "Existing analyzer engine PID $($discoveredEnginePids[0]) retained; no duplicate engine started." -ForegroundColor Yellow
  if ($lockHandle) { $lockHandle.Dispose() }
  Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
  if (-not $NoWait) { Wait-ForKey }
  exit 0
}

Write-Host "IMPORTANT: Analyzer reads CSV/JSONL from THIS folder only:"
Write-Host "  $analyzerDataDir"
Write-Host "Research dashboard (Flask, this PC only): http://127.0.0.1:$AnalyzerPort/"
Write-Host ""
Write-Host "Mode: $(if ($Once) { 'single pass (--once)' } else { 'continuous loop (every 30 min)' })"
Write-Host ""

$pyArgs = @("analyzer_research_engine_v62.py")
if ($Once) { $pyArgs += "--once" }
$pyArgs += "--owner-port=$AnalyzerPort"
$pyArgs += $expectedRevisionMarker

if ($NoWait) {
  Write-Host "Starting analyzer detached on :$AnalyzerPort ..."
  Assert-AnalyzerScenarioLaunchConfig -Receipt $scenarioLaunch
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
  # Keep the exclusive start claim until the child PID is durably published.
  # A concurrent Start click must observe either this lock or the new owner.
  if ($lockHandle) {
    try { $lockHandle.Dispose() } catch { }
    $lockHandle = $null
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
  }
  exit 0
}

$exitCode = 0
try {
  Write-Host "Starting analyzer in $agentDir ..."
  Write-Host ""
  Assert-AnalyzerScenarioLaunchConfig -Receipt $scenarioLaunch
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
