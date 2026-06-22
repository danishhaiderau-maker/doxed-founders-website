# Runs slow bridge commands off the :7810 listener thread (prevents bridge freeze).
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("stop-bot", "stop-analyzer", "stop-all", "start-bot", "start-analyzer", "wipe-research", "pause-trading", "resume-trading")]
  [string]$Action,
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "home-stack-common.ps1")
. (Join-Path $scriptDir "home-stack-health.ps1")

switch ($Action) {
  "start-bot" {
    if (Test-BotHung) {
      Stop-ListenPortFast $BotPort | Out-Null
      Stop-PythonMatching "btc_conservative_agent" | Out-Null
      & taskkill.exe /F /FI "WINDOWTITLE eq Doxed Bot :7800" 2>$null | Out-Null
      Start-Sleep -Seconds 1
    }
    if (-not (Test-BotHealthy)) {
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort", "-NoWait") -NoExit -WindowTitle "Doxed Bot :7800" -Show Normal
    }
  }
  "start-analyzer" {
    if (Test-AnalyzerHung) {
      Stop-PythonMatching "analyzer_research_engine" | Out-Null
      & taskkill.exe /F /FI "WINDOWTITLE eq Doxed Analyzer" 2>$null | Out-Null
      Stop-ListenPortFast $AnalyzerPort | Out-Null
      Start-Sleep -Seconds 1
    }
    if (-not (Test-AnalyzerHealthy)) {
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @("-NoWait") -NoExit -WindowTitle "Doxed Analyzer" -Show Normal
    } elseif (-not (Test-PortOpen $AnalyzerPort)) {
      Start-AnalyzerDashboard | Out-Null
    }
  }
  "stop-bot" {
    Stop-ListenPortFast $BotPort | Out-Null
    Stop-PythonMatching "btc_conservative_agent" | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq Doxed Bot :7800" 2>$null | Out-Null
  }
  "stop-analyzer" {
    Stop-PythonMatching "analyzer_research_engine" | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq Doxed Analyzer" 2>$null | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq Doxed Analyzer (once)" 2>$null | Out-Null
    Stop-ListenPortFast $AnalyzerPort | Out-Null
    Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
  }
  "stop-all" {
    Stop-AllHomeStackFast | Out-Null
  }
  "wipe-research" {
    if (Test-PortOpen $BotPort) {
      Invoke-WebRequest -Uri "http://127.0.0.1:$BotPort/api/reset" -Method POST -UseBasicParsing -TimeoutSec 180 | Out-Null
    }
  }
  "pause-trading" {
    if (Test-PortOpen $BotPort) {
      Invoke-WebRequest -Uri "http://127.0.0.1:$BotPort/api/pause" -Method POST -UseBasicParsing -TimeoutSec 30 | Out-Null
    }
  }
  "resume-trading" {
    if (Test-PortOpen $BotPort) {
      Invoke-WebRequest -Uri "http://127.0.0.1:$BotPort/api/resume" -Method POST -UseBasicParsing -TimeoutSec 30 | Out-Null
    }
  }
}
