# Cloudflare Tunnel — expose home BTC bot (default localhost:7800) as HTTPS.
# Run on the machine that hosts the research bot (10.0.0.102 or this PC).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/setup-home-bot-tunnel.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/setup-home-bot-tunnel.ps1 -Quick
#   powershell -ExecutionPolicy Bypass -File scripts/setup-home-bot-tunnel.ps1 -Port 7800

param(
  [switch]$Quick,
  [int]$Port = 7800,
  [string]$Hostname = "bot.doxxedcrypto.digital",
  [string]$TunnelName = "doxed-btc-bot"
)

$ErrorActionPreference = "Stop"
$localUrl = "http://127.0.0.1:$Port"

function Ensure-Cloudflared {
  if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
    Write-Host "cloudflared: $(cloudflared --version 2>&1 | Select-Object -First 1)"
    return
  }
  Write-Host "Installing cloudflared via winget..."
  winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
  if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw "cloudflared not found after install. Download: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  }
}

Ensure-Cloudflared

if ($Quick) {
  Write-Host @"

=== Quick tunnel (testing) ===
Starting temporary public URL -> $localUrl
Copy the https://*.trycloudflare.com URL, then from repo machine:

  npm run wire:home-bot -- https://YOUR-trycloudflare-url

Press Ctrl+C to stop.

"@
  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $repoRoot = Split-Path -Parent $scriptDir
  $tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
  $logFile = Join-Path $repoRoot ".home-tunnel.log"
  Write-Host "Tunnel log: $logFile"
  Write-Host "URL file:   $tunnelUrlFile (auto-updated when URL appears)"
  Write-Host ""
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    cloudflared tunnel --url $localUrl 2>&1 | Tee-Object -FilePath $logFile | ForEach-Object {
      Write-Host $_
      if ($_ -match '(https://[a-z0-9-]+\.trycloudflare\.com)') {
        Set-Content -Path $tunnelUrlFile -Value $matches[1] -NoNewline
      }
    }
  } finally {
    $ErrorActionPreference = $prevEap
    Read-Host "Tunnel stopped - Press Enter to close this window"
  }
  exit 0
}

Write-Host @"

=== Named tunnel (production) ===
Hostname target: $Hostname -> $localUrl

Prerequisites (one-time, in Cloudflare dashboard):
  1. Domain doxxedcrypto.digital on Cloudflare
  2. cloudflared tunnel login   (opens browser)

Then this script will:
  - create tunnel '$TunnelName' if missing
  - route DNS $Hostname
  - run tunnel

"@

$login = Read-Host "Run 'cloudflared tunnel login' now? [Y/n]"
if ($login -ne 'n' -and $login -ne 'N') {
  cloudflared tunnel login
}

$tunnels = cloudflared tunnel list 2>&1 | Out-String
if ($tunnels -notmatch [regex]::Escape($TunnelName)) {
  Write-Host "Creating tunnel $TunnelName..."
  cloudflared tunnel create $TunnelName
}

Write-Host "Routing DNS $Hostname ..."
cloudflared tunnel route dns $TunnelName $Hostname

$configDir = Join-Path $env:USERPROFILE ".cloudflared"
$configPath = Join-Path $configDir "config.yml"
$credGlob = Join-Path $configDir "$TunnelName*.json"
$credFile = Get-ChildItem -Path $credGlob -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $credFile) {
  throw "Tunnel credentials not found in $configDir - run: cloudflared tunnel create $TunnelName"
}

@(
  "tunnel: $TunnelName"
  "credentials-file: $($credFile.FullName)"
  "ingress:"
  "  - hostname: $Hostname"
  "    service: $localUrl"
  "  - service: http_status:404"
) | Set-Content -Path $configPath -Encoding UTF8

Write-Host "Wrote $configPath"
Write-Host @"

Tunnel running. From repo machine (after bot is up on port $Port):

  npm run print:home-bot-env
  npm run wire:home-bot -- https://$Hostname --pause-railway-bot

"@

cloudflared tunnel run $TunnelName
