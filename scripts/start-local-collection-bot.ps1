# Local collection bot — frozen port 7002, separate data folder, no relay/tunnel.
param([switch]$NoWait)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

$legacyOwnerOptInName = "DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER"
$legacyOwnerOptInPhrase = "I_UNDERSTAND_THIS_STARTS_A_SECOND_AI_TRADING_OWNER"
$legacyOwnerOptIn = (Get-Item -Path "env:$legacyOwnerOptInName" -ErrorAction SilentlyContinue).Value
if ($legacyOwnerOptIn -cne $legacyOwnerOptInPhrase) {
  Write-Host "REFUSED: this obsolete local launcher would start a second AI/strategy owner." -ForegroundColor Red
  Write-Host "Use scripts\start-fly-desktop-mirror.ps1 for the Fly proxy, data sync, and analyzer."
  Write-Host "Disaster recovery only: set $legacyOwnerOptInName to the exact audited opt-in phrase for this process."
  exit 78
}
$flyCanonicalLock = Join-Path $repoRoot "config\fly-canonical.lock.json"
if (Test-Path -LiteralPath $flyCanonicalLock) {
  Write-Host "REFUSED: Fly canonical lock is present; local collection cannot start another owner." -ForegroundColor Red
  Write-Host "Lock: $flyCanonicalLock" -ForegroundColor Yellow
  exit 78
}
Write-Warning "DISASTER-RECOVERY OPT-IN ACCEPTED: starting the obsolete local collection bot."

. (Join-Path $scriptDir "local-collection-config.ps1")

$Port = $LocalCollection.BotPort
$Host.UI.RawUI.WindowTitle = "Local Collection Bot :$Port"
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
$env:STRICT_PATHWAY_VALIDATION = "0"
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
