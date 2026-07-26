# Functional regression: an accepted TCP connection that never sends an HTTP
# response must not wedge the single-threaded :7810 bridge status handler.
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "home-stack-common.ps1")
# The production launcher preloads this before accepting bridge requests. Keep
# assembly initialization outside the measured request deadline here as well.
Add-Type -AssemblyName System.Net.Http -ErrorAction Stop

$listener = [System.Net.Sockets.TcpListener]::new(
  [System.Net.IPAddress]::Loopback,
  0
)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$accept = $listener.AcceptTcpClientAsync()
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
try {
  $result = Test-TunnelHttpSmart -Url "http://127.0.0.1:$port" -TimeoutSec 1
} finally {
  $stopwatch.Stop()
  if ($accept.IsCompleted -and -not $accept.IsFaulted) {
    try { $accept.Result.Close() } catch { }
  }
  $listener.Stop()
}

if ($stopwatch.ElapsedMilliseconds -gt 4000) {
  throw "bounded tunnel probe took $($stopwatch.ElapsedMilliseconds)ms"
}
if ([bool]$result.Healthy) {
  throw "blackhole HTTP server was incorrectly reported healthy"
}
$tunnelElapsedMs = $stopwatch.ElapsedMilliseconds

# Two local blackholes must share one deadline rather than consuming the timeout
# serially. This covers the bot + analyzer portion of Get-FullStatus.
$localListeners = @()
$localAccepts = @()
$urls = @()
try {
  1..2 | ForEach-Object {
    $localListener = [System.Net.Sockets.TcpListener]::new(
      [System.Net.IPAddress]::Loopback,
      0
    )
    $localListener.Start()
    $localListeners += $localListener
    $localAccepts += $localListener.AcceptTcpClientAsync()
    $localPort = ([System.Net.IPEndPoint]$localListener.LocalEndpoint).Port
    $urls += "http://127.0.0.1:$localPort/"
  }
  $parallelStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $parallelResult = Test-HttpAliveParallel -Urls $urls -TimeoutMs 1000
  $parallelStopwatch.Stop()
} finally {
  for ($i = 0; $i -lt $localListeners.Count; $i++) {
    if ($localAccepts[$i].IsCompleted -and -not $localAccepts[$i].IsFaulted) {
      try { $localAccepts[$i].Result.Close() } catch { }
    }
    $localListeners[$i].Stop()
  }
}
if ($parallelStopwatch.ElapsedMilliseconds -gt 4000) {
  throw "parallel local probes took $($parallelStopwatch.ElapsedMilliseconds)ms"
}
foreach ($url in $urls) {
  if ([bool]$parallelResult[$url]) {
    throw "blackhole local probe $url was incorrectly reported healthy"
  }
}

Write-Output (
  "PASS: bridge probes failed closed; tunnel={0}ms local-parallel={1}ms" -f
  $tunnelElapsedMs,
  $parallelStopwatch.ElapsedMilliseconds
)
