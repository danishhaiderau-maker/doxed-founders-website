$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location (Join-Path $root "apps/web")

Get-Content (Join-Path $root ".env.self-host") | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    Set-Item -Path "env:$($matches[1].Trim())" -Value $matches[2].Trim().Trim('"')
  }
}

$webPort = if ($env:WEB_PORT) { $env:WEB_PORT } else { "3000" }
$webHost = if ($env:WEB_BIND_HOST) { $env:WEB_BIND_HOST } else { "127.0.0.1" }

if (-not (Test-Path ".next/BUILD_ID")) {
  Write-Host "ERROR: No production build. Run from project root:" -ForegroundColor Red
  Write-Host "  npm.cmd run build --workspace=@dcf/web" -ForegroundColor Yellow
  Write-Host "Or use dev mode instead: npm.cmd run dev:ensure" -ForegroundColor Yellow
  Read-Host "Press Enter to exit"
  exit 1
}

& npx.cmd next start -p $webPort -H $webHost
