$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
. (Join-Path $PSScriptRoot "write-dev-env.ps1")

function Write-DebugLog([string]$Message, [hashtable]$Data = @{}) {
  # #region agent log
  $payload = @{
    sessionId    = "acf3ea"
    runId        = "dev-lan"
    hypothesisId = "LAN"
    location     = "dev-lan.ps1"
    message      = $Message
    data         = $Data
    timestamp    = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  } | ConvertTo-Json -Compress
  Add-Content -Path (Join-Path $projectRoot "debug-acf3ea.log") -Value $payload -Encoding UTF8
  # #endregion
}

function Get-LanIPv4 {
  $cfg = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq "Up" } |
    Select-Object -First 1
  if ($cfg) { return $cfg.IPv4Address.IPAddress }
  return $null
}

function Get-PortListeners([int[]]$Ports) {
  $result = @{}
  foreach ($port in $Ports) {
    $pids = @(
      Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    )
    $result["$port"] = $pids
  }
  return $result
}

Write-Host ""
Write-Host "=== LAN dev mode (share on your Wi-Fi) ===" -ForegroundColor Cyan

Write-Host "Freeing ports 3000 and 4000 (stops stale servers)..." -ForegroundColor DarkGray
$beforePorts = Get-PortListeners @(3000, 4000)
Write-DebugLog "ports-before-stop" @{ ports = $beforePorts } "EADDRINUSE"

& (Join-Path $PSScriptRoot "stop-self-host.ps1")
& (Join-Path $PSScriptRoot "stop-dev.ps1")
Start-Sleep -Seconds 2

$afterPorts = Get-PortListeners @(3000, 4000)
Write-DebugLog "ports-after-stop" @{ ports = $afterPorts } "EADDRINUSE"

if ($afterPorts["4000"].Count -gt 0 -or $afterPorts["3000"].Count -gt 0) {
  Write-Host "WARN: Ports still in use after cleanup. Retrying..." -ForegroundColor Yellow
  & (Join-Path $PSScriptRoot "stop-dev.ps1")
  Start-Sleep -Seconds 2
  $afterPorts = Get-PortListeners @(3000, 4000)
  Write-DebugLog "ports-after-retry" @{ ports = $afterPorts } "EADDRINUSE"
}

$tunnelEnvPath = Join-Path $projectRoot ".env.tunnel.local"
$usingTunnel = $false

# Ensure stripe is installed (API fails without it)
npm install --workspace=@dcf/api stripe --no-fund --no-audit 2>&1 | Out-Null

if (Test-Path ".env.self-host") {
  Get-Content ".env.self-host" | ForEach-Object {
    if ($_ -match '^\s*(JWT_SECRET|NEXTAUTH_SECRET)=(.*)$') {
      Set-Item -Path "env:$($matches[1])" -Value $matches[2].Trim().Trim('"')
    }
  }
}

$tunnelVars = Read-TunnelEnvFile -Path $tunnelEnvPath
if ($tunnelVars -and $tunnelVars["NEXTAUTH_URL"]) {
  $webTunnelUrl = $tunnelVars["NEXTAUTH_URL"]
  if (Test-UrlReachable $webTunnelUrl) {
    $usingTunnel = $true
    Apply-TunnelEnvToProcess -Vars $tunnelVars
  } else {
    Write-Host ""
    Write-Host "WARN: .env.tunnel.local exists but web tunnel is not reachable:" -ForegroundColor Yellow
    Write-Host "       $webTunnelUrl" -ForegroundColor Yellow
    Write-Host "       Falling back to LAN/local dev (API proxied via Next.js)." -ForegroundColor Yellow
    Write-Host "       Restart dev-tunnel.cmd for phone HTTPS, then dev-lan.cmd again." -ForegroundColor Yellow
    Write-Host ""
  }
}

if ($usingTunnel) {
  $webUrl = $tunnelVars["NEXTAUTH_URL"]
  $tunnelApiUrl = if ($tunnelVars["TUNNEL_API_URL"]) { $tunnelVars["TUNNEL_API_URL"] } else { $tunnelVars["NEXT_PUBLIC_API_URL"] }
  $tunnelWebUrl = if ($tunnelVars["TUNNEL_WEB_URL"]) { $tunnelVars["TUNNEL_WEB_URL"] } else { $webUrl }

  if (-not $webUrl) {
    Write-Host "ERROR: .env.tunnel.local is incomplete. Run dev-tunnel.cmd first." -ForegroundColor Red
    exit 1
  }

  Write-TunnelDevEnv -ProjectRoot $projectRoot -WebUrl $webUrl -ApiUrl $(if ($tunnelApiUrl) { $tunnelApiUrl } else { "http://127.0.0.1:4000" })
  Apply-TunnelEnvToProcess -Vars (Read-TunnelEnvFile -Path $tunnelEnvPath)

  $env:API_BIND_HOST = "127.0.0.1"
  $env:API_URL = "http://127.0.0.1:4000"
  $env:NEXT_PUBLIC_API_URL = ""

  Write-DebugLog "tunnel-config" @{
    webUrl = $webUrl
    apiProxy = "same-origin /api -> 127.0.0.1:4000"
  }

  Write-Host ""
  Write-Host "========================================" -ForegroundColor Green
  Write-Host "  TUNNEL MODE (Cloudflare HTTPS)" -ForegroundColor Green
  Write-Host "========================================" -ForegroundColor Green
  Write-Host ""
  Write-Host "  Web (phone):  $webUrl" -ForegroundColor Green
  Write-Host "  API:          proxied via Next.js (/api -> localhost:4000)" -ForegroundColor Green
  Write-Host ""
  Write-Host "Keep dev-tunnel.cmd running in Terminal 2." -ForegroundColor Cyan
  Write-Host "Phone works on 4G or Wi-Fi - no separate API tunnel needed." -ForegroundColor Cyan
  Write-Host "Restart dev-lan after every dev-tunnel restart (URLs change)." -ForegroundColor Yellow
  Write-Host ""
} else {
  $lanIp = Get-LanIPv4
  if (-not $lanIp) {
    Write-Host "ERROR: Could not detect your Wi-Fi/LAN IP." -ForegroundColor Red
    exit 1
  }

  $webUrl = "http://${lanIp}:3000"
  $apiUrl = "http://${lanIp}:4000"

  Write-LanDevEnv -ProjectRoot $projectRoot -LanIp $lanIp -WebUrl $webUrl -ApiUrl $apiUrl

  $nextAuthUrl = if (Test-GoogleOAuthConfigured -ProjectRoot $projectRoot) {
    'http://localhost:3000'
  } else {
    $webUrl
  }
  $env:NEXTAUTH_URL = $nextAuthUrl
  $env:NEXT_PUBLIC_API_URL = ""
  $env:API_URL = "http://127.0.0.1:4000"
  $env:CORS_ORIGINS = "$webUrl,http://localhost:3000,http://127.0.0.1:3000"
  $env:API_BIND_HOST = "0.0.0.0"
  $env:LAN_DEV_ORIGIN = $lanIp

  Write-DebugLog "lan-config" @{
    lanIp   = $lanIp
    webUrl  = $webUrl
    apiUrl  = $apiUrl
  }

  Write-Host ""
  Write-Host "On THIS laptop:     http://localhost:3000" -ForegroundColor Green
  if ($nextAuthUrl -eq 'http://localhost:3000' -and $webUrl -ne $nextAuthUrl) {
    Write-Host "Google sign-in:     http://localhost:3000/login  (required - Google blocks LAN IPs)" -ForegroundColor Cyan
  }
  Write-Host "On OTHER devices:   $webUrl" -ForegroundColor Green
  Write-Host "API:                proxied via Next.js (/api -> localhost:4000)" -ForegroundColor DarkGray
  Write-Host ""
  Write-Host "IMPORTANT: Do NOT use http://127.0.0.1:3000 on phones/tablets." -ForegroundColor Yellow
  Write-Host "           127.0.0.1 only works on the laptop itself." -ForegroundColor Yellow
  Write-Host ""
  Write-Host "HTTPS on phone (no browser warning): run dev-tunnel.cmd in Terminal 2" -ForegroundColor Cyan
  Write-Host ""
  Write-Host "If other devices cannot connect, allow ports 3000+4000 in Windows Firewall" -ForegroundColor DarkGray
  Write-Host "(Private network only). Run as Admin:" -ForegroundColor DarkGray
  Write-Host '  netsh advfirewall firewall add rule name="DCF Web 3000" dir=in action=allow protocol=TCP localport=3000 profile=private' -ForegroundColor DarkGray
  Write-Host '  netsh advfirewall firewall add rule name="DCF API 4000" dir=in action=allow protocol=TCP localport=4000 profile=private' -ForegroundColor DarkGray
  Write-Host ""
  Write-Host "Keep this window OPEN while others use the site." -ForegroundColor Cyan
  Write-Host ""
}

npm run dev
