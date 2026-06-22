# Local collection bot — frozen port 7002, separate data folder, no relay/tunnel.
param([switch]$NoWait)

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "local-collection-config.ps1")

$Port = $LocalCollection.BotPort
$Host.UI.RawUI.WindowTitle = "Local Collection Bot :$Port"
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"

function Wait-ForKey {
  Write-Host ""
  Write-Host "--- Press Enter to close ---" -ForegroundColor Cyan
  try { Read-Host } catch { while ($true) { Start-Sleep -Seconds 3600 } }
}

if (-not (Test-Path $vaultEnv)) {
  Write-Host "Missing $vaultEnv - run: npm run print:home-bot-env" -ForegroundColor Red
  Wait-ForKey
  exit 1
}

Get-Content $vaultEnv | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    Set-Item -Path ("env:" + $matches[1].Trim()) -Value $matches[2].Trim()
  }
}

$env:PORT = "$Port"
$env:DASHBOARD_PORT = "$Port"
$env:BTC_AGENT_DATA_DIR = $LocalCollection.DataDir
$env:LOCAL_COLLECTION_MODE = "1"
$env:HOME_BOT_LOCAL = "1"
$env:HOME_RESEARCH_FULL = "1"
if ($LocalCollection.DisableRelay) {
  $env:SHOWCASE_RELAY_WEBHOOK_URL = ""
}

Set-Location $LocalCollection.AgentDir
Write-Host "=== LOCAL COLLECTION BOT (frozen :$Port) ===" -ForegroundColor Cyan
Write-Host "Data: $($LocalCollection.DataDir)"
Write-Host "NOT wired to doxxedcrypto / tunnel / Bybit-15m-research-bot"
Write-Host "Dashboard: http://127.0.0.1:$Port"
Write-Host ""

$entry = @("btc_conservative_agent.py", "bot.py") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $entry) {
  Write-Host "No bot entry (btc_conservative_agent.py or bot.py) in $($LocalCollection.AgentDir)" -ForegroundColor Red
  Wait-ForKey
  exit 1
}

$exitCode = 0
try {
  python $entry
  if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE }
} catch {
  Write-Host "Bot error: $($_.Exception.Message)" -ForegroundColor Red
  $exitCode = 1
} finally {
  if (-not $NoWait) { Wait-ForKey }
}
exit $exitCode
