# fly-setup-volume.ps1
# One-time helper to create the Fly persistent volume + verify the writable dirs
# are symlinks to it. Run from the repo root after `flyctl auth login`.
#
#   powershell -ExecutionPolicy Bypass -File deploy\scripts\fly-setup-volume.ps1
#
# Prereqs:
#   - flyctl installed + authenticated
#   - app `doxed-btc-bot` created (flyctl apps create doxed-btc-bot)
#
# What it does:
#   1. Creates the `bot_data` volume in syd (1GB) if it doesn't already exist.
#   2. Prints the machine list so you can copy a machine ID into FLY_MACHINE_ID.
#   3. Reminds you to set the volume name in fly.toml (already wired) and the
#      BOT_DATA_DIR env (already set to /app/data in the Dockerfile).
#
# The actual symlink of research/ -> /app/data/research happens at container boot
# via services/btc-conservative-agent/fly-entrypoint.sh — no manual SSH needed.

[CmdletBinding()]
param(
    [string]$AppName = "doxed-btc-bot",
    [string]$Region = "syd",
    [int]$SizeGb = 1
)

$ErrorActionPreference = "Stop"

Write-Host "==> Ensuring flyctl is logged in..."
flyctl auth whoami 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Not logged in. Run: flyctl auth login"
    exit 1
}

Write-Host "==> Creating volume bot_data ($SizeGb GB, $Region) for $AppName (idempotent)..."
$existing = flyctl volumes list --app $AppName 2>$null | Select-String "bot_data"
if ($existing) {
    Write-Host "Volume bot_data already exists — skipping create."
} else {
    flyctl volumes create bot_data --app $AppName --region $Region --size $SizeGb
    if ($LASTEXITCODE -ne 0) { Write-Host "Volume create failed."; exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "==> Machines for $AppName (copy one machine ID into FLY_MACHINE_ID on Railway):"
flyctl machines list --app $AppName

Write-Host ""
Write-Host "Done. Next: set secrets (flyctl secrets set ...), then `fly deploy`."
