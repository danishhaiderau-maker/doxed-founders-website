# One-time: install cloudflared as a Windows service for stable bot.doxxedcrypto.digital
# Requires prior: cloudflared tunnel login + tunnel create doxed-btc-bot + route dns
param(
  [int]$Port = 7800,
  [string]$Hostname = "bot.doxxedcrypto.digital",
  [string]$TunnelName = "doxed-btc-bot"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$configDir = Join-Path $env:USERPROFILE ".cloudflared"
$configPath = Join-Path $configDir "config.yml"
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$namedFlag = Join-Path $repoRoot ".home-use-named-tunnel"
$localUrl = "http://127.0.0.1:$Port"
$stableUrl = "https://$Hostname"

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
  throw "Install cloudflared first: winget install Cloudflare.cloudflared"
}

$credFile = Get-ChildItem -Path (Join-Path $configDir "$TunnelName*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $credFile) {
  Write-Host @"

Named tunnel credentials not found.

Run these once (browser login required):

  cloudflared tunnel login
  cloudflared tunnel create $TunnelName
  cloudflared tunnel route dns $TunnelName $Hostname

Then re-run:

  npm run install:home-bot-tunnel-service

"@ -ForegroundColor Yellow
  exit 1
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
Set-Content -Path $namedFlag -Value "enabled" -NoNewline

Write-Host "Config written: $configPath"
Write-Host "Installing cloudflared Windows service (starts on boot, survives logoff)..."

# Stop interactive cloudflared so service can bind
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

cloudflared service install
Write-Host "Service installed. Starting tunnel service..."
Start-Service Cloudflared -ErrorAction SilentlyContinue

Write-Host @"

Done. Stable URL: $stableUrl
  - Survives closing PowerShell windows
  - Restarts on PC reboot (Windows service)

Wire production (once):

  npm run wire:home-bot -- $stableUrl

"@ -ForegroundColor Green
