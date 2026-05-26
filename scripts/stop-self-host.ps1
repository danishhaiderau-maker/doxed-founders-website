$ErrorActionPreference = "Continue"
$projectRoot = Split-Path $PSScriptRoot -Parent
$pidDir = Join-Path $projectRoot ".selfhost-pids"

function Stop-ByPidFile($name) {
  $file = Join-Path $pidDir "$name.pid"
  if (-not (Test-Path $file)) { return }
  $processId = Get-Content $file -ErrorAction SilentlyContinue
  if ($processId) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped $name (PID $processId)" -ForegroundColor Green
  }
  Remove-Item $file -Force -ErrorAction SilentlyContinue
}

Write-Host "Stopping self-host processes..." -ForegroundColor DarkGray
Stop-ByPidFile "web"
Stop-ByPidFile "web-dev"
Stop-ByPidFile "api"

# Fallback: kill node on our ports if pid files missing
foreach ($port in @(3000, 4000)) {
  $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($conn) {
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped process on port $port" -ForegroundColor Yellow
  }
}

Write-Host "Done." -ForegroundColor DarkGray
