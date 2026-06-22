# Stable Cloudflare named tunnel -> bot.doxxedcrypto.digital (production).
param(
  [int]$Port = 7800,
  [string]$Hostname = "bot.doxxedcrypto.digital",
  [string]$TunnelName = "doxed-btc-bot"
)

$Host.UI.RawUI.WindowTitle = "Doxed Cloudflare Tunnel (stable)"
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$localUrl = "http://127.0.0.1:$Port"
$stableUrl = "https://$Hostname"
$configDir = Join-Path $env:USERPROFILE ".cloudflared"
$configPath = Join-Path $configDir "config.yml"

function Ensure-Cloudflared {
  if (Get-Command cloudflared -ErrorAction SilentlyContinue) { return }
  throw "cloudflared not installed. Run: winget install Cloudflare.cloudflared"
}

Ensure-Cloudflared

$tokenFile = Join-Path $configDir "$TunnelName.token"
if (Test-Path $tokenFile) {
  Set-Content -Path $tunnelUrlFile -Value $stableUrl -NoNewline
  Write-Host "Stable tunnel (API token mode): $stableUrl -> $localUrl"
  Write-Host "Keep this window open. Ctrl+C stops tunnel only."
  Write-Host ""
  $token = (Get-Content $tokenFile -Raw).Trim()
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    cloudflared tunnel run --token $token
  } finally {
    $ErrorActionPreference = $prevEap
    Read-Host "Tunnel stopped - Press Enter to close this window"
  }
  exit $LASTEXITCODE
}

$credFile = Get-ChildItem -Path (Join-Path $configDir "$TunnelName*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $credFile) {
  Write-Host "Named tunnel not set up yet (one-time):" -ForegroundColor Yellow
  Write-Host "  1. cloudflared tunnel login"
  Write-Host "  2. cloudflared tunnel create $TunnelName"
  Write-Host "  3. cloudflared tunnel route dns $TunnelName $Hostname"
  Write-Host "  4. Re-run Start everything (will use stable URL automatically)"
  Write-Host ""
  Write-Host "Falling back to quick tunnel for this session..."
  & (Join-Path $scriptDir "setup-home-bot-tunnel.ps1") -Quick -Port $Port
  exit $LASTEXITCODE
}

@(
  "tunnel: $TunnelName"
  "credentials-file: $($credFile.FullName)"
  "ingress:"
  "  - hostname: $Hostname"
  "    service: $localUrl"
  "  - service: http_status:404"
) | Set-Content -Path $configPath -Encoding UTF8

Set-Content -Path $tunnelUrlFile -Value $stableUrl -NoNewline
Write-Host "Stable tunnel URL saved: $stableUrl"
Write-Host "Routing $Hostname -> $localUrl"
Write-Host "Keep this window open. Ctrl+C stops tunnel only."
Write-Host ""

$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
  cloudflared tunnel run $TunnelName
} finally {
  $ErrorActionPreference = $prevEap
  Read-Host "Tunnel stopped - Press Enter to close this window"
}
