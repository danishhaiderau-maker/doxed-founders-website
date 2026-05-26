param(
  [switch]$SkipBuild,
  [switch]$SkipFirewall,
  [switch]$UseDockerPostgres,
  [switch]$NonInteractive,
  [string]$SiteUrl = "",
  [string]$ApiUrl = ""
)

$projectRoot = Split-Path $PSScriptRoot -Parent
$setupScript = Join-Path $PSScriptRoot "setup-self-host.ps1"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
  Write-Host "Requesting Administrator approval (UAC prompt)..." -ForegroundColor Yellow
  $argList = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$setupScript`""
  )
  if ($SkipBuild) { $argList += "-SkipBuild" }
  if ($SkipFirewall) { $argList += "-SkipFirewall" }
  if ($UseDockerPostgres) { $argList += "-UseDockerPostgres" }
  if ($NonInteractive) { $argList += "-NonInteractive" }
  if ($SiteUrl) { $argList += "-SiteUrl"; $argList += "`"$SiteUrl`"" }
  if ($ApiUrl) { $argList += "-ApiUrl"; $argList += "`"$ApiUrl`"" }

  Start-Process powershell -Verb RunAs -ArgumentList $argList -Wait
  exit $LASTEXITCODE
}

Write-Host "Running as Administrator." -ForegroundColor Green
& $setupScript @PSBoundParameters
