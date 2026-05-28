# Bootstrap X login (Vercel) + X automation (Railway) from .env.x.secrets (gitignored).
# Copy .env.x.secrets.example to .env.x.secrets and fill values, then:
#   npm run bootstrap:x-automation

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$secretsFile = Join-Path $root ".env.x.secrets"
if (-not (Test-Path $secretsFile)) {
  Write-Host "Missing .env.x.secrets - copy .env.x.secrets.example and fill in X Developer keys." -ForegroundColor Red
  exit 1
}

function Read-DotEnv($path) {
  $map = @{}
  Get-Content $path | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim().Trim('"')
    $map[$k] = $v
  }
  return $map
}

$env = Read-DotEnv $secretsFile
$apiUrl = $env["API_URL"]
if (-not $apiUrl) { $apiUrl = "https://doxed-founders-website-production.up.railway.app" }

Write-Host "=== 1. Vercel - X login (OAuth 2.0) ===" -ForegroundColor Cyan
Push-Location (Join-Path $root "apps\web")
$vercelVars = @(
  "TWITTER_CLIENT_ID",
  "TWITTER_CLIENT_SECRET",
  "TWITTER_API_KEY",
  "TWITTER_API_SECRET"
)
foreach ($name in $vercelVars) {
  $val = $env[$name]
  if (-not $val) { continue }
  Write-Host "  Setting Vercel production: $name"
  $val | vercel env add $name production --force 2>&1 | Out-Null
}
Pop-Location

Write-Host "=== 2. Admin JWT for daily cron ===" -ForegroundColor Cyan
$adminEmail = $env["ADMIN_EMAIL"]
$adminPassword = $env["ADMIN_PASSWORD"]
if ($adminEmail -and $adminPassword) {
  try {
    $loginBody = @{ email = $adminEmail; password = $adminPassword } | ConvertTo-Json
    $login = Invoke-RestMethod -Uri "$apiUrl/api/auth/login" -Method POST -Body $loginBody -ContentType "application/json"
    $jwt = $login.accessToken
    if ($jwt) {
      Write-Host "  Admin JWT obtained"
      Push-Location (Join-Path $root "apps\web")
      $jwt | vercel env add ADMIN_SYNC_JWT production --force 2>&1 | Out-Null
      Pop-Location
      $env["ADMIN_SYNC_JWT"] = $jwt
    }
  } catch {
    Write-Host "  Could not login admin: $_" -ForegroundColor Yellow
  }
} else {
  Write-Host "  Set ADMIN_EMAIL + ADMIN_PASSWORD in .env.x.secrets to auto-fetch JWT" -ForegroundColor Yellow
}

Write-Host "=== 3. Railway variables (run: npm run apply:railway-x after railway login) ===" -ForegroundColor Cyan
$railwayKeys = @(
  "TWITTER_BEARER_TOKEN",
  "TWITTER_API_KEY",
  "TWITTER_API_SECRET",
  "TWITTER_ACCESS_TOKEN",
  "TWITTER_ACCESS_TOKEN_SECRET",
  "X_BRAND_HANDLE",
  "PUBLIC_SITE_URL",
  "ADMIN_SYNC_JWT"
)
foreach ($key in $railwayKeys) {
  if ($env[$key]) {
    Write-Host "  $key = (ready in .env.x.secrets)"
  }
}

Write-Host ""
Write-Host "=== 4. GitHub Actions secrets (repo Settings > Secrets) ===" -ForegroundColor Cyan
Write-Host "  API_URL = $apiUrl"
if ($env["ADMIN_SYNC_JWT"]) {
  Write-Host "  ADMIN_SYNC_JWT = (add same JWT to GitHub Secrets for daily cron)"
}

Write-Host ""
Write-Host "=== 5. X Developer Console callbacks ===" -ForegroundColor Cyan
Write-Host "  User login: https://doxxedcrypto.digital/api/auth/callback/twitter"
Write-Host "  Enable OAuth 2.0 + Read and Write for @Bitbro4crypto app"
Write-Host ""
Write-Host "=== 6. Redeploy ===" -ForegroundColor Cyan
Write-Host "  cd apps/web && vercel --prod"
Write-Host "  Railway: redeploy API after npm run apply:railway-x"
Write-Host ""
Write-Host "Check status: GET $apiUrl/api/x-social/status" -ForegroundColor Green
