# Stop all Founder Node main processes (keeps vault). Restart Explorer to clear ghost tray icons.
$ErrorActionPreference = 'SilentlyContinue'

Write-Host 'Stopping duplicate Founder Node processes...'
$mains = Get-CimInstance Win32_Process -Filter "Name = 'Founder Node.exe'" |
  Where-Object { $_.CommandLine -and $_.CommandLine -notmatch '--type=' }

foreach ($p in $mains) {
  Write-Host "  taskkill PID $($p.ProcessId)"
  taskkill /PID $p.ProcessId /T /F 2>$null
}

$lock = Join-Path $env:USERPROFILE 'FounderVault\.founder-node.lock'
if (Test-Path $lock) {
  Remove-Item $lock -Force
  Write-Host 'Removed stale lock file.'
}

Write-Host ''
Write-Host 'Optional: restart Explorer to clear old tray icons (log out/in also works).'
$answer = Read-Host 'Restart Explorer now? (y/N)'
if ($answer -eq 'y' -or $answer -eq 'Y') {
  Stop-Process -Name explorer -Force
  Start-Process explorer
  Write-Host 'Explorer restarted.'
}

$installed = Join-Path $env:LOCALAPPDATA 'Programs\@dcffounder-node\Founder Node.exe'
if (Test-Path $installed) {
  Write-Host ''
  Write-Host "Starting single instance: $installed"
  Start-Process -FilePath $installed
} else {
  Write-Host 'Install Founder Node from Founder OS (Settings → Builder) if not installed.'
}
