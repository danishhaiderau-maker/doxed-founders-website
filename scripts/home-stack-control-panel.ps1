# Visible control panel — stays open while bot / analyzer / tunnel run.
param(
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"

function Test-PortOpen([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $async = $c.ConnectAsync("127.0.0.1", $P)
    if (-not $async.Wait(1500)) { return $false }
    $c.Close()
    return $true
  } catch { return $false }
}

while ($true) {
  Clear-Host
  Write-Host "=== Doxed Home Stack Control Panel ===" -ForegroundColor Cyan
  Write-Host ("Time: {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
  Write-Host ""
  $bot = Test-PortOpen $BotPort
  $analyzer = Test-PortOpen $AnalyzerPort
  $cf = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $url = if (Test-Path $tunnelUrlFile) { (Get-Content $tunnelUrlFile -Raw).Trim() } else { "" }
  Write-Host ("  Bot :{0}      {1}" -f $BotPort, $(if ($bot) { "ONLINE" } else { "offline" }))
  Write-Host ("  Analyzer :{0} {1}" -f $AnalyzerPort, $(if ($analyzer) { "ONLINE" } else { "offline" }))
  Write-Host ("  Tunnel          {0}" -f $(if ($cf) { "ONLINE" } else { "offline" }))
  if ($url) { Write-Host ("  Public URL      {0}" -f $url) -ForegroundColor Green }
  Write-Host ""
  Write-Host "  Bridge (Agent Hub buttons): http://127.0.0.1:7810"
  Write-Host "  Bot dashboard:            http://127.0.0.1:$BotPort"
  Write-Host ""
  Write-Host "Keep this window open. Close bot/tunnel windows to stop services." -ForegroundColor Yellow
  Write-Host "Press Ctrl+C to close this panel only (services keep running)."
  Start-Sleep -Seconds 5
}
