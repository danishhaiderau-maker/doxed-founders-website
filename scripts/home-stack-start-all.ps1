# Background worker for Start everything (bot/analyzer/tunnel open visible consoles).
param(
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001
)

$ErrorActionPreference = "Continue"
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "home-stack-common.ps1")

$messages = [System.Collections.Generic.List[string]]::new()

if (-not (Test-BotRunning)) {
  Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -NoExit -WindowTitle "Doxed Bot :7800" -Show Normal
  $messages.Add("[1/3] Bot window opened on :$BotPort")
  Start-Sleep -Seconds 8
} else {
  $messages.Add("[1/3] Bot already online on :$BotPort")
}

if (-not (Test-AnalyzerRunning)) {
  Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @() -NoExit -WindowTitle "Doxed Analyzer" -Show Normal
  $messages.Add("[2/3] Analyzer window opened")
  Start-Sleep -Seconds 8
} else {
  $messages.Add("[2/3] Analyzer already running")
}

$tunnelUrl = Get-TunnelUrl
$cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
# Named tunnel: never kill a running cloudflared on start-all (watchdog handles recovery).
# Killing cloudflared here caused public URL flapping and bot "online then offline" on Agent Hub.
if ($cfRunning -and (Use-NamedTunnel)) {
  if (-not $tunnelUrl) {
    Set-Content -Path (Join-Path $repoRoot ".home-tunnel-url") -Value "https://bot.doxxedcrypto.digital" -NoNewline
    $tunnelUrl = "https://bot.doxxedcrypto.digital"
  }
  $messages.Add("[3/3] Named tunnel already running - $tunnelUrl")
} elseif ($cfRunning -and (Test-TunnelLiveCached $tunnelUrl)) {
  $messages.Add("[3/3] Tunnel already live: $tunnelUrl")
} else {
  if ($cfRunning) {
    Stop-Cloudflared | Out-Null
    Start-Sleep -Seconds 2
  }
  Start-DetachedPs1 (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort") -NoExit -WindowTitle "Doxed Cloudflare Tunnel" -Show Normal
  if (Use-NamedTunnel) {
    $messages.Add("[3/3] Named tunnel window opened - https://bot.doxxedcrypto.digital")
  } else {
    $messages.Add("[3/3] Quick tunnel window opened - URL in .home-tunnel-url")
  }
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
