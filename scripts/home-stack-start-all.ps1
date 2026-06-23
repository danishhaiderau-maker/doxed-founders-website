# Background worker for Start everything (bot/analyzer/tunnel open visible consoles).
param(
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001
)

$ErrorActionPreference = "Continue"
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort
. (Join-Path $scriptDir "home-stack-health.ps1")

$messages = [System.Collections.Generic.List[string]]::new()
$stableUrl = "https://bot.doxxedcrypto.digital"

if (-not (Test-BotHealthy)) {
  if (Test-BotHung) {
    Stop-PythonMatching "btc_conservative_agent" | Out-Null
    Stop-ListenPortFast $BotPort | Out-Null
    Start-Sleep -Seconds 2
  }
  $botScript = Join-Path $scriptDir "start-home-bot.ps1"
  Start-DetachedPs1 $botScript @("-Port", "$BotPort", "-NoWait") -NoExit -WindowTitle "Doxed Bot :$BotPort" -Show Normal
  $messages.Add("[1/3] Bot window opened on :$BotPort")
  Start-Sleep -Seconds 10
} else {
  $messages.Add("[1/3] Bot already healthy on :$BotPort")
}

if (-not (Test-AnalyzerHealthy)) {
  if (Test-AnalyzerHung) {
    Stop-PythonMatching "analyzer_research_engine" | Out-Null
    Stop-ListenPortFast $AnalyzerPort | Out-Null
    Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
  }
  Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Port", "$AnalyzerPort", "-NoWait") -NoExit -WindowTitle "Doxed Analyzer :$AnalyzerPort" -Show Normal
  $messages.Add("[2/3] Analyzer window opened")
  Start-Sleep -Seconds 10
} else {
  $messages.Add("[2/3] Analyzer already healthy")
}

$tunnelUrl = if ((Use-NamedTunnel) -and (Test-Path (Join-Path $repoRoot ".home-use-named-tunnel"))) { $stableUrl } else { Get-TunnelUrl }
$tunnelOk = if ($tunnelUrl) { Test-TunnelPublicHealthy $tunnelUrl } else { $false }
$cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0

if ($tunnelOk) {
  $messages.Add("[3/3] Tunnel already live: $tunnelUrl")
} elseif ((Use-NamedTunnel) -and $cfRunning) {
  Stop-Cloudflared | Out-Null
  Start-Sleep -Seconds 2
  try {
    Start-CloudflaredNamedHidden -Port $BotPort
    $messages.Add("[3/3] Named tunnel restarted hidden - $stableUrl")
  } catch {
    Start-DetachedPs1 (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort", "-Force") -NoExit -WindowTitle "Doxed Cloudflare Tunnel" -Show Normal
    $messages.Add("[3/3] Named tunnel window opened - $stableUrl")
  }
} else {
  if ($cfRunning) {
    Stop-Cloudflared | Out-Null
    Start-Sleep -Seconds 2
  }
  Start-DetachedPs1 (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort", "-Force") -NoExit -WindowTitle "Doxed Cloudflare Tunnel" -Show Normal
  if (Use-NamedTunnel) {
    $messages.Add("[3/3] Named tunnel window opened - $stableUrl")
  } else {
    $messages.Add("[3/3] Quick tunnel window opened - URL in .home-tunnel-url")
  }
}

if (-not (Test-HomeScriptRunning "auto-wire-after-tunnel.ps1")) {
  Start-HiddenPs1 (Join-Path $scriptDir "auto-wire-after-tunnel.ps1") @("-Quiet")
  $messages.Add("Auto-wire (hidden)")
}

if (-not (Test-HomeScriptRunning "home-stack-supervisor.ps1")) {
  Start-HiddenPs1 (Join-Path $scriptDir "home-stack-supervisor.ps1") @("-BotPort", "$BotPort", "-AnalyzerPort", "$AnalyzerPort")
  $messages.Add("Supervisor (hidden, 24/7 health)")
}

$log = Join-Path $repoRoot ".home-start-all.log"
$line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), ($messages -join " | ")
Add-Content -Path $log -Value $line
