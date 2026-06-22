# One command: bot + analyzer + Cloudflare quick tunnel (3 windows).
# Usage:
#   START-HOME.cmd
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-home-stack.ps1
param(
  [switch]$NoAnalyzer,
  [switch]$NoTunnel,
  [switch]$AnalyzerOnce,
  [int]$Port = 7800
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
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
