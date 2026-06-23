# Visible stop for global showcase (:7002 bot, :9500 analyzer, tunnel).
param(
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9500
)

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Doxed Stop Everything"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -Port 7810

Write-Host ""
Write-Host "=== Stopping global showcase ===" -ForegroundColor Yellow
Write-Host "Bot :$BotPort | Analyzer :$AnalyzerPort | tunnel | (bridge :7810 stays running)"
Write-Host ""

$result = Stop-GlobalStackFast -GlobalBotPort $BotPort -GlobalAnalyzerPort $AnalyzerPort
Write-Host "Bot port PIDs killed: $($result.botPort -join ', ')"
Write-Host "Analyzer port PIDs killed: $($result.analyzerPort -join ', ')"
Write-Host "Cloudflared PIDs killed: $($result.tunnel -join ', ')"
Write-Host ""

Start-Sleep -Seconds 3
$botOpen = Test-PortOpen $BotPort
$analyzerOpen = Test-PortOpen $AnalyzerPort
Write-Host "After stop: bot :$BotPort open=$botOpen | analyzer :$AnalyzerPort open=$analyzerOpen"
Write-Host ""
if ($botOpen -or $analyzerOpen) {
  Write-Host "Some ports still open - run Stop again or close console windows manually." -ForegroundColor Red
} else {
  Write-Host "Global showcase stopped. Local lab :7800/:9001 untouched." -ForegroundColor Green
}
Write-Host ""
Write-Host "--- Press Enter to close ---" -ForegroundColor Cyan
try { Read-Host } catch { Start-Sleep -Seconds 3600 }
