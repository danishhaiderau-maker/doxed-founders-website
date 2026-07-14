[CmdletBinding()]
param(
  [int]$Port = 7002,
  [string]$ExpectedSource = (Join-Path (Split-Path -Parent $PSScriptRoot) 'services\btc-conservative-agent\bot.py'),
  [int]$TimeoutSec = 15
)

# One-shot recovery for a bot listener left behind by an elevated launcher.
# It refuses to stop anything unless the listener, its health payload, and its
# source timestamp all prove that it is an older instance of this exact bot.
$ErrorActionPreference = 'Stop'

function Fail([string]$Message) {
  throw "[stale-home-bot] $Message"
}

$source = Get-Item -LiteralPath $ExpectedSource -ErrorAction Stop
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
  Select-Object -ExpandProperty OwningProcess -Unique)
if ($listeners.Count -eq 0) {
  [pscustomobject]@{ ok = $true; action = 'nothing-listening'; port = $Port } | ConvertTo-Json -Compress
  exit 0
}
if ($listeners.Count -ne 1) {
  Fail "expected exactly one listener on :$Port, found $($listeners.Count)"
}

$listenerPid = [int]$listeners[0]
try {
  $state = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/state" -TimeoutSec 8
} catch {
  Fail "listener did not provide the expected local state endpoint: $($_.Exception.Message)"
}

if ([int]$state.bot_pid -ne $listenerPid) {
  Fail "state PID $($state.bot_pid) does not match listener PID $listenerPid"
}

$reportedSource = [System.IO.Path]::GetFullPath([string]$state.bot_script)
$expectedFull = [System.IO.Path]::GetFullPath($source.FullName)
if (-not $reportedSource.Equals($expectedFull, [System.StringComparison]::OrdinalIgnoreCase)) {
  Fail "listener source does not match the canonical bot path"
}

$started = [DateTimeOffset]::FromUnixTimeSeconds([long][math]::Floor([double]$state.bot_start_time)).UtcDateTime
if ($source.LastWriteTimeUtc -le $started) {
  Fail "listener is not older than canonical source; refusing to replace it"
}

Stop-Process -Id $listenerPid -Force -ErrorAction Stop
$deadline = (Get-Date).AddSeconds($TimeoutSec)
do {
  Start-Sleep -Milliseconds 500
  $stillListening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
} while ($stillListening.Count -gt 0 -and (Get-Date) -lt $deadline)

if ($stillListening.Count -gt 0) {
  Fail "PID $listenerPid was not released from :$Port within $TimeoutSec seconds"
}

[pscustomobject]@{
  ok = $true
  action = 'stale-listener-stopped'
  port = $Port
  pid = $listenerPid
  listener_started_utc = $started.ToString('o')
  source_updated_utc = $source.LastWriteTimeUtc.ToString('o')
} | ConvertTo-Json -Compress
