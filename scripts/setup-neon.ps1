param(
  [string]$DatabaseUrl
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

Write-Host "`n=== Neon PostgreSQL Setup (No Docker) ===" -ForegroundColor Cyan

function Get-DatabaseUrl {
  param([string]$ExplicitUrl)

  if (-not [string]::IsNullOrWhiteSpace($ExplicitUrl)) {
    return $ExplicitUrl.Trim()
  }

  if (Test-Path ".env.neon") {
    $neonEnv = Get-Content ".env.neon" -Raw
    if ($neonEnv -match 'DATABASE_URL="([^"]+)"') {
      Write-Host "Using DATABASE_URL from .env.neon" -ForegroundColor Green
      return $Matches[1]
    }
    $line = ($neonEnv -split "`n" | Where-Object { $_ -match 'postgresql://' } | Select-Object -First 1)
    if ($line) {
      $line = $line.Trim().Trim('"')
      Write-Host "Using connection string from .env.neon" -ForegroundColor Green
      return $line
    }
  }

  if (Test-Path ".env") {
    $envText = Get-Content ".env" -Raw
    if ($envText -match 'DATABASE_URL="([^"]*(?:neon\.tech|aws\.neon\.tech)[^"]*)"') {
      Write-Host "Neon URL already in .env" -ForegroundColor Green
      return $Matches[1]
    }
  }

  Write-Host ""
  Write-Host "No Neon connection string found." -ForegroundColor Red
  Write-Host ""
  Write-Host "Do this:" -ForegroundColor Yellow
  Write-Host "  1. Copy .env.neon.example to .env.neon"
  Write-Host "  2. Open .env.neon and paste your Neon connection string from:"
  Write-Host "     https://console.neon.tech -> Connect"
  Write-Host "  3. Run: npm run setup:neon"
  Write-Host ""
  exit 1
}

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

$url = Get-DatabaseUrl -ExplicitUrl $DatabaseUrl

if ($url -notmatch 'sslmode=') {
  $separator = if ($url -match '\?') { '&' } else { '?' }
  $url = "$url${separator}sslmode=require"
  Write-Host "Added sslmode=require (required by Neon)" -ForegroundColor Yellow
}

$lines = Get-Content ".env" | Where-Object {
  $_ -notmatch '^(PRISMA_SCHEMA|DEV_DB)='
}
$envText = ($lines -join "`n").TrimEnd()

if ($envText -match 'DATABASE_URL=') {
  $envText = $envText -replace 'DATABASE_URL="[^"]*"', "DATABASE_URL=`"$url`""
} else {
  $envText += "`nDATABASE_URL=`"$url`""
}

Set-Content ".env" ($envText + "`n")
Write-Host "Updated .env for Neon PostgreSQL" -ForegroundColor Green

Write-Host "`nStopping dev servers (free Prisma files)..." -ForegroundColor Cyan
foreach ($port in @(4000, 3000)) {
  Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 2

Write-Host "Generating PostgreSQL Prisma client..." -ForegroundColor Cyan
npx prisma generate --schema prisma/schema.prisma
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running migrations..." -ForegroundColor Cyan
npx prisma migrate dev --name init --schema prisma/schema.prisma
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Seeding data..." -ForegroundColor Cyan
npx tsx prisma/seed.ts
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Neon setup complete!" -ForegroundColor Green
Write-Host "  Run: npm run dev"
Write-Host "  Frontend: http://localhost:3000"
Write-Host "  API:      http://localhost:4000/api/health"
Write-Host ""
