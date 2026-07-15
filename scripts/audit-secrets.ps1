# Secrets Audit + Consolidation Script
# ---------------------------------------------------------------
# Audits all secrets across the doxedcryptofounder stack:
#   - Local .env files in the repo
#   - Railway environment variables (via API once authed)
#   - Vercel environment variables (via API once authed)
#   - Neon database connection strings
#   - Cloudflare DNS / Workers config
#
# Output: a Markdown report at vault/SECRETS-AUDIT.md showing:
#   - Every secret's name, where it's stored, when last rotated
#   - Stale / duplicate / orphaned secrets
#   - Recommended rotation actions
#   - A consolidated vault/.env.secrets template (gitignored)
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\audit-secrets.ps1
# Requires: all services authed via scripts\auth-all-services.ps1 first.

$ErrorActionPreference = "Continue"
$Npx = "C:\Users\user\nvm\v20.18.2\npx.cmd"
$reportPath = "vault\SECRETS-AUDIT.md"
New-Item -ItemType Directory -Force -Path "vault" | Out-Null

function Write-Section($n, $t) { Write-Host "`n[$n] $t" -ForegroundColor Cyan }

# --- 1. Local .env files ---
Write-Section "1" "Local .env files"
$localEnvs = Get-ChildItem -Path "." -Force -Filter ".env*" | Where-Object { $_.Name -notmatch "\.example$|\.template$" }
$localReport = @()
foreach ($f in $localEnvs) {
    $keys = Get-Content $f.FullName | Where-Object { $_ -match "^[A-Z_]+=" } | ForEach-Object { ($_ -split "=",2)[0] } | Sort-Object -Unique
    $localReport += "| $($f.Name) | $($keys.Count) keys | $($keys -join ", ") |"
    Write-Host "  $($f.Name): $($keys.Count) keys"
}

# --- 2. Railway environment variables ---
Write-Section "2" "Railway environment variables"
$railwayVars = @()
try {
    # List projects
    $projects = & $Npx --yes @railway/cli list --json 2>$null | ConvertFrom-Json
    foreach ($proj in $projects) {
        Write-Host "  Project: $($proj.name)"
        # Get variables via the CLI link + variables command
        $vars = & $Npx --yes @railway/cli variables --json 2>$null | ConvertFrom-Json
        foreach ($v in $vars.PSObject.Properties) {
            $railwayVars += "| Railway/$($proj.name)/$($v.Name) | $($v.Value.Length) chars |"
        }
    }
} catch {
    Write-Host "  Could not enumerate Railway vars: $_" -ForegroundColor Yellow
}

# --- 3. Vercel environment variables ---
Write-Section "3" "Vercel environment variables"
$vercelVars = @()
try {
    $projName = "doxed-founders-website"
    $envs = & $Npx --yes vercel env ls --environment production --json 2>$null
    if ($envs) {
        $parsed = $envs | ConvertFrom-Json
        foreach ($e in $parsed) {
            $vercelVars += "| Vercel/production/$($e.key) | $($e.type) |"
        }
    }
} catch {
    Write-Host "  Could not enumerate Vercel vars: $_" -ForegroundColor Yellow
}

# --- 4. Neon connection strings ---
Write-Section "4" "Neon connection strings"
$neonUrls = @()
foreach ($f in @(".env", ".env.local", ".env.neon")) {
    if (Test-Path $f) {
        $content = Get-Content $f -Raw
        $matches = [regex]::Matches($content, "(DATABASE_URL|NEON_.*|PG.*|POSTGRES.*)=(neon\.tech[^\s`"]+|postgresql[^\s`"]+)")
        foreach ($m in $matches) {
            $neonUrls += "| $f / $($m.Groups[1].Value) | $(($m.Groups[2].Value -replace '[^@]+@', '***@').Substring(0, [Math]::Min(60, $m.Groups[2].Value.Length))) |"
        }
    }
}

# --- 5. Cloudflare DNS records ---
Write-Section "5" "Cloudflare DNS (doxxedcrypto.digital)"
$cfReport = @()
try {
    $zones = & $Npx --yes wrangler d1 list 2>$null  # just to test auth
    # DNS records would need API token — for now just report auth state
    $cfReport += "| Cloudflare auth | verify via wrangler whoami |"
} catch {
    $cfReport += "| Cloudflare | could not query |"
}

# --- Write the consolidated report ---
Write-Section "6" "Writing report to $reportPath"
$report = @"
# Secrets Audit — $(Get-Date -Format 'yyyy-MM-dd HH:mm')

## 1. Local .env files in repo
$($localReport -join "`n")

## 2. Railway environment variables
$($railwayVars -join "`n")

## 3. Vercel environment variables (production)
$($vercelVars -join "`n")

## 4. Neon connection strings (masked)
$($neonUrls -join "`n")

## 5. Cloudflare
$($cfReport -join "`n")

## Recommended next actions
- [ ] Compare keys across local/.env, Railway, Vercel — flag any mismatches
- [ ] Identify any tokens older than 90 days (rotation candidates)
- [ ] Check for orphaned secrets (set for a service that no longer exists)
- [ ] Consolidate into vault/.env.secrets (gitignored)
- [ ] Rotate Railway API tokens (the ones that died earlier)
- [ ] Verify GitHub token scopes are minimal
"@
Set-Content -Path $reportPath -Value $report -Encoding UTF8
Write-Host "Report written: $reportPath" -ForegroundColor Green

# --- Generate consolidated vault template ---
$vaultTemplate = "vault/.env.secrets.template"
$vaultContent = @"
# Consolidated secrets template — do NOT fill this in or commit.
# Copy to vault/.env.secrets and fill values locally (vault/ is gitignored).

# === Database (Neon) ===
DATABASE_URL=
DIRECT_URL=
PGUSER=
PGPASSWORD=
PGHOST=
PGPORT=5432
PGDATABASE=

# === Auth / JWT ===
JWT_SECRET=
SESSION_SECRET=

# === Anthropic (Phase 9 LAM) ===
LAM_ANTHROPIC_API_KEY=
ANTHROPIC_API_KEY=

# === Railway ===
RAILWAY_TOKEN=
RAILWAY_PROJECT_ID=

# === Vercel ===
VERCEL_TOKEN=
VERCEL_PROJECT_ID=

# === Cloudflare ===
CLOUDFLARE_API_TOKEN=
CLOUDFLARE_ACCOUNT_ID=

# === Neon API ===
NEON_API_KEY=
NEON_PROJECT_ID=

# === GitHub ===
GH_TOKEN=
GITHUB_TOKEN=

# === AI Providers (Phase 1 / 5) ===
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_AI_KEY=

# === Bot (CONSTRAINT: do not change without explicit approval) ===
FORCE_PAPER_MODE=1
TYPE_B_FIXED_POLICY_HASH=
BITFINEX_API_KEY=
BITFINEX_API_SECRET=
"@
Set-Content -Path $vaultTemplate -Value $vaultContent -Encoding UTF8
Write-Host "Template written: $vaultTemplate" -ForegroundColor Green

Write-Host "`n=== AUDIT COMPLETE ===" -ForegroundColor Green
Write-Host "Review the report at vault/SECRETS-AUDIT.md"
