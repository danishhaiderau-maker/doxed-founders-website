# Run this script as Administrator
# Right-click PowerShell -> Run as administrator
# Then: cd to project && npm run setup:repair

$ErrorActionPreference = "Continue"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "`n=== WSL / Docker Repair ===" -ForegroundColor Cyan
Write-Host "Fixes: 'WSL 2 kernel file is not found'"
Write-Host "Fixes: 'no distribution with the supplied name' (docker-desktop)`n"

if (-not $isAdmin) {
  Write-Host "WARNING: Not running as Administrator." -ForegroundColor Yellow
  Write-Host "Some steps may fail. Re-run as Admin for best results.`n"
}

Write-Host "[1/7] Enabling Windows features..." -ForegroundColor Yellow
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart 2>&1
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart 2>&1

Write-Host "`n[2/7] Updating WSL kernel..." -ForegroundColor Yellow
wsl --update 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "Trying web download..." -ForegroundColor Yellow
  wsl --update --web-download 2>&1
}

Write-Host "`n[3/7] Setting WSL default version to 2..." -ForegroundColor Yellow
wsl --set-default-version 2 2>&1

Write-Host "`n[4/7] WSL status..." -ForegroundColor Yellow
wsl --status 2>&1

Write-Host "`n[5/7] Installed distributions..." -ForegroundColor Yellow
wsl -l -v 2>&1

$distroList = wsl -l -q 2>&1 | Out-String
if ($distroList.Trim().Length -eq 0) {
  Write-Host "`nNo WSL distro found. Installing Ubuntu..." -ForegroundColor Yellow
  Write-Host "(This may take a few minutes and require a reboot)" -ForegroundColor Yellow
  wsl --install -d Ubuntu --no-launch 2>&1
} else {
  Write-Host "  Distros found - skipping Ubuntu install" -ForegroundColor Green
}

Write-Host "`n[6/7] Shutting down WSL..." -ForegroundColor Yellow
wsl --shutdown 2>&1

Write-Host "`n[7/7] Docker daemon check..." -ForegroundColor Yellow
docker info 2>&1 | Select-Object -First 3
if ($LASTEXITCODE -eq 0) {
  Write-Host "  Docker is running!" -ForegroundColor Green
} else {
  Write-Host "  Docker not running yet (expected until reboot + Docker Desktop start)" -ForegroundColor Yellow
}

Write-Host "`n=== REQUIRED: Do these steps now ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. RESTART your PC (mandatory after WSL feature install)" -ForegroundColor White
Write-Host ""
Write-Host "2. After reboot, open Docker Desktop" -ForegroundColor White
Write-Host "   - If it shows the same error, go to:" -ForegroundColor Gray
Write-Host "     Docker Desktop -> Settings -> Troubleshoot -> Reset to factory defaults" -ForegroundColor Gray
Write-Host "   - Or uninstall + reinstall Docker Desktop from docker.com" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Wait until Docker shows 'Engine running'" -ForegroundColor White
Write-Host ""
Write-Host "4. Run in project folder:" -ForegroundColor White
Write-Host "     npm run setup" -ForegroundColor Green
Write-Host "     npm run dev" -ForegroundColor Green
Write-Host ""
Write-Host "=== SKIP Docker entirely (works right now) ===" -ForegroundColor Cyan
Write-Host "You do NOT need Docker to continue Phase 1:" -ForegroundColor Yellow
Write-Host "  npm run setup:local    (local SQLite database, zero install)" -ForegroundColor Green
Write-Host "  npm run dev" -ForegroundColor Green
Write-Host ""
