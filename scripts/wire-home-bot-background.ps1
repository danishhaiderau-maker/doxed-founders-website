param(
  [Parameter(Mandatory = $true)]
  [string]$Url
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Push-Location $repoRoot
try {
  & npm.cmd run wire:home-bot -- $Url --skip-health-check --keep-railway-bot 2>&1 | Out-File (Join-Path $repoRoot ".home-wire.log") -Encoding utf8
} finally {
  Pop-Location
}
