param(
  [Parameter(Mandatory = $true)][string]$NeonUrl,
  [Parameter(Mandatory = $true)][string]$JwtSecret,
  [Parameter(Mandatory = $true)][string]$CorsOrigins
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

Write-Host "=== Railway API deploy ===" -ForegroundColor Cyan
railway whoami | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Run: railway login" -ForegroundColor Red
  exit 1
}

if (-not (Test-Path ".railway")) {
  railway init --name doxed-founders-api
}

railway variables set `
  "DATABASE_URL=$NeonUrl" `
  "JWT_SECRET=$JwtSecret" `
  "PRISMA_DB_PUSH=true" `
  "PRISMA_SCHEMA=prisma/schema.prisma" `
  "NODE_ENV=production" `
  "CORS_ORIGINS=$CorsOrigins"

Write-Host "Deploying API (first build may take several minutes)..." -ForegroundColor Yellow
railway up --detach

$domain = railway domain 2>&1
Write-Host "API URL: $domain" -ForegroundColor Green
Write-Host "Health: $domain/api/health" -ForegroundColor Green
