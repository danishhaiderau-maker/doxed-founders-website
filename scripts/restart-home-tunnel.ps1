param(
  [int]$Port = 0,
  [switch]$Force,
  [switch]$Hidden
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
if ($Port -le 0) {
  . (Join-Path $scriptDir "home-stack-mode.ps1")
  $Port = (Get-HomeStackMode).BotPort
}
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$stableUrl = "https://bot.doxxedcrypto.digital"

$BotPortForTunnel = $Port
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPortForTunnel -BridgePort 7810
$useNamed = Use-NamedTunnel
. (Join-Path $scriptDir "home-stack-health.ps1")

# Skip only when public tunnel actually responds (not just cloudflared process exists).
if ($useNamed -and -not $Force) {
  $cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  if ($cfRunning -and (Test-TunnelPublicHealthy $stableUrl)) {
    Set-Content -Path $tunnelUrlFile -Value $stableUrl -NoNewline
    Write-Host "Named tunnel live at $stableUrl - no restart needed."
    exit 0
  }
  if ($cfRunning) {
    Write-Host "cloudflared running but public URL dead - restarting connector."
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
  }
} elseif (-not $Force) {
  Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
} else {
  Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
}

# Brief grace window for stack-monitor: public URL may 530 while connector restarts.
Set-Content -Path (Join-Path $repoRoot ".home-tunnel-restarting") -Value (Get-Date -Format "o") -NoNewline

if ($useNamed) {
  Set-Content -Path $tunnelUrlFile -Value $stableUrl -NoNewline
  if ($Hidden) {
    try {
      Start-CloudflaredNamedHidden -Port $Port
      exit 0
    } catch {
      Write-Host "Hidden tunnel start failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
  }
} else {
  if (Test-Path $tunnelUrlFile) { Remove-Item $tunnelUrlFile -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 1
  if ($Hidden) {
    Start-HiddenPs1 -ScriptPath (Join-Path $scriptDir "setup-home-bot-tunnel.ps1") -ExtraArgs @("-Quick", "-Port", "$Port")
    exit 0
  }
}

# Run tunnel in this console (avoids orphan cmd windows stuck on pause).
$Host.UI.RawUI.WindowTitle = if ($useNamed) { "Doxed Cloudflare Tunnel (stable)" } else { "Doxed Cloudflare Tunnel" }
if ($useNamed) {
  & (Join-Path $scriptDir "run-named-bot-tunnel.ps1") -Port $Port
} else {
  & (Join-Path $scriptDir "setup-home-bot-tunnel.ps1") -Quick -Port $Port
}
exit $LASTEXITCODE
