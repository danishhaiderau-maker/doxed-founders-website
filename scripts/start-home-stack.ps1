# One command: bot + analyzer + Cloudflare quick tunnel (3 windows).
# Usage:
#   START-HOME.cmd
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-home-stack.ps1
param(
  [switch]$NoAnalyzer,
  [switch]$NoTunnel,
  [switch]$AnalyzerOnce,
  [int]$Port = 7002
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

# This former production stack starts both a Python strategy owner and a
# Cloudflare tunnel. It is retained only as reversible disaster-recovery code.
$legacyOwnerOptInName = "DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER"
$legacyOwnerOptInPhrase = "I_UNDERSTAND_THIS_STARTS_A_SECOND_AI_TRADING_OWNER"
$legacyOwnerOptIn = (Get-Item -Path "env:$legacyOwnerOptInName" -ErrorAction SilentlyContinue).Value
if ($legacyOwnerOptIn -cne $legacyOwnerOptInPhrase) {
  Write-Host "REFUSED: this obsolete stack would start a second AI/strategy bot and a desktop tunnel." -ForegroundColor Red
  Write-Host "Fly.io remains the sole production owner." -ForegroundColor Yellow
  Write-Host "Use scripts\start-fly-desktop-mirror.ps1 for :7002 proxy, data sync, and :9001 analyzer."
  Write-Host "Disaster recovery only: set $legacyOwnerOptInName to the exact audited opt-in phrase for this process."
  exit 78
}
$flyCanonicalLock = Join-Path $repoRoot "config\fly-canonical.lock.json"
if (Test-Path -LiteralPath $flyCanonicalLock) {
  Write-Host "REFUSED: Fly canonical lock is present; the obsolete Windows stack remains disabled." -ForegroundColor Red
  Write-Host "Lock: $flyCanonicalLock" -ForegroundColor Yellow
  exit 78
}
Write-Warning "DISASTER-RECOVERY OPT-IN ACCEPTED: starting the obsolete Windows bot/analyzer/tunnel stack."

$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
$kpiSrc = Join-Path (Split-Path -Parent $repoRoot) "research_kpi_engine.py"
$kpiDst = Join-Path $agentDir "research_kpi_engine.py"

if (-not (Test-Path $agentDir)) {
  throw "Bot folder not found: $agentDir"
}

if (-not (Test-Path $vaultEnv)) {
  Write-Host "Generating home-bot.env ..."
  Push-Location $repoRoot
  & npm.cmd run print:home-bot-env | Out-Null
  Pop-Location
  if (-not (Test-Path $vaultEnv)) {
    throw "Missing $vaultEnv - run: npm.cmd run print:home-bot-env"
  }
}

if ((Test-Path $kpiSrc) -and -not (Test-Path $kpiDst)) {
  Copy-Item $kpiSrc $kpiDst
  Write-Host "Copied research_kpi_engine.py into bot folder."
}

Write-Host ""
Write-Host "=== Doxed home stack ===" -ForegroundColor Cyan
Write-Host "  Bot dashboard : http://127.0.0.1:$Port"
Write-Host "  LAN dashboard : http://10.0.0.102:$Port"
Write-Host ""

$botRunner = Join-Path $scriptDir "start-home-bot.ps1"
$analyzerRunner = Join-Path $scriptDir "start-home-analyzer.ps1"
$tunnelRunner = Join-Path $scriptDir "setup-home-bot-tunnel.ps1"

# 1) Main bot
Start-Process powershell.exe -ArgumentList @(
  "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $botRunner, "-Port", $Port
) -WorkingDirectory $repoRoot -WindowStyle Normal
Write-Host "[1/3] Bot window opened" -ForegroundColor Green
Start-Sleep -Seconds 3

# 2) Analyzer
  if (-not $NoAnalyzer) {
    $analyzerArgs = @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $analyzerRunner)
    if ($AnalyzerOnce) { $analyzerArgs += "-Once" }
    Start-Process powershell.exe -ArgumentList $analyzerArgs -WorkingDirectory $repoRoot -WindowStyle Normal
  Write-Host "[2/3] Analyzer window opened" -ForegroundColor Green
} else {
  Write-Host "[2/3] Analyzer skipped (-NoAnalyzer)" -ForegroundColor Yellow
}

# 3) Tunnel
if (-not $NoTunnel) {
  Start-Process powershell.exe -ArgumentList @(
    "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $tunnelRunner, "-Quick", "-Port", $Port
  ) -WorkingDirectory $repoRoot -WindowStyle Normal
  Write-Host "[3/3] Tunnel window opened" -ForegroundColor Green
} else {
  Write-Host "[3/3] Tunnel skipped (-NoTunnel)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Three windows: bot, analyzer, tunnel." -ForegroundColor Cyan
Write-Host "When tunnel shows a trycloudflare.com URL, wire once:" -ForegroundColor Cyan
Write-Host "  cd /d `"$repoRoot`"" -ForegroundColor White
Write-Host "  npm.cmd run wire:home-bot -- https://YOUR-trycloudflare-url" -ForegroundColor White
Write-Host ""
