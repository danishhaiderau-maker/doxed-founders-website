$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

Write-Host ""
Write-Host "=== Cloudflare quick tunnel (HTTPS on phone, no domain) ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Restart sequence:" -ForegroundColor Yellow
Write-Host "  Terminal 1: dev-lan.cmd          (start API + web on localhost)" -ForegroundColor White
Write-Host "  Terminal 2: dev-tunnel.cmd       (creates HTTPS URLs, keep open)" -ForegroundColor White
Write-Host "  Terminal 1: Ctrl+C, dev-lan.cmd  (reload apps/web/.env.local)" -ForegroundColor White
Write-Host "  Phone:      open the Web tunnel URL - works on 4G or Wi-Fi" -ForegroundColor White
Write-Host ""

& (Join-Path $PSScriptRoot "start-quick-tunnels.ps1") -ProjectRoot $projectRoot
