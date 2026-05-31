# Sync Railway API variables from secrets vault and redeploy.
# Prerequisite: railway login  (https://railway.app/account/tokens)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

Write-Host "`n=== Fix Railway production API ===" -ForegroundColor Cyan

railway whoami 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Run: railway login" -ForegroundColor Red
  Write-Host "Or: `$env:RAILWAY_TOKEN = 'your-token'; railway whoami" -ForegroundColor Yellow
  exit 1
}

$vaultRoot = Join-Path (Split-Path $root -Parent) "doxedcryptofounder-secrets\vault"
$neonFile = Join-Path $vaultRoot ".env.neon"
$selfHost = Join-Path $vaultRoot ".env.self-host"

function Read-DotEnv($path) {
  $map = @{}
  if (-not (Test-Path $path)) { return $map }
  Get-Content $path | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim().Trim('"')
    if ($v) { $map[$k] = $v }
  }
  return $map
}

$neon = Read-DotEnv $neonFile
$envMap = Read-DotEnv $selfHost
$vercelCheck = Read-DotEnv (Join-Path $vaultRoot ".env.vercel.check")

$dbUrl = $neon["DATABASE_URL"]
if (-not $dbUrl -and $vercelCheck["DATABASE_URL"]) { $dbUrl = $vercelCheck["DATABASE_URL"] }
$jwt = $vercelCheck["JWT_SECRET"]
if (-not $jwt) { $jwt = $envMap["JWT_SECRET"] }
$cors = $envMap["CORS_ORIGINS"]
if (-not $cors) { $cors = "https://doxxedcrypto.digital,https://www.doxxedcrypto.digital,https://doxed-founders-website.vercel.app" }

if (-not $dbUrl) {
  Write-Host "Missing DATABASE_URL in vault\.env.neon or .env.self-host" -ForegroundColor Red
  exit 1
}
if (-not $jwt -or $jwt.Length -lt 32) {
  Write-Host "Missing JWT_SECRET (32+ chars) in vault\.env.self-host" -ForegroundColor Red
  exit 1
}

Write-Host "Link to service: doxed-founders-website (NOT @dcf/web)" -ForegroundColor Yellow
Write-Host "  railway link  -> pick project with doxed-founders-website-production.up.railway.app`n"

$vars = @(
  "DATABASE_URL=$dbUrl",
  "JWT_SECRET=$jwt",
  "NODE_ENV=production",
  "PRISMA_DB_PUSH=true",
  "PRISMA_SCHEMA=prisma/schema.prisma",
  "CORS_ORIGINS=$cors"
)

$credKey = $envMap["CREDENTIALS_ENCRYPTION_KEY"]
if ($credKey) { $vars += "CREDENTIALS_ENCRYPTION_KEY=$credKey" }

foreach ($v in $vars) {
  $name = ($v -split "=", 2)[0]
  Write-Host "Setting $name"
  railway variables set $v 2>&1 | Out-Null
}

Write-Host "`nDeploying (Dockerfile + node scripts/start-api-prod.mjs)..." -ForegroundColor Yellow
railway up --detach

Write-Host "`nDone. Verify:" -ForegroundColor Green
Write-Host "  https://doxed-founders-website-production.up.railway.app/api/health"
Write-Host "  https://doxxedcrypto.digital/api/health"
Write-Host "`nDelete duplicate Railway services @dcf/web and @dcf/api if present (web lives on Vercel)." -ForegroundColor Yellow
