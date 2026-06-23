# Load home-bot.env and run the research analyzer (30-min loop, or --Once).
# Embedded Flask research dashboard on :9001 (see research/research_dashboard.py).
param([switch]$Once, [switch]$NoWait)

$Host.UI.RawUI.WindowTitle = if ($Once) { "Doxed Analyzer (once)" } else { "Doxed Analyzer" }
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
$lockHandle = $null
try {
  $lockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
  Write-Host "Another analyzer start is in progress — not starting a duplicate." -ForegroundColor Yellow
  if (-not $NoWait) { Wait-ForKey }
  exit 0
}

Set-Location $agentDir
Get-Content $vaultEnv | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    Set-Item -Path ("env:" + $matches[1].Trim()) -Value $matches[2].Trim()
  }
}

$env:RESEARCH_DASHBOARD_BIND_HOST = "0.0.0.0"
$env:RESEARCH_DASHBOARD_PORT = "9001"
$env:RESEARCH_DASHBOARD_PUBLIC_URL = "http://10.0.0.102:9001/"
$env:BTC_AGENT_DATA_DIR = $agentDir

# Avoid duplicate analyzer windows — require healthy HTTP or running python loop, not port alone.
$existing = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*analyzer_research_engine*" } |
  Select-Object -First 1
if ($existing) {
  Write-Host "Analyzer already running (pid $($existing.ProcessId)) — not starting a duplicate." -ForegroundColor Yellow
  if ($lockHandle) { $lockHandle.Dispose() }
  if (-not $NoWait) { Wait-ForKey }
  exit 0
}
if (Test-PortOpen 9001) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:9001/api/status" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) {
      Write-Host "Analyzer dashboard healthy on :9001 — not starting a duplicate." -ForegroundColor Yellow
      if ($lockHandle) { $lockHandle.Dispose() }
      if (-not $NoWait) { Wait-ForKey }
      exit 0
    }
  } catch {
    Write-Host "Port 9001 open but /api/status failed — clearing stale listener..." -ForegroundColor Yellow
    netstat -ano | Select-String ":9001\s" | ForEach-Object {
      if ("$_" -match '\s(\d+)\s*$') {
        Stop-Process -Id ([int]$matches[1]) -Force -ErrorAction SilentlyContinue
      }
    }
    Start-Sleep -Seconds 2
  }
}

Write-Host "IMPORTANT: Analyzer reads CSV/JSONL from THIS folder only:"
Write-Host "  $agentDir"
Write-Host "Research dashboard (Flask): http://127.0.0.1:9001/  LAN: http://10.0.0.102:9001/"
Write-Host ""
Write-Host "Mode: $(if ($Once) { 'single pass (--once)' } else { 'continuous loop (every 30 min)' })"
Write-Host ""

$pyArgs = @("research\analyzer_research_engine_v62.py")
if ($Once) { $pyArgs += "--once" }

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
  if (-not $NoWait) { Wait-ForKey }
}
