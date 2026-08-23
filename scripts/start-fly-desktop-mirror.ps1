param(
  [switch]$NoWait,
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev"
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
    throw "Python runtime not found. Install Python or add python.exe to PATH before starting the Fly desktop mirror."
  }
}
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
$SourceUrl = Get-CanonicalFlyBotUrl -RequestedUrl $SourceUrl

# Stop only the former desktop production runtime and its relay publisher.
# The analyzer remains independent and is restarted below against Fly data.
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

# Start the local :7002 compatibility proxy. It has no AI or strategy code.
$proxyPidFile = Join-Path $repoRoot ".fly-dashboard-proxy.pid"
$proxyAlive = $false
$proxyEndpointAlive = $false
try {
  $proxyProbe = Invoke-WebRequest `
    -UseBasicParsing `
    -Uri "http://127.0.0.1:7002/health" `
    -TimeoutSec 8
  $proxyEndpointAlive = (
    [string]$proxyProbe.Headers["X-Desktop-Mirror"] -eq "fly"
  )
} catch { }
$proxyListenerPids = @(
  Get-NetTCPConnection `
    -LocalAddress "127.0.0.1" `
    -LocalPort 7002 `
    -State Listen `
    -ErrorAction SilentlyContinue |
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
# A recovery can be launched from a clean integration worktree while an older
# read-only mirror (from the normal desktop checkout) is already healthy. Adopt
# that sole listener instead of relying only on a worktree-local PID marker and
# accidentally creating a second SO_REUSEADDR listener on Windows.
if (-not $proxyAlive -and $proxyEndpointAlive) {
  $proxyAlive = $true
  if ($proxyListenerPids.Count -eq 1) {
    $proxyPid = [int]$proxyListenerPids[0]
    Set-Content -LiteralPath $proxyPidFile -Value "$proxyPid" -NoNewline -Encoding UTF8
  }
}
if (-not $proxyAlive -and $proxyListenerPids.Count -gt 0) {
  throw (
    "Desktop mirror port 127.0.0.1:7002 already has $($proxyListenerPids.Count) " +
    "unowned listener(s). Use the authenticated Reset desktop tools control; " +
    "recovery will not start another proxy or terminate an unverified process."
  )
}
if (-not $proxyAlive) {
  $proxyScript = Join-Path $scriptDir "fly-dashboard-proxy.py"
  $proxyArguments = "`"$proxyScript`" --bind 127.0.0.1 --port 7002 --upstream `"$SourceUrl`""
  $proxy = Start-Process -FilePath $python `
    -ArgumentList $proxyArguments `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -PassThru
  Set-Content -LiteralPath $proxyPidFile -Value "$($proxy.Id)" -NoNewline -Encoding UTF8
}

# Start one incremental Fly data synchronizer.
$syncLock = Join-Path $repoRoot ".fly-data-sync-loop.lock"
$syncHeartbeat = Join-Path $repoRoot ".fly-data-sync-loop.heartbeat.json"
$syncHeartbeatMaxAgeSec = 600
$syncAlive = $false
if (Test-Path -LiteralPath $syncLock) {
  try {
    $syncPid = [int](Get-Content -LiteralPath $syncLock -Raw)
    $syncProcess = Get-Process -Id $syncPid -ErrorAction SilentlyContinue
    if ($syncProcess -and $syncProcess.ProcessName -match "^(powershell|pwsh)$") {
      $syncAgeSec = ((Get-Date) - $syncProcess.StartTime).TotalSeconds
      $heartbeatAgeSec = if (Test-Path -LiteralPath $syncHeartbeat) {
        ((Get-Date).ToUniversalTime() - (Get-Item -LiteralPath $syncHeartbeat).LastWriteTimeUtc).TotalSeconds
      } else {
        $syncAgeSec
      }
      $syncAlive = ($heartbeatAgeSec -le $syncHeartbeatMaxAgeSec)
      if (-not $syncAlive) {
        Stop-Process -Id $syncPid -Force -ErrorAction SilentlyContinue
      }
    }
  } catch { }
}
if (-not $syncAlive) {
  Remove-Item -LiteralPath $syncLock,$syncHeartbeat -Force -ErrorAction SilentlyContinue
  Start-Process -FilePath "powershell.exe" `
    -ArgumentList (
      "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"" +
      (Join-Path $scriptDir "sync-fly-bot-data-loop.ps1") +
      "`" -SourceUrl `"$SourceUrl`""
    ) `
    -WindowStyle Hidden | Out-Null
}

# The existing analyzer launcher detects fly-canonical.lock.json and reads the
# synchronized mirror instead of files written by a second local bot.
& (Join-Path $scriptDir "start-home-analyzer.ps1") -Port 9001 -NoWait

if (-not $NoWait) {
  Write-Host "Fly is the sole AI/trading owner." -ForegroundColor Green
  Write-Host "Desktop :7002 proxies Fly; desktop :9001 analyzes the Fly data mirror."
}
