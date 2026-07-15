# auth-retry.ps1 - Re-auth only Railway and Cloudflare (the two that failed)
# Run with: powershell -ExecutionPolicy Bypass -File "C:\Users\user\Desktop\Final Bots\doxedcryptofounder\scripts\auth-retry.ps1"

$ErrorActionPreference = "Continue"
$Npx = "npx"

function Write-Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Err($m)  { Write-Host "[X]  $m" -ForegroundColor Red }

# --- Railway ---
Write-Step "1/2 Railway CLI"
$env:RAILWAY_TOKEN = $null
$rwWho = & $Npx --yes @railway/cli whoami 2>$null
if ($rwWho -and $rwWho -notmatch "UNAUTHENTICATED|Error|not logged") {
    Write-Ok "Railway authed: $rwWho"
} else {
    Write-Host "Opening browser for Railway login..."
    Write-Host "Click Authorize in the browser tab that opens." -ForegroundColor Yellow
    & $Npx --yes @railway/cli login
    if ($LASTEXITCODE -eq 0) {
        $rwWho = & $Npx --yes @railway/cli whoami 2>$null
        Write-Ok "Railway authed: $rwWho"
    } else {
        Write-Err "Railway login failed"
    }
}

# --- Cloudflare ---
Write-Step "2/2 Cloudflare Wrangler"
$cfWho = & $Npx --yes wrangler whoami 2>$null
if ($cfWho -and $cfWho -match "logged in|account") {
    Write-Ok "Cloudflare authed"
} else {
    Write-Host "Opening browser for Cloudflare login..."
    Write-Host "Click Allow in the browser tab that opens (you have 2 minutes)." -ForegroundColor Yellow
    & $Npx --yes wrangler login
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Cloudflare authed"
    } else {
        Write-Err "Cloudflare login failed"
    }
}

Write-Host "`n=== DONE ===" -ForegroundColor Green
Write-Host "Paste this whole output back to me." -ForegroundColor Yellow
