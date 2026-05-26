param(
  [switch]$SkipBuild,
  [switch]$SkipFirewall,
  [switch]$UseDockerPostgres,
  [switch]$NonInteractive,
  [string]$SiteUrl = "",
  [string]$ApiUrl = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

function Write-Section($title) {
  Write-Host "`n--- $title ---" -ForegroundColor Yellow
}

function New-Secret([int]$Bytes = 32) {
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $buffer = New-Object byte[] $Bytes
  $rng.GetBytes($buffer)
  return [Convert]::ToBase64String($buffer) -replace '[+/=]', 'x'
}

function Test-Command($name) {
  return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

Write-Host "`n=== DoxedCryptoFounder - Secure Self-Host Setup ===" -ForegroundColor Cyan
Write-Host "Host from your laptop (~`$0/month). Public HTTPS via Cloudflare Tunnel.`n" -ForegroundColor DarkGray

Write-Section "Security principles (read this)"
Write-Host @"
  OK   Cloudflare Tunnel - HTTPS without opening router ports
  OK   API + Web bind to 127.0.0.1 only (localhost, not your Wi‑Fi)
  OK   Strong auto-generated JWT / NextAuth secrets
  OK   SQLite file DB on disk (simple backup: copy prisma/selfhost.db)

  DO NOT port-forward 3000 or 4000 on your router
  DO NOT run 'npm run dev' for public access - use production builds
  DO NOT commit .env.self-host or share your secrets
  DO change admin password after first login (default: Admin123!)
"@ -ForegroundColor White

Write-Section "Docker Desktop - what it is (optional here)"
Write-Host @"
  Docker Desktop is FREE for personal / small-business use on Windows.

  What it does:
    - Runs lightweight Linux containers on your PC (like mini virtual servers)
    - Your repo's docker-compose can run PostgreSQL in a container locally
    - It does NOT publish your website to the internet by itself

  For this experiment:
    - Recommended: SQLite (no Docker needed) - setup below uses this by default
    - Optional: Docker Postgres if you want PostgreSQL practice
    - You still need Cloudflare Tunnel for a public https:// URL

  Docker is NOT a replacement for Railway/Vercel - it's just a way to run
  databases/services locally in isolated boxes.
"@ -ForegroundColor DarkGray

$nodeOk = Test-Command node
if (-not $nodeOk) {
  Write-Host "FAIL  Node.js 20+ is required. Install from https://nodejs.org" -ForegroundColor Red
  exit 1
}

Write-Section "Step 1 - Environment file + secrets"
$envFile = ".env.self-host"
if (-not (Test-Path $envFile)) {
  Copy-Item ".env.self-host.example" $envFile
  Write-Host "Created $envFile from template" -ForegroundColor Green
} else {
  Write-Host "Using existing $envFile" -ForegroundColor DarkGray
}

$jwt = New-Secret
$nextAuth = New-Secret
$content = Get-Content $envFile -Raw
$content = $content -replace 'JWT_SECRET="[^"]*"', "JWT_SECRET=`"$jwt`""
$content = $content -replace 'NEXTAUTH_SECRET="[^"]*"', "NEXTAUTH_SECRET=`"$nextAuth`""
Set-Content $envFile ($content.TrimEnd() + "`n")

Write-Host "Generated new JWT_SECRET and NEXTAUTH_SECRET" -ForegroundColor Green

if ($NonInteractive) {
  if (-not $SiteUrl) { $SiteUrl = "http://127.0.0.1:3000" }
  if (-not $ApiUrl) { $ApiUrl = "http://127.0.0.1:4000" }
  Write-Host "Non-interactive mode - Site: $SiteUrl  API: $ApiUrl" -ForegroundColor DarkGray
} else {
  Write-Host "`nEnter your public URLs (from Cloudflare Tunnel + domain):" -ForegroundColor Cyan
  Write-Host "  Example site: https://doxed.example.com" -ForegroundColor DarkGray
  Write-Host "  Example API:  https://api.doxed.example.com" -ForegroundColor DarkGray
  Write-Host "  Press Enter to skip and use localhost for now." -ForegroundColor DarkGray
  $SiteUrl = Read-Host "Site URL (NEXTAUTH_URL)"
  $ApiUrl = Read-Host "API URL (NEXT_PUBLIC_API_URL)"
}

if ($SiteUrl -and $ApiUrl) {
  $content = Get-Content $envFile -Raw
  $content = $content -replace 'NEXTAUTH_URL="[^"]*"', "NEXTAUTH_URL=`"$SiteUrl`""
  $content = $content -replace 'CORS_ORIGINS="[^"]*"', "CORS_ORIGINS=`"$SiteUrl`""
  $content = $content -replace 'API_URL="[^"]*"', "API_URL=`"$ApiUrl`""
  $content = $content -replace 'NEXT_PUBLIC_API_URL="[^"]*"', "NEXT_PUBLIC_API_URL=`"$ApiUrl`""
  Set-Content $envFile ($content.TrimEnd() + "`n")
  Write-Host "Updated URLs in $envFile" -ForegroundColor Green
} else {
  Write-Host "SKIP  URLs not set - edit $envFile before going public" -ForegroundColor Yellow
}

Write-Section "Step 2 - Database"
if ($UseDockerPostgres) {
  if (-not (Test-Command docker)) {
    Write-Host "FAIL  Docker not found. Install Docker Desktop or re-run without -UseDockerPostgres" -ForegroundColor Red
    exit 1
  }
  Write-Host "Starting Postgres (localhost:5432 only)..." -ForegroundColor Cyan
  docker compose -f docker-compose.self-host.yml up -d
  $content = Get-Content $envFile -Raw
  $content = $content -replace 'DATABASE_URL="[^"]*"', 'DATABASE_URL="postgresql://dcf:dcf_selfhost_change_me@127.0.0.1:5432/doxedcryptofounder?schema=public"'
  $content = $content -replace 'PRISMA_SCHEMA="[^"]*"', 'PRISMA_SCHEMA="prisma/schema.prisma"'
  $content = $content -replace 'DEV_DB=sqlite', '# DEV_DB=sqlite'
  Set-Content $envFile ($content.TrimEnd() + "`n")
  Write-Host "WARN  Change POSTGRES_PASSWORD in docker-compose.self-host.yml before production use" -ForegroundColor Yellow
} else {
  Write-Host "Using SQLite (prisma/selfhost.db) - no Docker required" -ForegroundColor Green
  node scripts/bootstrap-self-host.mjs
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  Write-Host "OK    SQLite database ready" -ForegroundColor Green
}

Write-Section "Step 3 - Production build"
if (-not $SkipBuild) {
  npm run build:utils
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  npx prisma generate --schema $(
    if ($UseDockerPostgres) { 'prisma/schema.prisma' } else { 'prisma/schema.sqlite.prisma' }
  )
  npm run build --workspace=@dcf/api
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  npm run build --workspace=@dcf/web
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  Write-Host "OK    Production builds complete" -ForegroundColor Green
} else {
  Write-Host "SKIP  Build skipped (-SkipBuild)" -ForegroundColor Yellow
}

Write-Section "Step 4 - Windows Firewall (block public inbound to app ports)"
if (-not $SkipFirewall) {
  $ports = @(3000, 4000)
  foreach ($p in $ports) {
    $ruleName = "DCF Self-Host Block Public TCP $p"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
      Write-Host "OK    Firewall rule already exists for port $p" -ForegroundColor DarkGray
    } else {
      try {
        New-NetFirewallRule `
          -DisplayName $ruleName `
          -Direction Inbound `
          -LocalPort $p `
          -Protocol TCP `
          -Action Block `
          -Profile Public `
          -ErrorAction Stop | Out-Null
        Write-Host "OK    Blocked public inbound on port $p (Cloudflare Tunnel still works)" -ForegroundColor Green
      } catch {
        Write-Host "WARN  Could not create firewall rule for port $p (run PowerShell as Administrator)" -ForegroundColor Yellow
      }
    }
  }
} else {
  Write-Host "SKIP  Firewall rules skipped (-SkipFirewall)" -ForegroundColor Yellow
}

Write-Section "Step 5 - Cloudflare Tunnel (public HTTPS, no router ports)"
Write-Host @"
  Install:  winget install Cloudflare.cloudflared
  Login:    cloudflared tunnel login
  Create:   cloudflared tunnel create dcf-home
  Config:   copy config\cloudflared.self-host.example.yml → %USERPROFILE%\.cloudflared\config.yml
  DNS:      cloudflared tunnel route dns dcf-home YOUR-DOMAIN.com
            cloudflared tunnel route dns dcf-home api.YOUR-DOMAIN.com

  Quick test (no domain, temporary URL):
    Terminal 1: npm run start:self-host
    Terminal 2: cloudflared tunnel --url http://127.0.0.1:3000
    (API still needs api.* URL - use a domain for the full app)
"@ -ForegroundColor White

Write-Section "Step 6 - Start commands"
Write-Host @"
  Start app:     npm run start:self-host
  Stop app:      npm run stop:self-host
  Start tunnel:  cloudflared tunnel run dcf-home

  Local only:    http://127.0.0.1:3000  (web)
                 http://127.0.0.1:4000/api/health  (api)

  After seed, sign in and CHANGE the admin password:
    admin@doxedcryptofounder.local / Admin123!
"@ -ForegroundColor Green

Write-Host "`n=== Self-host setup complete ===" -ForegroundColor Cyan
Write-Host "Your secrets live in $envFile - keep it private.`n" -ForegroundColor DarkGray
