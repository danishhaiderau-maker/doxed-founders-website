# Background worker: local collection start everything (bot :7002 + analyzer :9500, no tunnel).
param(
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9500
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort
. (Join-Path $scriptDir "home-stack-health.ps1")
. (Join-Path $scriptDir "local-collection-config.ps1")

$flagFile = Join-Path $repoRoot ".local-collection-mode"
Set-Content -Path $flagFile -Value "bot=$BotPort analyzer=$AnalyzerPort" -NoNewline

$messages = [System.Collections.Generic.List[string]]::new()

# Local collection is isolated — release production ports so :7002/:9500 can bind reliably.
foreach ($prodPort in @(7800, 9001)) {
  if (Test-PortOpen $prodPort) {
    Stop-ListenPortFast $prodPort | Out-Null
    $messages.Add("Released production :$prodPort for local collection")
    Start-Sleep -Seconds 2
  }
}
Stop-PythonMatching "btc_conservative_agent" | Out-Null
$prodAnalyzer = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like "*analyzer_research_engine*" -and $_.CommandLine -notlike "*9500*" } |
  Select-Object -First 1
if ($prodAnalyzer) {
  Stop-Process -Id $prodAnalyzer.ProcessId -Force -ErrorAction SilentlyContinue
  $messages.Add("Stopped production analyzer pid $($prodAnalyzer.ProcessId)")
  Start-Sleep -Seconds 2
}

function Start-LocalBotWindow {
  if (Test-BotHung) {
    Stop-ListenPortFast $BotPort | Out-Null
    Stop-PythonMatching "btc_conservative_agent" | Out-Null
    Stop-PythonMatching "bot.py" | Out-Null
    & taskkill.exe /F /FI "WINDOWTITLE eq Local Collection Bot :$BotPort" 2>$null | Out-Null
    Start-Sleep -Seconds 2
  }
  if (-not (Test-BotHealthy)) {
    Start-DetachedPs1 $botScript @("-NoWait") -NoExit -WindowTitle "Local Collection Bot :$BotPort" -Show Normal
    $messages.Add("[1/2] Bot window opened on :$BotPort")
    Start-Sleep -Seconds 18
  } else {
    $messages.Add("[1/2] Bot already healthy on :$BotPort")
  }
}

function Start-LocalAnalyzerWindow {
  if (Test-AnalyzerHung) {
    Stop-PythonMatching "analyzer_research_engine" | Out-Null
    Stop-ListenPortFast $AnalyzerPort | Out-Null
    Remove-Item (Join-Path $repoRoot ".local-collection-analyzer.lock") -Force -ErrorAction SilentlyContinue
    & taskkill.exe /F /FI "WINDOWTITLE eq Local Collection Analyzer :$AnalyzerPort" 2>$null | Out-Null
    Start-Sleep -Seconds 2
  }
  if (-not (Test-AnalyzerHealthy)) {
    $analyzerScript = Join-Path $scriptDir "start-local-collection-analyzer.ps1"
    Start-DetachedPs1 $analyzerScript @("-NoWait") -NoExit -WindowTitle "Local Collection Analyzer :$AnalyzerPort" -Show Normal
    $messages.Add("[2/2] Analyzer window opened on :$AnalyzerPort")
    Start-Sleep -Seconds 20
  } else {
    $messages.Add("[2/2] Analyzer already healthy on :$AnalyzerPort")
  }
}

Start-LocalBotWindow
Start-LocalAnalyzerWindow

if (-not (Test-HomeScriptRunning "home-stack-supervisor-local.ps1")) {
  Start-HiddenPs1 (Join-Path $scriptDir "home-stack-supervisor-local.ps1") @(
    "-BotPort", "$BotPort",
    "-AnalyzerPort", "$AnalyzerPort"
  )
  $messages.Add("Local supervisor (24/7, no tunnel)")
}

$log = Join-Path $repoRoot ".home-start-all.log"
$line = "{0} [local-collection] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), ($messages -join " | ")
Add-Content -Path $log -Value $line
