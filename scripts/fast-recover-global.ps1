# One-shot recovery: bridge :7810 + global showcase :7002/:9001 (no WMI / no Get-NetTCPConnection).
param([switch]$Quiet)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-mode.ps1")
$mode = Get-HomeStackMode
$botPort = $mode.BotPort
$analyzerPort = $mode.AnalyzerPort
$bridgePort = 7810

function Log([string]$msg) {
  if (-not $Quiet) { Write-Host $msg }
}

function Test-BridgeUp {
  try {
    $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$bridgePort/health")
    $req.Method = "GET"
    $req.Timeout = 1500
    $req.ReadWriteTimeout = 1500
    $resp = $req.GetResponse()
    $ok = ($resp.StatusCode -eq 200)
    $resp.Close()
    return $ok
  } catch {
    return $false
  }
}

Log "=== Fast recover global stack ==="
Log "Ports: bridge :$bridgePort | bot :$botPort | analyzer :$analyzerPort"

$supervisorPidFile = Join-Path $repoRoot ".home-stack-supervisor.pid"
if (Test-Path $supervisorPidFile) {
  $spid = [int](Get-Content $supervisorPidFile -ErrorAction SilentlyContinue)
  if ($spid -gt 0) { Stop-Process -Id $spid -Force -ErrorAction SilentlyContinue }
  Remove-Item $supervisorPidFile -Force -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.lock") -Force -ErrorAction SilentlyContinue

$titles = @(
  "Doxed Home Bridge :$bridgePort",
  "Doxed Bot :$botPort",
  "Doxed Analyzer :$analyzerPort",
  "Doxed Analyzer (once)",
  "Doxed Start Everything",
  "Doxed Cloudflare Tunnel",
  "Doxed Cloudflare Tunnel (stable)"
)
foreach ($t in $titles) {
  & taskkill.exe /F /FI "WINDOWTITLE eq $t" 2>$null | Out-Null
}
& taskkill.exe /F /IM cloudflared.exe 2>$null | Out-Null
Start-Sleep -Seconds 2

if (-not (Test-BridgeUp)) {
  Log "Starting bridge on :$bridgePort ..."
  $launcher = Join-Path $scriptDir "home-stack-launcher.ps1"
  Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcher
  ) -WorkingDirectory $repoRoot -WindowStyle Normal
  $deadline = (Get-Date).AddSeconds(40)
  while ((Get-Date) -lt $deadline) {
    if (Test-BridgeUp) { break }
    Start-Sleep -Seconds 1
  }
} else {
  Log "Restarting bridge to load latest script..."
  & taskkill.exe /F /FI "WINDOWTITLE eq Doxed Home Bridge :$bridgePort" 2>$null | Out-Null
  Start-Sleep -Seconds 2
  $launcher = Join-Path $scriptDir "home-stack-launcher.ps1"
  Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcher
  ) -WorkingDirectory $repoRoot -WindowStyle Normal
  $deadline = (Get-Date).AddSeconds(40)
  while ((Get-Date) -lt $deadline) {
    if (Test-BridgeUp) { break }
    Start-Sleep -Seconds 1
  }
}

if (-not (Test-BridgeUp)) {
  Write-Host "Bridge still down on :$bridgePort. Open RESTART-LAUNCHER.cmd manually and read the bridge window." -ForegroundColor Red
  exit 1
}
Log "Bridge OK"

Log "Queuing start-all-global ..."
try {
  $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$bridgePort/cmd/start-all-global")
  $req.Method = "GET"
  $req.Timeout = 8000
  $req.ReadWriteTimeout = 8000
  $resp = $req.GetResponse()
  $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
  $body = $reader.ReadToEnd()
  $reader.Close()
  $resp.Close()
  Log $body
} catch {
  Write-Host "start-all-global failed: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}

Log ""
Log "Wait 30-60s, then hard-refresh Agent Hub (Ctrl+F5)."
Log "Windows to keep open: Doxed Home Bridge :7810, Doxed Bot :$botPort, Doxed Analyzer :$analyzerPort, Cloudflare tunnel."
exit 0
