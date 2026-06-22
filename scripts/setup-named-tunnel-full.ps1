# One-shot permanent tunnel: login (if needed) -> create -> DNS -> Windows service -> wire production.
param(
  [int]$Port = 7800,
  [string]$Hostname = "bot.doxxedcrypto.digital",
  [string]$TunnelName = "doxed-btc-bot",
  [switch]$SkipWire,
  [switch]$SkipService
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$configDir = Join-Path $env:USERPROFILE ".cloudflared"
$certPath = Join-Path $configDir "cert.pem"
$namedFlag = Join-Path $repoRoot ".home-use-named-tunnel"
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$stableUrl = "https://$Hostname"
$localUrl = "http://127.0.0.1:$Port"

function Ensure-Cloudflared {
  if (Get-Command cloudflared -ErrorAction SilentlyContinue) { return }
  throw "Install cloudflared: winget install Cloudflare.cloudflared"
}

function Ensure-Cert {
  if (Test-Path $certPath) {
    Write-Host "OK  cert.pem found at $certPath" -ForegroundColor Green
    return
  }
  Write-Host ""
  Write-Host "=== cloudflared tunnel login (browser) ===" -ForegroundColor Cyan
  Write-Host "A browser window will open. Log in to Cloudflare and authorize this PC."
  Write-Host "When done, cert.pem is saved to: $configDir"
  Write-Host ""
  New-Item -ItemType Directory -Path $configDir -Force | Out-Null
  $loginJob = Start-Job -ScriptBlock { cloudflared tunnel login 2>&1 }
  $deadline = (Get-Date).AddMinutes(5)
  Write-Host "Waiting for browser authorization (up to 5 min)..."
  while ((Get-Date) -lt $deadline) {
    if (Test-Path $certPath) { break }
    Start-Sleep -Seconds 2
  }
  if (Get-Job $loginJob.Id -ErrorAction SilentlyContinue) {
    Stop-Job $loginJob -ErrorAction SilentlyContinue
    Remove-Job $loginJob -Force -ErrorAction SilentlyContinue
  }
  if (-not (Test-Path $certPath)) {
    throw "cert.pem not found. In the browser: pick zone doxxedcrypto.digital and click Authorize, then re-run npm run setup:named-tunnel"
  }
  Write-Host "OK  Login complete" -ForegroundColor Green
}

function Ensure-Tunnel {
  $list = cloudflared tunnel list 2>&1 | Out-String
  if ($list -match [regex]::Escape($TunnelName)) {
    Write-Host "OK  Tunnel '$TunnelName' already exists" -ForegroundColor Green
    return
  }
  Write-Host "Creating tunnel $TunnelName ..."
  cloudflared tunnel create $TunnelName
  Write-Host "OK  Tunnel created" -ForegroundColor Green
}

function Ensure-Dns {
  Write-Host "Routing DNS $Hostname -> $TunnelName ..."
  $out = cloudflared tunnel route dns $TunnelName $Hostname 2>&1 | Out-String
  if ($out -match "already exists|CNAME") {
    Write-Host "OK  DNS route already configured" -ForegroundColor Green
  } else {
    Write-Host "OK  DNS routed" -ForegroundColor Green
  }
}

Ensure-Cloudflared
Ensure-Cert
Ensure-Tunnel
Ensure-Dns

Set-Content -Path $namedFlag -Value "enabled" -NoNewline
Set-Content -Path $tunnelUrlFile -Value $stableUrl -NoNewline
Write-Host "OK  Named tunnel mode enabled -> $stableUrl" -ForegroundColor Green

if (-not $SkipService) {
  Write-Host ""
  Write-Host "=== Installing Windows service ===" -ForegroundColor Cyan
  & (Join-Path $scriptDir "install-named-tunnel-service.ps1") -Port $Port -Hostname $Hostname -TunnelName $TunnelName
}

if (-not $SkipWire) {
  Write-Host ""
  Write-Host "=== Wiring to Neon + Railway ===" -ForegroundColor Cyan
  Push-Location $repoRoot
  try {
    $deadline = (Get-Date).AddMinutes(3)
    $live = $false
    while ((Get-Date) -lt $deadline -and -not $live) {
      try {
        $r = Invoke-WebRequest -Uri "$stableUrl/api/ping" -UseBasicParsing -TimeoutSec 10
        $live = $r.StatusCode -eq 200
      } catch {
        Start-Sleep -Seconds 5
      }
    }
    if (-not $live) {
      Write-Host "Tunnel not live yet - wiring with --skip-health-check" -ForegroundColor Yellow
      & npm.cmd run wire:home-bot -- $stableUrl --skip-health-check
    } else {
      & npm.cmd run wire:home-bot -- $stableUrl
    }
  } finally {
    Pop-Location
  }
}

Write-Host ""
Write-Host "=== Permanent tunnel setup complete ===" -ForegroundColor Green
Write-Host "  URL:    $stableUrl"
Write-Host "  Survives reboot (Windows service)"
Write-Host "  Verify: curl $stableUrl/api/ping"
Write-Host "  Site:   https://doxxedcrypto.digital/agent-hub/conservative-btc"
