# Start BTC bot on home PC using vault/home-bot.env
param(
  [int]$Port = 7800,
  [string]$VaultEnv = "",
  [switch]$NoWait
)

$Host.UI.RawUI.WindowTitle = "Doxed Bot :$Port"
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

if (-not $VaultEnv) {
  $VaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
}

function Wait-ForKey {
  Write-Host ""
  Write-Host "--- Console stays open so you can copy logs. Press Enter to close ---" -ForegroundColor Cyan
  try { Read-Host } catch { while ($true) { Start-Sleep -Seconds 3600 } }
}

if (-not (Test-Path $VaultEnv)) {
  Write-Host "Missing $VaultEnv - run: npm run print:home-bot-env"
  Wait-ForKey
  exit 1
}

Get-Content $VaultEnv | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    $name = $matches[1].Trim()
    $val = $matches[2].Trim()
    Set-Item -Path "env:$name" -Value $val
  }
}

$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
if (-not (Test-Path $agentDir)) {
  Write-Host "Monorepo agent dir not found: $agentDir"
  Wait-ForKey
  exit 1
}

Set-Location $agentDir
$env:PORT = "$Port"
$env:DASHBOARD_PORT = "$Port"
Write-Host "Starting bot on port $Port from $agentDir ..."
Write-Host ('Dashboard: http://127.0.0.1:' + $Port)
Write-Host "Exports: /api/export_csv  /api/export_session_trades.csv"
Write-Host ""

if ($NoWait) {
  Write-Host "Starting bot detached on port $Port ..."
  Start-Process -FilePath "python" -ArgumentList @("btc_conservative_agent.py") -WorkingDirectory $agentDir -WindowStyle Hidden
  exit 0
}

$exitCode = 0
try {
  python btc_conservative_agent.py
  $exitCode = $LASTEXITCODE
} catch {
  Write-Host "Bot crashed: $($_.Exception.Message)" -ForegroundColor Red
  $exitCode = 1
} finally {
  if ($exitCode -ne 0) {
    Write-Host "Bot exited with code $exitCode" -ForegroundColor Yellow
  } else {
    Write-Host "Bot process ended." -ForegroundColor Yellow
  }
  Wait-ForKey
}
