# Load home-bot.env and run the research analyzer (30-min loop, or --Once).
# Embedded Flask research dashboard on :9001 (see research/research_dashboard.py).
param([switch]$Once)

$Host.UI.RawUI.WindowTitle = if ($Once) { "Doxed Analyzer (once)" } else { "Doxed Analyzer" }
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"

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

Set-Location $agentDir
Get-Content $vaultEnv | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    Set-Item -Path ("env:" + $matches[1].Trim()) -Value $matches[2].Trim()
  }
}

$env:RESEARCH_DASHBOARD_BIND_HOST = "0.0.0.0"
$env:RESEARCH_DASHBOARD_PORT = "9001"
$env:RESEARCH_DASHBOARD_PUBLIC_URL = "http://10.0.0.102:9001/"

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
  if ($exitCode -ne 0) {
    Write-Host "Analyzer exited with code $exitCode" -ForegroundColor Yellow
  } elseif ($Once) {
    Write-Host "Analyzer single pass complete." -ForegroundColor Green
  } else {
    Write-Host "Analyzer loop ended." -ForegroundColor Yellow
  }
  Wait-ForKey
}
