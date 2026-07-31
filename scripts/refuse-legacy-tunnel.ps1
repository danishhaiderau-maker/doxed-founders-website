# Shared fail-closed guard for retired laptop/Cloudflare bot tunnel commands.
$legacyTunnelRepoRoot = Split-Path -Parent $PSScriptRoot
$legacyTunnelFlyLock = Join-Path $legacyTunnelRepoRoot "config\fly-canonical.lock.json"
if (Test-Path -LiteralPath $legacyTunnelFlyLock) {
  try {
    $legacyTunnelLock = Get-Content -LiteralPath $legacyTunnelFlyLock -Raw | ConvertFrom-Json
    if ([bool]$legacyTunnelLock.frozen -and -not [bool]$legacyTunnelLock.desktopBotEnabled) {
      Write-Host (
        "REFUSED_LEGACY_TUNNEL: Fly.io is the sole bot owner; the desktop " +
        "Cloudflare bot tunnel is retired."
      ) -ForegroundColor Red
      exit 78
    }
  } catch {
    Write-Host "REFUSED_LEGACY_TUNNEL: canonical Fly lock is unreadable." -ForegroundColor Red
    exit 78
  }
}
