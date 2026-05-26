param(
  [int]$WebPort = 3000,
  [int]$ApiPort = 4000,
  [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent)
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "write-dev-env.ps1")

function Get-CloudflaredPath {
  $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }

  $candidates = @(
    (Join-Path $env:ProgramFiles "Cloudflare\cloudflared\cloudflared.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Cloudflare\cloudflared\cloudflared.exe"),
    (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\cloudflared.exe")
  )
  foreach ($path in $candidates) {
    if ($path -and (Test-Path $path)) { return $path }
  }
  return $null
}

function Test-PortOpen([int]$Port) {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    $ok = $async.AsyncWaitHandle.WaitOne(800, $false)
    if ($ok) { $client.EndConnect($async) }
    $client.Close()
    return $ok
  } catch {
    return $false
  }
}

function Get-TunnelUrlFromLog([string]$LogPath, [int]$TimeoutSec = 90) {
  $pattern = 'https://[a-z0-9-]+\.trycloudflare\.com'
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if (Test-Path $LogPath) {
      $text = Get-Content $LogPath -Raw -ErrorAction SilentlyContinue
      if ($text -match "($pattern)") {
        return $Matches[1]
      }
    }
    Start-Sleep -Milliseconds 500
  }
  return $null
}

$cloudflared = Get-CloudflaredPath
if (-not $cloudflared) {
  Write-Host ""
  Write-Host "ERROR: cloudflared not found." -ForegroundColor Red
  Write-Host ""
  Write-Host "Install (pick one):" -ForegroundColor Yellow
  Write-Host "  winget install Cloudflare.cloudflared" -ForegroundColor White
  Write-Host "  https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/" -ForegroundColor DarkGray
  Write-Host ""
  Write-Host "Then close and reopen this terminal, and run dev-tunnel.cmd again." -ForegroundColor Yellow
  exit 1
}

if (-not (Test-PortOpen $WebPort)) {
  Write-Host "ERROR: Nothing listening on http://127.0.0.1:${WebPort}" -ForegroundColor Red
  Write-Host "Start dev servers first (Terminal 1): dev-lan.cmd" -ForegroundColor Yellow
  exit 1
}

if (-not (Test-PortOpen $ApiPort)) {
  Write-Host "ERROR: Nothing listening on http://127.0.0.1:${ApiPort}" -ForegroundColor Red
  Write-Host "Start dev servers first (Terminal 1): dev-lan.cmd" -ForegroundColor Yellow
  exit 1
}

$logDir = Join-Path $env:TEMP "dcf-quick-tunnels"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$webLog = Join-Path $logDir "web.err.log"
$apiLog = Join-Path $logDir "api.err.log"
Remove-Item $webLog, $apiLog -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Starting Cloudflare quick tunnels (free HTTPS)..." -ForegroundColor Cyan
Write-Host "  Web -> http://127.0.0.1:${WebPort}" -ForegroundColor DarkGray
Write-Host "  API -> http://127.0.0.1:${ApiPort}" -ForegroundColor DarkGray
Write-Host ""

$webProc = Start-Process -FilePath $cloudflared -ArgumentList @(
  "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:${WebPort}"
) -RedirectStandardError $webLog -PassThru -WindowStyle Hidden

$apiProc = Start-Process -FilePath $cloudflared -ArgumentList @(
  "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:${ApiPort}"
) -RedirectStandardError $apiLog -PassThru -WindowStyle Hidden

Write-Host "Waiting for trycloudflare.com URLs (up to 90s each)..." -ForegroundColor DarkGray

$webUrl = Get-TunnelUrlFromLog $webLog
$apiUrl = Get-TunnelUrlFromLog $apiLog

if (-not $webUrl) {
  Write-Host "ERROR: Web tunnel URL not found. Log: $webLog" -ForegroundColor Red
  Stop-Process -Id $webProc.Id, $apiProc.Id -Force -ErrorAction SilentlyContinue
  exit 1
}

if (-not $apiUrl) {
  Write-Host "ERROR: API tunnel URL not found. Log: $apiLog" -ForegroundColor Red
  Stop-Process -Id $webProc.Id, $apiProc.Id -Force -ErrorAction SilentlyContinue
  exit 1
}

Write-TunnelDevEnv -ProjectRoot $ProjectRoot -WebUrl $webUrl -ApiUrl $apiUrl

Write-Host ""
Write-Host "=== HTTPS tunnel URLs (copy for your phone) ===" -ForegroundColor Green
Write-Host ""
Write-Host "  Web (open on phone):  $webUrl" -ForegroundColor Green
Write-Host "  API:                  proxied via Next.js (/api -> localhost:4000)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Saved:" -ForegroundColor Cyan
Write-Host "  .env.tunnel.local" -ForegroundColor DarkGray
Write-Host "  apps/web/.env.local   (same-origin /api proxy - no stale tunnel API URL)" -ForegroundColor DarkGray
Write-Host "  apps/api/.env.local   (CORS for tunnel origin)" -ForegroundColor DarkGray
Write-Host ""
Write-Host 'NEXT STEP - restart dev with tunnel URLs:' -ForegroundColor Yellow
Write-Host '  1. Go to Terminal 1 (dev-lan) and press Ctrl+C' -ForegroundColor White
Write-Host '  2. Run dev-lan.cmd again (Next.js reads .env.local on start)' -ForegroundColor White
Write-Host '  3. Open the Web URL above on your phone - works on 4G or Wi-Fi' -ForegroundColor White
Write-Host ""
Write-Host "Verify local API: curl http://127.0.0.1:4000/api/health" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Note: Quick tunnel URLs change every time you restart this script." -ForegroundColor DarkGray
Write-Host "Keep this window OPEN while using the site on your phone." -ForegroundColor Cyan
Write-Host "Press Ctrl+C here to stop both tunnels." -ForegroundColor DarkGray
Write-Host ""

try {
  while (-not $webProc.HasExited -and -not $apiProc.HasExited) {
    Start-Sleep -Seconds 2
  }
  if ($webProc.HasExited) {
    Write-Host "WARN: Web tunnel exited (code $($webProc.ExitCode)). Log: $webLog" -ForegroundColor Red
  }
  if ($apiProc.HasExited) {
    Write-Host "WARN: API tunnel exited (code $($apiProc.ExitCode)). Log: $apiLog" -ForegroundColor Red
  }
} finally {
  Write-Host "Stopping tunnels..." -ForegroundColor DarkGray
  Stop-Process -Id $webProc.Id, $apiProc.Id -Force -ErrorAction SilentlyContinue
}
