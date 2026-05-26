param(
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

Write-Host "`n=== DoxedCryptoFounder - Production Setup ===" -ForegroundColor Cyan

if (-not (Test-Path ".env.production.example")) {
  Write-Host "Missing .env.production.example" -ForegroundColor Red
  exit 1
}

Write-Host "`n--- Step 1: Neon database ---" -ForegroundColor Yellow
Write-Host "Run: npm run setup:neon"
Write-Host "Paste your Neon connection string when prompted."
$runNeon = Read-Host "Run setup:neon now? (y/N)"
if ($runNeon -eq 'y') {
  npm run setup:neon
}

Write-Host "`n--- Step 2: Local production build check ---" -ForegroundColor Yellow
if (-not $SkipBuild) {
  npm run verify:prod
  if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Host "`n--- Step 3: Railway (API) ---" -ForegroundColor Yellow
Write-Host @"
1. Create project at https://railway.app → Deploy from GitHub repo
2. Set environment variables from .env.production.example:
   - DATABASE_URL (Neon)
   - JWT_SECRET, NEXTAUTH_SECRET (long random strings)
   - NEXTAUTH_URL (your Vercel URL, set after step 4)
   - CORS_ORIGINS (same Vercel URL)
   - PRISMA_DB_PUSH=true  (FIRST deploy only)
   - STRIPE_* (optional)
3. Railway uses railway.toml automatically
4. After deploy: https://YOUR-API.up.railway.app/api/health
"@

Write-Host "`n--- Step 4: Vercel (Web) ---" -ForegroundColor Yellow
Write-Host @"
1. Import repo at https://vercel.com
2. Root Directory: apps/web
3. Environment variables:
   - NEXT_PUBLIC_API_URL = Railway API URL (no trailing slash)
   - NEXTAUTH_URL = https://your-app.vercel.app
   - NEXTAUTH_SECRET = same as Railway JWT secret area (use NEXTAUTH_SECRET)
   - GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (optional)
4. Deploy → open site → test /projects and /paper-trading
"@

Write-Host "`n--- Step 5: Seed production database ---" -ForegroundColor Yellow
Write-Host @"
With DATABASE_URL pointing to Neon (in .env or Railway):
  npm run db:seed
Or run once locally after setup:neon with production DATABASE_URL in .env
"@

Write-Host "`n--- Step 6: Post-deploy ---" -ForegroundColor Yellow
Write-Host @"
- Stripe webhook: https://YOUR-API/api/paper-trading/stripe/webhook
- Google OAuth redirect: https://YOUR-DOMAIN/api/auth/callback/google
- Run: `$env:API_URL='https://YOUR-API'; npm run smoke:test`
- Remove PRISMA_DB_PUSH from Railway after first successful deploy
"@

Write-Host "`n=== Setup guide complete ===" -ForegroundColor Green
Write-Host "Copy .env.production.example values into Railway + Vercel dashboards.`n"
