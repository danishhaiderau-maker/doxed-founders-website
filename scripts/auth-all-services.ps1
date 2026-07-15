# Service Authentication Script
# ---------------------------------------------------------------
# Logs you into all cloud services used by the doxedcryptofounder stack.
# Each login opens a browser popup on YOUR machine where you click "Authorize".
# After completion, all CLIs have local session credentials that auto-refresh.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\auth-all-services.ps1
#
# What gets stored where (all under your user profile, none in the repo):
#   GitHub        -> gh CLI session (~/.config/gh/)
#   Vercel        -> ~/.local/share/com.vercel.cli/
#   Railway       -> ~/.railway/sessions
#   Cloudflare    -> ~/.wrangler/config/default.toml
#   Neon          -> ~/.neonctl/
#   Fly.io        -> ~/.fly/config.yml (optional, only if you have an account)
#
# Safe to re-run anytime. Already-authed services are skipped.

$ErrorActionPreference = "Continue"
$Node = "C:\Users\user\nvm\v20.18.2\node.exe"
$Npx = "C:\Users\user\nvm\v20.18.2\npx.cmd"

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!]  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[X]  $msg" -ForegroundColor Red }

# --- 1. GitHub --------------------------------------------------------------
Write-Step "1/6 GitHub CLI"
$ghStatus = gh auth status 2>&1
if ($ghStatus -match "Logged in") {
    Write-Ok "GitHub already authed: $(($ghStatus -split "`n")[1].Trim())"
} else {
    Write-Host "Opening browser for GitHub login..."
    gh auth login --web --git-protocol https
    if ($LASTEXITCODE -eq 0) { Write-Ok "GitHub authed" } else { Write-Err "GitHub login failed" }
}

# --- 2. Vercel --------------------------------------------------------------
Write-Step "2/6 Vercel CLI"
$vercelWho = & $Npx --yes vercel whoami 2>$null
if ($vercelWho -and $vercelWho -notmatch "Error|error|not authenticated") {
    Write-Ok "Vercel already authed: $vercelWho"
} else {
    Write-Host "Opening browser for Vercel login..."
    & $Npx --yes vercel login
    if ($LASTEXITCODE -eq 0) { Write-Ok "Vercel authed" } else { Write-Err "Vercel login failed" }
}

# --- 3. Railway -------------------------------------------------------------
Write-Step "3/6 Railway CLI"
# Clear any stale RAILWAY_TOKEN env vars that interfere with browser login
$env:RAILWAY_TOKEN = $null
$userRailwayToken = [System.Environment]::GetEnvironmentVariable("RAILWAY_TOKEN","User")
if ($userRailwayToken) {
    Write-Warn "Found stale user-level RAILWAY_TOKEN (length $($userRailwayToken.Length)). Removing it..."
    [System.Environment]::SetEnvironmentVariable("RAILWAY_TOKEN", $null, "User")
    Write-Ok "Stale RAILWAY_TOKEN removed from user env"
}
$railwayWho = & $Npx --yes @railway/cli whoami 2>$null
if ($railwayWho -and $railwayWho -notmatch "Unauthorized|error|Error") {
    Write-Ok "Railway already authed: $railwayWho"
} else {
    Write-Host "Opening browser for Railway login..."
    & $Npx --yes @railway/cli login
    if ($LASTEXITCODE -eq 0) {
        $newWho = & $Npx --yes @railway/cli whoami 2>$null
        Write-Ok "Railway authed: $newWho"
    } else {
        Write-Err "Railway login failed"
    }
}

# --- 4. Cloudflare ----------------------------------------------------------
Write-Step "4/6 Cloudflare Wrangler"
$cfWho = & $Npx --yes wrangler whoami 2>&1
if ($cfWho -match "logged in|You are logged in") {
    Write-Ok "Cloudflare already authed"
} else {
    Write-Host "Opening browser for Cloudflare login..."
    & $Npx --yes wrangler login
    if ($LASTEXITCODE -eq 0) { Write-Ok "Cloudflare authed" } else { Write-Err "Cloudflare login failed" }
}

# --- 5. Neon ----------------------------------------------------------------
Write-Step "5/6 Neon CLI"
$neonWho = & $Npx --yes neonctl whoami 2>$null
if ($neonWho -and $neonWho -notmatch "Error|error|not authenticated|UNAUTHENTICATED") {
    Write-Ok "Neon already authed: $neonWho"
} else {
    Write-Host "Opening browser for Neon login..."
    & $Npx --yes neonctl auth
    if ($LASTEXITCODE -eq 0) { Write-Ok "Neon authed" } else { Write-Warn "Neon login failed (we have DATABASE_URL working already, may not need this)" }
}

# --- 6. Fly.io (optional) ---------------------------------------------------
Write-Step "6/6 Fly.io (optional - only if you have a Fly account)"
$flyWho = & $Npx --yes flyctl auth whoami 2>$null
if ($flyWho -and $flyWho -notmatch "Error|error|not logged") {
    Write-Ok "Fly.io already authed: $flyWho"
} else {
    $doFly = Read-Host "Do you have a Fly.io account to log into? (y/N)"
    if ($doFly -match "^[yY]") {
        & $Npx --yes flyctl auth login
        if ($LASTEXITCODE -eq 0) { Write-Ok "Fly.io authed" } else { Write-Warn "Fly.io login skipped/failed" }
    } else {
        Write-Warn "Skipping Fly.io (not in use for this stack)"
    }
}

# --- Summary ----------------------------------------------------------------
Write-Step "AUTHENTICATION SUMMARY"
Write-Host "Re-running whoami on each service..."
Write-Host ""
Write-Host "GitHub:     $(gh auth status 2>&1 | Select-String 'Logged in' | Select-Object -First 1)"
Write-Host "Vercel:     $(& $Npx --yes vercel whoami 2>$null)"
Write-Host "Railway:    $(& $Npx --yes @railway/cli whoami 2>$null)"
$cfStatus = & $Npx --yes wrangler whoami 2>&1 | Out-String
Write-Host "Cloudflare: $(if ($cfStatus -match 'logged in') {'authed'} else {'NOT authed'})"
Write-Host "Neon:       $(& $Npx --yes neonctl whoami 2>$null)"
Write-Host ""
Write-Host "If any show errors, re-run this script or the specific login command." -ForegroundColor Yellow
Write-Host "All session credentials are stored in your user profile (NOT in the git repo)." -ForegroundColor Green
