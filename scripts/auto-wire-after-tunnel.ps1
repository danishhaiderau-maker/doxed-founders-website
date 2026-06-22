# Wait for .home-tunnel-url then wire to Neon + Railway (background after Start everything).
param([int]$MaxWaitSec = 180)

$Host.UI.RawUI.WindowTitle = "Doxed Auto-Wire"
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$logFile = Join-Path $repoRoot ".home-wire.log"
$deadline = (Get-Date).AddSeconds($MaxWaitSec)

Write-Host "Waiting for tunnel URL (max ${MaxWaitSec}s)..."
while ((Get-Date) -lt $deadline) {
  if (Test-Path $tunnelUrlFile) {
    $url = (Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue).Trim()
    if ($url -match '^https://') {
      Write-Host "Tunnel URL found: $url"
      Write-Host "Wiring to site..."
      Push-Location $repoRoot
      try {
        & npm.cmd run wire:home-bot -- $url 2>&1 | Tee-Object -FilePath $logFile
        Write-Host "Wire complete. See $logFile"
      } finally {
        Pop-Location
      }
      Read-Host "Press Enter to close auto-wire window"
      exit 0
    }
  }
  Start-Sleep -Seconds 3
}
Write-Host "Timed out waiting for tunnel URL in $tunnelUrlFile"
Read-Host "Press Enter to close auto-wire window"
exit 1
