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
Write-Host "Bot :$BotPort | Analyzer :$AnalyzerPort | tunnel | supervisor | old consoles"
Write-Host "Bridge :7810 stays running. Local lab :7800/:9001 untouched."
Write-Host ""

# Close stale stop/start windows from earlier sessions (keep this window).
$exclude = @($PID)
try {
  $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction SilentlyContinue).ParentProcessId
  if ($parent -gt 0) { $exclude += $parent }
} catch { }
$stale = Close-StaleOrchestratorConsoles -ExcludeProcessIds $exclude
if ($stale.Count -gt 0) {
  Write-Host "Closed $($stale.Count) stale console(s) from prior session." -ForegroundColor DarkYellow
}

$result = Stop-GlobalStackFast -GlobalBotPort $BotPort -GlobalAnalyzerPort $AnalyzerPort -ExcludeProcessIds $exclude
Write-Host "Supervisor PIDs stopped: $($result.supervisor -join ', ')"
Write-Host "Bot port PIDs killed: $($result.botPort -join ', ')"
Write-Host "Analyzer port PIDs killed: $($result.analyzerPort -join ', ')"
Write-Host "Cloudflared PIDs killed: $($result.tunnel -join ', ')"
Write-Host "Console windows closed: $($result.consoles.Count)"
Write-Host ""

Start-Sleep -Seconds 3
$botOpen = Test-PortOpen $BotPort
$analyzerOpen = Test-PortOpen $AnalyzerPort
$cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
Write-Host "After stop: bot :$BotPort open=$botOpen | analyzer :$AnalyzerPort open=$analyzerOpen | cloudflared=$cfRunning"
Write-Host ""
if ($botOpen -or $analyzerOpen -or $cfRunning) {
  Write-Host "Some services still running - run Stop again or close remaining windows manually." -ForegroundColor Red
} else {
  Write-Host "Global showcase stopped." -ForegroundColor Green
}
Write-Host ""
Write-Host "You can close duplicate 'Doxed Home Bridge' windows manually (keep one on :7810)." -ForegroundColor DarkGray
Write-Host ""
Write-Host "--- Press Enter to close this window ---" -ForegroundColor Cyan
try { Read-Host } catch { Start-Sleep -Seconds 3600 }
