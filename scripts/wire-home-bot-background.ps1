param(
  [Parameter(Mandatory = $true)]
  [string]$Url
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logFile = Join-Path $repoRoot ".home-wire.log"
$line = "{0} Wiring {1} to Neon + Railway..." -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Url
Add-Content -Path $logFile -Value $line
Push-Location $repoRoot
try {
  & npm.cmd run wire:home-bot -- $Url 2>&1 | Tee-Object -FilePath $logFile -Append
  Add-Content -Path $logFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Done."
} catch {
  Add-Content -Path $logFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Wire failed: $_"
} finally {
  Pop-Location
}
