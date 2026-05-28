param(
  [Parameter(Mandatory = $true)][string]$ApiUrl,
  [Parameter(Mandatory = $true)][string]$WebUrl,
  [Parameter(Mandatory = $true)][string]$NextAuthSecret,
  [Parameter(Mandatory = $true)][string]$GoogleClientId,
  [Parameter(Mandatory = $true)][string]$GoogleClientSecret
)

$ErrorActionPreference = "Stop"
$webDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "apps\web"
Set-Location $webDir

function Set-VercelEnv([string]$Name, [string]$Value) {
  Write-Host "Setting $Name..." -ForegroundColor DarkGray
  $Value | vercel env add $Name production --force 2>&1 | Out-Null
}

Set-VercelEnv "API_URL" $ApiUrl.TrimEnd('/')
Set-VercelEnv "NEXTAUTH_URL" $WebUrl.TrimEnd('/')
Set-VercelEnv "NEXTAUTH_SECRET" $NextAuthSecret
Set-VercelEnv "GOOGLE_CLIENT_ID" $GoogleClientId
Set-VercelEnv "GOOGLE_CLIENT_SECRET" $GoogleClientSecret

Write-Host "Deploying to Vercel production..." -ForegroundColor Yellow
vercel --prod --yes

Write-Host "Live site: $WebUrl" -ForegroundColor Green
