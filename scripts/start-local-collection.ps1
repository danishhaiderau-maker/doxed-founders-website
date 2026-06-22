# Start frozen local collection stack: bot :7002 + analyzer :9500 (visible consoles, no tunnel).
param([switch]$OnceAnalyzer)

$ErrorActionPreference = "Continue"
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "local-collection-config.ps1")

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$flagFile = Join-Path $repoRoot ".local-collection-mode"
Set-Content -Path $flagFile -Value "bot=$($LocalCollection.BotPort) analyzer=$($LocalCollection.AnalyzerPort)" -NoNewline

Write-Host ""
Write-Host "=== Local collection (frozen ports) ===" -ForegroundColor Green
Write-Host "  Bot:      http://127.0.0.1:$($LocalCollection.BotPort)"
Write-Host "  Analyzer: http://127.0.0.1:$($LocalCollection.AnalyzerPort)/"
Write-Host "  Data:     $($LocalCollection.DataDir)"
Write-Host "  Production doxxedcrypto stack (7800/9001) is NOT started."
Write-Host ""

Start-Process powershell -ArgumentList @(
  "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-File", (Join-Path $scriptDir "start-local-collection-bot.ps1")
) -WorkingDirectory $repoRoot -WindowStyle Normal

Start-Sleep -Seconds 8

$analyzerArgs = @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $scriptDir "start-local-collection-analyzer.ps1"))
if ($OnceAnalyzer) { $analyzerArgs += "-Once" }
Start-Process powershell -ArgumentList $analyzerArgs -WorkingDirectory $repoRoot -WindowStyle Normal

Write-Host "Two console windows opened. Watch logs there."
Write-Host "Lock file: config\local-collection.lock.json (ports frozen until you edit it)."
