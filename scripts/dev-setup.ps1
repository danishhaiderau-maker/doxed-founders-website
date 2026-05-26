$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

function Test-PortOpen([int]$Port) {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    $ok = $async.AsyncWaitHandle.WaitOne(2000, $false)
    if ($ok) { $client.EndConnect($async) }
    $client.Close()
    return $ok
  } catch {
    return $false
  }
}

function Invoke-DatabaseSetup() {
  Write-Host "Running migrations..." -ForegroundColor Cyan
  npm run db:migrate
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  Write-Host "Seeding data..." -ForegroundColor Cyan
  npm run db:seed
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  Write-Host ""
  Write-Host "Setup complete. Run: npm run dev" -ForegroundColor Green
  Write-Host "  Frontend: http://localhost:3000"
  Write-Host "  API:      http://localhost:4000/api/health"
}

Write-Host "DoxedCryptoFounder setup" -ForegroundColor Cyan

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example"
}

$envContent = Get-Content ".env" -Raw
$usesCloudDb = $envContent -match 'neon\.tech|supabase\.co|\.aws\.neon\.tech'

if ($usesCloudDb) {
  Write-Host "Cloud database detected in .env - skipping Docker" -ForegroundColor Green
  Invoke-DatabaseSetup
  exit 0
}

if (Test-PortOpen 5432) {
  Write-Host "PostgreSQL detected on localhost:5432 - skipping Docker" -ForegroundColor Green
  Invoke-DatabaseSetup
  exit 0
}

Write-Host "Checking Docker..." -ForegroundColor Cyan
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "Docker Desktop is not running and no database found on :5432." -ForegroundColor Red
  Write-Host ""
  Write-Host "Choose ONE option:" -ForegroundColor Yellow
  Write-Host ""
  Write-Host "  Option A - Local SQLite (works immediately, no install):" -ForegroundColor Cyan
  Write-Host "    npm run setup:local"
  Write-Host ""
  Write-Host "  Option B - Fix Docker (recommended long-term):" -ForegroundColor Cyan
  Write-Host "    npm run setup:repair    (runs wsl --update, then RESTART PC)"
  Write-Host "    Open Docker Desktop -> wait for Running"
  Write-Host "    npm run setup"
  Write-Host ""
  Write-Host "  Option C - Cloud DB (no Docker):" -ForegroundColor Cyan
  Write-Host "    npm run setup:neon      (free PostgreSQL from neon.tech)"
  Write-Host ""
  exit 1
}

Write-Host "Starting PostgreSQL + Redis..." -ForegroundColor Cyan
docker compose up -d

Write-Host "Waiting for PostgreSQL..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  docker compose exec -T postgres pg_isready -U dcf -d doxedcryptofounder 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) {
    $ready = $true
    break
  }
  Start-Sleep -Seconds 2
}

if (-not $ready) {
  Write-Host "PostgreSQL did not become ready in time." -ForegroundColor Red
  docker compose ps
  exit 1
}

Invoke-DatabaseSetup
