$ErrorActionPreference = "Continue"
Write-Host "Stopping dev servers on ports 3000 and 4000..." -ForegroundColor Cyan
foreach ($port in @(4000, 3000)) {
  $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
  foreach ($conn in $connections) {
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "  Stopped process on port $port (PID $($conn.OwningProcess))" -ForegroundColor Green
  }
}
Write-Host "Done." -ForegroundColor Green
