param(
  [string]$ClientId = "",
  [string]$ClientSecret = "",
  [switch]$OpenConsole
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
. (Join-Path $PSScriptRoot "write-dev-env.ps1")

function Set-EnvValue {
  param(
    [string]$FilePath,
    [string]$Key,
    [string]$Value
  )

  if (-not (Test-Path $FilePath)) {
    Copy-Item ".env.example" $FilePath -ErrorAction SilentlyContinue
  }
  if (-not (Test-Path $FilePath)) {
    New-Item -ItemType File -Path $FilePath | Out-Null
  }

  $content = Get-Content $FilePath -Raw -ErrorAction SilentlyContinue
  if (-not $content) { $content = "" }

  if ($content -match "(?m)^$Key=") {
    $content = $content -replace "(?m)^$Key=.*$", "$Key=`"$Value`""
  } else {
    $content = ($content.TrimEnd() + "`n$Key=`"$Value`"`n")
  }
  Set-Content $FilePath ($content.TrimEnd() + "`n") -Encoding UTF8
}

function Get-RedirectUris {
  $urls = @(
    "http://localhost:3000",
    "http://127.0.0.1:3000"
  )

  foreach ($file in @("apps\web\.env.local", ".env.tunnel.local", ".env", ".env.self-host")) {
    $path = Join-Path $projectRoot $file
    if (-not (Test-Path $path)) { continue }
    Get-Content $path | ForEach-Object {
      if ($_ -match '^\s*NEXTAUTH_URL="([^"]+)"') { $urls += $matches[1] }
      if ($_ -match '^\s*TUNNEL_WEB_URL="([^"]+)"') { $urls += $matches[1] }
    }
  }

  return ($urls | ForEach-Object { $_.TrimEnd('/') } | Select-Object -Unique | ForEach-Object {
    "$_/api/auth/callback/google"
  })
}

function Sync-AuthSecretsToWeb {
  $auth = Read-AuthEnvFile -ProjectRoot $projectRoot
  $webEnv = Join-Path $projectRoot "apps\web\.env.local"
  if (-not (Test-Path $webEnv)) { return }

  foreach ($key in @('NEXTAUTH_SECRET', 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET')) {
    if ($auth[$key]) {
      Set-EnvValue -FilePath $webEnv -Key $key -Value $auth[$key]
    }
  }
}

Write-Host "`n=== Google Sign-In setup (one time for the whole site) ===" -ForegroundColor Cyan
Write-Host "Account: use danish.haider.au in Google Cloud Console`n" -ForegroundColor DarkGray

$redirects = Get-RedirectUris

Write-Host "In Google Cloud, create OAuth client ID (Web application)." -ForegroundColor White
Write-Host "Add ALL of these Authorized redirect URIs:`n" -ForegroundColor White
foreach ($uri in $redirects) {
  Write-Host "   $uri" -ForegroundColor Green
}

Write-Host @"

Quick links:
  Credentials:  https://console.cloud.google.com/apis/credentials
  OAuth screen: https://console.cloud.google.com/apis/credentials/consent

Steps:
  1. OAuth consent screen -> External -> App name: Doxed Founders -> Save
  2. Credentials -> Create credentials -> OAuth client ID -> Web application
  3. Paste redirect URIs above -> Create -> copy Client ID + Client Secret

"@ -ForegroundColor DarkGray

if ($OpenConsole) {
  Start-Process "https://console.cloud.google.com/apis/credentials"
}

if (-not $ClientId) { $ClientId = Read-Host "GOOGLE_CLIENT_ID" }
if (-not $ClientSecret) { $ClientSecret = Read-Host "GOOGLE_CLIENT_SECRET" }

if (-not $ClientId -or -not $ClientSecret) {
  Write-Host "FAIL  Both Client ID and Client Secret are required." -ForegroundColor Red
  Write-Host "Re-run: npm run setup:google" -ForegroundColor Yellow
  exit 1
}

foreach ($file in @(".env", ".env.self-host")) {
  Set-EnvValue -FilePath (Join-Path $projectRoot $file) -Key "GOOGLE_CLIENT_ID" -Value $ClientId
  Set-EnvValue -FilePath (Join-Path $projectRoot $file) -Key "GOOGLE_CLIENT_SECRET" -Value $ClientSecret
}

$webEnv = Join-Path $projectRoot "apps\web\.env.local"
Set-EnvValue -FilePath $webEnv -Key "GOOGLE_CLIENT_ID" -Value $ClientId
Set-EnvValue -FilePath $webEnv -Key "GOOGLE_CLIENT_SECRET" -Value $ClientSecret
Sync-AuthSecretsToWeb

Write-Host "`nOK  Google OAuth saved to .env + apps/web/.env.local" -ForegroundColor Green
Write-Host "Restart dev-lan.cmd (Window 1), then test Continue with Google at /login" -ForegroundColor Yellow
Write-Host "Other users only click the button - no setup for them.`n" -ForegroundColor DarkGray
