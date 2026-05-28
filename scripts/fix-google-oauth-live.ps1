# Verifies Google OAuth redirect URIs and opens the OAuth client in Google Cloud Console.
$ErrorActionPreference = "Stop"

$ClientId = "84665204636-sl9vqeu6eqr0gg3nrgo490vvvfagsfb0.apps.googleusercontent.com"

$Origins = @(
  "https://doxxedcrypto.digital"
  "https://www.doxxedcrypto.digital"
  "https://doxed-founders-website.vercel.app"
  "http://localhost:3000"
)

$RedirectUris = @(
  "https://doxxedcrypto.digital/api/auth/callback/google"
  "https://www.doxxedcrypto.digital/api/auth/callback/google"
  "https://doxed-founders-website.vercel.app/api/auth/callback/google"
  "http://localhost:3000/api/auth/callback/google"
)

function Test-GoogleRedirectUri {
  param([string]$RedirectUri)
  $enc = [uri]::EscapeDataString($RedirectUri)
  $url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=$ClientId&redirect_uri=$enc&response_type=code&scope=openid%20email%20profile"
  $tmp = Join-Path $env:TEMP "google-oauth-check.html"
  curl.exe -sL $url -o $tmp | Out-Null
  $html = Get-Content $tmp -Raw
  if ($html -match "request is invalid|redirect_uri_mismatch|doesn't comply with Google's OAuth") {
    return $false
  }
  if ($html -match "Sign in - Google Accounts") {
    return $true
  }
  return $null
}

Write-Host "`n=== Google Sign-In live fix ===" -ForegroundColor Cyan
Write-Host "Console login email (danish3517@gmail.com) does NOT need to match users signing in.`n" -ForegroundColor DarkGray

Write-Host "Checking redirect URIs registered in Google Cloud...`n" -ForegroundColor White
foreach ($uri in $RedirectUris) {
  $ok = Test-GoogleRedirectUri -RedirectUri $uri
  $status = if ($ok -eq $true) { "OK  " } elseif ($ok -eq $false) { "FAIL" } else { "??  " }
  $color = if ($ok -eq $true) { "Green" } elseif ($ok -eq $false) { "Red" } else { "Yellow" }
  Write-Host "  [$status] $uri" -ForegroundColor $color
}

$clipboard = @"
--- Authorized JavaScript origins (paste all) ---
$($Origins -join "`n")

--- Authorized redirect URIs (paste all) ---
$($RedirectUris -join "`n")
"@

Set-Clipboard -Value $clipboard.Trim()
Write-Host "`nCopied origins + redirect URIs to your clipboard." -ForegroundColor Green

$editUrl = "https://console.cloud.google.com/apis/credentials/oauthclient/$ClientId"
$consentUrl = "https://console.cloud.google.com/apis/credentials/consent"

Write-Host @"

NEXT STEPS (in the browser that opens):
  1. Log in with the Google account that OWNS this Cloud project (likely danish3517@gmail.com).
  2. OAuth client -> paste clipboard into JavaScript origins + Redirect URIs -> SAVE.
  3. OAuth consent screen -> Publish app (Testing blocks most Gmail users).
  4. Authorized domains: add doxxedcrypto.digital

Then re-run: npm run fix:google-live
"@ -ForegroundColor Yellow

Start-Process $editUrl
Start-Sleep -Seconds 1
Start-Process $consentUrl

Write-Host "Opened OAuth client + consent screen in your browser.`n" -ForegroundColor Green
