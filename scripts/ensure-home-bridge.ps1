# Kill stale bridge listeners and start a single home-stack-launcher on :7810.
param(
  [int]$Port = 7810,
  [switch]$Quiet,
  [switch]$Force
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$ensureLog = Join-Path $repoRoot ".home-ensure-bridge.log"
. (Join-Path $scriptDir "home-stack-common.ps1") -BridgePort $Port

function Log([string]$msg) {
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ensure[$PID] $msg"
  Add-Content -LiteralPath $ensureLog -Value $line -ErrorAction SilentlyContinue
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

Log "Stopping stale bridge only (fast path, no WMI)..."

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
# A pre-PID-tracking or interrupted bridge can still own the HTTP.sys request
# queue while .home-bridge.pid points elsewhere. Resolve only the launcher's
# exact window title through user32 (no taskkill/process-provider scan), verify
# its executable, and stop that exact owner.
foreach ($windowOwnerPid in @(Get-ProcessIdsByExactWindowTitleFast "Doxed Home Bridge :$Port")) {
  if ($windowOwnerPid -le 0 -or $windowOwnerPid -eq $PID) { continue }
  $windowOwnerName = Get-ProcessExecutableNameFast $windowOwnerPid
  if (@("powershell", "pwsh", "cmd") -notcontains $windowOwnerName) {
    throw "Bridge window owner pid=$windowOwnerPid executable=$windowOwnerName is not an approved launcher host."
  }
  $stopped = Stop-ProcessIdFast $windowOwnerPid
  if (-not $stopped) {
    $stopped = Stop-ExactProcessViaRestartManagerFast $windowOwnerPid
  }
  $deadline = (Get-Date).AddSeconds(5)
  while ((Test-ProcessIdAliveFast $windowOwnerPid) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 100
  }
  if (Test-ProcessIdAliveFast $windowOwnerPid) {
    throw "Unable to stop exact bridge window owner pid=$windowOwnerPid."
  }
  Log "  stop exact bridge window owner pid $windowOwnerPid"
}
# Hidden bridge owners have no user32 window title. Match only this repo's
# exact ensure/launcher script paths from the native command-line query, then
# stop that revalidated PID. This closes the HTTP.sys orphan case where the
# TCP table correctly reports PID 4 and the recorded PID file is stale.
$bridgeScriptNeedles = @(
  [regex]::Escape((Join-Path $scriptDir "ensure-home-bridge.ps1")),
  [regex]::Escape((Join-Path $scriptDir "home-stack-launcher.ps1"))
)
foreach ($executable in @("powershell.exe", "pwsh.exe")) {
  foreach ($hiddenOwnerPid in @(Get-ProcessIdsByExecutableNameFast $executable)) {
    if ($hiddenOwnerPid -le 0 -or $hiddenOwnerPid -eq $PID) { continue }
    $commandLine = Get-ProcessCommandLineFast $hiddenOwnerPid
    if (
      -not $commandLine -or
      @($bridgeScriptNeedles | Where-Object { $commandLine -match "(?i)$_" }).Count -eq 0
    ) {
      continue
    }
    $stopped = Stop-ProcessIdFast $hiddenOwnerPid
    if (-not $stopped) {
      $stopped = Stop-ExactProcessViaRestartManagerFast $hiddenOwnerPid
    }
    $deadline = (Get-Date).AddSeconds(10)
    while ((Test-ProcessIdAliveFast $hiddenOwnerPid) -and (Get-Date) -lt $deadline) {
      Start-Sleep -Milliseconds 200
    }
    if (Test-ProcessIdAliveFast $hiddenOwnerPid) {
      throw "Unable to stop exact hidden bridge owner pid=$hiddenOwnerPid."
    }
    Log "  stop exact hidden bridge owner pid $hiddenOwnerPid"
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

# The watchdog already launched this script in a detached owner process. Keep
# that process as the one durable listener instead of starting another nested
# console whose lifetime and elevation are ambiguous.
Log "Starting bridge listener in recovery owner pid=$PID"
& $launcher
$code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 1 }
Log "Bridge listener exited code=$code"
exit $code
