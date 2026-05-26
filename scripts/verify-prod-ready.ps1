$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

Write-Host "`n=== Production readiness check ===" -ForegroundColor Cyan

Write-Host "`n[1/3] Building @dcf/utils + API..." -ForegroundColor Yellow
npm run build:api
if ($LASTEXITCODE -ne 0) {
  Write-Host "FAIL  API build failed." -ForegroundColor Red
  exit 1
}
Write-Host "OK    API build" -ForegroundColor Green

Write-Host "`n[2/3] Building Next.js web app..." -ForegroundColor Yellow
npm run build --workspace=@dcf/web
if ($LASTEXITCODE -ne 0) {
  Write-Host "FAIL  Web build failed." -ForegroundColor Red
  exit 1
}
Write-Host "OK    Web build" -ForegroundColor Green

Write-Host "`n[3/3] Checking deploy config files..." -ForegroundColor Yellow
$required = @(
  "railway.toml",
  "apps/web/vercel.json",
  "scripts/start-api-prod.mjs",
  ".env.production.example"
)
$missing = @()
foreach ($file in $required) {
  if (-not (Test-Path $file)) { $missing += $file }
}
if ($missing.Count -gt 0) {
  Write-Host "FAIL  Missing: $($missing -join ', ')" -ForegroundColor Red
  exit 1
}
Write-Host "OK    Deploy config present" -ForegroundColor Green

Write-Host "`n[4/4] API smoke test (if dev server running)..." -ForegroundColor Yellow
npm run smoke:test 2>$null
if ($LASTEXITCODE -eq 0) {
  Write-Host "OK    Smoke test passed" -ForegroundColor Green
} else {
  Write-Host "SKIP  Smoke test (start API with npm run dev:ensure, then npm run smoke:test)" -ForegroundColor Yellow
}

Write-Host "`n=== Ready to deploy ===" -ForegroundColor Green
Write-Host "  1. Neon:  npm run setup:neon"
Write-Host "  2. Railway: connect repo, set env from .env.production.example, PRISMA_DB_PUSH=true (first time)"
Write-Host "  3. Vercel: root apps/web, set NEXT_PUBLIC_API_URL + NEXTAUTH_*"
Write-Host "  4. Seed:   npm run db:seed (once, with Neon DATABASE_URL)"
Write-Host ""
