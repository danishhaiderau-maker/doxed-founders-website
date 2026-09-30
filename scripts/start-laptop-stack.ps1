# Laptop desktop stack: the read-only :7002 Fly dashboard proxy plus one tick
# of the laptop-chain supervisor, which owns the v2 segment pull, the analyzer
# cycle and the :9001 dashboard. Fly is the sole AI/trading owner; nothing here
# runs strategy code, mutates Fly, or starts a second pull/analyzer owner.
param(
  [switch]$NoWait,
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev",
  [string]$SupervisorTaskName = "DoxxedLaptopChainSupervisor"
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
  $python = $pythonCommand.Source
} else {
  $pythonCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe")
  )
  $python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  if (-not $python) {
    throw "Python runtime not found. Install Python or add python.exe to PATH before starting the laptop stack."
  }
}
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
$SourceUrl = Get-CanonicalFlyBotUrl -RequestedUrl $SourceUrl

# Stop only the former desktop production runtime and its relay publisher.
foreach ($name in @(
  ".home-bot.pid",
  ".home-bot-crash-monitor.pid",
  ".home-bot-starter.pid",
  ".home-relay-pusher.pid"
)) {
  $path = Join-Path $repoRoot $name
  if (Test-Path -LiteralPath $path) {
    try {
      $procId = [int](Get-Content -LiteralPath $path -Raw)
      if ($procId -gt 0) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
      }
    } catch { }
  }
}

# Local :7002 compatibility proxy. It has no AI or strategy code.
$proxyPidFile = Join-Path $repoRoot ".fly-dashboard-proxy.pid"
$proxyAlive = $false
$proxyEndpointAlive = $false
try {
  $proxyProbe = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:7002/health" -TimeoutSec 8
  $proxyEndpointAlive = ([string]$proxyProbe.Headers["X-Desktop-Mirror"] -eq "fly")
} catch { }
$proxyListenerPids = @(
  Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort 7002 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
)
if (Test-Path -LiteralPath $proxyPidFile) {
  try {
    $proxyPid = [int](Get-Content -LiteralPath $proxyPidFile -Raw)
    $proxyAlive = [bool](
      (Get-Process -Id $proxyPid -ErrorAction SilentlyContinue) -and
      ($proxyEndpointAlive -or ($proxyPid -in $proxyListenerPids))
    )
  } catch { }
}
# Adopt a healthy sole listener started from another checkout instead of
# creating a second SO_REUSEADDR listener on Windows.
if (-not $proxyAlive -and $proxyEndpointAlive) {
  $proxyAlive = $true
  if ($proxyListenerPids.Count -eq 1) {
    $proxyPid = [int]$proxyListenerPids[0]
    Set-Content -LiteralPath $proxyPidFile -Value "$proxyPid" -NoNewline -Encoding UTF8
  }
}
if (-not $proxyAlive -and $proxyListenerPids.Count -gt 0) {
  throw (
    "Desktop port 127.0.0.1:7002 already has $($proxyListenerPids.Count) " +
    "unowned listener(s). Use the authenticated Reset desktop tools control; " +
    "recovery will not start another proxy or terminate an unverified process."
  )
}
if (-not $proxyAlive) {
  $proxyScript = Join-Path $scriptDir "fly-dashboard-proxy.py"
  $proxyArguments = "`"$proxyScript`" --bind 127.0.0.1 --port 7002 --upstream `"$SourceUrl`""
  $proxy = Start-Process -FilePath $python -ArgumentList $proxyArguments `
    -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
  Set-Content -LiteralPath $proxyPidFile -Value "$($proxy.Id)" -NoNewline -Encoding UTF8
}

# The registered supervisor task is the single owner of the segment pull,
# analyzer and :9001 dashboard; its tick is mutex-guarded and returns quickly.
$task = Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
if ($task) {
  if ($task.State -eq "Disabled") {
    throw "$SupervisorTaskName is disabled; re-register it with scripts\register-laptop-chain-task.ps1."
  }
  Start-ScheduledTask -TaskName $SupervisorTaskName
} else {
  $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
  Start-Process -FilePath $powershell -WorkingDirectory $repoRoot -WindowStyle Hidden -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
    "-File", (Join-Path $scriptDir "laptop-chain-supervisor.ps1"), "-RepoRoot", $repoRoot
  ) | Out-Null
}

if (-not $NoWait) {
  Write-Host "Fly is the sole AI/trading owner." -ForegroundColor Green
  Write-Host "Desktop :7002 proxies Fly; the laptop-chain supervisor owns the v2 segment pull and :9001 analyzer."
}
