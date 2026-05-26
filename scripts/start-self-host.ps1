$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

function Write-DebugLog([string]$Message, [hashtable]$Data = @{}, [string]$HypothesisId = "D") {
  # #region agent log
  $payload = @{
    sessionId    = "acf3ea"
    runId        = "start-self-host"
    hypothesisId = $HypothesisId
    location     = "start-self-host.ps1"
    message      = $Message
    data         = $Data
    timestamp    = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  } | ConvertTo-Json -Compress
  $logPath = Join-Path $projectRoot "debug-acf3ea.log"
  Add-Content -Path $logPath -Value $payload -Encoding UTF8
  # #endregion
}

if (-not (Test-Path ".env.self-host")) {
  Write-Host "Missing .env.self-host - run finish-self-host.cmd first" -ForegroundColor Red
  exit 1
}

if (-not (Test-Path "prisma/selfhost.db")) {
  if (Test-Path "prisma/dev.db") {
    Write-Host "Copying prisma/dev.db to prisma/selfhost.db..." -ForegroundColor Yellow
    Copy-Item "prisma/dev.db" "prisma/selfhost.db" -Force
  } else {
    Write-Host "Missing prisma/selfhost.db - run finish-self-host.cmd first" -ForegroundColor Red
    exit 1
  }
}

if (-not (Test-Path "apps/web/.next/BUILD_ID")) {
  Write-Host "WARN: Production web build not found. Run build-self-host.cmd first." -ForegroundColor Yellow
  Write-Host "      (Or npm.cmd run build --workspace=@dcf/web)" -ForegroundColor Yellow
}

$apiPort = 4000
$webPort = 3000
Get-Content ".env.self-host" | ForEach-Object {
  if ($_ -match '^\s*API_PORT=(.*)$') { $apiPort = $matches[1].Trim().Trim('"') }
  if ($_ -match '^\s*WEB_PORT=(.*)$') { $webPort = $matches[1].Trim().Trim('"') }
}

$pidDir = Join-Path $projectRoot ".selfhost-pids"
New-Item -ItemType Directory -Force -Path $pidDir | Out-Null

function Test-PortOpen([int]$Port) {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    $ok = $async.AsyncWaitHandle.WaitOne(800, $false)
    if ($ok) { $client.EndConnect($async) }
    $client.Close()
    return $ok
  } catch { return $false }
}

$webUp = Test-PortOpen $webPort
$apiUp = Test-PortOpen $apiPort
$hasProdBuild = Test-Path "apps/web/.next/BUILD_ID"
Write-DebugLog "port-check" @{
  webUp        = $webUp
  apiUp        = $apiUp
  hasProdBuild = $hasProdBuild
  webPort      = $webPort
  apiPort      = $apiPort
} "A"

if (-not $apiUp) {
  Write-Host "Starting API on 127.0.0.1:${apiPort}..." -ForegroundColor Cyan
  $api = Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$projectRoot\scripts\run-api-selfhost.ps1`"" -WindowStyle Minimized -PassThru
  $api.Id | Set-Content (Join-Path $pidDir "api.pid")
  Start-Sleep -Seconds 6
  $apiUp = Test-PortOpen $apiPort
  Write-DebugLog "api-start" @{ pid = $api.Id; apiUp = $apiUp } "A"
}

if (-not $webUp) {
  if (-not $hasProdBuild) {
    Write-Host "No production web build - starting dev frontend (npm run dev:web)..." -ForegroundColor Yellow
    Write-DebugLog "web-dev-fallback" @{ reason = "missing BUILD_ID" } "C"
    $web = Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"Set-Location '$projectRoot'; npm.cmd run dev:web`"" -WindowStyle Minimized -PassThru
    $web.Id | Set-Content (Join-Path $pidDir "web-dev.pid")
    Start-Sleep -Seconds 8
    $webUp = Test-PortOpen $webPort
    Write-DebugLog "web-dev-start" @{ pid = $web.Id; webUp = $webUp } "C"
  } else {
    Write-Host "Starting Web on 127.0.0.1:${webPort}..." -ForegroundColor Cyan
    $web = Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$projectRoot\scripts\run-web-selfhost.ps1`"" -WindowStyle Minimized -PassThru
    $web.Id | Set-Content (Join-Path $pidDir "web.pid")
    Start-Sleep -Seconds 6
    $webUp = Test-PortOpen $webPort
    Write-DebugLog "web-prod-start" @{ pid = $web.Id; webUp = $webUp } "C"
  }
} elseif (-not $apiUp) {
  Write-Host "WARN: Frontend on :${webPort} but API on :${apiPort} is still down." -ForegroundColor Red
  Write-DebugLog "partial-stack" @{ webUp = $true; apiUp = $false } "D"
}

$webUp = Test-PortOpen $webPort
$apiUp = Test-PortOpen $apiPort
Write-DebugLog "final-state" @{ webUp = $webUp; apiUp = $apiUp } "A"

Write-Host ""
if ($webUp -and $apiUp) {
  Write-Host "=== Self-host running (localhost only) ===" -ForegroundColor Green
} elseif ($webUp -and -not $apiUp) {
  Write-Host "=== Partial - web up, API down ===" -ForegroundColor Red
  Write-Host "Check minimized PowerShell window for API errors." -ForegroundColor Yellow
} elseif (-not $webUp -and $apiUp) {
  Write-Host "=== Partial - API up, web down ===" -ForegroundColor Red
} else {
  Write-Host "=== Failed to start services ===" -ForegroundColor Red
}
Write-Host "  Web: http://127.0.0.1:${webPort}"
Write-Host "  API: http://127.0.0.1:${apiPort}/api/health"
Write-Host ""
Write-Host "Public URL: cloudflared tunnel run dcf-home" -ForegroundColor Cyan
Write-Host ""
