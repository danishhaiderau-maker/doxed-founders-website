# Kill stale bridge listeners and start a single home-stack-launcher on :7810.
param(
  [int]$Port = 7810,
  [switch]$Quiet,
  [switch]$Force
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-common.ps1") -BridgePort $Port

function Log([string]$msg) {
  if (-not $Quiet) { Write-Host $msg }
}

function Test-BridgeHealthy {
  try {
    $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/health")
    $req.Method = "GET"
    $req.Timeout = 7000
    $req.ReadWriteTimeout = 7000
    $resp = $req.GetResponse()
    $ok = ($resp.StatusCode -eq 200)
    $resp.Close()
    return $ok
  } catch {
    return $false
  }
}

if ((Test-BridgeHealthy) -and -not $Force) {
  Log "Bridge already healthy on :$Port"
  exit 0
}

if ($Force -and (Test-BridgeHealthy)) {
  Log "Force restart requested - recycling bridge on :$Port"
}

Log "Stopping stale bridge + supervisor (fast path, no WMI)..."
$supervisorPidFile = Join-Path $repoRoot ".home-stack-supervisor.pid"
if (Test-Path $supervisorPidFile) {
  $stoppedSupervisor = @(
    Stop-RecordedProcess $supervisorPidFile @("powershell", "pwsh", "cmd")
  )
  foreach ($stoppedPid in $stoppedSupervisor) {
    Log "  stop supervisor pid $stoppedPid"
  }
}
Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.lock") -Force -ErrorAction SilentlyContinue

# Bridge reload must not depend on WMI/CIM. Win32_Process queries can hang for
# minutes on this home PC, leaving :7810 down after the old bridge exits. The
# launcher records its own PID, so a direct Stop-Process is deterministic.
$bridgePidFile = Join-Path $repoRoot ".home-bridge.pid"
if (Test-Path -LiteralPath $bridgePidFile) {
  # The bridge writes its PID only after loading helpers and binding http.sys.
  # Allow the observed startup-to-PID-file delay while still checking exact
  # executable name and start time before terminating a reused numeric PID.
  $stoppedBridge = @(
    Stop-RecordedProcess $bridgePidFile @("powershell", "pwsh") 20
  )
  foreach ($stoppedPid in $stoppedBridge) {
    if ($stoppedPid -ne $PID) { Log "  stop launcher pid $stoppedPid" }
  }
}
# The exact bridge PID above is authoritative. Do not enumerate PowerShell
# windows here: Get-Process has also stalled on this host during recovery.
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
# -Force tells the launcher to bind even if :$Port still answers (we already
# killed the old bridge above); without it the launcher's duplicate-guard would
# no-op on a still-healthy bridge.
if (Test-BridgeHealthy) {
  Log "Bridge OK on :$Port (existing listener)"
  exit 0
}

Start-VisibleConsole $launcher @() -Title "Doxed Home Bridge :$Port"

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
