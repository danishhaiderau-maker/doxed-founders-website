# Provider-free elevated recovery for a Windows host whose process/WMI
# providers are stalled. This preserves all paper/research data and does not
# touch the website relay state, tile configuration, prompts, or sizing.
param(
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9001
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$Host.UI.RawUI.WindowTitle = "Doxed Provider-Free Recovery"

function Stop-ExactWindowTree([string]$Title) {
  & taskkill.exe /F /T /FI "WINDOWTITLE eq $Title" 2>$null | Out-Null
}

# Close only the supported home-stack launch windows. Exact title filters
# avoid touching unrelated PowerShell/cmd processes or any manual application.
foreach ($title in @(
  "Doxed Start Everything",
  "Doxed Bot :$BotPort",
  "Doxed Analyzer :$AnalyzerPort",
  "Doxed Home Bridge :7810"
)) {
  Stop-ExactWindowTree $title
}

Start-Sleep -Seconds 2

# File locks are released by the terminated owners. Removing their now-stale
# pathnames lets the replacement owners acquire a clean exclusive handle.
foreach ($lockName in @(
  ".home-start-everything.lock",
  ".home-bot-start.lock",
  ".home-analyzer-start.lock",
  ".home-stack-supervisor.lock",
  ".home-bridge-watchdog.lock"
)) {
  Remove-Item -LiteralPath (Join-Path $repoRoot $lockName) -Force -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath (Join-Path $repoRoot ".home-stack-user-stopped") -Force -ErrorAction SilentlyContinue

& (Join-Path $scriptDir "ensure-home-bridge.ps1") -Port 7810 -Force
if ($LASTEXITCODE -ne 0) {
  throw "Bridge recovery failed; refusing to start dependent services."
}

& (Join-Path $scriptDir "home-stack-start-everything.ps1") `
  -BotPort $BotPort `
  -AnalyzerPort $AnalyzerPort `
  -SkipBridgeRestart `
  -NoWait
