# Background worker for Start everything (no visible window).
param(
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001
)

$ErrorActionPreference = "Continue"
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "home-stack-common.ps1")

$messages = [System.Collections.Generic.List[string]]::new()

if (-not (Test-BotRunning)) {
  Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -NoExit -WindowTitle "Doxed Bot :7800" -Show Minimized
  $messages.Add("[1/3] Bot started minimized on :$BotPort")
  Start-Sleep -Seconds 4
} else {
  $messages.Add("[1/3] Bot already online on :$BotPort")
}

if (-not (Test-AnalyzerRunning)) {
  Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @() -NoExit -WindowTitle "Doxed Analyzer" -Show Minimized
  $messages.Add("[2/3] Analyzer started minimized")
} else {
  $messages.Add("[2/3] Analyzer already running")
}

$tunnelUrl = Get-TunnelUrl
$tunnelOk = Test-TunnelLiveCached $tunnelUrl
if (-not $tunnelOk) {
  if (@(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0) {
    Stop-Cloudflared | Out-Null
    Start-Sleep -Seconds 2
  }
  Start-HiddenPs1 (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort")
  if (Use-NamedTunnel) {
    $messages.Add("[3/3] Named tunnel restart (hidden)")
  } else {
    $messages.Add("[3/3] Quick tunnel restart (hidden) - URL in .home-tunnel-url")
  }
} else {
  $messages.Add("[3/3] Tunnel already live: $tunnelUrl")
}

if (-not (Test-HomeScriptRunning "auto-wire-after-tunnel.ps1")) {
  Start-HiddenPs1 (Join-Path $scriptDir "auto-wire-after-tunnel.ps1") @("-Quiet")
  $messages.Add("Auto-wire (hidden)")
}

if (-not (Test-HomeScriptRunning "tunnel-watchdog.ps1")) {
  Start-HiddenPs1 (Join-Path $scriptDir "tunnel-watchdog.ps1") @("-BotPort", "$BotPort")
  $messages.Add("Watchdog (hidden)")
}

$log = Join-Path $repoRoot ".home-start-all.log"
$line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), ($messages -join " | ")
Add-Content -Path $log -Value $line
