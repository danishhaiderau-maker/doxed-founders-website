# Runs slow bridge commands off the :7810 listener thread (prevents bridge freeze).
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("stop-bot", "stop-analyzer", "stop-all", "stop-all-global", "stop-all-local", "start-bot", "start-analyzer", "start-all-global", "start-all-local", "reset-home-stack", "wipe-research", "pause-trading", "resume-trading")]
  [string]$Action,
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 0,
  [ValidateSet("production", "local-collection")]
  [string]$StackMode = "production"
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# Resolve the analyzer port from the frozen stack mode (config/home-showcase.lock.json)
# rather than a hardcoded default. The global showcase stack is production -> :9001,
# the legacy local lab is local-collection -> :9500. A stale bridge may still pass 9500
# for the production stack (cached before the lock-file fix); override that too so the
# global showcase never accidentally launches the legacy lab analyzer on :9500.
. (Join-Path $scriptDir "home-stack-mode.ps1")
if ($AnalyzerPort -le 0 -or ($AnalyzerPort -eq 9500 -and $StackMode -eq "production")) {
  $resolvedMode = Get-HomeStackMode
  if ($resolvedMode.AnalyzerPort -gt 0) { $AnalyzerPort = [int]$resolvedMode.AnalyzerPort }
}
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -BridgePort 7810
. (Join-Path $scriptDir "home-stack-health.ps1")

$isLocal = $StackMode -eq "local-collection"
$botTitle = if ($isLocal) { "Local Collection Bot :$BotPort" } else { "Doxed Bot :$BotPort" }
$analyzerTitle = if ($isLocal) { "Local Collection Analyzer :$AnalyzerPort" } else { "Doxed Analyzer" }

switch ($Action) {
  "start-bot" {
    Clear-HomeStackUserStopped
    if (Test-BotHung) {
      Stop-ListenPortFast $BotPort | Out-Null
      Stop-PythonMatching "btc_conservative_agent" | Out-Null
      Stop-PythonMatching "bot.py" | Out-Null
      & taskkill.exe /F /FI "WINDOWTITLE eq $botTitle" 2>$null | Out-Null
      Start-Sleep -Seconds 1
    }
    if (-not (Test-BotHealthy)) {
      if ($isLocal) {
        Start-DetachedPs1 (Join-Path $scriptDir "start-local-collection-bot.ps1") @("-NoWait") -NoExit -WindowTitle $botTitle -Show Normal
      } else {
        Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort", "-NoWait") -NoExit -WindowTitle $botTitle -Show Normal
      }
    }
  }
  "start-analyzer" {
    Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
    if (Test-AnalyzerHung) {
      Stop-PythonMatching "analyzer_research_engine" | Out-Null
      & taskkill.exe /F /FI "WINDOWTITLE eq $analyzerTitle" 2>$null | Out-Null
      Stop-ListenPortFast $AnalyzerPort | Out-Null
      Start-Sleep -Seconds 1
    }
    if (-not (Test-AnalyzerHealthy)) {
      if ($isLocal) {
        Start-DetachedPs1 (Join-Path $scriptDir "start-local-collection-analyzer.ps1") @("-NoWait") -NoExit -WindowTitle $analyzerTitle -Show Normal
      } else {
        Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Port", "$AnalyzerPort", "-NoWait") -NoExit -WindowTitle $analyzerTitle -Show Normal
      }
    }
  }
  "stop-bot" {
    Set-HomeStackUserStopped
    Stop-BotPidFile | Out-Null
    Stop-ListenPortFast $BotPort | Out-Null
    Stop-PythonMatching "btc_conservative_agent" | Out-Null
    Stop-PythonMatching "bot.py" | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq $botTitle" 2>$null | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq Doxed Bot :7800" 2>$null | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq Local Collection Bot :7002" 2>$null | Out-Null
    Start-Sleep -Seconds 1
    Stop-ListenPortFast $BotPort | Out-Null
  }
  "stop-analyzer" {
    Stop-PythonMatching "analyzer_research_engine" | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq $analyzerTitle" 2>$null | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq Doxed Analyzer" 2>$null | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq Doxed Analyzer (once)" 2>$null | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq Local Collection Analyzer :9500" 2>$null | Out-Null
    Stop-ListenPortFast $AnalyzerPort | Out-Null
    Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $repoRoot ".local-collection-analyzer.lock") -Force -ErrorAction SilentlyContinue
  }
  "stop-all" {
    Stop-GlobalStackFast -GlobalBotPort $BotPort -GlobalAnalyzerPort $AnalyzerPort | Out-Null
  }
  "stop-all-global" {
    Stop-GlobalStackFast -GlobalBotPort $BotPort -GlobalAnalyzerPort $AnalyzerPort | Out-Null
  }
  "stop-all-local" {
    Stop-LocalLabFast | Out-Null
  }
  "start-all-global" {
    Clear-HomeStackUserStopped
    Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
    Start-VisibleConsole (Join-Path $scriptDir "home-stack-start-everything.ps1") @(
      "-BotPort", "$BotPort",
      "-AnalyzerPort", "$AnalyzerPort"
    ) -Title "Doxed Start Everything"
  }
  "start-all-local" {
    $labScript = Join-Path $scriptDir "home-stack-local-lab.ps1"
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $labScript, "-Action", "start"
    ) -WorkingDirectory $repoRoot -WindowStyle Normal
  }
  "reset-home-stack" {
    Clear-HomeStackUserStopped
    Stop-GlobalStackFast -GlobalBotPort $BotPort -GlobalAnalyzerPort $AnalyzerPort | Out-Null
    Start-Sleep -Seconds 8
    Start-VisibleConsole (Join-Path $scriptDir "home-stack-start-everything.ps1") @(
      "-BotPort", "$BotPort",
      "-AnalyzerPort", "$AnalyzerPort",
      "-NoWait"
    ) -Title "Doxed Start Everything"
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
