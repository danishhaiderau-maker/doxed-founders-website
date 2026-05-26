$ErrorActionPreference = "Continue"
$projectRoot = Split-Path $PSScriptRoot -Parent

Write-Host "`n=== DoxedCryptoFounder Service Check ===" -ForegroundColor Cyan

function Test-PortOpen([int]$Port) {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    $ok = $async.AsyncWaitHandle.WaitOne(1500, $false)
    if ($ok) { $client.EndConnect($async) }
    $client.Close()
    return $ok
  } catch {
    return $false
  }
}

Write-Host "`n[Docker CLI]"
try {
  $dockerVersion = docker --version 2>&1
  Write-Host "  OK  $dockerVersion" -ForegroundColor Green
} catch {
  Write-Host "  FAIL Docker CLI not found on PATH" -ForegroundColor Red
}

Write-Host "`n[Docker Daemon]"
try {
  docker info 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK  Docker Desktop is running" -ForegroundColor Green
  } else {
    Write-Host "  FAIL Docker Desktop is not running" -ForegroundColor Red
    Write-Host "       Open Docker Desktop and wait until it shows 'Running'" -ForegroundColor Yellow
  }
} catch {
  Write-Host "  FAIL Docker Desktop is not running" -ForegroundColor Red
}

Write-Host "`n[WSL]"
try {
  wsl --status 2>&1
} catch {
  Write-Host "  WARN WSL status unavailable" -ForegroundColor Yellow
}

Write-Host "`n[PostgreSQL :5432]"
if (Test-PortOpen 5432) {
  Write-Host "  OK  Port 5432 is open" -ForegroundColor Green
} else {
  Write-Host "  FAIL Nothing listening on port 5432" -ForegroundColor Red
  Write-Host "       Run: docker compose up -d" -ForegroundColor Yellow
}

Write-Host "`n[Redis :6379]"
if (Test-PortOpen 6379) {
  Write-Host "  OK  Port 6379 is open" -ForegroundColor Green
} else {
  Write-Host "  WARN Nothing listening on port 6379 (optional for Phase 1)" -ForegroundColor Yellow
}

Write-Host "`n[API :4000]"
if (Test-PortOpen 4000) {
  Write-Host "  OK  Port 4000 is open" -ForegroundColor Green
  try {
    $health = Invoke-RestMethod -Uri "http://localhost:4000/api/health" -TimeoutSec 5
    Write-Host "  Health: $($health.status) | database=$($health.services.database)" -ForegroundColor Cyan
  } catch {
    Write-Host "  WARN Port open but health check failed" -ForegroundColor Yellow
  }
} else {
  Write-Host "  INFO API not running (start with: npm run dev:api)" -ForegroundColor DarkGray
}

Write-Host "`n[Frontend :3000]"
if (Test-PortOpen 3000) {
  Write-Host "  OK  Port 3000 is open" -ForegroundColor Green
} else {
  Write-Host "  FAIL Frontend not running" -ForegroundColor Red
  Write-Host "       ERR_CONNECTION_REFUSED on localhost:3000 pages" -ForegroundColor Yellow
  Write-Host "       Run: npm run dev  or  npm run dev:ensure" -ForegroundColor Yellow
}

if ((Test-PortOpen 4000) -and -not (Test-PortOpen 3000)) {
  Write-Host "`n[!] Partial dev: API only - browser pages will fail until frontend starts" -ForegroundColor Red
}

Write-Host "`n=== Next steps ===" -ForegroundColor Cyan
Write-Host "If Docker is down:"
Write-Host "  npm run setup:local    (fastest - local SQLite, no install)"
Write-Host "  npm run setup:repair   (fix WSL, then RESTART PC)"
Write-Host "  npm run setup:neon     (skip Docker - free cloud DB)"
Write-Host ""
Write-Host "If Docker is running:"
Write-Host "1. cd `"$projectRoot`""
Write-Host "2. npm run setup"
Write-Host "3. npm run dev          (starts BOTH web :3000 + api :4000)"
Write-Host "   npm run dev:ensure   (auto-starts whatever is missing)"
Write-Host ""
