# Start/stop the Final Bots local lab (:7800 / :9001) — separate from global showcase.
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("start", "stop", "status")]
  [string]$Action
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$labRoot = Split-Path -Parent $repoRoot
$startScript = Join-Path $labRoot "start_stack.ps1"
$stopScript = Join-Path $labRoot "stop_stack.ps1"

function Test-LabPort([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $async = $c.ConnectAsync("127.0.0.1", $P)
    if (-not $async.Wait(1200)) { return $false }
    $c.Close()
    return $true
  } catch {
    return $false
  }
}

function Get-LabStatus {
  return @{
    botPort = 7800
    analyzerPort = 9001
    botOnline = Test-LabPort 7800
    analyzerOnline = Test-LabPort 9001
    labRoot = $labRoot
  }
}

switch ($Action) {
  "status" {
    Get-LabStatus | ConvertTo-Json -Compress
  }
  "start" {
    if (-not (Test-Path $startScript)) {
      throw "Missing $startScript"
    }
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
      "-NoProfile", "-ExecutionPolicy", "Bypass",
      "-File", $startScript
    ) -WorkingDirectory $labRoot -WindowStyle Normal
    @{ ok = $true; message = "Local lab start queued (:7800 bot + :9001 analyzer)." }
  }
  "stop" {
    if (-not (Test-Path $stopScript)) {
      throw "Missing $stopScript"
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript
    @{ ok = $true; message = "Local lab stopped (:7800 / :9001)." }
  }
}
