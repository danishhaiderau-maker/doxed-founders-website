# Keeps the self-aware daemon (127.0.0.1:9021) alive. Run every 5 minutes by
# the DoxxedSelfAware task from a pinned non-OneDrive checkout.
# Laptop-only: starts the daemon if nothing answers, restarts it only when the
# port is held by a hung self-aware process. Never touches Fly, trading,
# the relay or Bitfinex. Every start/restart is journalled.
param(
  [string]$HomeDir = 'C:\DoxxedCrypto\self-aware',
  [int]$Port = 9021
)

$ErrorActionPreference = 'Stop'
if ($HomeDir.ToLowerInvariant().Contains('\onedrive\') -or $PSScriptRoot.ToLowerInvariant().Contains('\onedrive\')) { exit 0 }
$python = Join-Path $HomeDir 'venv\Scripts\python.exe'
$logDir = Join-Path $HomeDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('tick-{0}.log' -f [datetime]::UtcNow.ToString('yyyyMMdd'))
$journal = Join-Path $HomeDir 'repair-journal.jsonl'
function Write-TickLog([string]$Message) {
  Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0} pid={1} {2}' -f [datetime]::UtcNow.ToString('o'), $PID, $Message)
}
function Write-Journal([string]$Action, [string]$Outcome, [string]$Reason) {
  $row = [ordered]@{ at = [datetime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'); kind = 'REPAIR'; action = $Action; mode = 'AUTO';
                     outcome = $Outcome; reason = $Reason; description = 'self-aware tick keeps the :9021 daemon alive' }
  Add-Content -LiteralPath $journal -Encoding UTF8 -Value ($row | ConvertTo-Json -Compress)
}

$answered = $false
try {
  $ping = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/ping" -TimeoutSec 20 -UseBasicParsing
  $answered = [bool]$ping.ok
} catch { }
if ($answered) { Write-TickLog 'OK'; exit 0 }

$mine = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*self_aware.engine*' })
$listening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($listening.Count -gt 0) {
  $owner = $listening[0].OwningProcess
  $ownerProc = $mine | Where-Object { $_.ProcessId -eq $owner } | Select-Object -First 1
  if (-not $ownerProc) {
    # The venv launcher's child interpreter owns the socket; match it through its parent.
    $child = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
    if ($child -and ($mine | Where-Object { $_.ProcessId -eq $child.ParentProcessId -or $_.ProcessId -eq $owner })) { $ownerProc = $child }
  }
  if (-not $ownerProc) { Write-TickLog "PORT_HELD_BY_OTHER pid=$owner"; exit 0 }
  if ($ownerProc.CreationDate -and ((Get-Date) - $ownerProc.CreationDate).TotalMinutes -lt 10) {
    Write-TickLog "STARTING pid=$owner (within 10 min start grace)"; exit 0
  }
  foreach ($p in $mine) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Write-Journal 'restart_hung_self_aware' 'EXECUTED' "port $Port held by self-aware pid $owner but /api/ping did not answer in 20 s"
  Write-TickLog "RESTART hung pid=$owner"
  Start-Sleep -Seconds 3
} elseif ($mine.Count -gt 0) {
  Write-TickLog "STARTING existing pid(s)=$($mine.ProcessId -join ',') not listening yet"
  exit 0
}

$out = Join-Path $logDir 'daemon.out.log'
$err = Join-Path $logDir 'daemon.err.log'
foreach ($f in @($out, $err)) { if ((Test-Path $f) -and (Get-Item $f).Length -gt 20MB) { Move-Item $f "$f.1" -Force } }
$env:PYTHONIOENCODING = 'utf-8'
$proc = Start-Process -FilePath $python -ArgumentList @('-m', 'self_aware.engine') -WorkingDirectory $PSScriptRoot `
  -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
Write-Journal 'start_self_aware' 'EXECUTED' "nothing answered on 127.0.0.1:$Port"
Write-TickLog "STARTED pid=$($proc.Id)"
exit 0
