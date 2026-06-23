# Kill stale bridge listeners and start a single home-stack-launcher on :7810.
param(
  [int]$Port = 7810,
  [switch]$Quiet
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-common.ps1") -Port $Port

function Log([string]$msg) {
  if (-not $Quiet) { Write-Host $msg }
}

function Test-BridgeHealthy {
  try {
    $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/health")
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

if (Test-BridgeHealthy) {
  Log "Bridge already healthy on :$Port"
  exit 0
}

Log "Stopping stale bridge + supervisor (fast path, no WMI)..."
$supervisorPidFile = Join-Path $repoRoot ".home-stack-supervisor.pid"
if (Test-Path $supervisorPidFile) {
  $supervisorPid = [int](Get-Content $supervisorPidFile -ErrorAction SilentlyContinue)
  if ($supervisorPid -gt 0) {
    Log "  stop supervisor pid $supervisorPid"
    Stop-Process -Id $supervisorPid -Force -ErrorAction SilentlyContinue
  }
  Remove-Item $supervisorPidFile -Force -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.lock") -Force -ErrorAction SilentlyContinue
& taskkill.exe /F /FI "WINDOWTITLE eq Doxed Home Bridge :$Port" 2>$null | Out-Null
Stop-ListenPortFast $Port | Out-Null
Start-Sleep -Seconds 3

# Wait for :7810 to release (http.sys can lag after kill).
$portDeadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $portDeadline) {
  $busy = $false
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $async = $c.ConnectAsync("127.0.0.1", $Port)
    if ($async.Wait(800)) {
      $busy = $true
      $c.Close()
    }
  } catch { }
  if (-not $busy) { break }
  Start-Sleep -Seconds 1
}

$launcher = Join-Path $scriptDir "home-stack-launcher.ps1"
Remove-Item (Join-Path $repoRoot ".home-bridge.err.log") -Force -ErrorAction SilentlyContinue
Start-DetachedPs1 $launcher @() -NoExit -WindowTitle "Doxed Home Bridge :$Port" -Show Normal

$deadline = (Get-Date).AddSeconds(35)
while ((Get-Date) -lt $deadline) {
  if (Test-BridgeHealthy) {
    Log "Bridge OK on :$Port"
    exit 0
  }
  Start-Sleep -Seconds 1
}

$errLog = Join-Path $repoRoot ".home-bridge.err.log"
if (Test-Path $errLog) {
  Write-Host (Get-Content $errLog -Raw) -ForegroundColor Red
}
Write-Host "Bridge failed to start on :$Port - check Doxed Home Bridge window for errors." -ForegroundColor Red
exit 1
