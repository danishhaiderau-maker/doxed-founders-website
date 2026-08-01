# Canonical desktop recovery for the Fly-owned Conservative BTC stack.
#
# Fly.io is the only AI, strategy, paper-execution, and relay-signal owner.
# Windows provides a compatibility dashboard proxy (:7002), the external
# analyzer (:9001), and the local command bridge (:7810). This script must
# never start a local Python strategy runtime or a Cloudflare tunnel.
param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$bridgePort = 7810
. (Join-Path $scriptDir "home-stack-common.ps1") -BridgePort $bridgePort -BotPort 7002 -AnalyzerPort 9001

function Write-RecoveryStatus([string]$Message) {
  if (-not $Quiet) { Write-Host $Message }
}

function Test-LocalHttp([string]$Url, [int]$TimeoutMs = 2500) {
  try {
    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.Method = "GET"
    $request.Timeout = $TimeoutMs
    $request.ReadWriteTimeout = $TimeoutMs
    $response = $request.GetResponse()
    $ok = ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 500)
    $response.Close()
    return $ok
  } catch {
    return $false
  }
}

Write-RecoveryStatus "=== Recover Fly desktop mirror ==="
Write-RecoveryStatus "Owner: Fly.io | dashboard proxy :7002 | analyzer :9001 | bridge :7810"
# This command is an explicit restart request. Clear the old voluntary-stop
# marker so the bridge/analyzer watchdogs are allowed to self-heal again.
Clear-HomeStackUserStopped

$mirrorScript = Join-Path $scriptDir "start-fly-desktop-mirror.ps1"
if (-not (Test-Path -LiteralPath $mirrorScript)) {
  throw "Missing canonical mirror launcher: $mirrorScript"
}
& $mirrorScript -NoWait

if (-not (Test-LocalHttp "http://127.0.0.1:$bridgePort/health")) {
  Write-RecoveryStatus "Starting local command bridge on :$bridgePort ..."
  $bridgeScript = Join-Path $scriptDir "ensure-home-bridge.ps1"
  Start-Process -FilePath "powershell.exe" `
    -ArgumentList (
      "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"" +
      $bridgeScript +
      "`" -Force -Quiet"
    ) `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden | Out-Null

  $deadline = (Get-Date).AddSeconds(30)
  while ((Get-Date) -lt $deadline) {
    if (Test-LocalHttp "http://127.0.0.1:$bridgePort/health") { break }
    Start-Sleep -Milliseconds 500
  }
}

if (-not (Test-LocalHttp "http://127.0.0.1:7002/health")) {
  throw "Desktop Fly proxy did not become reachable on :7002"
}
if (-not (Test-LocalHttp "http://127.0.0.1:9001/")) {
  throw "Desktop external analyzer did not become reachable on :9001"
}
if (-not (Test-LocalHttp "http://127.0.0.1:$bridgePort/health")) {
  throw "Desktop command bridge did not become reachable on :$bridgePort"
}

Write-RecoveryStatus "Ready: desktop views are mirrors of the single Fly owner."
Write-RecoveryStatus "Dashboard: http://127.0.0.1:7002/"
Write-RecoveryStatus "Analyzer:  http://127.0.0.1:9001/"
exit 0
