# Local collection analyzer — frozen port 9500, reads CSVs from local-collection-data.
param([switch]$Once, [switch]$NoWait)

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "local-collection-config.ps1")

$AnalyzerPort = $LocalCollection.AnalyzerPort
$Host.UI.RawUI.WindowTitle = if ($Once) { "Local Collection Analyzer (once)" } else { "Local Collection Analyzer :$AnalyzerPort" }
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
$lockFile = Join-Path $repoRoot ".local-collection-analyzer.lock"

function Test-PortOpen([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $async = $c.ConnectAsync("127.0.0.1", $P)
    if (-not $async.Wait(1200)) { return $false }
    $c.Close()
    return $true
  } catch { return $false }
}

function Wait-ForKey {
  Write-Host ""
  Write-Host "--- Press Enter to close ---" -ForegroundColor Cyan
  try { Read-Host } catch { while ($true) { Start-Sleep -Seconds 3600 } }
}

if (-not (Test-Path $vaultEnv)) {
  Write-Host "Missing $vaultEnv" -ForegroundColor Red
  Wait-ForKey
  exit 1
}

Get-Content $vaultEnv | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    Set-Item -Path ("env:" + $matches[1].Trim()) -Value $matches[2].Trim()
  }
}

$env:RESEARCH_DASHBOARD_BIND_HOST = "0.0.0.0"
$env:RESEARCH_DASHBOARD_PORT = "$AnalyzerPort"
$env:RESEARCH_DASHBOARD_PUBLIC_URL = "http://127.0.0.1:$AnalyzerPort/"
$env:BTC_AGENT_DATA_DIR = $LocalCollection.DataDir
$env:LOCAL_COLLECTION_MODE = "1"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $LocalCollection.BotPort -AnalyzerPort $AnalyzerPort

if (Test-PortOpen $AnalyzerPort) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$AnalyzerPort/api/status" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) {
      Write-Host "Analyzer dashboard healthy on :$AnalyzerPort - not starting a duplicate." -ForegroundColor Yellow
      if (-not $NoWait) { Wait-ForKey }
      exit 0
    }
  } catch {
    Write-Host "Port $AnalyzerPort open but unhealthy - clearing stale listener..." -ForegroundColor Yellow
    Stop-ListenPortFast $AnalyzerPort | Out-Null
    Start-Sleep -Seconds 2
  }
}

$lockHandle = $null
try {
  $lockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
  Write-Host "Analyzer start already in progress." -ForegroundColor Yellow
  if (-not $NoWait) { Wait-ForKey }
  exit 0
}

Set-Location $LocalCollection.AgentDir
Write-Host "=== LOCAL COLLECTION ANALYZER (frozen :$AnalyzerPort) ===" -ForegroundColor Cyan
Write-Host "Data: $($LocalCollection.DataDir)"
Write-Host "Dashboard: http://127.0.0.1:$AnalyzerPort/"
Write-Host ""

$pyArgs = @("research\analyzer_research_engine_v62.py")
if ($Once) { $pyArgs += "--once" }

$exitCode = 0
try {
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
  if (-not $NoWait) { Wait-ForKey }
}
exit $exitCode
