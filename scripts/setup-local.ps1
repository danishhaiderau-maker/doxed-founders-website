$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

Write-Host "`n=== Local SQLite Setup (No Docker, No Cloud) ===" -ForegroundColor Cyan
Write-Host "Uses a local file database for Phase 1 development.`n"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

$envContent = Get-Content ".env" -Raw
if ($envContent -notmatch 'DEV_DB=sqlite') {
  if ($envContent -match 'DATABASE_URL=') {
    $envContent = $envContent -replace 'DATABASE_URL="[^"]*"', 'DATABASE_URL="file:./prisma/dev.db"'
  } else {
    $envContent += "`nDATABASE_URL=`"file:./prisma/dev.db`"`n"
  }
  if ($envContent -notmatch 'PRISMA_SCHEMA=') {
    $envContent += "PRISMA_SCHEMA=prisma/schema.sqlite.prisma`n"
  }
  $envContent += "DEV_DB=sqlite`n"
  Set-Content ".env" ($envContent.TrimEnd() + "`n")
  Write-Host "Updated .env for local SQLite" -ForegroundColor Green
}

Write-Host "Generating SQLite schema..." -ForegroundColor Cyan
node scripts/generate-sqlite-schema.mjs

Write-Host "Generating Prisma client..." -ForegroundColor Cyan
npx prisma generate --schema prisma/schema.sqlite.prisma
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Pushing schema to dev.db..." -ForegroundColor Cyan
npx prisma db push --schema prisma/schema.sqlite.prisma
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Seeding data..." -ForegroundColor Cyan
npm run db:seed
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Local SQLite setup complete!" -ForegroundColor Green
Write-Host "  Database: prisma/dev.db"
Write-Host "  Run: npm run dev"
Write-Host "  Frontend: http://localhost:3000"
Write-Host "  API:      http://localhost:4000/api/health"
Write-Host ""
Write-Host "Note: Production will use PostgreSQL. Switch back before deploy." -ForegroundColor Yellow
Write-Host ""
