# Background worker for Start everything (opened by bridge so :7810 returns instantly).
param(
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001
)

$ErrorActionPreference = "Continue"
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "home-stack-common.ps1")

$messages = [System.Collections.Generic.List[string]]::new()

if (-not (Test-BotRunning)) {
  Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -NoExit -WindowTitle "Doxed Bot :7800"
  $messages.Add("[1/4] Bot window opened on :$BotPort")
  Start-Sleep -Seconds 4
} else {
  $messages.Add("[1/4] Bot already online on :$BotPort")
}

if (-not (Test-AnalyzerRunning)) {
  Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @() -NoExit -WindowTitle "Doxed Analyzer"
  $messages.Add("[2/4] Analyzer console opened")
} else {
  $messages.Add("[2/4] Analyzer already running")
}

if (Start-AnalyzerDashboard) {
  $messages.Add("[2b] Analyzer dashboard on http://127.0.0.1:$AnalyzerPort/")
}

$tunnelUrl = Get-TunnelUrl
$tunnelOk = Test-TunnelLiveCached $tunnelUrl
if (-not $tunnelOk) {
  if (@(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0) {
    Stop-Cloudflared | Out-Null
    Start-Sleep -Seconds 2
  }
  Start-DetachedPs1 (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort") -WindowTitle "Doxed Tunnel Restart"
  if (Use-NamedTunnel) {
    $messages.Add("[3/4] Named tunnel restart queued (bot.doxxedcrypto.digital)")
  } else {
    $messages.Add("[3/4] Quick tunnel restart queued (watch Doxed Cloudflare Tunnel window)")
  }
} else {
  $messages.Add("[3/4] Tunnel already live: $tunnelUrl")
}

Start-DetachedPs1 (Join-Path $scriptDir "home-stack-control-panel.ps1") @("-BotPort", "$BotPort", "-AnalyzerPort", "$AnalyzerPort") -NoExit -WindowTitle "Doxed Stack Control Panel"
$messages.Add("[4/4] Control panel opened")

if (-not (Test-HomeScriptRunning "auto-wire-after-tunnel.ps1")) {
  Start-DetachedPs1 (Join-Path $scriptDir "auto-wire-after-tunnel.ps1") @() -WindowTitle "Doxed Auto-Wire"
  $messages.Add("Auto-wire started")
}

if (-not (Test-HomeScriptRunning "tunnel-watchdog.ps1")) {
  Start-DetachedPs1 (Join-Path $scriptDir "tunnel-watchdog.ps1") @("-BotPort", "$BotPort") -WindowTitle "Doxed Tunnel Watchdog"
  $messages.Add("Tunnel watchdog started")
}

$log = Join-Path $repoRoot ".home-start-all.log"
$line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), ($messages -join " | ")
Add-Content -Path $log -Value $line
Write-Host $line
