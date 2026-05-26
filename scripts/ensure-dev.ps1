$ErrorActionPreference = "Continue"
$projectRoot = Split-Path $PSScriptRoot -Parent

function Test-PortOpen([int]$Port) {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    $ok = $async.AsyncWaitHandle.WaitOne(1500, $false)
    if ($ok) { $client.EndConnect($async) }
    $client.Close()
    return $ok
  } catch {
    return $false
  }
}

$webUp = Test-PortOpen 3000
$apiUp = Test-PortOpen 4000

Write-Host "`n=== Dev server check ===" -ForegroundColor Cyan

if ($webUp -and $apiUp) {
  Write-Host "OK  Frontend (:3000) and API (:4000) are running." -ForegroundColor Green
  Write-Host "    http://localhost:3000/feed" -ForegroundColor DarkGray
  exit 0
}

Set-Location $projectRoot

if (-not $webUp -and -not $apiUp) {
  Write-Host "Starting frontend + API (npm run dev)..." -ForegroundColor Yellow
  Write-Host "Keep this terminal open while browsing.`n" -ForegroundColor DarkGray
  npm run dev
  exit $LASTEXITCODE
}

if (-not $webUp -and $apiUp) {
  Write-Host "WARN API is running but frontend (:3000) is DOWN." -ForegroundColor Red
  Write-Host "     This causes ERR_CONNECTION_REFUSED on /feed, /login, etc." -ForegroundColor Yellow
  Write-Host "Starting frontend only (npm run dev:web)...`n" -ForegroundColor Yellow
  npm run dev:web
  exit $LASTEXITCODE
}

if ($webUp -and -not $apiUp) {
  Write-Host "WARN Frontend is running but API (:4000) is DOWN." -ForegroundColor Red
  Write-Host "Starting API only (npm run dev:api)...`n" -ForegroundColor Yellow
  npm run dev:api
  exit $LASTEXITCODE
}
