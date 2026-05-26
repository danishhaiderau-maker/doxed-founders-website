param(
  [int]$WaitSeconds = 600
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

$keysFile = Join-Path $projectRoot "google-keys.txt"

function Read-KeysFromFile {
  if (-not (Test-Path $keysFile)) { return $null, $null }
  $text = Get-Content $keysFile -Raw
  $id = $null
  $secret = $null

  if ($text -match '(?m)^CLIENT_ID=(.+)$') {
    $id = $matches[1].Trim().Trim('"')
  }
  if ($text -match '(?m)^CLIENT_SECRET=(.+)$') {
    $secret = $matches[1].Trim().Trim('"')
  }

  if ($id -match 'PASTE_|YOUR_|HERE') { $id = $null }
  if ($secret -match 'PASTE_|YOUR_|HERE') { $secret = $null }

  return $id, $secret
}

Write-Host "`n=== Auto Google OAuth setup ===" -ForegroundColor Cyan
Write-Host "1. Browser opens Google Cloud - click Create OAuth client ID (Web app)" -ForegroundColor White
Write-Host "2. Redirect URI: http://localhost:3000/api/auth/callback/google" -ForegroundColor Green
Write-Host "3. Paste Client ID + Secret into google-keys.txt (Notepad) and SAVE" -ForegroundColor White
Write-Host "4. This window applies keys automatically`n" -ForegroundColor White

Start-Process "https://console.cloud.google.com/auth/clients/create?project=_"
Start-Process notepad.exe $keysFile

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
  $clientId, $clientSecret = Read-KeysFromFile
  if ($clientId -and $clientSecret) {
    Write-Host "Keys detected - saving..." -ForegroundColor Green
    & (Join-Path $PSScriptRoot "setup-google-oauth.ps1") -ClientId $clientId -ClientSecret $clientSecret
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "`nRestarting dev servers..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "stop-dev.ps1")
    Start-Sleep -Seconds 2
    Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $projectRoot "launch1.ps1")

    Start-Sleep -Seconds 20
    try {
      $providers = Invoke-RestMethod -Uri "http://localhost:3000/api/auth/providers" -TimeoutSec 10
      if ($providers.google) {
        Write-Host "`nSUCCESS - Google login is ready!" -ForegroundColor Green
        Write-Host "Open: http://localhost:3000/login" -ForegroundColor Green
        Start-Process "http://localhost:3000/login"
        exit 0
      }
    } catch {}

    Write-Host "`nKeys saved. Restart dev-lan if login page still shows setup message." -ForegroundColor Yellow
    exit 0
  }

  Start-Sleep -Seconds 2
}

Write-Host "Timed out waiting for google-keys.txt - open setup-google.cmd or edit google-keys.txt and re-run." -ForegroundColor Red
exit 1
