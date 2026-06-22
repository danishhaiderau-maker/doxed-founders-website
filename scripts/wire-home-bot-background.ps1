param(
  [Parameter(Mandatory = $true)]
  [string]$Url
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Write-Host "Wiring $Url to Neon + Railway..."
Push-Location $repoRoot
try {
  & npm.cmd run wire:home-bot -- $Url 2>&1 | Tee-Object -FilePath (Join-Path $repoRoot ".home-wire.log")
  Write-Host "Done. Log: $repoRoot\.home-wire.log"
} catch {
  Write-Host "Wire failed: $_"
} finally {
  Pop-Location
}
Write-Host "Press Enter to close."
Read-Host | Out-Null
