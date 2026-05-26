$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

function Test-PortOpen([int]$Port) {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    $ok = $async.AsyncWaitHandle.WaitOne(800, $false)
    if ($ok) { $client.EndConnect($async) }
    $client.Close()
    return $ok
  } catch { return $false }
}

function Wait-ForPorts([int[]]$Ports, [int]$TimeoutSec = 120) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $all = $true
    foreach ($p in $Ports) {
      if (-not (Test-PortOpen $p)) { $all = $false; break }
    }
    if ($all) { return $true }
    Start-Sleep -Seconds 2
  }
  return $false
}

Write-Host ""
Write-Host "=== Starting dev + Cloudflare tunnels ===" -ForegroundColor Cyan

& (Join-Path $PSScriptRoot "stop-self-host.ps1")
& (Join-Path $PSScriptRoot "stop-dev.ps1")
Start-Sleep -Seconds 2

Write-Host "[1/4] Starting dev servers (first pass)..." -ForegroundColor Yellow
$devJob = Start-Process powershell -ArgumentList @(
  "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-Command", "Set-Location '$projectRoot'; npm run dev"
) -PassThru -WindowStyle Normal

if (-not (Wait-ForPorts @(3000, 4000) -TimeoutSec 120)) {
  Write-Host "ERROR: Dev servers did not start on 3000/4000 in time." -ForegroundColor Red
  Stop-Process -Id $devJob.Id -Force -ErrorAction SilentlyContinue
  exit 1
}

Write-Host "[2/4] Starting Cloudflare tunnels..." -ForegroundColor Yellow
$tunnelJob = Start-Process powershell -ArgumentList @(
  "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-File", (Join-Path $PSScriptRoot "start-quick-tunnels.ps1")
) -PassThru -WindowStyle Normal

$tunnelEnv = Join-Path $projectRoot ".env.tunnel.local"
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
  if ((Test-Path $tunnelEnv) -and (Test-Path (Join-Path $projectRoot "apps\web\.env.local"))) {
    break
  }
  Start-Sleep -Seconds 2
}

if (-not (Test-Path $tunnelEnv)) {
  Write-Host "ERROR: Tunnels failed - .env.tunnel.local not created." -ForegroundColor Red
  exit 1
}

Write-Host "[3/4] Restarting dev with tunnel URLs..." -ForegroundColor Yellow
& (Join-Path $PSScriptRoot "stop-dev.ps1")
Start-Sleep -Seconds 3
Stop-Process -Id $devJob.Id -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$devJob = Start-Process powershell -ArgumentList @(
  "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-File", (Join-Path $PSScriptRoot "dev-lan.ps1")
) -PassThru -WindowStyle Normal

if (-not (Wait-ForPorts @(3000, 4000) -TimeoutSec 180)) {
  Write-Host "ERROR: Dev restart failed." -ForegroundColor Red
  exit 1
}

Write-Host "[4/4] Done." -ForegroundColor Green
Write-Host ""
Get-Content $tunnelEnv | ForEach-Object {
  if ($_ -match 'NEXTAUTH_URL=') {
    Write-Host "Phone (Web): $($_.Split('=')[1].Trim().Trim('"'))" -ForegroundColor Green
  }
}
Write-Host ""
Write-Host "Keep both PowerShell windows open while using the site." -ForegroundColor Cyan
Write-Host ""
