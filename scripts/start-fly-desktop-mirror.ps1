param(
  [switch]$NoWait,
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev"
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$python = (Get-Command python -ErrorAction Stop).Source
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
if (Test-Path -LiteralPath $proxyPidFile) {
  try {
    $proxyPid = [int](Get-Content -LiteralPath $proxyPidFile -Raw)
    $proxyAlive = [bool](Get-Process -Id $proxyPid -ErrorAction SilentlyContinue)
  } catch { }
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
$syncAlive = $false
if (Test-Path -LiteralPath $syncLock) {
  try {
    $syncPid = [int](Get-Content -LiteralPath $syncLock -Raw)
    $syncAlive = [bool](Get-Process -Id $syncPid -ErrorAction SilentlyContinue)
  } catch { }
}
if (-not $syncAlive) {
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
