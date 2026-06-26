# Start BTC bot on home PC using vault/home-bot.env
param(
  [int]$Port = 0,
  [string]$VaultEnv = "",
  [switch]$NoWait
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-mode.ps1")
$stackMode = Get-HomeStackMode
if ($Port -le 0) { $Port = [int]$stackMode.BotPort }
$BotListenPort = $Port

if ($BotListenPort -eq 7810) {
  Write-Host "ERROR: Bot cannot use port 7810 (home bridge). Use -Port 7002 for showcase." -ForegroundColor Red
  exit 1
}

$Host.UI.RawUI.WindowTitle = "Doxed Bot :$BotListenPort"
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotListenPort -BridgePort 7810 -AnalyzerPort ([int]$stackMode.AnalyzerPort)

function Wait-ForKey {
  Write-Host ""
  Write-Host "--- Console stays open so you can copy logs. Press Enter to close ---" -ForegroundColor Cyan
  try { Read-Host } catch { while ($true) { Start-Sleep -Seconds 3600 } }
}

if (-not $VaultEnv) {
  $VaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
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

# Always kill duplicate bot processes before a fresh start (common cause of :7002 slowness).
$existing = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*btc_conservative_agent*" })
if ($existing.Count -gt 0) {
  Write-Host "Stopping $($existing.Count) existing bot process(es) before start..." -ForegroundColor Yellow
  Stop-PythonMatching "btc_conservative_agent" | Out-Null
  Stop-ListenPortFast $BotListenPort | Out-Null
  Start-Sleep -Seconds 2
} elseif (Test-PortOpen $BotListenPort) {
  Write-Host ("Port :" + $BotListenPort + " in use - clearing listener...") -ForegroundColor Yellow
  Stop-ListenPortFast $BotListenPort | Out-Null
  Start-Sleep -Seconds 1
}

Set-Location $agentDir
# Script port wins over anything in home-bot.env (vault must not override showcase :7002).
$env:PORT = "$BotListenPort"
$env:DASHBOARD_PORT = "$BotListenPort"
Write-Host "Starting bot on port $BotListenPort from $agentDir ..."
Write-Host ('Dashboard: http://127.0.0.1:' + $BotListenPort)
Write-Host '/api/ping responds in ~1s while bot loads (full dashboard ~60-90s on home PC)'
Write-Host "Exports: /api/export_csv  /api/export_session_trades.csv"
Write-Host ""

if ($NoWait) {
  Write-Host "Starting bot detached on port $BotListenPort ..."
  Start-Process -FilePath "python" -ArgumentList @("btc_conservative_agent.py") -WorkingDirectory $agentDir -WindowStyle Hidden
  Start-Sleep -Seconds 2
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
